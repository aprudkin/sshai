// internal/cli/query.go
package cli

import (
	"bytes"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"unicode/utf8"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/config"
	"github.com/pmezard/go-difflib/difflib"
)

// defaultQueryBudget is q/diff's own --budget default (500 tokens). This is
// deliberately independent of config.BudgetTokens (also 500 today, but that
// is `run`'s output budget and could diverge from this one in the future) —
// per the task brief, "this is q's own default, independent of config's run
// budget".
const defaultQueryBudget = 500

// clipSuffix is appended verbatim (per the task brief) when trimToBudget
// clips output. The trailing "N" is literal, not a substituted number: it
// tells the caller which flag to use, not what value would have avoided the
// clip.
const clipSuffix = "\n… [output clipped at budget — raw tool output preserved nowhere, rerun with --budget N]"

// trimToBudget truncates s to approximately budgetTokens tokens (using
// artifact.EstTokens's own ~4-bytes-per-token estimate, inverted) and
// appends clipSuffix when truncation happened. It is a small local helper
// rather than a reuse of artifact.RenderPassport's trimmer: that function is
// passport-shaped (status line + tail3 fallback for command output baked
// into a run's own artifact) and does not fit trimming arbitrary local-tool
// stdout/stderr for q/diff.
func trimToBudget(s string, budgetTokens int) string {
	if budgetTokens <= 0 || artifact.EstTokens([]byte(s)) <= budgetTokens {
		return s
	}
	maxBytes := budgetTokens * 4
	if maxBytes > len(s) {
		maxBytes = len(s)
	}
	if maxBytes < 0 {
		maxBytes = 0
	}
	// This tool's remote output can be arbitrary UTF-8 (Cyrillic hostnames,
	// pwsh CLIXML, etc.), so a raw byte cut can land mid-rune. Back off to
	// the start of the rune straddling the cut — never forward, so the
	// result never exceeds the requested budget by even one byte.
	for maxBytes > 0 && maxBytes < len(s) && !utf8.RuneStart(s[maxBytes]) {
		maxBytes--
	}
	return s[:maxBytes] + clipSuffix
}

// resolveArtifact opens the store's root and resolves id to its artifact
// file path, printing a distinct stderr message for each of the two ways a
// lookup can fail:
//   - unknown id: no such run was ever recorded.
//   - pruned id: the run is recorded (Meta is populated) but its artifact
//     bytes were reclaimed by retention (artifact.ErrPruned).
//
// Both are exit 96 (usage: the id given is not usable as-is) — the brief
// explicitly allows sharing the exit code as long as the messages differ,
// which is what lets a caller (human or agent) tell the two apart from
// stderr alone.
func resolveArtifact(store *artifact.Store, id string, cmdName string, stderr io.Writer) (path string, ok bool) {
	_, path, err := store.Get(id)
	if err != nil {
		if errors.Is(err, artifact.ErrPruned) {
			fmt.Fprintf(stderr, "%s: artifact %s has been pruned (metadata retained, data reclaimed by retention)\n", cmdName, id)
		} else {
			fmt.Fprintf(stderr, "%s: unknown artifact id %s: %v\n", cmdName, id, err)
		}
		return "", false
	}
	return path, true
}

// openQueryStore loads config (for its Root only — q/diff never touch
// config.BudgetTokens, see defaultQueryBudget) and opens the artifact store
// beneath it.
func openQueryStore() (*artifact.Store, error) {
	cfg, err := config.Load()
	if err != nil {
		return nil, fmt.Errorf("load config: %w", err)
	}
	store, err := artifact.OpenStore(cfg.Root)
	if err != nil {
		return nil, fmt.Errorf("open store: %w", err)
	}
	return store, nil
}

// Q implements `sshai q <id> -- <tool> <args...>`: it resolves id to its
// artifact's local file path and runs tool locally against it, with the
// path appended as tool's final argument, so an agent can grep/jq/awk a
// stored artifact without ever pulling its raw bytes back into context.
// stdout and stderr are each budget-trimmed independently (see
// trimToBudget) and the local tool's own exit code is mirrored — except
// when the tool binary itself cannot be found (exit 96, per the reserved
// usage code).
func Q(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("q", flag.ContinueOnError)
	fs.SetOutput(stderr)
	budget := fs.Int("budget", defaultQueryBudget, "output budget in tokens (~bytes/4)")

	if err := fs.Parse(args); err != nil {
		return exitUsage
	}

	rest := fs.Args()
	before, after, found := splitAtDashDash(rest)
	if !found || len(before) != 1 || len(after) == 0 {
		fmt.Fprintln(stderr, "q: usage: sshai q [--budget N] <id> -- <tool> <args...>")
		return exitUsage
	}
	id := before[0]
	toolName, toolArgs := after[0], after[1:]

	toolPath, err := exec.LookPath(toolName)
	if err != nil {
		fmt.Fprintf(stderr, "q: tool %q not found in PATH: %v\n", toolName, err)
		return exitUsage
	}

	store, err := openQueryStore()
	if err != nil {
		fmt.Fprintf(stderr, "q: %v\n", err)
		return exitUsage
	}
	defer store.Close()

	path, ok := resolveArtifact(store, id, "q", stderr)
	if !ok {
		return exitUsage
	}

	// The path is appended as the FINAL argument, per the brief's exact
	// exec.Command(tool, append(args, path)...) shape — toolArgs is copied
	// first so the caller-supplied slice is never mutated by append.
	fullArgs := append(append([]string{}, toolArgs...), path)
	cmd := exec.Command(toolPath, fullArgs...)
	var outBuf, errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	runErr := cmd.Run()

	io.WriteString(stdout, trimToBudget(outBuf.String(), *budget))
	io.WriteString(stderr, trimToBudget(errBuf.String(), *budget))

	if runErr == nil {
		return 0
	}
	var exitErr *exec.ExitError
	if errors.As(runErr, &exitErr) {
		code := exitErr.ExitCode()
		if code < 0 {
			// Negative means the tool died from a signal (ExitCode's own
			// documented contract), not a normal exit — passing that
			// straight to os.Exit would wrap to a nonsensical status (e.g.
			// -1 -> 255, indistinguishable from a real exit 255). Report it
			// and fall back to the reserved usage code instead of
			// mirroring a code that was never a real exit status.
			fmt.Fprintf(stderr, "q: %s: %v\n", toolName, exitErr)
			return exitUsage
		}
		return code
	}
	// The tool binary was found via LookPath but still failed to start
	// (e.g. permission denied, not actually executable) — not one of the
	// reserved policy/transport codes, so this falls back to usage.
	fmt.Fprintf(stderr, "q: run %s: %v\n", toolName, runErr)
	return exitUsage
}

// Diff implements `sshai diff <id1> <id2>`: it reads both artifacts'
// bytes, unified-diffs them locally with go-difflib, budget-trims the
// result, and prints it. Two artifacts with identical content produce an
// empty diff (go-difflib's own behavior — GetGroupedOpCodes returns no
// groups when every line matches), which is reported as the friendlier
// "no difference" rather than a blank line.
func Diff(args []string, stdout, stderr io.Writer) int {
	fs := flag.NewFlagSet("diff", flag.ContinueOnError)
	fs.SetOutput(stderr)
	budget := fs.Int("budget", defaultQueryBudget, "output budget in tokens (~bytes/4)")

	if err := fs.Parse(args); err != nil {
		return exitUsage
	}

	rest := fs.Args()
	if len(rest) != 2 {
		fmt.Fprintln(stderr, "diff: usage: sshai diff [--budget N] <id1> <id2>")
		return exitUsage
	}
	id1, id2 := rest[0], rest[1]

	store, err := openQueryStore()
	if err != nil {
		fmt.Fprintf(stderr, "diff: %v\n", err)
		return exitUsage
	}
	defer store.Close()

	path1, ok := resolveArtifact(store, id1, "diff", stderr)
	if !ok {
		return exitUsage
	}
	path2, ok := resolveArtifact(store, id2, "diff", stderr)
	if !ok {
		return exitUsage
	}

	data1, err := os.ReadFile(path1)
	if err != nil {
		fmt.Fprintf(stderr, "diff: read artifact %s: %v\n", id1, err)
		return exitUsage
	}
	data2, err := os.ReadFile(path2)
	if err != nil {
		fmt.Fprintf(stderr, "diff: read artifact %s: %v\n", id2, err)
		return exitUsage
	}

	diffText, err := difflib.GetUnifiedDiffString(difflib.UnifiedDiff{
		A:        linesOf(data1),
		B:        linesOf(data2),
		FromFile: id1,
		ToFile:   id2,
		Context:  3,
	})
	if err != nil {
		fmt.Fprintf(stderr, "diff: compute diff: %v\n", err)
		return exitUsage
	}

	if diffText == "" {
		fmt.Fprintln(stdout, "no difference")
		return 0
	}

	// Exit 0 here too, deliberately: the brief only pins the identical
	// case's exit code, and this is a query tool for an agent to read
	// output from, not POSIX diff(1) — a caller expecting diff(1)'s exit 1
	// on a non-empty diff will not get it. If that distinction ever
	// matters to a caller, it's already visible in the output itself
	// ("no difference" line vs. a printed diff).
	io.WriteString(stdout, trimToBudget(diffText, *budget))
	return 0
}

// linesOf splits an artifact's raw bytes into difflib's expected line
// form (each element keeps its trailing "\n", per difflib.SplitLines).
func linesOf(data []byte) []string {
	return difflib.SplitLines(string(data))
}
