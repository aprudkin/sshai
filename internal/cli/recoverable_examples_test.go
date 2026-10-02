package cli

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

// Exercise the documented bodies, real Bash wrappers, CLI parsing and artifact
// persistence. This catches replay after exec/unknown, wrong scheduler arguments,
// lost status/result evidence and false certainty about a dispatched submission.
// The scheduler is synthetic: no service manager, SSH or detached child is used.
func TestRecoverableExamples(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("synthetic Linux recipe requires Unix executable scripts and Bash")
	}
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("synthetic Linux recipe requires local Bash")
	}
	for _, tool := range []string{"env", "base64", "tr"} {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("Bash state epilogue requires local %s", tool)
		}
	}
	if os.Getenv("SSHAI_RECOVERABLE_TEST_CHILD") != "1" {
		// The outer deadline includes CLI initialization and persistence, unlike
		// the recipe's per-call 15-second remote budget. It also bounds a stuck CLI.
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestRecoverableExamples$", "-test.v")
		cmd.Env = append(os.Environ(), "SSHAI_RECOVERABLE_TEST_CHILD=1")
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("isolated recipe test: %v (deadline: %v)\n%s", err, ctx.Err(), out)
		}
		t.Logf("isolated recipe test:\n%s", out)
		return
	}

	recipes := recoverableDocRecipes(t)
	for _, tc := range []struct {
		name, properties string
		completed        bool
	}{
		{"completed", "LoadState=loaded\nInvocationID=0123456789abcdef0123456789abcdef\nActiveState=active\nSubState=exited\nResult=success\nExecMainCode=1\nExecMainStatus=0\n", true},
		{"still-running", "LoadState=loaded\nInvocationID=0123456789abcdef0123456789abcdef\nActiveState=active\nSubState=running\nResult=success\nExecMainCode=0\nExecMainStatus=0\n", false},
		{"unknown", "LoadState=not-found\nInvocationID=\nActiveState=inactive\nSubState=dead\nResult=success\nExecMainCode=0\nExecMainStatus=0\n", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			dir := t.TempDir()
			root := filepath.Join(dir, "sshai")
			t.Setenv("SSHAI_ROOT", root)
			t.Setenv("SSHAI_CTX", "recoverable-synthetic")
			if err := session.SaveFacts(root, "linux01", session.Facts{OS: "linux"}); err != nil {
				t.Fatal(err)
			}
			tr := newRecoverableTransport(t, bash, dir)
			// Pre-record identity and verification criteria before submission. The
			// hash is independently computed from the stable synthetic input, not
			// taken from status or the fake journal's fixed hash.
			record := "linux01/user-manager/sshai-report-example-001.service\n/usr/bin/sha256sum -- /srv/reports/input.dat\n"
			recoverableWrite(t, filepath.Join(dir, "operation-record"), record, 0o600)
			expectedHash := fmt.Sprintf("%x", sha256.Sum256([]byte("abc")))

			invoke := func(recipe int, wantExit int) (artifact.Meta, string) {
				t.Helper()
				bodyFile := filepath.Join(dir, fmt.Sprintf("body-%d", recipe))
				recoverableWrite(t, bodyFile, recipes[recipe].body, 0o600)
				args := append([]string{"--result-format=json"}, recipes[recipe].args...)
				for i := range args {
					if args[i] == "--body-file" {
						args[i+1] = bodyFile
					}
				}
				var out, errOut bytes.Buffer
				if code := runWith(tr, args, &out, &errOut); code != wantExit || errOut.Len() != 0 || tr.err != nil {
					t.Fatalf("call %d: exit=%d want=%d transport=%v stderr=%s stdout=%s", recipe, code, wantExit, tr.err, &errOut, &out)
				}
				var envelope struct {
					SchemaVersion string                 `json:"schema_version"`
					Runs          []artifact.ResultEntry `json:"runs"`
				}
				if err := json.Unmarshal(out.Bytes(), &envelope); err != nil || envelope.SchemaVersion != "v1" || len(envelope.Runs) != 1 {
					t.Fatalf("result envelope: %s error=%v", &out, err)
				}
				entry := envelope.Runs[0]
				store, err := artifact.OpenStore(root)
				if err != nil {
					t.Fatal(err)
				}
				defer store.Close()
				meta, path, err := store.Get(entry.ID)
				if err != nil {
					t.Fatal(err)
				}
				if entry.ArtifactPath != path || entry.Host != "linux01" || entry.FailurePhase != meta.FailurePhase || entry.RemoteCompletion != meta.RemoteCompletion || entry.TransportError != meta.TransportErr {
					t.Fatalf("JSON/persisted evidence mismatch: entry=%+v metadata=%+v", entry, meta)
				}
				body := recoverableRead(t, path)
				if meta.Truncated || meta.Binary || meta.Bytes != int64(len(body)) || meta.SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(body))) {
					t.Fatalf("retained artifact integrity: %+v body=%q", meta, body)
				}
				return meta, body
			}

			launch, launchBody := invoke(0, exitTransport)
			if launch.TransportErr != "timeout" || launch.FailurePhase != "exec" || launch.RemoteCompletion != "unknown" || launch.TransportDiagnostic != "connection timed out" || launchBody != "transport diagnostic: connection timed out\n" {
				t.Fatalf("lost observation must be canonical exec/unknown, not cancellation: %+v body=%q", launch, launchBody)
			}
			if got := recoverableRead(t, filepath.Join(dir, "accepted")); got != record {
				t.Fatalf("submission was not accepted for pre-recorded operation: %q", got)
			}

			properties := "Id=sshai-report-example-001.service\n" + tc.properties + "ExecStart={ path=/usr/bin/sha256sum ; argv[]=/usr/bin/sha256sum -- /srv/reports/input.dat ; }\n"
			recoverableWrite(t, filepath.Join(dir, "properties"), properties, 0o600)
			status, statusBody := invoke(1, 0)
			if status.ID == launch.ID || status.Exit != 0 || status.TransportErr != "" || status.FailurePhase != "" || status.RemoteCompletion != "" || statusBody != properties {
				t.Fatalf("fresh status observation must retain scheduler evidence: %+v body=%q", status, statusBody)
			}
			// Even completed status has no verified hash/result. Running with the
			// default Result=success is pending, and not-found is unknown despite
			// the acceptance marker. Neither permits a replay or result claim.
			if strings.Contains(statusBody, expectedHash) || recoverableRead(t, filepath.Join(dir, "calls")) != "submit\nstatus\n" {
				t.Fatal("status was mistaken for verified task success or triggered another action")
			}
			wantCalls := "submit\nstatus\n"
			if tc.completed {
				result, resultBody := invoke(2, 0)
				if result.ID == status.ID || result.ID == launch.ID || result.TransportErr != "" || result.FailurePhase != "" || result.RemoteCompletion != "" || resultBody != expectedHash+"  /srv/reports/input.dat\n" {
					t.Fatalf("completed process still requires independently verified retained result: %+v body=%q", result, resultBody)
				}
				wantCalls += "result\n"
			}
			if got := recoverableRead(t, filepath.Join(dir, "calls")); got != wantCalls {
				t.Fatalf("duplicate submission, unbounded polling or automatic cleanup: %q", got)
			}
			if got := recoverableRead(t, filepath.Join(dir, "accepted")); got != record {
				t.Fatalf("running/unknown/completed observation removed accepted operation: %q", got)
			}
		})
	}
}

type recoverableRecipe struct {
	args []string
	body string
}

func recoverableDocRecipes(t *testing.T) []recoverableRecipe {
	t.Helper()
	doc := recoverableRead(t, filepath.Join("..", "..", "docs", "agent-usage.md"))
	_, section, ok := strings.Cut(doc, "## Recoverable long-running operations\n")
	if !ok {
		t.Fatal("missing recoverable recipe section")
	}
	section, _, _ = strings.Cut(section, "\n## ")
	blocks := regexp.MustCompile("(?s)```bash\\nsshai run ([^\\n]+) <<'BASH'\\n(.*?)\\nBASH\\n```").FindAllStringSubmatch(section, -1)
	if len(blocks) != 3 {
		t.Fatalf("want three quoted-heredoc command recipes, got %d", len(blocks))
	}
	var recipes []recoverableRecipe
	for _, block := range blocks {
		recipes = append(recipes, recoverableRecipe{args: strings.Fields(block[1]), body: block[2] + "\n"})
	}
	return recipes
}

// Execute only cached-host Bash wrappers with a private, allowlisted PATH.
// The loss is injected AFTER the submission script has written its acceptance
// record; output from that execution is deliberately discarded, as on timeout.
type recoverableTransport struct {
	bash, dir string
	err       error
	calls     int
}

func (tr *recoverableTransport) Exec(host, command string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	tr.calls++
	if host != "linux01" || command != "bash -s" || timeout <= 0 || timeout > 15*time.Second {
		tr.err = fmt.Errorf("unexpected execution host=%q command=%q budget=%s", host, command, timeout)
		return transport.Result{}, &transport.TransportError{Reason: "ssh"}
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	cmd := exec.CommandContext(ctx, tr.bash, "--noprofile", "--norc", "-s")
	cmd.Dir = tr.dir
	cmd.Env = []string{"PATH=" + filepath.Join(tr.dir, "bin"), "HOME=" + tr.dir, "LANG=C"}
	cmd.Stdin = bytes.NewReader(stdin)
	out, err := cmd.CombinedOutput()
	if err != nil {
		tr.err = fmt.Errorf("synthetic wrapper failed: %w output=%s", err, out)
		return transport.Result{}, &transport.TransportError{Reason: "ssh"}
	}
	if tr.calls == 1 {
		return transport.Result{}, transport.NewTransportError("timeout", []byte("operation timed out"))
	}
	return transport.Result{Output: out}, nil
}

func (tr *recoverableTransport) Put(string, string, string, time.Duration) error {
	tr.err = fmt.Errorf("unexpected staging in synthetic Linux recipe")
	return tr.err
}

func newRecoverableTransport(t *testing.T, bash, dir string) *recoverableTransport {
	t.Helper()
	bin := filepath.Join(dir, "bin")
	if err := os.Mkdir(bin, 0o700); err != nil {
		t.Fatal(err)
	}
	// These are the only external commands needed by the real Bash epilogue.
	// No inherited PATH fallback can reach SSH or the real service manager.
	for _, tool := range []string{"env", "base64", "tr"} {
		path, err := exec.LookPath(tool)
		if err != nil {
			t.Fatal(err)
		}
		path, err = filepath.Abs(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.Symlink(path, filepath.Join(bin, tool)); err != nil {
			t.Fatal(err)
		}
	}
	for name, script := range map[string]string{
		"systemd-run": `[[ "$*" == '--user --unit=sshai-report-example-001.service --property=Type=exec --property=RuntimeMaxSec=300 --property=TimeoutStopSec=10 --property=StandardOutput=journal --property=StandardError=journal --remain-after-exit -- /usr/bin/sha256sum -- /srv/reports/input.dat' ]] || exit 20
[[ ! -e accepted ]] || exit 21
while IFS= read -r line; do printf '%s\n' "$line"; done < operation-record > accepted
printf 'submit\n' >> calls
printf 'Running as unit: sshai-report-example-001.service\n'
`,
		"systemctl": `[[ "$*" == '--user show sshai-report-example-001.service --property=Id,LoadState,InvocationID,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,ExecStart' ]] || exit 22
[[ -f accepted ]] || exit 23
printf 'status\n' >> calls
while IFS= read -r line; do printf '%s\n' "$line"; done < properties
`,
		"journalctl": `[[ "$*" == '--user-unit=sshai-report-example-001.service --no-pager --lines=40 --output=cat' ]] || exit 24
[[ -f accepted ]] || exit 25
properties=$(<properties)
[[ "$properties" == *'SubState=exited'* && "$properties" == *'LoadState=loaded'* ]] || exit 26
printf 'result\n' >> calls
printf 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  /srv/reports/input.dat\n'
`,
	} {
		recoverableWrite(t, filepath.Join(bin, name), "#!"+bash+"\nset -eu\n"+script, 0o700)
	}
	return &recoverableTransport{bash: bash, dir: dir}
}

func recoverableWrite(t *testing.T, path, body string, mode os.FileMode) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), mode); err != nil {
		t.Fatal(err)
	}
}

func recoverableRead(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}
