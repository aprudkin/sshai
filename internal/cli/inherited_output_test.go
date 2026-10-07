package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

// Exercise real process capture and wrapper parsing through persistence and
// both result renderers. Only the SSH binary is replaced; no network is used.
func TestInheritedOutputResults(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake SSH and Bash fixture")
	}
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash not installed")
	}
	for _, remote := range []bool{false, true} {
		for _, format := range []string{"human", "json"} {
			for _, tc := range []struct {
				name, body, want string
				timeout          time.Duration
				cap              int64
				failure          string
				exit             int
			}{
				{"ordinary", "printf 'before\\n'; exit 3", "before\n", 2 * time.Second, 1 << 20, "", 3},
				{"delayed", "printf 'before\\n'; (sleep 0.4; printf 'after\\n') &\nexit 3", "before\nafter\n", 2 * time.Second, 1 << 20, "", 3},
				{"deadline", "printf 'before\\n'; (sleep 1; printf 'after\\n') &\nexit 0", "before\n", 300 * time.Millisecond, 1 << 20, "timeout", 0},
				{"cap", "trap - EXIT; printf 'before\\n'; (sleep 0.4; printf 'after\\n'; sleep 1) &\nexit 0", "before\na", 2 * time.Second, 8, "output-limit", 0},
			} {
				target := "local-bash"
				if remote {
					target = "synthetic01"
				}
				t.Run(target+"/"+format+"/"+tc.name, func(t *testing.T) {
					store := openOutcomeTestStore(t, t.TempDir())
					t.Setenv("SSHAI_ROOT", store.Root)
					var result hostRunResult
					started := time.Now()
					if remote {
						dir := t.TempDir()
						if err := os.WriteFile(filepath.Join(dir, "ssh"), []byte("#!/bin/sh\nexec bash -s\n"), 0o700); err != nil {
							t.Fatal(err)
						}
						t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
						if err := session.SaveFacts(store.Root, target, session.Facts{OS: "linux"}); err != nil {
							t.Fatal(err)
						}
						tr := transport.NewOpenSSH(testControlDir(t), "15m", tc.cap, transport.OpenSSHOptions{})
						result.outcome = runHost(Deps{Tr: tr, Store: store}, Opts{Host: target, Ctx: "test", Command: tc.body, Timeout: tc.timeout, Budget: 500}, &result.stdout, &result.stderr)
					} else {
						result.outcome = runLocal(store, tc.cap, runner.Run, localOpts{shell: "bash", target: target, ctx: "test", command: tc.body, timeout: tc.timeout, budget: 500}, &result.stdout, &result.stderr)
					}
					if elapsed := time.Since(started); elapsed > tc.timeout+500*time.Millisecond {
						t.Fatalf("capture exceeded deadline allowance: %v", elapsed)
					}
					var out, errOut bytes.Buffer
					rc := writeRunResults(store.Root, []hostRunResult{result}, resultModeOptions{format: format}, &out, &errOut)
					meta, path, err := store.Get(result.outcome.ArtifactID())
					if err != nil {
						t.Fatal(err)
					}
					body, err := os.ReadFile(path)
					if err != nil {
						t.Fatal(err)
					}
					wantRC := tc.exit
					if tc.failure != "" && !remote {
						wantRC = exitUsage
					}
					if tc.failure == "timeout" && remote {
						wantRC = exitTransport
						if meta.TransportErr != "timeout" || meta.TransportDiagnostic != "operation timed out" || meta.FailurePhase != "exec" || meta.RemoteCompletion != "unknown" || string(body) != "transport diagnostic: operation timed out\n" {
							t.Fatalf("remote incomplete capture not sanitized/uncertain: meta=%+v body=%q", meta, body)
						}
					} else if tc.failure == "timeout" {
						if !bytes.HasPrefix(body, []byte(tc.want)) || bytes.Contains(body, []byte("after\n")) {
							t.Fatalf("local timeout must retain partial raw capture: %q", body)
						}
					} else if string(body) != tc.want {
						t.Fatalf("captured output lost: %q, want %q", body, tc.want)
					}
					if rc != wantRC || meta.Exit != tc.exit || meta.Truncated != (tc.failure == "output-limit") || (!remote && meta.LocalError != tc.failure) {
						t.Fatalf("rc=%d want=%d meta=%+v stdout=%q stderr=%q", rc, wantRC, meta, out.String(), errOut.String())
					}
					if format == "human" {
						if !strings.Contains(out.String(), artifact.StatusLine(meta)) {
							t.Fatalf("passport disagrees with metadata: %q", out.String())
						}
					} else {
						var envelope struct {
							Runs []artifact.ResultEntry `json:"runs"`
						}
						if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 {
							t.Fatalf("invalid JSON: %v %q", err, out.String())
						}
						entry := envelope.Runs[0]
						if entry.Exit != meta.Exit || entry.LocalError != meta.LocalError || entry.TransportError != meta.TransportErr || entry.TransportDiagnostic != meta.TransportDiagnostic || entry.FailurePhase != meta.FailurePhase || entry.RemoteCompletion != meta.RemoteCompletion || entry.Truncated != meta.Truncated || entry.Bytes != int64(len(body)) {
							t.Fatalf("JSON disagrees with artifact: %+v vs %+v", entry, meta)
						}
					}
					if tc.failure != "" {
						if _, ok, err := session.LoadState(store.Root, target, "test"); err != nil || ok {
							t.Fatalf("incomplete capture saved shell state: exists=%v err=%v", ok, err)
						}
					}
				})
			}
		}
	}
}
