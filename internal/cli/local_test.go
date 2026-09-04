package cli

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
)

var localSentinelRE = regexp.MustCompile(`__SSHAI_[0-9a-f]+__`)

func successfulLocalResult(t *testing.T, argv []string, stdin []byte, cwd string, env map[string]string, output string, exit int) runner.Result {
	t.Helper()
	script := stdin
	if len(argv) == 4 && argv[0] == "pwsh" {
		var err error
		script, err = os.ReadFile(argv[3])
		if err != nil {
			t.Fatalf("read staged script: %v", err)
		}
	}
	sentinel := localSentinelRE.Find(script)
	if sentinel == nil {
		t.Fatalf("wrapped script has no sentinel: %q", script)
	}
	var dump []byte
	if argv[0] == "bash" {
		for k, v := range env {
			dump = append(dump, []byte(k+"="+v)...)
			dump = append(dump, 0)
		}
	} else {
		first := true
		for k, v := range env {
			if !first {
				dump = append(dump, '\n')
			}
			first = false
			dump = append(dump, []byte(k+"="+v)...)
		}
	}
	raw := output + "\n" + string(sentinel) + "\n" + cwd + "\n" + base64.StdEncoding.EncodeToString(dump) + "\n"
	return runner.Result{ExitCode: exit, Output: []byte(raw)}
}

func localArtifactID(t *testing.T, output string) string {
	t.Helper()
	re := regexp.MustCompile(`(?m)^file=.*/(a[0-9]+)$`)
	match := re.FindStringSubmatch(output)
	if match == nil {
		t.Fatalf("no artifact path in output: %q", output)
	}
	return match[1]
}

func TestLocalValidation(t *testing.T) {
	t.Setenv("SSHAI_ROOT", t.TempDir())
	tests := []struct {
		name string
		args []string
		want string
	}{
		{"missing shell", []string{"--", "true"}, "invalid --shell"},
		{"invalid shell", []string{"--shell", "sh", "--", "true"}, "invalid --shell"},
		{"missing body", []string{"--shell", "bash"}, "command is required"},
		{"empty inline body", []string{"--shell", "bash", "--"}, "command is required"},
		{"body forms conflict", []string{"--shell", "bash", "--body-file", "x", "--", "true"}, "conflicts"},
		{"remote flag", []string{"--shell", "bash", "--proxy-jump", "none", "--", "true"}, "flag provided but not defined"},
		{"follow", []string{"--shell", "bash", "--follow", "--", "true"}, "flag provided but not defined"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			var out, errOut bytes.Buffer
			called := false
			rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
				called = true
				return runner.Result{}
			}, tt.args, &out, &errOut)
			if rc != exitUsage || called || !strings.Contains(errOut.String(), tt.want) {
				t.Fatalf("rc=%d called=%t stdout=%q stderr=%q", rc, called, out.String(), errOut.String())
			}
		})
	}
}

func TestLocalBashPersistsArtifactAndMirrorsExit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	var gotArgv []string
	var gotStdin []byte
	run := func(argv []string, stdin []byte, timeout time.Duration, cap int64) runner.Result {
		gotArgv = append([]string(nil), argv...)
		gotStdin = append([]byte(nil), stdin...)
		if timeout != 7*time.Second || cap != 1234 {
			t.Fatalf("timeout=%v cap=%d", timeout, cap)
		}
		return successfulLocalResult(t, argv, stdin, "/work", map[string]string{"A": "one"}, "hello\n", 23)
	}
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("stream_cap_bytes = 1234\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	rc := localWithRunner(run, []string{"--shell", "bash", "--timeout", "7", "--", "printf", "hello"}, &out, &errOut)
	if rc != 23 || errOut.Len() != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
	if strings.Join(gotArgv, "|") != "bash|-s" || !bytes.Contains(gotStdin, []byte("printf hello")) {
		t.Fatalf("argv=%q stdin=%q", gotArgv, gotStdin)
	}
	if strings.Contains(strings.Join(gotArgv, " "), "printf") {
		t.Fatalf("body leaked into argv: %q", gotArgv)
	}
	id := localArtifactID(t, out.String())
	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	meta, path, err := store.Get(id)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := os.ReadFile(path)
	if meta.Host != "local-bash" || meta.Exit != 23 || meta.LocalError != "" || string(body) != "hello\n" {
		t.Fatalf("meta=%+v body=%q", meta, body)
	}
}

func TestLocalPowerShellUsesPrivateTemporaryFileAndCleansIt(t *testing.T) {
	t.Setenv("SSHAI_ROOT", t.TempDir())
	var scriptPath string
	run := func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
		if len(argv) != 4 || argv[0] != "pwsh" || argv[1] != "-NoProfile" || argv[2] != "-File" || len(stdin) != 0 {
			t.Fatalf("argv=%q stdin=%q", argv, stdin)
		}
		scriptPath = argv[3]
		info, err := os.Stat(scriptPath)
		if err != nil || info.Mode().Perm() != 0o600 {
			t.Fatalf("temp info=%v err=%v", info, err)
		}
		script, err := os.ReadFile(scriptPath)
		if err != nil || !bytes.Contains(script, []byte("Get-Date")) {
			t.Fatalf("script=%q err=%v", script, err)
		}
		if strings.Contains(strings.Join(argv, " "), "Get-Date") {
			t.Fatalf("body leaked into argv: %q", argv)
		}
		return successfulLocalResult(t, argv, nil, `C:\work`, map[string]string{"A": "one"}, "ok\n", 0)
	}
	var out, errOut bytes.Buffer
	if rc := localWithRunner(run, []string{"--shell", "pwsh", "--", "Get-Date"}, &out, &errOut); rc != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
	if _, err := os.Stat(scriptPath); !os.IsNotExist(err) {
		t.Fatalf("temporary script remains: %v", err)
	}
}

func TestLocalErrorsAreDistinctAndSaved(t *testing.T) {
	tests := []struct {
		name       string
		result     runner.Result
		wantError  string
		wantTrunc  bool
		wantBody   string
		wantStderr string
	}{
		{"start", runner.Result{StartErr: os.ErrNotExist}, "start", false, "", "failed to start"},
		{"timeout", runner.Result{ExitCode: -1, Output: []byte("partial"), TimedOut: true}, "timeout", false, "partial", "timed out"},
		{"output limit", runner.Result{ExitCode: -1, Output: []byte("1234"), Truncated: true}, "output-limit", true, "1234", ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			var out, errOut bytes.Buffer
			rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result { return tt.result },
				[]string{"--shell", "bash", "--result-format", "json", "--", "echo", "secret"}, &out, &errOut)
			if rc != exitUsage || !strings.Contains(errOut.String(), tt.wantStderr) {
				t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
			}
			var env struct {
				Summary artifact.Summary       `json:"summary"`
				Runs    []artifact.ResultEntry `json:"runs"`
			}
			if err := json.Unmarshal(out.Bytes(), &env); err != nil {
				t.Fatal(err)
			}
			if env.Summary.LocalErrors != 1 || env.Summary.Failed != 1 || env.Runs[0].LocalError != tt.wantError || env.Runs[0].Exit != 0 || env.Runs[0].Truncated != tt.wantTrunc {
				t.Fatalf("envelope=%+v", env)
			}
			body, err := os.ReadFile(env.Runs[0].ArtifactPath)
			if err != nil || string(body) != tt.wantBody {
				t.Fatalf("body=%q err=%v", body, err)
			}
		})
	}
}

func TestLocalBodyFileIsHashOnlyInMetadataAndAudit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	bodyPath := filepath.Join(t.TempDir(), "body.sh")
	secret := "TOKEN=do-not-persist\nprintf done\n"
	if err := os.WriteFile(bodyPath, []byte(secret), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	rc := localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
		if !bytes.Contains(stdin, []byte(secret)) {
			t.Fatalf("body missing from stdin wrapper")
		}
		return successfulLocalResult(t, argv, stdin, "/tmp", map[string]string{}, "done\n", 0)
	}, []string{"--shell", "bash", "--body-file", bodyPath}, &out, &errOut)
	if rc != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	meta, _, err := store.Get(localArtifactID(t, out.String()))
	store.Close()
	if err != nil || !strings.HasPrefix(meta.Command, "body:") || strings.Contains(meta.Command, "do-not-persist") {
		t.Fatalf("meta=%+v err=%v", meta, err)
	}
	audit, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
	if err != nil || bytes.Contains(audit, []byte("do-not-persist")) || !bytes.Contains(audit, []byte(`"Subcommand":"local"`)) {
		t.Fatalf("audit=%s err=%v", audit, err)
	}
}

func TestLocalBodyFileStdin(t *testing.T) {
	t.Setenv("SSHAI_ROOT", t.TempDir())
	readEnd, writeEnd, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	oldStdin := os.Stdin
	os.Stdin = readEnd
	t.Cleanup(func() { os.Stdin = oldStdin; readEnd.Close() })
	_, _ = writeEnd.WriteString("printf from-stdin\n")
	writeEnd.Close()
	var out, errOut bytes.Buffer
	rc := localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
		if !bytes.Contains(stdin, []byte("printf from-stdin")) {
			t.Fatalf("stdin body missing: %q", stdin)
		}
		return successfulLocalResult(t, argv, stdin, "/tmp", map[string]string{}, "ok\n", 0)
	}, []string{"--shell", "bash", "--body-file", "-"}, &out, &errOut)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
	}
}

func TestLocalDeltaAndStateAreShellAndContextQualified(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	call := 0
	run := func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
		call++
		if call == 3 && !bytes.Contains(stdin, []byte("export A='two'")) {
			t.Fatalf("third wrapper did not restore changed state: %q", stdin)
		}
		env := map[string]string{"A": "one"}
		if call >= 2 {
			env["A"] = "two"
		}
		return successfulLocalResult(t, argv, stdin, "/work", env, "value\n", 0)
	}
	for i := 0; i < 3; i++ {
		var out, errOut bytes.Buffer
		args := []string{"--shell", "bash", "--ctx", "build", "--delta", "--", "echo", "value"}
		if rc := localWithRunner(run, args, &out, &errOut); rc != 0 {
			t.Fatalf("run %d rc=%d stdout=%q stderr=%q", i, rc, out.String(), errOut.String())
		}
		if i == 0 && !strings.Contains(out.String(), "no previous run") {
			t.Fatalf("first delta=%q", out.String())
		}
		if i > 0 && !strings.Contains(out.String(), "delta=a") {
			t.Fatalf("later delta=%q", out.String())
		}
	}
	st, ok, err := session.LoadState(root, "local-bash", "build")
	if err != nil || !ok || st.Cwd != "/work" || st.Env["A"] != "two" {
		t.Fatalf("state=%+v ok=%t err=%v", st, ok, err)
	}
	if _, ok, err := session.LoadState(root, "local-pwsh", "build"); err != nil || ok {
		t.Fatalf("pwsh state leaked: ok=%t err=%v", ok, err)
	}
	if _, ok, err := session.LoadState(root, "local-bash", "other"); err != nil || ok {
		t.Fatalf("context state leaked: ok=%t err=%v", ok, err)
	}
}

func TestLocalBashRuntime(t *testing.T) {
	if _, err := exec.LookPath("bash"); err != nil {
		t.Skip("bash is unavailable")
	}
	t.Setenv("SSHAI_ROOT", t.TempDir())
	var out, errOut bytes.Buffer
	if rc := Local([]string{"--shell", "bash", "--", "printf", "runtime-ok"}, &out, &errOut); rc != 0 || !strings.Contains(out.String(), "runtime-ok") {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
}

func TestLocalPowerShellRuntime(t *testing.T) {
	if _, err := exec.LookPath("pwsh"); err != nil {
		t.Skip("pwsh is unavailable")
	}
	t.Setenv("SSHAI_ROOT", t.TempDir())
	var out, errOut bytes.Buffer
	if rc := Local([]string{"--shell", "pwsh", "--", "Write-Output", "runtime-ok"}, &out, &errOut); rc != 0 || !strings.Contains(out.String(), "runtime-ok") {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
}

func TestLocalMalformedEpiloguePreservesPriorState(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	before := map[string]string{"KEEP": "yes"}
	if err := session.SaveState(root, "local-bash", "default", shell.State{Cwd: "/known", Env: before}); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	if rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
		return runner.Result{ExitCode: 0, Output: []byte("body without epilogue\n")}
	}, []string{"--shell", "bash", "--", "true"}, &out, &errOut); rc != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
	after, ok, err := session.LoadState(root, "local-bash", "default")
	if err != nil || !ok || after.Cwd != "/known" || after.Env["KEEP"] != "yes" {
		t.Fatalf("state=%+v ok=%t err=%v", after, ok, err)
	}
}

func TestLocalOpportunisticGCKeepsCurrentArtifact(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("retention_max_bytes = 1\nretention_days = 0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	if rc := localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
		return successfulLocalResult(t, argv, stdin, "/tmp", map[string]string{}, "larger than cap\n", 0)
	}, []string{"--shell", "bash", "--", "true"}, &out, &errOut); rc != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
	id := localArtifactID(t, out.String())
	if _, err := os.Stat(filepath.Join(root, "art", id)); err != nil {
		t.Fatalf("current artifact was pruned: %v", err)
	}
}

func TestHostsDoesNotListLocalTargets(t *testing.T) {
	root := t.TempDir()
	home := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	t.Setenv("HOME", home)
	if err := session.SaveState(root, "local-bash", "default", shell.State{Cwd: "/tmp"}); err != nil {
		t.Fatal(err)
	}
	var out, errOut bytes.Buffer
	if rc := Hosts(nil, &out, &errOut); rc != 0 || strings.Contains(out.String(), "local-bash") {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
}

func TestLocalJSONResultOutAndLogStatus(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	resultPath := filepath.Join(t.TempDir(), "result.json")
	var out, errOut bytes.Buffer
	rc := localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
		return runner.Result{TimedOut: true, Output: []byte("partial")}
	}, []string{"--shell", "bash", "--result-format", "json", "--result-out", resultPath, "--", "sleep", "9"}, &out, &errOut)
	if rc != exitUsage {
		t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
	}
	fileBody, err := os.ReadFile(resultPath)
	if err != nil || !bytes.Equal(bytes.TrimSpace(out.Bytes()), bytes.TrimSpace(fileBody)) {
		t.Fatalf("stdout=%q file=%q err=%v", out.Bytes(), fileBody, err)
	}
	out.Reset()
	errOut.Reset()
	if rc := Log([]string{"--host", "local-bash"}, &out, &errOut); rc != 0 || !strings.Contains(out.String(), "local-error=timeout") {
		t.Fatalf("log rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
	}
}
