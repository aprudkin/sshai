package cli

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
)

func TestShellStateReplay(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		for _, fixture := range []string{"absent", "state-only", "baseline-only", "both"} {
			t.Run(subcommand+"/"+fixture, func(t *testing.T) {
				root := t.TempDir()
				prior := shell.State{Cwd: "/synthetic", Env: map[string]string{
					"SAME": "one", "CHANGED": "two", "ADDED": "three", "PWD": "/ignored", "SSH_TEST": "ignored",
				}}
				hasState := fixture == "state-only" || fixture == "both"
				hasBase := fixture == "baseline-only" || fixture == "both"
				if hasState {
					if err := session.SaveState(root, "target", "work", prior); err != nil {
						t.Fatal(err)
					}
				}
				if hasBase {
					if err := session.SaveBaseline(root, "target", map[string]string{"SAME": "one", "CHANGED": "one", "REMOVED": "old"}); err != nil {
						t.Fatal(err)
					}
				}
				state, err := loadShellState(root, "target", "work", subcommand)
				if err != nil {
					t.Fatal(err)
				}
				wantState := shell.State{}
				wantRestore := map[string]string{}
				if hasState {
					wantState = prior
					wantRestore = map[string]string{"CHANGED": "two", "ADDED": "three"}
					if !hasBase {
						wantRestore["SAME"] = "one"
					}
				}
				if !reflect.DeepEqual(state.prior, wantState) || !reflect.DeepEqual(state.restore, wantRestore) {
					t.Fatalf("replay state=%+v restore=%v, want %+v %v", state.prior, state.restore, wantState, wantRestore)
				}
				// Preparation never creates missing snapshots.
				_, stateExists, err := session.LoadState(root, "target", "work")
				if err != nil || stateExists != hasState {
					t.Fatalf("state presence=%t err=%v", stateExists, err)
				}
				_, baseExists, err := session.LoadBaseline(root, "target")
				if err != nil || baseExists != hasBase {
					t.Fatalf("baseline presence=%t err=%v", baseExists, err)
				}
			})
		}
	}
}

func TestShellStateLoadErrors(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		for _, fixture := range []string{"state", "baseline", "both"} {
			t.Run(subcommand+"/"+fixture, func(t *testing.T) {
				root := t.TempDir()
				dir := filepath.Join(root, "state", "target")
				if err := os.MkdirAll(dir, 0o700); err != nil {
					t.Fatal(err)
				}
				for _, name := range []string{"state", "baseline"} {
					if fixture != name && fixture != "both" {
						continue
					}
					file := "baseline.json"
					if name == "state" {
						file = "work.json"
					}
					if err := os.WriteFile(filepath.Join(dir, file), []byte("{"), 0o600); err != nil {
						t.Fatal(err)
					}
				}
				_, err := loadShellState(root, "target", "work", subcommand)
				// Both invalid snapshots must still report the state failure first.
				want := subcommand + ": load state for target/work: "
				if fixture == "baseline" {
					want = subcommand + ": load baseline for target: "
				}
				if err == nil || !strings.HasPrefix(err.Error(), want) {
					t.Fatalf("error=%v, want prefix %q", err, want)
				}
			})
		}
	}
}

func TestShellStatePersistence(t *testing.T) {
	prior := shell.State{Cwd: "/before", Env: map[string]string{"KEEP": "before"}}
	for _, hasBase := range []bool{false, true} {
		for _, tc := range []struct {
			name         string
			parsed, want shell.State
		}{
			{"partial", shell.State{}, prior},
			{"cwd-only", shell.State{Cwd: "/after"}, shell.State{Cwd: "/after", Env: prior.Env}},
			{"env-only", shell.State{Env: map[string]string{"BODY": "exported"}}, shell.State{Cwd: "/before", Env: map[string]string{"BODY": "exported"}}},
			{"empty-env", shell.State{Env: map[string]string{}}, shell.State{Cwd: "/before", Env: map[string]string{}}},
			{"complete", shell.State{Cwd: "/after", Env: map[string]string{"BODY": "exported"}}, shell.State{Cwd: "/after", Env: map[string]string{"BODY": "exported"}}},
		} {
			t.Run(fmt.Sprintf("baseline=%t/%s", hasBase, tc.name), func(t *testing.T) {
				root := t.TempDir()
				if err := session.SaveState(root, "target", "work", prior); err != nil {
					t.Fatal(err)
				}
				baseline := map[string]string{"ORIGINAL": "baseline"}
				if hasBase {
					if err := session.SaveBaseline(root, "target", baseline); err != nil {
						t.Fatal(err)
					}
				}
				state, err := loadShellState(root, "target", "work", "run")
				if err != nil {
					t.Fatal(err)
				}
				var diagnostics bytes.Buffer
				state.save(tc.parsed, &diagnostics)
				got, ok, err := session.LoadState(root, "target", "work")
				if err != nil || !ok || !reflect.DeepEqual(got, tc.want) || diagnostics.Len() != 0 {
					t.Fatalf("state=%+v ok=%t err=%v diagnostics=%q, want %+v", got, ok, err, diagnostics.String(), tc.want)
				}
				gotBase, ok, err := session.LoadBaseline(root, "target")
				wantBase := tc.parsed.Env
				if hasBase {
					wantBase = baseline
				}
				if err != nil || ok != (hasBase || tc.parsed.Env != nil) || !reflect.DeepEqual(gotBase, wantBase) {
					t.Fatalf("baseline=%v ok=%t err=%v want=%v", gotBase, ok, err, wantBase)
				}
				for _, key := range [][2]string{{"other", "work"}, {"target", "other"}, {"local-pwsh", "work"}} {
					if _, ok, err := session.LoadState(root, key[0], key[1]); err != nil || ok {
						t.Fatalf("cross-key state: %v ok=%t err=%v", key, ok, err)
					}
				}
			})
		}
	}
}

func TestShellStateSaveFailures(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		for _, failure := range []string{"state", "baseline", "both"} {
			t.Run(subcommand+"/"+failure, func(t *testing.T) {
				root := t.TempDir()
				state, err := loadShellState(root, "target", "work", subcommand)
				if err != nil {
					t.Fatal(err)
				}
				dir := filepath.Join(root, "state", "target")
				if err := os.MkdirAll(dir, 0o700); err != nil {
					t.Fatal(err)
				}
				// Refuse non-regular destinations deterministically, including as root.
				statePath, basePath := filepath.Join(dir, "work.json"), filepath.Join(dir, "baseline.json")
				if failure != "baseline" {
					if err := os.Mkdir(statePath, 0o700); err != nil {
						t.Fatal(err)
					}
				}
				if failure != "state" {
					if err := os.Mkdir(basePath, 0o700); err != nil {
						t.Fatal(err)
					}
				}
				parsed := shell.State{Cwd: "/after", Env: map[string]string{"BODY": "exported"}}
				var diagnostics bytes.Buffer
				state.save(parsed, &diagnostics)
				var want string
				if failure != "baseline" {
					want += fmt.Sprintf("%s: save state for target/work: refuse non-regular state destination %s\n", subcommand, statePath)
				}
				if failure != "state" {
					want += fmt.Sprintf("%s: save baseline for target: refuse non-regular state destination %s\n", subcommand, basePath)
				}
				if diagnostics.String() != want {
					t.Fatalf("diagnostics=%q want=%q", diagnostics.String(), want)
				}
				if failure == "state" {
					got, ok, err := session.LoadBaseline(root, "target")
					if err != nil || !ok || !reflect.DeepEqual(got, parsed.Env) {
						t.Fatalf("state failure skipped baseline: %v %t %v", got, ok, err)
					}
				}
				if failure == "baseline" {
					got, ok, err := session.LoadState(root, "target", "work")
					if err != nil || !ok || !reflect.DeepEqual(got, parsed) {
						t.Fatalf("baseline failure lost state: %+v %t %v", got, ok, err)
					}
				}
			})
		}
	}
}

func TestShellStateLocalEligibility(t *testing.T) {
	for _, interpreter := range []string{"bash", "pwsh"} {
		for _, kind := range []string{"nonzero", "start", "timeout", "output-limit", "capture"} {
			t.Run(interpreter+"/"+kind, func(t *testing.T) {
				root := t.TempDir()
				t.Setenv("SSHAI_ROOT", root)
				target := "local-" + interpreter
				prior := shell.State{Cwd: "/before", Env: map[string]string{"KEEP": "before"}}
				if err := session.SaveState(root, target, "work", prior); err != nil {
					t.Fatal(err)
				}
				var out, diagnostics bytes.Buffer
				rc := localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
					// Even a complete-looking epilogue cannot make a runner failure
					// eligible for state persistence. No interpreter is executed.
					res := successfulLocalResult(t, argv, stdin, "/after", map[string]string{"BODY": "exported"}, "result\n", 7)
					switch kind {
					case "start":
						res.StartErr = os.ErrNotExist
					case "timeout":
						res.TimedOut = true
					case "output-limit":
						res.Truncated = true
					case "capture":
						res.CaptureErr = os.ErrClosed
					}
					return res
				}, []string{"--shell", interpreter, "--ctx", "work", "--", "synthetic"}, &out, &diagnostics)
				wantRC, wantState := exitUsage, prior
				if kind == "nonzero" {
					wantRC = 7
					wantState = shell.State{Cwd: "/after", Env: map[string]string{"BODY": "exported"}}
				}
				if rc != wantRC {
					t.Fatalf("rc=%d want=%d stderr=%q", rc, wantRC, diagnostics.String())
				}
				got, ok, err := session.LoadState(root, target, "work")
				if err != nil || !ok || !reflect.DeepEqual(got, wantState) {
					t.Fatalf("state=%+v ok=%t err=%v want=%+v", got, ok, err, wantState)
				}
				base, ok, err := session.LoadBaseline(root, target)
				if err != nil || ok != (kind == "nonzero") || (ok && !reflect.DeepEqual(base, wantState.Env)) {
					t.Fatalf("baseline=%v ok=%t err=%v", base, ok, err)
				}
				if !strings.Contains(out.String(), "file=") {
					t.Fatalf("missing artifact: %q", out.String())
				}
			})
		}
	}
}
