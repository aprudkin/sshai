package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/runlog"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// Replace only SSH delivery: execute the CLI's real staged Windows wrapper
// with local pwsh. This validates generated code, not Windows/OpenSSH parity.
// Follow receives the completed output as one chunk; timing is not under test.
type bomPowerShellTransport struct {
	t                 *testing.T
	localPath, remote string
	streamed          bool
}

func (f *bomPowerShellTransport) Put(_ string, local, remote string, _ time.Duration) error {
	f.localPath, f.remote = local, remote
	return nil
}

func (f *bomPowerShellTransport) Exec(_ string, command string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	f.t.Helper()
	if f.localPath == "" || len(stdin) != 0 || !strings.Contains(command, "-File "+f.remote) {
		f.t.Fatal("expected invocation of the staged PowerShell file without body stdin")
	}
	result := runner.Run([]string{"pwsh", "-NoProfile", "-File", f.localPath}, nil, timeout, 1<<20)
	if result.StartErr != nil || result.TimedOut || result.Truncated {
		f.t.Fatalf("local fixture execution failed: start=%v timeout=%v truncated=%v", result.StartErr, result.TimedOut, result.Truncated)
	}
	return transport.Result{ExitCode: result.ExitCode, Output: result.Output}, nil
}

func (f *bomPowerShellTransport) ExecStream(host, command string, stdin []byte, timeout time.Duration, out func([]byte)) (transport.Result, error) {
	f.streamed = true
	result, err := f.Exec(host, command, stdin, timeout)
	out(result.Output)
	return result, err
}

// File-backed stdin avoids pipe capacity and goroutine coordination. The
// original source file must remain unchanged after either input route.
func bomBodyInput(t *testing.T, body, source string) (arg, path string) {
	t.Helper()
	path = filepath.Join(t.TempDir(), "body.txt")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	if source == "file" {
		return path, path
	}
	in, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stdin
	os.Stdin = in
	t.Cleanup(func() { os.Stdin = old; in.Close() })
	return "-", path
}

func TestPowerShellBodyBOMExecution(t *testing.T) {
	if _, err := exec.LookPath("pwsh"); err != nil {
		t.Skip("pwsh is unavailable")
	}
	for _, mode := range []string{"local", "remote", "follow"} {
		for _, source := range []string{"file", "stdin"} {
			for _, first := range []struct{ name, body string }{
				{"command", "Write-Output 'Привет a\ufeffb'\n"},
				{"comment", "# café\nWrite-Output 'Привет a\ufeffb'\n"},
				{"param", "param($text = 'Привет a\ufeffb')\nWrite-Output $text\n"},
			} {
				for _, bom := range []bool{false, true} {
					t.Run(fmt.Sprintf("%s/%s/%s/bom=%t", mode, source, first.name, bom), func(t *testing.T) {
						root := t.TempDir()
						t.Setenv("SSHAI_ROOT", root)
						cwd, err := filepath.EvalSymlinks(t.TempDir())
						if err != nil {
							t.Fatal(err)
						}
						body := first.body + "Write-Output $env:SSHAI_BOM_TEST\n$env:SSHAI_BOM_TEST = 'updated'\n"
						if bom {
							body = "\ufeff" + body
						}
						input, path := bomBodyInput(t, body, source)
						target := "win-test"
						if mode == "local" {
							target = "local-pwsh"
						} else if err := session.SaveFacts(root, target, session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh"}); err != nil {
							t.Fatal(err)
						}
						if err := session.SaveBaseline(root, target, map[string]string{"SSHAI_BOM_TEST": "baseline"}); err != nil {
							t.Fatal(err)
						}
						if err := session.SaveState(root, target, "bom-test", shell.State{Cwd: cwd, Env: map[string]string{"SSHAI_BOM_TEST": "restored"}}); err != nil {
							t.Fatal(err)
						}
						args := []string{"--ctx", "bom-test", "--timeout", "10", "--result-format=json", "--body-file", input}
						var stdout, stderr bytes.Buffer
						var rc int
						if mode == "local" {
							rc = Local(append(args, "--shell", "pwsh"), &stdout, &stderr)
						} else {
							tr := &bomPowerShellTransport{t: t}
							if mode == "follow" {
								args = append(args, "--follow")
							}
							rc = runWith(tr, append(args, target), &stdout, &stderr)
							if tr.streamed != (mode == "follow") {
								t.Fatal("wrong streaming execution path")
							}
							if _, err := os.Stat(tr.localPath); !os.IsNotExist(err) {
								t.Fatalf("temporary staged script remains: %v", err)
							}
						}
						if rc != 0 {
							t.Fatalf("body execution exit=%d, want 0", rc)
						}
						var envelope struct {
							Summary artifact.Summary       `json:"summary"`
							Runs    []artifact.ResultEntry `json:"runs"`
						}
						if err := json.Unmarshal(stdout.Bytes(), &envelope); err != nil {
							t.Fatal(err)
						}
						if len(envelope.Runs) != 1 || envelope.Summary.OK != 1 {
							t.Fatalf("unexpected result count/summary: %+v", envelope.Summary)
						}
						run := envelope.Runs[0]
						// Hashes identify the exact submitted bytes, not normalized execution text.
						digest := fmt.Sprintf("%x", sha256.Sum256([]byte(body)))
						if run.Command != "body:"+digest[:16] || run.Exit != 0 || run.Host != target {
							t.Fatalf("unexpected body metadata: %+v", run)
						}
						output, err := os.ReadFile(run.ArtifactPath)
						if err != nil || string(output) != "Привет a\ufeffb\nrestored\n" {
							t.Fatalf("artifact output=%q err=%v", output, err)
						}
						st, ok, err := session.LoadState(root, target, "bom-test")
						if err != nil || !ok || st.Cwd != cwd || st.Env["SSHAI_BOM_TEST"] != "updated" {
							t.Fatalf("state did not round-trip: ok=%t cwd=%q test-env=%q err=%v", ok, st.Cwd, st.Env["SSHAI_BOM_TEST"], err)
						}
						audit, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
						if err != nil {
							t.Fatal(err)
						}
						var entry runlog.AuditEntry
						if err := json.Unmarshal(audit, &entry); err != nil || entry.BodySHA256 != digest || entry.CommandPreview != run.Command {
							t.Fatalf("audit did not preserve raw body hash: %v", err)
						}
						if after, err := os.ReadFile(path); err != nil || string(after) != body {
							t.Fatalf("source body file changed: %v", err)
						}
						if mode == "follow" {
							for _, event := range []string{`"type":"started"`, `"type":"output"`, `"type":"completed"`} {
								if !strings.Contains(stderr.String(), event) {
									t.Errorf("missing follow event %s", event)
								}
							}
						} else if stderr.Len() != 0 {
							t.Fatalf("unexpected stderr: %s", &stderr)
						}
					})
				}
			}
		}
	}
}

func TestBodyBOMPreservedForBashAndPOSIX(t *testing.T) {
	const body = "\ufeffprintf 'a\ufeffb\\n'\n"
	for _, mode := range []string{"local", "bash", "posix"} {
		for _, source := range []string{"file", "stdin"} {
			t.Run(mode+"/"+source, func(t *testing.T) {
				root := t.TempDir()
				t.Setenv("SSHAI_ROOT", root)
				input, _ := bomBodyInput(t, body, source)
				var stdout, stderr bytes.Buffer
				var wrapped []byte
				var rc int
				if mode == "local" {
					rc = localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
						wrapped = stdin
						return successfulLocalResult(t, argv, stdin, "/tmp", nil, "ok\n", 0)
					}, []string{"--shell", "bash", "--body-file", input}, &stdout, &stderr)
				} else {
					seedLinuxFacts(t, root, "linux-test")
					tr := &fakeTr{}
					args := []string{"--body-file", input}
					if mode == "posix" {
						args = append(args, "--posix-shell", "/bin/sh")
					}
					rc = runWith(tr, append(args, "linux-test"), &stdout, &stderr)
					wrapped = tr.lastStdin
				}
				if rc != 0 || !bytes.HasSuffix(wrapped, []byte(body)) {
					t.Fatalf("Bash/POSIX body bytes changed: rc=%d stderr=%s", rc, &stderr)
				}
			})
		}
	}
}
