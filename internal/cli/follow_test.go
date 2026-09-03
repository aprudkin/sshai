package cli

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

type followTr struct {
	calls int
	rc    int
	delay time.Duration
}

func (f *followTr) Exec(host, command string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	return transport.Result{}, &transport.TransportError{Reason: "ssh"}
}
func (f *followTr) Put(host, local, remote string) error { return nil }
func (f *followTr) ExecStream(host, command string, stdin []byte, timeout time.Duration, out func([]byte)) (transport.Result, error) {
	f.calls++
	marker := ""
	for _, line := range strings.Split(string(stdin), "\n") {
		if strings.Contains(line, "_FOLLOW") {
			marker = strings.Trim(strings.TrimPrefix(line, "printf '%s\\n' "), " '")
			break
		}
	}
	sentinel := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	data := []byte(marker + "\nfirst\nsecond\n\n" + sentinel + "\n/tmp\n" + env + "\n")
	if f.delay > 0 {
		out([]byte(marker + "\n"))
		time.Sleep(f.delay)
		out(data[len(marker)+1:])
	} else {
		out(data)
	}
	return transport.Result{ExitCode: f.rc, Output: data}, nil
}

func TestFollowEventsHideControlAndEpilogue(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "h", session.Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	f := &followTr{}
	if got := runWith(f, []string{"--follow", "--follow-interval", "1", "h", "--", "echo", "x"}, &stdout, &stderr); got != 0 {
		t.Fatalf("rc=%d stderr=%s", got, stderr.String())
	}
	if f.calls != 1 {
		t.Fatalf("stream calls=%d", f.calls)
	}
	if strings.Contains(stdout.String(), "_FOLLOW") || strings.Contains(stdout.String(), "__SSHAI_") {
		t.Fatalf("control leaked to final stdout: %q", stdout.String())
	}
	var types []string
	for _, line := range strings.Split(strings.TrimSpace(stderr.String()), "\n") {
		var e struct {
			Type string `json:"type"`
		}
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			t.Fatal(err)
		}
		types = append(types, e.Type)
	}
	if got, want := strings.Join(types, ","), "started,output,completed"; got != want {
		t.Fatalf("event order=%q want %q; stderr=%s", got, want, stderr.String())
	}
}

type splitFollowTr struct{ followTr }

func (f *splitFollowTr) ExecStream(host, command string, stdin []byte, timeout time.Duration, out func([]byte)) (transport.Result, error) {
	marker := ""
	for _, line := range strings.Split(string(stdin), "\n") {
		if strings.Contains(line, "_FOLLOW") {
			marker = strings.Trim(strings.TrimPrefix(line, "printf '%s\\n' "), " '")
		}
	}
	sentinel := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/isolated\x00"))
	stdout := []byte(marker + "\nbody\n\n" + sentinel + "\n/isolated\n" + env + "\n")
	out(stdout) // Live preview is trusted stdout only.
	return transport.Result{Output: stdout}, nil
}

func TestFollowParsesUnifiedRemoteStream(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "h", session.Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
	var stdout, stderr bytes.Buffer
	if rc := runWith(&splitFollowTr{}, []string{"--follow", "h", "--", "true"}, &stdout, &stderr); rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
	}
	if !strings.Contains(stdout.String(), "body") {
		t.Fatalf("artifact output missing body: %q", stdout.String())
	}
	st, ok, err := session.LoadState(root, "h", "default")
	if err != nil || !ok || st.Cwd != "/isolated" || st.Env["PATH"] != "/isolated" {
		t.Fatalf("state=%+v ok=%v err=%v", st, ok, err)
	}
}

func TestFollowJSONResultOutMatchesFinalStdout(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "h", session.Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "result.json")
	var stdout, stderr bytes.Buffer
	if rc := runWith(&followTr{}, []string{"--follow", "--result-format=json", "--result-out", path, "h", "--", "true"}, &stdout, &stderr); rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
	}
	file, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(file, stdout.Bytes()) {
		t.Fatalf("result-out differs: err=%v file=%q stdout=%q", err, file, stdout.Bytes())
	}
	if !strings.Contains(stderr.String(), `"type":"completed"`) || !json.Valid(stdout.Bytes()) {
		t.Fatalf("follow/result output malformed: stderr=%s stdout=%s", stderr.String(), stdout.String())
	}
}

type transportFailFollowTr struct{ calls int }

func (f *transportFailFollowTr) Exec(host, command string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	panic("follow mode must not call Exec")
}
func (f *transportFailFollowTr) Put(host, local, remote string) error { return nil }
func (f *transportFailFollowTr) ExecStream(host, command string, stdin []byte, timeout time.Duration, out func([]byte)) (transport.Result, error) {
	f.calls++
	return transport.Result{}, transport.NewTransportError("ssh", []byte("Permission denied for secret-host.example"))
}

func prepareFollowHost(t *testing.T, root string) {
	t.Helper()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "h", session.Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
}

func TestFollowRemoteNonzeroAndSilentHeartbeat(t *testing.T) {
	root := t.TempDir()
	prepareFollowHost(t, root)
	var stdout, stderr bytes.Buffer
	f := &followTr{rc: 5, delay: 1100 * time.Millisecond}
	if rc := runWith(f, []string{"--follow", "--follow-interval", "1", "h", "--", "false"}, &stdout, &stderr); rc != 5 {
		t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
	}
	events := followEvents(t, &stderr)
	var heartbeat bool
	for _, event := range events {
		if event["type"] == "heartbeat" {
			heartbeat = true
			if event["elapsed_ms"].(float64) < 1000 {
				t.Fatalf("early heartbeat: %v", event)
			}
		}
	}
	completed := events[len(events)-1]
	outcome := completed["outcome"].(map[string]any)
	if !heartbeat || completed["type"] != "completed" || completed["process_exit"].(float64) != 5 || outcome["kind"] != "remote_nonzero" || outcome["exit"].(float64) != 5 {
		t.Fatalf("events=%v", events)
	}
}

func TestFollowTransportFailureIsSanitizedAndNotRetried(t *testing.T) {
	root := t.TempDir()
	prepareFollowHost(t, root)
	var stdout, stderr bytes.Buffer
	f := &transportFailFollowTr{}
	if rc := runWith(f, []string{"--follow", "h", "--", "true"}, &stdout, &stderr); rc != exitTransport {
		t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
	}
	if f.calls != 1 {
		t.Fatalf("ExecStream calls=%d", f.calls)
	}
	events := followEvents(t, &stderr)
	if len(events) != 1 || events[0]["type"] != "completed" {
		t.Fatalf("events=%v", events)
	}
	encoded, _ := json.Marshal(events[0])
	if bytes.Contains(encoded, []byte("secret-host")) || !bytes.Contains(encoded, []byte("permission denied")) {
		t.Fatalf("transport diagnostic is not safely canonicalized: %s", encoded)
	}
}

func TestFollowPolicyAndUnavailableTransportCompleteWithoutStarting(t *testing.T) {
	t.Run("policy", func(t *testing.T) {
		root := t.TempDir()
		prepareFollowHost(t, root)
		if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("[hosts.h]\nreadonly = true\n"), 0o600); err != nil {
			t.Fatal(err)
		}
		var stdout, stderr bytes.Buffer
		f := &followTr{}
		if rc := runWith(f, []string{"--follow", "h", "--", "rm", "not-allowed"}, &stdout, &stderr); rc != exitPolicy {
			t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
		}
		events := followEvents(t, &stderr)
		if f.calls != 0 || len(events) != 1 || events[0]["type"] != "completed" || events[0]["outcome"].(map[string]any)["kind"] != "policy_denied" {
			t.Fatalf("calls=%d events=%v", f.calls, events)
		}
	})

	t.Run("streaming unavailable", func(t *testing.T) {
		root := t.TempDir()
		prepareFollowHost(t, root)
		var stdout, stderr bytes.Buffer
		if rc := runWith(&fakeTr{}, []string{"--follow", "h", "--", "true"}, &stdout, &stderr); rc != exitUsage {
			t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
		}
		events := followEvents(t, &stderr)
		if len(events) != 1 || events[0]["type"] != "completed" || !strings.Contains(events[0]["diagnostics"].(string), "streaming transport") {
			t.Fatalf("events=%v", events)
		}
	})
}

func TestFollowResultOutFailureIsTerminalDiagnostic(t *testing.T) {
	root := t.TempDir()
	prepareFollowHost(t, root)
	var stdout, stderr bytes.Buffer
	if rc := runWith(&followTr{}, []string{"--follow", "--result-format=json", "--result-out", t.TempDir(), "h", "--", "true"}, &stdout, &stderr); rc != exitUsage {
		t.Fatalf("rc=%d stderr=%s", rc, stderr.String())
	}
	if !json.Valid(stdout.Bytes()) {
		t.Fatalf("final stdout is not the unchanged JSON envelope: %q", stdout.Bytes())
	}
	events := followEvents(t, &stderr)
	completed := events[len(events)-1]
	if completed["process_exit"].(float64) != exitUsage || !strings.Contains(completed["diagnostics"].(string), "--result-out") {
		t.Fatalf("completed=%v", completed)
	}
}

func TestFollowValidation(t *testing.T) {
	var out, err bytes.Buffer
	if got := runWith(&followTr{}, []string{"--follow", "a", "b", "--", "echo", "x"}, &out, &err); got != exitUsage || !strings.Contains(err.String(), "exactly one") {
		t.Fatalf("rc=%d stderr=%q", got, err.String())
	}
	err.Reset()
	if got := runWith(&followTr{}, []string{"--follow-interval", "2", "a", "--", "echo", "x"}, &out, &err); got != exitUsage || !strings.Contains(err.String(), "requires --follow") {
		t.Fatalf("rc=%d stderr=%q", got, err.String())
	}
}
