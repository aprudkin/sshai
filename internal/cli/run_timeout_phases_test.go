package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Catches a fresh upload/exec budget, cached-path bypasses, and loss of elapsed
// probing time in saved results or the follow completion envelope.
func TestWindowsRemoteBudget(t *testing.T) {
	for _, tc := range []struct {
		name   string
		cached bool
		steps  []remoteBudgetStep
		wantRC int
	}{
		{"cached-stage-timeout", true, []remoteBudgetStep{{"stage", 200 * time.Millisecond, transport.Result{}}}, exitTransport},
		{"cached-exec-timeout", true, []remoteBudgetStep{
			{"stage", 60 * time.Millisecond, transport.Result{}},
			{"exec", 60 * time.Millisecond, transport.Result{}},
		}, exitTransport},
		{"uncached-stage-timeout", false, []remoteBudgetStep{
			{"probe", 30 * time.Millisecond, transport.Result{ExitCode: 1}},
			{"setup", 30 * time.Millisecond, transport.Result{}},
			{"stage", 60 * time.Millisecond, transport.Result{}},
		}, exitTransport},
		{"uncached-exec-timeout", false, []remoteBudgetStep{
			{"probe", 20 * time.Millisecond, transport.Result{ExitCode: 1}},
			{"setup", 20 * time.Millisecond, transport.Result{}},
			{"stage", 20 * time.Millisecond, transport.Result{}},
			{"exec", 60 * time.Millisecond, transport.Result{}},
		}, exitTransport},
		{"uncached-success", false, []remoteBudgetStep{
			{"probe", 10 * time.Millisecond, transport.Result{ExitCode: 1}},
			{"setup", 10 * time.Millisecond, transport.Result{}},
			{"stage", 10 * time.Millisecond, transport.Result{}},
			{"exec", 10 * time.Millisecond, transport.Result{Output: []byte("done\n")}},
		}, 0},
	} {
		for _, follow := range []bool{false, true} {
			t.Run(tc.name+map[bool]string{false: "/normal", true: "/follow"}[follow], func(t *testing.T) {
				root := t.TempDir()
				store, err := artifact.OpenStore(root)
				if err != nil {
					t.Fatal(err)
				}
				defer store.Close()
				if tc.cached {
					if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
						t.Fatal(err)
					}
				}
				tr := &remoteBudgetTr{t: t, steps: tc.steps}
				deps := Deps{Tr: tr, Store: store}
				opts := []Opts{{Host: "windows01", Ctx: "test", Command: "Write-Output done", Timeout: 100 * time.Millisecond, Budget: 100}}
				var out, errOut bytes.Buffer
				mode := resultModeOptions{format: "json", resultOut: filepath.Join(root, "result.json")}
				var rc int
				var outcomes []RunOutcome
				start := time.Now()
				if follow {
					deps.Follow = newFollowEmitter(&errOut, "windows01", "marker", "sentinel")
					rc, outcomes = runInvocationFollow(deps, opts, mode, &out, &errOut, time.Second)
				} else {
					rc, outcomes = runInvocation(deps, opts, mode, &out, &errOut)
				}
				if rc != tc.wantRC || len(outcomes) != 1 || len(tr.kinds) != len(tc.steps) {
					t.Fatalf("rc=%d calls=%v outcomes=%v stderr=%s", rc, tr.kinds, outcomes, &errOut)
				}
				// Includes real temporary SQLite persistence and JSON publication;
				// leave scheduling headroom without tolerating a fresh remote budget.
				if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
					t.Fatalf("local completion exceeded bounded test allowance: %v", elapsed)
				}
				meta, _, err := store.Get(outcomes[0].ArtifactID())
				if err != nil {
					t.Fatal(err)
				}
				wantError := ""
				minimumDuration := int64(40)
				if tc.wantRC == exitTransport {
					wantError, minimumDuration = "timeout", 95
				}
				if meta.TransportErr != wantError || meta.DurationMs < minimumDuration || meta.DurationMs > 400 {
					t.Fatalf("saved result excludes earlier phases or has wrong class: %+v", meta)
				}
				for i := 1; i < len(tr.budgets); i++ {
					if tr.budgets[i] > tr.budgets[i-1]-tc.steps[i-1].cost {
						t.Fatalf("phase %d renewed the budget: %v", i, tr.budgets)
					}
				}
				var envelope struct {
					Runs []struct {
						TransportError string `json:"transport_error"`
						DurationMs     int64  `json:"duration_ms"`
					} `json:"runs"`
				}
				if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 || envelope.Runs[0].DurationMs != meta.DurationMs || envelope.Runs[0].TransportError != wantError {
					t.Fatalf("JSON and artifact disagree: %s err=%v", &out, err)
				}
				published, err := os.ReadFile(mode.resultOut)
				if err != nil || !bytes.Equal(published, out.Bytes()) {
					t.Fatalf("result publication differs, err=%v", err)
				}
				if follow {
					var event struct {
						Type        string `json:"type"`
						ProcessExit int    `json:"process_exit"`
						Outcome     struct {
							TransportError string `json:"transport_error"`
							DurationMs     int64  `json:"duration_ms"`
						} `json:"outcome"`
					}
					// This fake emits no live output; only completed should appear.
					if err := json.Unmarshal(bytes.TrimSpace(errOut.Bytes()), &event); err != nil || event.Type != "completed" || event.Outcome.TransportError != wantError || event.Outcome.DurationMs != meta.DurationMs || event.ProcessExit != tc.wantRC {
						t.Fatalf("follow completion disagrees: %s err=%v", &errOut, err)
					}
				}
			})
		}
	}
}

// Models successful delivery returned during cleanup after the deadline. A
// later body must not start merely because the previous phase reported success.
type expiredPhaseTr struct {
	phase string
	calls []string
}

func (f *expiredPhaseTr) Exec(_ string, cmd string, _ []byte, timeout time.Duration) (transport.Result, error) {
	f.calls = append(f.calls, "exec")
	if cmd == "uname -s" && f.phase == "probe" {
		time.Sleep(timeout + time.Millisecond)
		return transport.Result{Output: []byte("Linux\n")}, nil
	}
	return transport.Result{}, nil
}
func (f *expiredPhaseTr) Put(_, _, _ string, timeout time.Duration) error {
	f.calls = append(f.calls, "stage")
	time.Sleep(timeout + time.Millisecond)
	return nil
}

func TestRunDoesNotStartPhaseAfterExpiration(t *testing.T) {
	for _, phase := range []string{"probe", "stage", "cached-linux", "cached-windows"} {
		t.Run(phase, func(t *testing.T) {
			root := t.TempDir()
			store, err := artifact.OpenStore(root)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			timeout := 20 * time.Millisecond
			if phase != "probe" {
				osName := "windows"
				if phase == "cached-linux" {
					osName = "linux"
				}
				if err := session.SaveFacts(root, "host01", session.Facts{OS: osName, Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
					t.Fatal(err)
				}
			}
			if strings.HasPrefix(phase, "cached-") {
				timeout = 0
			}
			tr := &expiredPhaseTr{phase: phase}
			var out, errOut bytes.Buffer
			outcome := runHost(Deps{Tr: tr, Store: store}, Opts{Host: "host01", Ctx: "test", Command: "echo done", Timeout: timeout}, &out, &errOut)
			meta, ok := outcome.Meta()
			wantCalls := 1
			if timeout == 0 {
				wantCalls = 0
			}
			if !ok || meta.TransportErr != "timeout" || meta.TransportDiagnostic != "operation timed out" || len(tr.calls) != wantCalls {
				t.Fatalf("outcome=%+v calls=%v stderr=%s", outcome, tr.calls, &errOut)
			}
		})
	}
}

type fanoutBudgetTr map[string]*remoteBudgetTr

func (f fanoutBudgetTr) Exec(host, cmd string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	return f[host].Exec(host, cmd, stdin, timeout)
}
func (f fanoutBudgetTr) Put(host, local, remote string, timeout time.Duration) error {
	return f[host].Put(host, local, remote, timeout)
}

func TestFanoutTimeoutsAreIndependent(t *testing.T) {
	store, err := artifact.OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	for _, host := range []string{"short", "long"} {
		if err := session.SaveFacts(store.Root, host, session.Facts{OS: "linux"}); err != nil {
			t.Fatal(err)
		}
	}
	tr := fanoutBudgetTr{
		"short": {t: t, steps: []remoteBudgetStep{{"exec", 200 * time.Millisecond, transport.Result{}}}},
		"long":  {t: t, steps: []remoteBudgetStep{{"exec", 150 * time.Millisecond, transport.Result{}}}},
	}
	var out, errOut bytes.Buffer
	rc, outcomes := runInvocation(Deps{Tr: tr, Store: store}, []Opts{
		{Host: "short", Ctx: "test", Command: "true", Timeout: 100 * time.Millisecond},
		{Host: "long", Ctx: "test", Command: "true", Timeout: 300 * time.Millisecond},
	}, resultModeOptions{format: "json"}, &out, &errOut)
	if rc != exitTransport || len(outcomes) != 2 || outcomes[0].Kind() != runOutcomeTransportFailure || outcomes[1].Kind() != runOutcomeSuccess {
		t.Fatalf("one host affected another: rc=%d outcomes=%+v stderr=%s", rc, outcomes, &errOut)
	}
	if tr["short"].budgets[0] > 100*time.Millisecond || tr["long"].budgets[0] < 200*time.Millisecond {
		t.Fatalf("host budgets shared: short=%v long=%v", tr["short"].budgets, tr["long"].budgets)
	}
}
