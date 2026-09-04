// internal/session/state_test.go
package session

import (
	"errors"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// --- Facts persistence round-trip ---

func TestFactsRoundTrip(t *testing.T) {
	root := t.TempDir()
	want := Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh", WindowsProbeVersion: currentWindowsProbeVersion}
	if err := SaveFacts(root, "h1", want); err != nil {
		t.Fatal(err)
	}
	got, ok, err := LoadFacts(root, "h1")
	if err != nil || !ok {
		t.Fatalf("got=%+v ok=%v err=%v", got, ok, err)
	}
	if got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestLoadFactsInvalidatesLegacyWindowsProbe(t *testing.T) {
	root := t.TempDir()
	legacy := Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "cmd"}
	if err := writeJSON(factsPath(root, "h1"), legacy); err != nil {
		t.Fatal(err)
	}
	got, ok, err := LoadFacts(root, "h1")
	if err != nil {
		t.Fatal(err)
	}
	if ok {
		t.Fatalf("legacy Windows facts loaded as current: %+v", got)
	}
	if got != legacy {
		t.Fatalf("got %+v, want legacy payload preserved for diagnostics", got)
	}
}

func TestLoadFactsMissingReturnsFalse(t *testing.T) {
	root := t.TempDir()
	got, ok, err := LoadFacts(root, "no-such-host")
	if err != nil || ok {
		t.Fatalf("got=%+v ok=%v err=%v, want ok=false err=nil", got, ok, err)
	}
}

// --- State persistence round-trip ---

func TestStateRoundTrip(t *testing.T) {
	root := t.TempDir()
	want := shell.State{Cwd: "/home/alice", Env: map[string]string{"FOO": "bar"}}
	if err := SaveState(root, "h1", "default", want); err != nil {
		t.Fatal(err)
	}
	got, ok, err := LoadState(root, "h1", "default")
	if err != nil || !ok {
		t.Fatalf("got=%+v ok=%v err=%v", got, ok, err)
	}
	if got.Cwd != want.Cwd || len(got.Env) != len(want.Env) || got.Env["FOO"] != "bar" {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestLoadStateMissingReturnsFalse(t *testing.T) {
	root := t.TempDir()
	got, ok, err := LoadState(root, "h1", "no-such-ctx")
	if err != nil || ok {
		t.Fatalf("got=%+v ok=%v err=%v, want ok=false err=nil", got, ok, err)
	}
}

// TestStateRoundTripSeparateContexts checks that two contexts on the same
// host persist independently — a later task's exec flow keys per-context
// state by ctx name, and a collision here would silently cross-contaminate
// sessions.
func TestStateRoundTripSeparateContexts(t *testing.T) {
	root := t.TempDir()
	a := shell.State{Cwd: "/a"}
	b := shell.State{Cwd: "/b"}
	if err := SaveState(root, "h1", "ctx-a", a); err != nil {
		t.Fatal(err)
	}
	if err := SaveState(root, "h1", "ctx-b", b); err != nil {
		t.Fatal(err)
	}
	gotA, _, _ := LoadState(root, "h1", "ctx-a")
	gotB, _, _ := LoadState(root, "h1", "ctx-b")
	if gotA.Cwd != "/a" || gotB.Cwd != "/b" {
		t.Fatalf("gotA=%+v gotB=%+v, contexts crossed", gotA, gotB)
	}
}

// --- Baseline persistence round-trip ---

func TestBaselineRoundTrip(t *testing.T) {
	root := t.TempDir()
	want := map[string]string{"PATH": "/usr/bin", "LANG": "C.UTF-8"}
	if err := SaveBaseline(root, "h1", want); err != nil {
		t.Fatal(err)
	}
	got, ok, err := LoadBaseline(root, "h1")
	if err != nil || !ok {
		t.Fatalf("got=%+v ok=%v err=%v", got, ok, err)
	}
	if len(got) != len(want) || got["PATH"] != "/usr/bin" || got["LANG"] != "C.UTF-8" {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestLoadBaselineMissingReturnsFalse(t *testing.T) {
	root := t.TempDir()
	got, ok, err := LoadBaseline(root, "no-such-host")
	if err != nil || ok {
		t.Fatalf("got=%+v ok=%v err=%v, want ok=false err=nil", got, ok, err)
	}
}

// --- Corrupt JSON: a genuine error, distinct from missing (ok=false, nil). ---

func TestLoadFactsCorruptJSONReturnsError(t *testing.T) {
	root := t.TempDir()
	if err := SaveFacts(root, "h1", Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
	path := root + "/state/h1/facts.json"
	if err := os.WriteFile(path, []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, ok, err := LoadFacts(root, "h1")
	if err == nil || ok {
		t.Fatalf("ok=%v err=%v, want ok=false and a non-nil error", ok, err)
	}
}

// --- File/dir permissions: 0o700 dirs, 0o600 files. ---

func TestSaveFactsPermissions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX permission bits are not meaningful on Windows")
	}
	root := t.TempDir()
	if err := SaveFacts(root, "h1", Facts{OS: "linux"}); err != nil {
		t.Fatal(err)
	}
	dirInfo, err := os.Stat(root + "/state/h1")
	if err != nil {
		t.Fatal(err)
	}
	if perm := dirInfo.Mode().Perm(); perm != 0o700 {
		t.Fatalf("dir perm = %o, want 0700", perm)
	}
	fileInfo, err := os.Stat(root + "/state/h1/facts.json")
	if err != nil {
		t.Fatal(err)
	}
	if perm := fileInfo.Mode().Perm(); perm != 0o600 {
		t.Fatalf("file perm = %o, want 0600", perm)
	}
}

// --- Probe: first-contact OS/shell detection against a scripted fake
// transport.Transport. No network — see the fakeTransport below. ---

// fakeResp is one canned (Result, error) response for fakeTransport.Exec.
type fakeResp struct {
	res transport.Result
	err error
}

// fakeTransport answers a fixed queue of responses in call order and
// records every command it was asked to run, for tests that need to
// assert on the exact invocation shape. It never touches the network.
type fakeTransport struct {
	responses []fakeResp
	next      int
	commands  []string
}

func (f *fakeTransport) Exec(host, command string, stdin []byte, timeout time.Duration) (transport.Result, error) {
	f.commands = append(f.commands, command)
	if f.next >= len(f.responses) {
		panic("fakeTransport: Exec called more times than responses were scripted")
	}
	r := f.responses[f.next]
	f.next++
	return r.res, r.err
}

func (f *fakeTransport) Put(host, localPath, remotePath string) error {
	panic("fakeTransport: Put not expected during Probe")
}

func TestProbe(t *testing.T) {
	const pwshShell = shell.PwshDefaultShell

	cases := []struct {
		name          string
		responses     []fakeResp
		want          Facts
		allowFallback bool
		wantErr       bool
		wantErrReason string
		checkCommands func(t *testing.T, cmds []string)
	}{
		{
			// Case A: uname -s succeeds and reports Linux -> linux, no shell probing at all.
			name: "uname reports Linux",
			responses: []fakeResp{
				{res: transport.Result{ExitCode: 0, Output: []byte("Linux\n")}},
			},
			want: Facts{OS: "linux"},
		},
		{
			// Case B: uname fails (no bash on this host), the cmd-form
			// New-Item trips the pwsh-default parser signature, so the
			// loop advances and the pwsh-form succeeds.
			name: "windows, cmd form rejected then pwsh form succeeds",
			responses: []fakeResp{
				{res: transport.Result{ExitCode: 1, Output: []byte("command not found")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("ParserError: Unexpected token")}},
				{res: transport.Result{ExitCode: 0, Output: []byte("")}},
			},
			want: Facts{OS: "windows", Shell: pwshShell, Form: "pwsh"},
			// Independent evidence that Probe actually issued the
			// documented three calls in order — uname first, then the
			// cmd form (no "& " prefix), then the pwsh form ("& "
			// prefix) — not just that some three calls happened to
			// return the expected Facts.
			checkCommands: func(t *testing.T, cmds []string) {
				if len(cmds) != 3 {
					t.Fatalf("commands = %q, want 3 calls", cmds)
				}
				if cmds[0] != "uname -s" {
					t.Fatalf("call 0 = %q, want %q", cmds[0], "uname -s")
				}
				if !strings.Contains(cmds[1], shell.RemoteDir) || !strings.Contains(cmds[1], "New-Item") {
					t.Fatalf("cmd-form call = %q, want it to target New-Item on %q", cmds[1], shell.RemoteDir)
				}
				if strings.HasPrefix(cmds[1], "& ") {
					t.Fatalf("cmd-form call = %q, must not lead with \"& \"", cmds[1])
				}
				if !strings.HasPrefix(cmds[2], "& ") {
					t.Fatalf("pwsh-form call = %q, must lead with \"& \"", cmds[2])
				}
			},
		},
		{
			// Case C: uname itself fails at the transport layer (ssh exit
			// 255) -> Probe returns the TransportError, no further calls.
			name: "uname transport failure propagates",
			responses: []fakeResp{
				{err: &transport.TransportError{Reason: "ssh"}},
			},
			wantErr:       true,
			wantErrReason: "ssh",
		},
		{
			// A missing PowerShell 7 executable on a cmd-default Windows
			// OpenSSH server returns a plain remote exit, not a transport
			// error. Probe must not cache that missing executable as usable;
			// it should fall back to the in-box Windows PowerShell path.
			name: "windows, default pwsh missing falls back to windows powershell",
			responses: []fakeResp{
				{res: transport.Result{ExitCode: 1, Output: []byte("command not found")}},
				{res: transport.Result{ExitCode: 1, Output: []byte(`"C:\Program" is not recognized as an internal or external command`)}},
				{res: transport.Result{ExitCode: 1, Output: []byte(`& was unexpected at this time.`)}},
				{res: transport.Result{ExitCode: 0, Output: []byte("")}},
			},
			want:          Facts{OS: "windows", Shell: shell.WindowsPowerShellShell, Form: "cmd"},
			allowFallback: true,
			checkCommands: func(t *testing.T, cmds []string) {
				if len(cmds) != 4 {
					t.Fatalf("commands = %q, want 4 calls", cmds)
				}
				if !strings.Contains(cmds[1], shell.PwshDefaultShell) {
					t.Fatalf("call 1 = %q, want default pwsh candidate", cmds[1])
				}
				if !strings.Contains(cmds[3], shell.WindowsPowerShellShell) {
					t.Fatalf("call 3 = %q, want windows powershell fallback", cmds[3])
				}
			},
		},
		{
			// An explicit pwsh selection is strict: if PowerShell 7 cannot
			// create the scratch directory, Probe must not silently execute
			// the body with Windows PowerShell 5.1 instead.
			name: "windows, explicit pwsh failure does not fall back",
			responses: []fakeResp{
				{res: transport.Result{ExitCode: 1, Output: []byte("command not found")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("Access is denied.")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("Access is denied.")}},
			},
			wantErr:       true,
			wantErrReason: "ssh",
			checkCommands: func(t *testing.T, cmds []string) {
				for _, cmd := range cmds {
					if strings.Contains(cmd, shell.WindowsPowerShellShell) {
						t.Fatalf("explicit pwsh probe used Windows PowerShell fallback: %q", cmds)
					}
				}
			},
		},
		{
			// If no Windows PowerShell candidate can create the scratch
			// directory, Probe must fail instead of caching a guessed shell
			// form that cannot execute staged scripts.
			name: "windows, all powershell candidates fail reports transport error",
			responses: []fakeResp{
				{res: transport.Result{ExitCode: 1, Output: []byte("command not found")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("ParserError: Unexpected token")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("ParserError: Unexpected token")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("Access is denied.")}},
				{res: transport.Result{ExitCode: 1, Output: []byte("Access is denied.")}},
			},
			allowFallback: true,
			wantErr:       true,
			wantErrReason: "ssh",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			tr := &fakeTransport{responses: tc.responses}
			got, err := Probe(tr, "h1", pwshShell, tc.allowFallback, time.Minute)
			if tc.wantErr {
				var te *transport.TransportError
				if !errors.As(err, &te) {
					t.Fatalf("want *transport.TransportError, got %v (facts=%+v)", err, got)
				}
				if tc.wantErrReason != "" && te.Reason != tc.wantErrReason {
					t.Fatalf("TransportError.Reason = %q, want %q", te.Reason, tc.wantErrReason)
				}
				if tc.checkCommands != nil {
					tc.checkCommands(t, tr.commands)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Fatalf("got %+v, want %+v", got, tc.want)
			}
			if tc.checkCommands != nil {
				tc.checkCommands(t, tr.commands)
			}
		})
	}
}
