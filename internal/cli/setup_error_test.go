package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

const rawSetupOutput = `secret-host.example C:\private\workspace SHA256:TOPSECRET AppLocker denied pwsh.exe`

// setupFailureTr reaches the Windows setup probe and rejects every candidate.
type setupFailureTr struct {
	mu    sync.Mutex
	calls []string
}

func (f *setupFailureTr) Exec(_ string, command string, _ []byte, _ time.Duration) (transport.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, command)
	return transport.Result{ExitCode: 1, Output: []byte(rawSetupOutput)}, nil
}
func (*setupFailureTr) Put(string, string, string) error { panic("user body must not be staged") }

func (f *setupFailureTr) callCount() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.calls)
}

func TestRunWindowsSetupFailureIsSanitizedAndDoesNotCacheFacts(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	tr := &setupFailureTr{}
	var stdout, stderr bytes.Buffer
	if rc := runWith(tr, []string{"win01", "--", "Get-Date"}, &stdout, &stderr); rc != exitSetup {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitSetup, stderr.String())
	}
	got := stdout.String()
	if !strings.Contains(got, "setup-error=windows-shell") || !strings.Contains(got, session.RemoteSetupDiagnostic) {
		t.Fatalf("passport=%q", got)
	}
	if strings.Contains(got, rawSetupOutput) || strings.Contains(stderr.String(), rawSetupOutput) {
		t.Fatalf("raw setup output leaked: stdout=%q stderr=%q", got, stderr.String())
	}
	if tr.callCount() != 5 { // uname + cmd/pwsh forms for pwsh and Windows PowerShell.
		t.Fatalf("calls=%d, want 5", tr.callCount())
	}
	if _, ok, err := session.LoadFacts(root, "win01"); err != nil || ok {
		t.Fatalf("facts cached after setup failure: ok=%v err=%v", ok, err)
	}
	entries, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var audit map[string]any
	if err := json.Unmarshal(entries, &audit); err != nil {
		t.Fatal(err)
	}
	if audit["SetupErr"] != "windows-shell" || audit["Verdict"] != "allowed" {
		t.Fatalf("audit=%v", audit)
	}
	if bytes.Contains(entries, []byte(rawSetupOutput)) {
		t.Fatalf("raw setup output leaked into audit: %s", entries)
	}
	artifactBody, err := os.ReadFile(filepath.Join(root, "art", "a1"))
	if err != nil {
		t.Fatal(err)
	}
	wantArtifact := "setup diagnostic: " + session.RemoteSetupDiagnostic + "\n"
	if string(artifactBody) != wantArtifact || strings.Contains(string(artifactBody), rawSetupOutput) {
		t.Fatalf("artifact=%q, want %q", artifactBody, wantArtifact)
	}
	var storedSetup, storedDiagnostic string
	store, err := openQueryStore()
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.DB.QueryRow(`SELECT setup_error, setup_diagnostic FROM runs WHERE art_id='a1'`).Scan(&storedSetup, &storedDiagnostic); err != nil {
		t.Fatal(err)
	}
	if storedSetup != session.RemoteSetupWindowsShell || storedDiagnostic != session.RemoteSetupDiagnostic {
		t.Fatalf("stored setup=(%q, %q)", storedSetup, storedDiagnostic)
	}
}

func TestRunWindowsSetupFailureJSONAndFanoutSummary(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	var stdout, stderr bytes.Buffer
	if rc := runWith(&setupFailureTr{}, []string{"--result-format=json", "win01", "--", "Get-Date"}, &stdout, &stderr); rc != exitSetup {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitSetup, stderr.String())
	}
	var result struct {
		Summary struct {
			SetupErrors int `json:"setup_errors"`
			Failed      int `json:"failed"`
		}
		Runs []struct {
			SetupError      string `json:"setup_error"`
			SetupDiagnostic string `json:"setup_diagnostic"`
			Exit            int    `json:"exit"`
		} `json:"runs"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		t.Fatalf("JSON: %v: %s", err, stdout.String())
	}
	if result.Summary.SetupErrors != 1 || result.Summary.Failed != 1 || len(result.Runs) != 1 || result.Runs[0].SetupError != "windows-shell" || result.Runs[0].SetupDiagnostic != session.RemoteSetupDiagnostic || result.Runs[0].Exit != 0 {
		t.Fatalf("result=%+v", result)
	}
	if strings.Contains(stdout.String(), rawSetupOutput) || strings.Contains(stderr.String(), rawSetupOutput) {
		t.Fatalf("raw setup output leaked: stdout=%q stderr=%q", stdout.String(), stderr.String())
	}
}

func TestRunWindowsSetupFailureHumanFanoutAndFollow(t *testing.T) {
	t.Run("fanout", func(t *testing.T) {
		root := t.TempDir()
		t.Setenv("SSHAI_ROOT", root)
		var stdout, stderr bytes.Buffer
		if rc := runWith(&setupFailureTr{}, []string{"win01", "win02", "--", "Get-Date"}, &stdout, &stderr); rc != exitSetup {
			t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitSetup, stderr.String())
		}
		if !strings.Contains(stdout.String(), "hosts=2 ok=0 failed=2 transport-errors=0 setup-errors=2") {
			t.Fatalf("aggregate=%q", stdout.String())
		}
	})

	t.Run("follow", func(t *testing.T) {
		root := t.TempDir()
		t.Setenv("SSHAI_ROOT", root)
		var stdout, stderr bytes.Buffer
		if rc := runWith(&setupFailureTr{}, []string{"--follow", "win01", "--", "Get-Date"}, &stdout, &stderr); rc != exitSetup {
			t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitSetup, stderr.String())
		}
		events := followEvents(t, &stderr)
		if len(events) != 1 || events[0]["type"] != "completed" || events[0]["process_exit"].(float64) != exitSetup {
			t.Fatalf("events=%v", events)
		}
		outcome := events[0]["outcome"].(map[string]any)
		if outcome["kind"] != "setup_failure" || outcome["setup_error"] != "windows-shell" {
			t.Fatalf("outcome=%v", outcome)
		}
	})
}

func TestFormatLogLineSetupError(t *testing.T) {
	got := formatLogLine(artifact.Meta{ID: "a1", Host: "win01", SetupErr: "windows-shell", Ts: time.Unix(0, 0)})
	if !strings.Contains(got, "setup-error=windows-shell") || strings.Contains(got, "exit=0") {
		t.Fatalf("log line=%q", got)
	}
}
