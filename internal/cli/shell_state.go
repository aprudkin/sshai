package cli

import (
	"fmt"
	"io"

	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
)

// shellStateLifecycle prepares replay and retains eligible parsed updates for
// one invocation. Callers own execution and parsing eligibility; no-state runs
// must bypass loading entirely. Persistence uses the state observed at load,
// without introducing locking or changing concurrent-update semantics.
type shellStateLifecycle struct {
	prior   shell.State
	restore map[string]string

	root, host, ctx, subcommand string
	haveBaseline                bool
}

func loadShellState(root, host, ctx, subcommand string) (shellStateLifecycle, error) {
	st, _, err := session.LoadState(root, host, ctx)
	if err != nil {
		return shellStateLifecycle{}, fmt.Errorf("%s: load state for %s/%s: %w", subcommand, host, ctx, err)
	}
	baseline, haveBaseline, err := session.LoadBaseline(root, host)
	if err != nil {
		return shellStateLifecycle{}, fmt.Errorf("%s: load baseline for %s: %w", subcommand, host, err)
	}
	return shellStateLifecycle{
		prior: st, restore: shell.EnvRestoreSet(baseline, st.Env),
		root: root, host: host, ctx: ctx, subcommand: subcommand,
		haveBaseline: haveBaseline,
	}, nil
}

// save is called once, only for an eligible parsed update. Persistence failures
// are diagnostic-only: a state-save failure must not skip the baseline attempt
// or prevent artifact finalization.
func (s shellStateLifecycle) save(parsed shell.State, stderr io.Writer) {
	// A partial epilogue has empty cwd or nil environment for fields that were
	// not updated. A non-nil empty environment, however, replaces the old map.
	merged := s.prior
	if parsed.Cwd != "" {
		merged.Cwd = parsed.Cwd
	}
	if parsed.Env != nil {
		merged.Env = parsed.Env
	}
	if err := session.SaveState(s.root, s.host, s.ctx, merged); err != nil {
		fmt.Fprintf(stderr, "%s: save state for %s/%s: %v\n", s.subcommand, s.host, s.ctx, err)
	}

	// Preserve the accepted first-contact simplification: this run's parsed
	// environment becomes the baseline, including values exported by its body.
	// Use parsed.Env, not merged.Env; a partial parse cannot seed a baseline.
	if !s.haveBaseline && parsed.Env != nil {
		if err := session.SaveBaseline(s.root, s.host, parsed.Env); err != nil {
			fmt.Fprintf(stderr, "%s: save baseline for %s: %v\n", s.subcommand, s.host, err)
		}
	}
}
