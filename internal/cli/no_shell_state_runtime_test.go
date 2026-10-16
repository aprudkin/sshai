package cli

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Runs the staged wrapper locally in a disposable, synthetic environment.
// This checks the full result path, not Windows/OpenSSH interoperability.
type noStateRuntimeTr struct {
	noStateTr
	path, dir string
	cap       int
}

func (f *noStateRuntimeTr) Exec(h, cmd string, body []byte, timeout time.Duration) (transport.Result, error) {
	return f.ExecStream(h, cmd, body, timeout, nil)
}

func (f *noStateRuntimeTr) ExecStream(_, _ string, _ []byte, timeout time.Duration, emit func([]byte)) (transport.Result, error) {
	path := filepath.Join(f.dir, "staged.ps1")
	if err := os.WriteFile(path, []byte(f.script), 0o600); err != nil {
		f.t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, f.path, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", path)
	cmd.Dir = f.dir
	cmd.Env = []string{"HOME=" + f.dir, "USERPROFILE=" + f.dir, "PATH=" + filepath.Dir(f.path), "SSHAI_ENV_ONLY=synthetic-inherited-only-canary"}
	raw, err := cmd.CombinedOutput()
	truncated := f.cap > 0 && len(raw) > f.cap
	if truncated {
		raw = raw[:f.cap]
	}
	if emit != nil {
		emit(raw)
	}
	if ctx.Err() != nil {
		return transport.Result{}, &transport.TransportError{Reason: "timeout"}
	}
	if cmd.ProcessState == nil {
		f.t.Fatalf("start interpreter: %v", err)
	}
	return transport.Result{Output: raw, ExitCode: cmd.ProcessState.ExitCode(), Truncated: truncated}, nil
}

func TestNoShellStateRuntimeEvidence(t *testing.T) {
	path, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh unavailable; no runtime evidence")
	}
	for _, follow := range []bool{false, true} {
		for _, tc := range []struct {
			name, body string
			exit, cap  int
		}{
			{"success", "Write-Output 'permitted'", 0, 0},
			{"nonzero", "Write-Output 'permitted'; exit 7", 7, 0},
			{"exception", "throw 'synthetic-exception'", 1, 0},
			{"truncated", "Write-Output ('x' * 1024)", 0, 256},
			{"timeout", "Write-Output 'permitted'; Start-Sleep -Seconds 5", exitTransport, 0},
		} {
			t.Run(tc.name+map[bool]string{false: "/normal", true: "/follow"}[follow], func(t *testing.T) {
				root := t.TempDir()
				t.Setenv("SSHAI_ROOT", root)
				if err := session.SaveFacts(root, "windows01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh"}); err != nil {
					t.Fatal(err)
				}
				tr := &noStateRuntimeTr{noStateTr: noStateTr{t: t}, path: path, dir: t.TempDir(), cap: tc.cap}
				bodyPath := filepath.Join(t.TempDir(), "body.ps1")
				if err := os.WriteFile(bodyPath, []byte(tc.body), 0o600); err != nil {
					t.Fatal(err)
				}
				timeout := "10"
				if tc.name == "timeout" {
					timeout = "1"
				}
				args := []string{"--no-shell-state", "--ctx", "test", "--timeout", timeout, "--result-format=json", "--body-file", bodyPath}
				if follow {
					args = append(args, "--follow")
				}
				args = append(args, "windows01")
				var out, errOut bytes.Buffer
				if rc := runWith(tr, args, &out, &errOut); rc != tc.exit {
					t.Fatalf("rc=%d want=%d %s %s", rc, tc.exit, &out, &errOut)
				}
				var envelope struct {
					Runs []artifact.ResultEntry `json:"runs"`
				}
				if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 {
					t.Fatalf("envelope=%s err=%v", &out, err)
				}
				result := envelope.Runs[0]
				if result.ShellStateMode != "none" || result.Truncated != (tc.cap > 0) {
					t.Fatalf("result=%+v", result)
				}
				body, err := os.ReadFile(result.ArtifactPath)
				if err != nil {
					t.Fatal(err)
				}
				if tc.name == "success" && string(body) != "permitted\n" {
					t.Fatalf("artifact=%q", body)
				}
				if tc.name == "timeout" && (result.FailurePhase != "exec" || result.RemoteCompletion != "unknown") {
					t.Fatalf("timeout evidence=%+v", result)
				}
				for _, name := range []string{"test.json", "baseline.json"} {
					if _, err := os.Stat(filepath.Join(root, "state", "windows01", name)); !os.IsNotExist(err) {
						t.Fatalf("snapshot created: %s %v", name, err)
					}
				}
				var history, logErr bytes.Buffer
				if rc := Log(nil, &history, &logErr); rc != 0 {
					t.Fatalf("log=%d %s", rc, &logErr)
				}
				audit, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
				if err != nil {
					t.Fatal(err)
				}
				for _, data := range []string{tr.script, string(body), out.String(), errOut.String(), history.String(), string(audit)} {
					for _, canary := range []string{"synthetic-inherited-only-canary", "SSHAI_ENV_ONLY", base64.StdEncoding.EncodeToString([]byte("SSHAI_ENV_ONLY=synthetic-inherited-only-canary"))} {
						if strings.Contains(data, canary) {
							t.Fatal("environment-only canary escaped")
						}
					}
				}
			})
		}
	}
}

func TestNoShellStateProbeFailures(t *testing.T) {
	for _, follow := range []bool{false, true} {
		for _, setup := range []bool{false, true} {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			var tr transport.Transport = &probeFailsTr{}
			want := exitTransport
			if setup {
				tr = &setupFailureTr{}
				want = exitSetup
			}
			args := []string{"--no-shell-state", "--ctx", "test", "--result-format=json"}
			if follow {
				args = append(args, "--follow")
			}
			args = append(args, "windows01", "--", "Write-Output 'permitted'")
			var out, errOut bytes.Buffer
			if rc := runWith(tr, args, &out, &errOut); rc != want {
				t.Fatalf("rc=%d %s", rc, &errOut)
			}
			var envelope struct {
				Runs []artifact.ResultEntry `json:"runs"`
			}
			if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || len(envelope.Runs) != 1 {
				t.Fatalf("%s %v", &out, err)
			}
			m := envelope.Runs[0]
			if m.FailurePhase != "probe" || m.RemoteCompletion != "not_started" || m.ShellStateMode != "none" {
				t.Fatalf("evidence=%+v", m)
			}
			if _, err := os.Stat(filepath.Join(root, "state", "windows01")); !os.IsNotExist(err) {
				t.Fatalf("snapshot directory created on probe failure: %v", err)
			}
		}
	}
}
