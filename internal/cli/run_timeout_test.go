package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

// Each step models remote work with a known cost. Its per-call timeout is
// honored; the production orchestration must prevent those timers accumulating.
type remoteBudgetStep struct {
	kind string
	cost time.Duration
	res  transport.Result
}

type remoteBudgetTr struct {
	t       *testing.T
	steps   []remoteBudgetStep
	budgets []time.Duration
	kinds   []string
}

func (f *remoteBudgetTr) step(kind string, timeout time.Duration) (transport.Result, error) {
	f.t.Helper()
	i := len(f.kinds)
	if i >= len(f.steps) || f.steps[i].kind != kind {
		f.t.Errorf("unexpected phase %q after %v", kind, f.kinds)
		return transport.Result{}, &transport.TransportError{Reason: "ssh"}
	}
	f.kinds = append(f.kinds, kind)
	f.budgets = append(f.budgets, timeout)
	step := f.steps[i]
	if timeout <= step.cost {
		time.Sleep(max(timeout, 0))
		return transport.Result{}, &transport.TransportError{Reason: "timeout"}
	}
	time.Sleep(step.cost)
	return step.res, nil
}

func (f *remoteBudgetTr) Exec(_ string, cmd string, _ []byte, timeout time.Duration) (transport.Result, error) {
	kind := "exec"
	if cmd == "uname -s" {
		kind = "probe"
	} else if strings.Contains(cmd, "New-Item") {
		kind = "setup"
	}
	return f.step(kind, timeout)
}

func (f *remoteBudgetTr) Put(_, _, _ string, timeout time.Duration) error {
	_, err := f.step("stage", timeout)
	return err
}

func (f *remoteBudgetTr) ExecStream(host, cmd string, stdin []byte, timeout time.Duration, _ func([]byte)) (transport.Result, error) {
	return f.Exec(host, cmd, stdin, timeout)
}

func TestRunHostSharesProbeAndExecutionBudget(t *testing.T) {
	for _, follow := range []bool{false, true} {
		t.Run(map[bool]string{false: "normal", true: "follow"}[follow], func(t *testing.T) {
			store, err := artifact.OpenStore(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			tr := &remoteBudgetTr{t: t, steps: []remoteBudgetStep{
				{"probe", 60 * time.Millisecond, transport.Result{Output: []byte("Linux\n")}},
				{"exec", 60 * time.Millisecond, transport.Result{Output: []byte("done\n")}},
			}}
			deps := Deps{Tr: tr, Store: store}
			if follow {
				deps.Follow = newFollowEmitter(&bytes.Buffer{}, "linux01", "marker", "sentinel")
			}
			var out, errOut bytes.Buffer
			outcome := runHost(deps, Opts{Host: "linux01", Ctx: "test", Command: "echo done", Timeout: 100 * time.Millisecond}, &out, &errOut)
			if follow {
				deps.Follow.completed(store.Root, outcome, outcome.ExitCode(), errOut.String())
			}
			meta, _, err := store.Get(outcome.ArtifactID())
			if err != nil || meta.TransportErr != "timeout" {
				t.Fatalf("want saved timeout, meta=%+v err=%v stdout=%s stderr=%s", meta, err, &out, &errOut)
			}
			if meta.DurationMs < 95 || meta.DurationMs > 400 {
				t.Fatalf("duration must include probe and bounded execution, got %dms", meta.DurationMs)
			}
			if len(tr.kinds) != 2 || tr.budgets[1] > 40*time.Millisecond {
				t.Fatalf("execution did not receive remaining budget: %v %v", tr.kinds, tr.budgets)
			}
		})
	}
}

func TestRunConfiguredAndExplicitTimeoutShareSemantics(t *testing.T) {
	for _, explicit := range []bool{false, true} {
		t.Run(map[bool]string{false: "configured", true: "explicit"}[explicit], func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			configured := "timeout_sec = 1\n"
			args := []string{"linux01", "--", "echo done"}
			if explicit {
				configured = "timeout_sec = 9\n"
				args = append([]string{"--timeout", "1"}, args...)
			}
			if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(configured), 0o600); err != nil {
				t.Fatal(err)
			}
			tr := &remoteBudgetTr{t: t, steps: []remoteBudgetStep{
				{"probe", 20 * time.Millisecond, transport.Result{Output: []byte("Linux\n")}},
				{"exec", 0, transport.Result{Output: []byte("done\n")}},
			}}
			var out, errOut bytes.Buffer
			if rc := runWith(tr, args, &out, &errOut); rc != 0 {
				t.Fatalf("rc=%d stderr=%s", rc, &errOut)
			}
			if len(tr.budgets) != 2 || tr.budgets[0] > time.Second || tr.budgets[0] < 500*time.Millisecond || tr.budgets[1] > tr.budgets[0]-20*time.Millisecond {
				t.Fatalf("want one 1s budget, got %v", tr.budgets)
			}
			// A cached invocation has no probe but retains the same total budget.
			if _, ok, err := session.LoadFacts(root, "linux01"); err != nil || !ok {
				t.Fatalf("facts ok=%v err=%v", ok, err)
			}
			tr = &remoteBudgetTr{t: t, steps: []remoteBudgetStep{{"exec", 0, transport.Result{}}}}
			if rc := runWith(tr, args, &out, &errOut); rc != 0 || len(tr.budgets) != 1 || tr.budgets[0] > time.Second || tr.budgets[0] < 500*time.Millisecond {
				t.Fatalf("cached rc=%d budgets=%v stderr=%s", rc, tr.budgets, &errOut)
			}
		})
	}
}
