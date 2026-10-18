package cli

import (
	"bytes"
	"fmt"
	"io"
	"path/filepath"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/delta"
	"github.com/aprudkin/sshai/internal/runlog"
)

// runFinalization contains an already classified result and retained output.
// Callers prepare safe command/audit representations and any remote-only pipe
// advisory; they do not coordinate persistence, presentation or recovery.
// meta.Ts is the artifact timestamp, not the later audit timestamp.
type runFinalization struct {
	meta           artifact.Meta
	output         []byte
	delta          bool
	budget         int
	subcommand     string // "run" or "local"; also the diagnostic prefix
	commandPreview string
	bodySHA256     string
	pipeAdvisory   string // empty for local execution
}

// finalizeRun completes evidence for runHost/runLocal, including retained local
// runner failures. Remote setup/transport failures have separate handlers.
// The writers are per-host buffers; writeRunResults still owns publication.
func finalizeRun(store *artifact.Store, result runFinalization, stdout, stderr io.Writer) RunOutcome {
	meta := result.meta
	out := result.output
	meta.Binary = bytes.IndexByte(out[:min(8192, len(out))], 0) >= 0
	key := delta.Key(meta.Host, meta.Ctx, meta.Command)

	// Lookup must precede Save, both to exclude this run and to persist its
	// DeltaBase in that same save. Lookup failure must not discard new output.
	var prevMeta artifact.Meta
	var havePrev bool
	if result.delta {
		var err error
		prevMeta, havePrev, err = store.LastByKeyMode(key, meta.ShellStateMode)
		if err != nil {
			fmt.Fprintf(stderr, "%s: delta lookup for %s: %v\n", result.subcommand, meta.Host, err)
			havePrev = false
		}
		if havePrev {
			meta.DeltaBase = prevMeta.ID
		}
	}

	savedMeta, err := store.Save(meta, key, out)
	if err != nil {
		fmt.Fprintf(stderr, "%s: save artifact: %v\n", result.subcommand, err)
		return newInternalFailureOutcome(exitUsage)
	}

	artPath := filepath.Join(store.Root, "art", savedMeta.ID)
	var passport string
	switch {
	case result.delta && havePrev && !savedMeta.Binary:
		prevPath := filepath.Join(store.Root, "art", prevMeta.ID)
		deltaBody, err := delta.Render(prevPath, out, prevMeta.ID, prevMeta.Ts, result.budget)
		if err != nil {
			// Save and DeltaBase survive a missing predecessor body or other
			// rendering error; only presentation falls back to the passport.
			fmt.Fprintf(stderr, "%s: render delta for %s: %v\n", result.subcommand, meta.Host, err)
			passport = artifact.RenderPassport(savedMeta, artPath, out, result.budget)
		} else {
			passport = artifact.StatusLine(savedMeta) + "\nfile=" + artPath + "\n" + deltaBody
		}
	default:
		// Binary output stays metadata-only, without losing a valid DeltaBase.
		passport = artifact.RenderPassport(savedMeta, artPath, out, result.budget)
		if result.delta && !havePrev {
			passport += "\ndelta: no previous run for this key" // #nosec G101 -- user-facing delta status, not a credential.
		}
	}
	if result.pipeAdvisory != "" {
		passport += "\n" + result.pipeAdvisory
	}
	fmt.Fprintln(stdout, passport)

	// Audit is best-effort and follows passport output, with its own timestamp.
	if err := runlog.AppendAudit(store.Root, runlog.AuditEntry{
		Ts: time.Now(), Host: meta.Host, Ctx: meta.Ctx, Subcommand: result.subcommand,
		CommandPreview: result.commandPreview, BodySHA256: result.bodySHA256,
		Verdict: "allowed", Exit: meta.Exit, LocalError: meta.LocalError,
	}); err != nil {
		fmt.Fprintf(stderr, "%s: append audit: %v\n", result.subcommand, err)
	}
	return newSavedRunOutcome(savedMeta)
}
