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
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Failures are selected by orchestration, never inferred from diagnostic text.
// The synthetic side effect models work that survived a local transport failure.
type failureEvidenceTr struct {
	phase, sideEffect, marker string
	err                       error
	exit                      int
}

func (f *failureEvidenceTr) Exec(_ string, command string, _ []byte, _ time.Duration) (transport.Result, error) {
	if command == "uname -s" {
		if f.phase == "probe" {
			return transport.Result{}, f.err
		}
		return transport.Result{Output: []byte("Linux\n")}, nil
	}
	if f.sideEffect != "" {
		if err := os.WriteFile(f.sideEffect, []byte("body dispatched\n"), 0o600); err != nil {
			return transport.Result{}, err
		}
	}
	if f.phase == "exec" {
		return transport.Result{}, f.err
	}
	return transport.Result{ExitCode: f.exit, Output: []byte("command output\n")}, nil
}
func (f *failureEvidenceTr) Put(string, string, string, time.Duration) error {
	if f.phase == "stage" {
		return f.err
	}
	return nil
}
func (f *failureEvidenceTr) ExecStream(host, command string, stdin []byte, timeout time.Duration, output func([]byte)) (transport.Result, error) {
	if f.marker != "" {
		output([]byte(f.marker + "\n"))
	}
	return f.Exec(host, command, stdin, timeout)
}

func assertFailureEvidence(t *testing.T, entry map[string]any, phase, completion string) {
	t.Helper()
	for key, want := range map[string]string{"failure_phase": phase, "remote_completion": completion} {
		got, exists := entry[key]
		if want == "" {
			if exists {
				t.Errorf("%s must be omitted, got %v", key, got)
			}
		} else if got != want {
			t.Errorf("%s=%v, want %s", key, got, want)
		}
	}
}

// Catches missing/wrong phase routing, false certainty after dispatch, and
// disagreement between SQLite, JSON, human passports and follow completion.
func TestRemoteFailureEvidence(t *testing.T) {
	for _, tc := range []struct {
		name, phase, completion, osName string
		cached                          bool
		exit                            int
		err                             error
	}{
		{"probe", "probe", "not_started", "", false, exitTransport, transport.NewTransportError("ssh", []byte("Host key verification failed."))},
		{"probe-timeout", "probe", "not_started", "", false, exitTransport, &transport.TransportError{Reason: "timeout"}},
		{"stage", "stage", "not_started", "windows", true, exitTransport, transport.NewTransportError("scp", []byte("private diagnostic secret"))},
		{"stage-timeout", "stage", "not_started", "windows", true, exitTransport, &transport.TransportError{Reason: "timeout"}},
		{"exec-linux", "exec", "unknown", "linux", true, exitTransport, transport.NewTransportError("ssh", []byte("Host key verification failed."))},
		{"exec-windows", "exec", "unknown", "windows", true, exitTransport, &transport.TransportError{Reason: "ssh"}},
		{"exec-timeout-side-effect", "exec", "unknown", "linux", true, exitTransport, &transport.TransportError{Reason: "timeout"}},
		{"success", "", "", "linux", true, 0, nil},
		{"success-windows", "", "", "windows", true, 0, nil},
		{"remote-nonzero", "", "", "linux", true, 98, nil},
		{"remote-nonzero-windows", "", "", "windows", true, 98, nil},
		{"windows-setup", "probe", "not_started", "", false, exitSetup, nil},
	} {
		for _, follow := range []bool{false, true} {
			t.Run(tc.name+map[bool]string{false: "/normal", true: "/follow"}[follow], func(t *testing.T) {
				store := openOutcomeTestStore(t, t.TempDir())
				if tc.cached {
					if err := session.SaveFacts(store.Root, "host01", session.Facts{OS: tc.osName, Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
						t.Fatal(err)
					}
				}
				fake := &failureEvidenceTr{phase: tc.phase, err: tc.err}
				if strings.HasPrefix(tc.name, "remote-nonzero") {
					fake.exit = tc.exit
				}
				if tc.name == "exec-timeout-side-effect" {
					fake.sideEffect = filepath.Join(t.TempDir(), "side-effect")
				}
				var tr transport.Transport = fake
				if tc.name == "windows-setup" {
					tr = &setupFailureTr{}
				}
				deps := Deps{Tr: tr, Store: store}
				var out, errOut bytes.Buffer
				opts := []Opts{{Host: "host01", Ctx: "test", Command: "synthetic body", Timeout: time.Second, Budget: 500}}
				var rc int
				var outcomes []RunOutcome
				if follow {
					deps.Follow = newFollowEmitter(&errOut, "host01", "marker", "sentinel")
					// A started marker and even a canonical preconnection diagnostic
					// do not change the uncertainty of an attempted body dispatch.
					fake.marker = "marker"
					rc, outcomes = runInvocationFollow(deps, opts, resultModeOptions{format: "json"}, &out, &errOut, time.Second)
				} else {
					rc, outcomes = runInvocation(deps, opts, resultModeOptions{format: "json"}, &out, &errOut)
				}
				if rc != tc.exit || len(outcomes) != 1 {
					t.Fatalf("rc=%d outcomes=%v stderr=%s", rc, outcomes, &errOut)
				}
				var envelope struct {
					SchemaVersion string           `json:"schema_version"`
					Runs          []map[string]any `json:"runs"`
				}
				if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 || envelope.SchemaVersion != "v1" {
					t.Fatalf("JSON=%s err=%v", &out, err)
				}
				assertFailureEvidence(t, envelope.Runs[0], tc.phase, tc.completion)
				meta, path, err := store.Get(outcomes[0].ArtifactID())
				if err != nil {
					t.Fatal(err)
				}
				var persistedPhase, persistedCompletion string
				if err := store.DB.QueryRow(`SELECT failure_phase, remote_completion FROM runs WHERE art_id=?`, meta.ID).Scan(&persistedPhase, &persistedCompletion); err != nil {
					t.Fatal(err)
				}
				if persistedPhase != tc.phase || persistedCompletion != tc.completion {
					t.Errorf("persisted evidence=(%q,%q), want (%q,%q)", persistedPhase, persistedCompletion, tc.phase, tc.completion)
				}
				body, err := os.ReadFile(path)
				if err != nil {
					t.Fatal(err)
				}
				passport := artifact.RenderPassport(meta, path, body, 500)
				for key, value := range map[string]string{"failure-phase": tc.phase, "remote-completion": tc.completion} {
					if value == "" && strings.Contains(passport, key+"=") || value != "" && !strings.Contains(passport, key+"="+value) {
						t.Errorf("passport evidence: %s", passport)
					}
				}
				if bytes.Contains(body, []byte("private diagnostic")) || bytes.Contains(out.Bytes(), []byte("private diagnostic")) || strings.Contains(passport, rawSetupOutput) {
					t.Fatalf("raw diagnostic leaked: %s %s", body, &out)
				}
				if fake.sideEffect != "" {
					if data, err := os.ReadFile(fake.sideEffect); err != nil || string(data) != "body dispatched\n" {
						t.Fatalf("side-effect fixture: %q err=%v", data, err)
					}
				}
				if follow {
					events := followEvents(t, &errOut)
					last := events[len(events)-1]
					assertFailureEvidence(t, last["outcome"].(map[string]any), tc.phase, tc.completion)
				}
			})
		}
	}
}

// Catches labeling a rejected dispatch as unknown merely because a prior
// probe/upload used Exec/Put or the budget expired immediately before Exec.
func TestFailureEvidenceBeforeBodyDispatch(t *testing.T) {
	for _, phase := range []string{"probe", "stage", "cached-linux"} {
		t.Run(phase, func(t *testing.T) {
			store := openOutcomeTestStore(t, t.TempDir())
			timeout := 10 * time.Millisecond
			if phase != "probe" {
				osName := "windows"
				if phase == "cached-linux" {
					osName, timeout = "linux", 0
				}
				if err := session.SaveFacts(store.Root, "host01", session.Facts{OS: osName, Shell: shell.PwshDefaultShell, Form: "cmd"}); err != nil {
					t.Fatal(err)
				}
			}
			var out, errOut bytes.Buffer
			outcome := runHost(Deps{Tr: &expiredPhaseTr{phase: phase}, Store: store}, Opts{Host: "host01", Ctx: "test", Command: "synthetic body", Timeout: timeout}, &out, &errOut)
			meta, _, err := store.Get(outcome.ArtifactID())
			if err != nil {
				t.Fatal(err)
			}
			var entry map[string]any
			encoded, _ := json.Marshal(artifact.ResultEntryForMeta(store.Root, meta))
			if err := json.Unmarshal(encoded, &entry); err != nil {
				t.Fatal(err)
			}
			assertFailureEvidence(t, entry, "exec", "not_started")
		})
	}
}

// Storage failure must not invent a saved artifact or dispatch evidence.
// Absent fields on this unchanged internal outcome do not prove non-execution.
func TestUnsavedRemoteFailureEvidence(t *testing.T) {
	for _, phase := range []string{"probe", "exec", "setup"} {
		t.Run(phase, func(t *testing.T) {
			store := openOutcomeTestStore(t, t.TempDir())
			if phase == "exec" {
				seedLinuxFacts(t, store.Root, "host01")
			}
			poisonArtifactDir(t, store.Root)
			var out, errOut bytes.Buffer
			var tr transport.Transport = &failureEvidenceTr{phase: phase, err: &transport.TransportError{Reason: "ssh"}}
			wantExit := exitTransport
			if phase == "setup" {
				tr, wantExit = &setupFailureTr{}, exitSetup
			}
			deps := Deps{Tr: tr, Store: store, Follow: newFollowEmitter(&errOut, "host01", "marker", "sentinel")}
			// Setup fixture fails before reaching the streaming capability check.
			rc, outcomes := runInvocationFollow(deps, []Opts{{Host: "host01", Ctx: "test", Command: "synthetic body", Timeout: time.Second}}, resultModeOptions{format: "json"}, &out, &errOut, time.Second)
			if rc != exitUsage || outcomes[0].ExitCode() != wantExit || outcomes[0].Kind() != runOutcomeInternalFailure || outcomes[0].ArtifactID() != "" {
				t.Fatalf("classification changed: rc=%d outcome=%+v", rc, outcomes[0])
			}
			if _, ok := outcomes[0].Meta(); ok {
				t.Fatal("unsaved failure falsely carries saved metadata")
			}
			if out.Len() != 0 {
				t.Fatalf("unsaved JSON artifact claimed: %s", &out)
			}
			events := followEvents(t, &errOut)
			entry := events[len(events)-1]["outcome"].(map[string]any)
			assertFailureEvidence(t, entry, "", "")
			if entry["id"] != "" || entry["artifact_path"] != "" {
				t.Fatalf("unsaved artifact claimed: %v", entry)
			}
		})
	}
}

// Local interpreter outcomes must never acquire remote dispatch claims.
func TestLocalOmitsRemoteFailureEvidence(t *testing.T) {
	for _, tc := range []struct {
		name string
		res  runner.Result
		rc   int
	}{
		{"success", runner.Result{Output: []byte("local output\n")}, 0},
		{"nonzero", runner.Result{ExitCode: 98}, 98},
		{"timeout", runner.Result{TimedOut: true}, exitUsage},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			var out, errOut bytes.Buffer
			rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
				return tc.res
			}, []string{"--shell", "bash", "--result-format=json", "--", "synthetic body"}, &out, &errOut)
			if rc != tc.rc {
				t.Fatalf("rc=%d want %d stderr=%s", rc, tc.rc, &errOut)
			}
			var envelope struct {
				Runs []map[string]any `json:"runs"`
			}
			if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 {
				t.Fatalf("JSON=%s err=%v", &out, err)
			}
			assertFailureEvidence(t, envelope.Runs[0], "", "")
			store := openOutcomeTestStore(t, root)
			meta, _, err := store.Get(envelope.Runs[0]["id"].(string))
			if err != nil {
				t.Fatal(err)
			}
			if meta.FailurePhase != "" || meta.RemoteCompletion != "" {
				t.Fatalf("local metadata gained remote claims: %+v", meta)
			}
			if status := artifact.StatusLine(meta); strings.Contains(status, "failure-phase=") || strings.Contains(status, "remote-completion=") {
				t.Fatalf("local passport gained remote claims: %s", status)
			}
		})
	}
}
