// internal/cli/run_test.go
package cli

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// probeThenRunTr fakes the two-call sequence runHost makes when no facts
// are cached yet: first session.Probe's "uname -s", then the actual
// wrapped run via "bash -s". calls records every command seen, in order,
// so a test can assert the probe actually happened before the run.
type probeThenRunTr struct {
	calls []string
}

func (f *probeThenRunTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.calls = append(f.calls, cmd)
	if cmd == "uname -s" {
		return transport.Result{ExitCode: 0, Output: []byte("Linux\n")}, nil
	}
	sent := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	out := []byte("hello\n\n" + sent + "\n/tmp\n" + env + "\n")
	return transport.Result{ExitCode: 0, Output: out}, nil
}

func (f *probeThenRunTr) Put(host, l, r string) error { return nil }

// TestRunProbesFactsWhenNotCached covers the facts cache-miss branch
// (session.Probe): with no facts pre-seeded, runHost must probe before
// running, cache the result, and still complete the run.
func TestRunProbesFactsWhenNotCached(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	f := &probeThenRunTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "hello"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if len(f.calls) < 2 || f.calls[0] != "uname -s" {
		t.Fatalf("expected a \"uname -s\" probe before the run, got calls=%v", f.calls)
	}
	facts, ok, err := session.LoadFacts(root, "web01")
	if err != nil || !ok {
		t.Fatalf("LoadFacts after probe: ok=%v err=%v", ok, err)
	}
	if facts.OS != "linux" {
		t.Fatalf("facts.OS=%q, want linux", facts.OS)
	}
	if !strings.Contains(out.String(), "a1 host=web01 exit=0") {
		t.Fatalf("passport: %q", out.String())
	}
}

// probeFailsTr fakes a transport whose very first call (the probe's
// "uname -s") fails with a TransportError — the host is unreachable
// before anything about the actual command is even known.
type probeFailsTr struct {
	calls     []string
	rawOutput []byte
}

func (f *probeFailsTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.calls = append(f.calls, cmd)
	if len(f.rawOutput) != 0 {
		return transport.Result{}, transport.NewTransportError("ssh", f.rawOutput)
	}
	return transport.Result{}, &transport.TransportError{Reason: "ssh"}
}

func (f *probeFailsTr) Put(host, l, r string) error { return nil }

// TestRunProbeTransportErrorProducesTransportErrorPassport covers the
// facts cache-miss branch's transport-error path: session.Probe itself
// can fail with a *transport.TransportError (host unreachable), which
// must route through handleTransportError exactly like a failed main
// exec — exit 98, passport transport-error=..., and no facts cached.
func TestRunProbeTransportErrorProducesTransportErrorPassport(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	f := &probeFailsTr{rawOutput: []byte("SHA256:TOPSECRET\nHost key verification failed.")}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "hello"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitTransport, out.String(), errB.String())
	}
	if !strings.Contains(out.String(), "transport-error=ssh") ||
		!strings.Contains(out.String(), "transport diagnostic: host key verification failed") {
		t.Fatalf("passport missing safe transport diagnostic: %q", out.String())
	}
	if strings.Contains(out.String(), "TOPSECRET") {
		t.Fatalf("raw SSH output leaked into passport: %q", out.String())
	}
	if len(f.calls) != 1 || f.calls[0] != "uname -s" {
		t.Fatalf("expected exactly one \"uname -s\" probe call, got calls=%v", f.calls)
	}
	if _, ok, _ := session.LoadFacts(root, "web01"); ok {
		t.Fatal("facts must not be cached when the probe itself fails")
	}
	body, err := os.ReadFile(filepath.Join(root, "art", "a1"))
	if err != nil {
		t.Fatalf("read transport artifact: %v", err)
	}
	if got, want := string(body), "transport diagnostic: host key verification failed\n"; got != want {
		t.Fatalf("transport artifact=%q, want %q", got, want)
	}
}

// pwshTr fakes the Windows Put+Exec sequence. Put records every
// remotePath it was given — the regression-test hook for the BodySlug
// bug fixed in b6c3f18 (slugging the wrapped script, which carries a
// fresh random sentinel every run, made the remote path differ on every
// single call). Since the sentinel travels inside the uploaded script
// file rather than over Exec's stdin (the Windows path's Exec call passes
// nil stdin — the body was already delivered via Put), Put reads the
// staged file back and extracts the sentinel from it with the same
// sentinelFromStdin helper the bash fakes use: PwshScript embeds the
// sentinel between single quotes too ("Write-Output '<sentinel>'"), the
// same shape BashWrap uses, so the existing extractor works unmodified.
type pwshTr struct {
	putPaths []string
	lastCmd  string
	sentinel string
}

func (f *pwshTr) Put(host, localPath, remotePath string) error {
	f.putPaths = append(f.putPaths, remotePath)
	data, err := os.ReadFile(localPath)
	if err != nil {
		return err
	}
	f.sentinel = sentinelFromStdin(data)
	return nil
}

func (f *pwshTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.lastCmd = cmd
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin"))
	out := []byte("hello\n\n" + f.sentinel + "\n/tmp\n" + env + "\n")
	return transport.Result{ExitCode: 0, Output: out}, nil
}

// TestRunWindowsHappyPathAndStableSlug covers the Windows branch
// end to end (Facts.OS=="windows"): Put is called with the expected
// RemoteDir/<slug>.ps1 path, Exec's command is built via PwshInvocation
// with the expected pwsh-form flags, and the passport reflects the
// parsed exit/output. It then re-runs the identical command and asserts
// the remote path is unchanged — the direct regression test for the
// BodySlug bug fixed in b6c3f18 (see pwshTr's doc comment).
func TestRunWindowsHappyPathAndStableSlug(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "dc01", session.Facts{OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &pwshTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "dc01", "--", "Get-Date"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}

	wantSlug := shell.BodySlug([]byte("Get-Date"))
	wantPath := shell.RemoteDir + "/" + wantSlug + ".ps1"
	if len(f.putPaths) != 1 || f.putPaths[0] != wantPath {
		t.Fatalf("Put remotePath(s) = %v, want exactly [%q]", f.putPaths, wantPath)
	}
	if !strings.Contains(f.lastCmd, "-NoProfile -ExecutionPolicy Bypass -File "+wantPath) {
		t.Fatalf("Exec command = %q, want it to invoke -File %s", f.lastCmd, wantPath)
	}
	if !strings.HasPrefix(f.lastCmd, `& "`+shell.PwshDefaultShell+`"`) {
		t.Fatalf("Exec command must use PwshInvocation's pwsh form (\"& \" prefix): %q", f.lastCmd)
	}
	if !strings.Contains(out.String(), "a1 host=dc01 exit=0") || !strings.Contains(out.String(), "hello") {
		t.Fatalf("passport: %q", out.String())
	}

	rc2 := runWith(f, []string{"--ctx", "t1", "dc01", "--", "Get-Date"}, &out, &errB)
	if rc2 != 0 {
		t.Fatalf("second run rc=%d stderr=%s", rc2, errB.String())
	}
	if len(f.putPaths) != 2 {
		t.Fatalf("expected two Put calls total after a second identical run, got %d: %v", len(f.putPaths), f.putPaths)
	}
	if f.putPaths[1] != f.putPaths[0] {
		t.Fatalf("remote path changed across two runs of the identical command: %q != %q (BodySlug regression)", f.putPaths[1], f.putPaths[0])
	}
}

func TestRunWindowsBodySelectsPowerShellHost(t *testing.T) {
	body := []byte("Get-Host | Select-Object -ExpandProperty Version\n")
	tests := []struct {
		name       string
		selector   string
		executable string
	}{
		{name: "PowerShell 7", selector: powerShellHostPwsh, executable: shell.PwshDefaultShell},
		{name: "Windows PowerShell 5.1", selector: powerShellHostWindowsPowerShell, executable: shell.WindowsPowerShellShell},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			if err := session.SaveFacts(root, "sccm01", session.Facts{
				OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh",
			}); err != nil {
				t.Fatalf("SaveFacts: %v", err)
			}
			bodyPath := filepath.Join(root, "check.ps1")
			if err := os.WriteFile(bodyPath, body, 0o600); err != nil {
				t.Fatalf("WriteFile: %v", err)
			}

			f := &pwshTr{}
			var out, errB bytes.Buffer
			rc := runWith(f, []string{
				"--powershell-host", tt.selector,
				"--body-file", bodyPath,
				"sccm01",
			}, &out, &errB)
			if rc != 0 {
				t.Fatalf("rc=%d stderr=%s", rc, errB.String())
			}

			wantPath := shell.RemoteDir + "/" + shell.BodySlug(body) + ".ps1"
			if len(f.putPaths) != 1 || f.putPaths[0] != wantPath {
				t.Fatalf("Put remotePath(s) = %v, want exactly [%q]", f.putPaths, wantPath)
			}
			if !strings.HasPrefix(f.lastCmd, `& "`+tt.executable+`"`) {
				t.Fatalf("Exec command did not select %s: %q", tt.name, f.lastCmd)
			}
			if !strings.Contains(f.lastCmd, "-NoProfile -ExecutionPolicy Bypass -File "+wantPath) {
				t.Fatalf("Exec command = %q, want body-file invocation for %s", f.lastCmd, wantPath)
			}
		})
	}
}

func TestRunWindowsIgnoresPOSIXShell(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "dc01", session.Facts{
		OS: "windows", Shell: shell.PwshDefaultShell, Form: "pwsh",
	}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &pwshTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--posix-shell", "/bin/ash", "dc01", "--", "Get-Date"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	wantPath := shell.RemoteDir + "/" + shell.BodySlug([]byte("Get-Date")) + ".ps1"
	if len(f.putPaths) != 1 || f.putPaths[0] != wantPath {
		t.Fatalf("Put remotePath(s) = %v, want exactly [%q]", f.putPaths, wantPath)
	}
	if !strings.HasPrefix(f.lastCmd, `& "`+shell.PwshDefaultShell+`"`) ||
		strings.Contains(f.lastCmd, "/bin/ash") {
		t.Fatalf("Windows Exec must retain the PowerShell path: %q", f.lastCmd)
	}
}

func TestRunRejectsInvalidPowerShellHost(t *testing.T) {
	t.Setenv("SSHAI_ROOT", t.TempDir())
	var out, errB bytes.Buffer
	rc := runWith(&fakeTr{}, []string{"--powershell-host", "powershell", "h1", "--", "true"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d", rc, exitUsage)
	}
	if !strings.Contains(errB.String(), `invalid --powershell-host="powershell"`) {
		t.Fatalf("stderr=%q", errB.String())
	}
}

// fakeTr is a network-free transport.Transport: Exec synthesizes a
// BashWrap-shaped reply directly from the stdin it receives, rather than
// relying on a second-phase hook — the sentinel is random per run, but it
// travels to Exec inside stdin, so Exec can parse it out and echo it back
// in the reply it fabricates, entirely within one call. This keeps the
// test free of any coordination between "capture the sentinel" and "build
// the response" phases.
type fakeTr struct {
	calls     int
	lastCmd   string
	lastStdin []byte
	rc        int
	body      string // overrides the default "hello" body — e.g. a NUL-embedded binary body
}

// sentinelFromStdin extracts the sentinel BashWrap embeds between single
// quotes in its epilogue's `printf '\n%s\n' '<sentinel>'` line.
func sentinelFromStdin(stdin []byte) string {
	s := string(stdin)
	i := strings.Index(s, "'__SSHAI_")
	if i < 0 {
		return ""
	}
	rest := s[i+1:]
	j := strings.Index(rest, "'")
	if j < 0 {
		return ""
	}
	return rest[:j]
}

func (f *fakeTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.calls++
	f.lastCmd, f.lastStdin = cmd, stdin
	sent := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	b := f.body
	if b == "" {
		b = "hello"
	}
	out := []byte(b + "\n\n" + sent + "\n/tmp\n" + env + "\n")
	return transport.Result{ExitCode: f.rc, Output: out}, nil
}

func (f *fakeTr) Put(host, l, r string) error {
	f.calls++
	return nil
}

func TestRunLinuxShellSelection(t *testing.T) {
	tests := []struct {
		name       string
		posixShell string
		wantCmd    string
	}{
		{name: "default bash", wantCmd: "bash -s"},
		{name: "explicit ash", posixShell: "/bin/ash", wantCmd: shell.POSIXShellInvocation("/bin/ash")},
		{name: "metacharacter path remains quoted", posixShell: "/opt/ash;$(id)'", wantCmd: shell.POSIXShellInvocation("/opt/ash;$(id)'")},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
				t.Fatalf("SaveFacts: %v", err)
			}

			args := []string{"--ctx", "t1"}
			if tt.posixShell != "" {
				args = append(args, "--posix-shell", tt.posixShell)
			}
			args = append(args, "web01", "--", "echo", "hello")
			f := &fakeTr{}
			var out, errB bytes.Buffer
			rc := runWith(f, args, &out, &errB)
			if rc != 0 {
				t.Fatalf("rc=%d stderr=%s", rc, errB.String())
			}
			if f.lastCmd != tt.wantCmd {
				t.Fatalf("Exec command=%q, want %q", f.lastCmd, tt.wantCmd)
			}
			if tt.posixShell != "" && strings.Contains(f.lastCmd, "bash") {
				t.Fatalf("explicit POSIX shell command must not invoke bash: %q", f.lastCmd)
			}
			if strings.Contains(f.lastCmd, "echo hello") || !strings.Contains(string(f.lastStdin), "echo hello") {
				t.Fatalf("body must travel only on stdin: command=%q stdin=%q", f.lastCmd, f.lastStdin)
			}
			if !strings.Contains(out.String(), "a1 host=web01 exit=0") {
				t.Fatalf("passport: %q", out.String())
			}
		})
	}
}

func TestRunRejectsInvalidPOSIXShellBeforeTransport(t *testing.T) {
	for _, tt := range []struct {
		name  string
		value string
	}{
		{name: "explicit empty", value: ""},
		{name: "whitespace", value: "/bin/ash bad"},
		{name: "newline", value: "/bin/ash\nbad"},
		{name: "control", value: "/bin/ash\x00bad"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("SSHAI_ROOT", t.TempDir())
			f := &fakeTr{}
			var out, errB bytes.Buffer
			rc := runWith(f, []string{"--posix-shell=" + tt.value, "web01", "--", "echo", "hello"}, &out, &errB)
			if rc != exitUsage {
				t.Fatalf("rc=%d, want %d; stderr=%q", rc, exitUsage, errB.String())
			}
			want := fmt.Sprintf(`run: invalid --posix-shell=%q (want one path without whitespace or control characters)`, tt.value)
			if !strings.Contains(errB.String(), want) {
				t.Fatalf("stderr=%q, want %q", errB.String(), want)
			}
			if f.calls != 0 {
				t.Fatalf("transport calls=%d, want none", f.calls)
			}
		})
	}
}

// TestRunRejectsReservedCtxName covers constraint 1 from the task brief: a
// ctx equal to "facts" or "baseline" would alias session's own
// facts.json/baseline.json. Rejected before any facts lookup, so no probe
// or facts.json is ever written for the host.
func TestRunRejectsReservedCtxName(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "facts", "web01", "--", "echo", "hi"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitUsage, out.String(), errB.String())
	}
	if errB.Len() == 0 {
		t.Fatal("expected a usage error on stderr")
	}
	if _, ok, _ := session.LoadFacts(root, "web01"); ok {
		t.Fatal("ctx validation must reject before any facts lookup happens")
	}
}

func TestValidateHostRejectsStatePathTraversal(t *testing.T) {
	for _, host := range []string{"../outside", `..\\outside`, ".", "..", "host/name", "host name"} {
		if err := validateHost(host); err == nil {
			t.Errorf("validateHost(%q) succeeded", host)
		}
	}
	for _, host := range []string{"web01", "db-01.example", "host_name"} {
		if err := validateHost(host); err != nil {
			t.Errorf("validateHost(%q): %v", host, err)
		}
	}
}

// TestRunRejectsCtxWithSlash covers the other half of constraint 1: a ctx
// containing "/" could otherwise address a path outside
// <root>/state/<host>/.
func TestRunRejectsCtxWithSlash(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "a/b", "web01", "--", "echo", "hi"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitUsage, errB.String())
	}
}

// TestRunPolicyDenialWritesAuditAndStatusLine covers the brief's policy
// path: a readonly host denies a non-allowlisted command before ever
// reaching facts/exec (f.lastCmd stays empty — Exec is never called), and
// prints the exact "<host> policy-denied" status line, exits 97, and
// appends a "denied-readonly" audit entry.
func TestRunPolicyDenialWritesAuditAndStatusLine(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	toml := "[hosts.web01]\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatalf("write config.toml: %v", err)
	}

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"web01", "--", "rm", "-rf", "/"}, &out, &errB)
	if rc != exitPolicy {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitPolicy, out.String(), errB.String())
	}
	if !strings.Contains(out.String(), "web01 policy-denied") {
		t.Fatalf("stdout missing policy-denied status line: %q", out.String())
	}
	if f.lastCmd != "" {
		t.Fatal("Exec must not be called once the policy check denies the command")
	}
	auditData, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
	if err != nil {
		t.Fatalf("read audit.jsonl: %v", err)
	}
	if !strings.Contains(string(auditData), "denied-readonly") {
		t.Fatalf("audit.jsonl missing denied-readonly verdict: %s", auditData)
	}
}

// partialParseTr's Exec returns output where the sentinel appears but
// nothing meaningful follows it (an empty cwd line, no env line at all) —
// the truncated/malformed-epilogue shape BashParse documents as ok=true
// with Cwd=="" and Env==nil, distinct from ok=false.
type partialParseTr struct{ lastCmd string }

func (f *partialParseTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.lastCmd = cmd
	sent := sentinelFromStdin(stdin)
	out := []byte("out\n\n" + sent + "\n")
	return transport.Result{ExitCode: 0, Output: out}, nil
}

func (f *partialParseTr) Put(host, l, r string) error { return nil }

// TestRunLinuxPartialParseMergesWithPreviousState covers constraint 2: a
// partial epilogue must not clobber a known-good previously saved Cwd/Env
// with the empty/nil values a truncated parse produces.
func TestRunLinuxPartialParseMergesWithPreviousState(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}
	prev := shell.State{Cwd: "/old", Env: map[string]string{"A": "1"}}
	if err := session.SaveState(root, "web01", "t1", prev); err != nil {
		t.Fatalf("SaveState: %v", err)
	}

	f := &partialParseTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "out"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}

	got, ok, err := session.LoadState(root, "web01", "t1")
	if err != nil || !ok {
		t.Fatalf("LoadState: ok=%v err=%v", ok, err)
	}
	if got.Cwd != "/old" {
		t.Fatalf("Cwd clobbered by partial parse: got %q, want %q", got.Cwd, "/old")
	}
	if got.Env["A"] != "1" {
		t.Fatalf("Env clobbered by partial parse: got %v, want A=1", got.Env)
	}
}

// multiHostTr fakes a transport whose Exec outcome differs per host,
// exercising fan-out's per-host result collection (Task 12). rcs maps
// host -> the ExitCode a normal run should observe; transportErr maps
// host -> the Reason a *transport.TransportError should carry instead
// (simulating an unreachable host, e.g. real ssh's exit 255). Both maps
// are read-only after construction and never written to from Exec, so
// concurrent calls from fan-out's goroutines need no locking — Go maps
// are safe for concurrent reads with no concurrent writes.
// body overrides the first line of a host's fake command output (default
// "hello") — used to prove classification can't be fooled by remote
// output that happens to look like a status line.
type multiHostTr struct {
	rcs              map[string]int
	transportErr     map[string]string
	body             map[string]string
	failStateSave    bool
	acceptedHostKeys map[string]transport.HostKey
}

func (f *multiHostTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	if reason, ok := f.transportErr[host]; ok {
		return transport.Result{}, &transport.TransportError{Reason: reason}
	}
	sent := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	b := f.body[host]
	if b == "" {
		b = "hello"
	}
	out := []byte(b + "\n\n" + sent + "\n/tmp\n" + env + "\n")
	if f.failStateSave {
		if err := os.Mkdir(filepath.Join(os.Getenv("SSHAI_ROOT"), "state", host, "default.json"), 0o700); err != nil {
			return transport.Result{}, err
		}
	}
	return transport.Result{ExitCode: f.rcs[host], Output: out}, nil
}

func (f *multiHostTr) Put(host, l, r string) error { return nil }

func (f *multiHostTr) AcceptedHostKey(host string) (transport.HostKey, bool, error) {
	key, ok := f.acceptedHostKeys[host]
	return key, ok, nil
}

func TestRunAcceptNewHostKeyRequiresOneExactTarget(t *testing.T) {
	for _, tc := range []struct {
		name string
		args []string
	}{
		{"missing target", []string{"--accept-new-host-key", "db01", "web01", "--", "true"}},
		{"duplicate target", []string{"--accept-new-host-key", "web01", "web01", "web01", "--", "true"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("SSHAI_ROOT", t.TempDir())
			f := &probeThenRunTr{}
			var out, errB bytes.Buffer
			rc := runWith(f, tc.args, &out, &errB)
			if rc != exitUsage ||
				!strings.Contains(errB.String(), "must name exactly one host alias") {
				t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errB.String())
			}
			if len(f.calls) != 0 {
				t.Fatalf("transport ran for invalid scope: %v", f.calls)
			}
		})
	}
}

func TestRunRejectsUnsupportedProxyJumpOverride(t *testing.T) {
	t.Setenv("SSHAI_ROOT", t.TempDir())
	f := &probeThenRunTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--proxy-jump", "jump01", "web01", "--", "true"}, &out, &errB)
	if rc != exitUsage || !strings.Contains(errB.String(), "want none") {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errB.String())
	}
	if len(f.calls) != 0 {
		t.Fatalf("transport ran for invalid route override: %v", f.calls)
	}
}

func TestRunPersistsAndRendersAcceptedHostKeyEvidence(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "new-host")
	key := transport.HostKey{Algorithm: "ssh-ed25519", Fingerprint: "SHA256:abc123"}
	f := &multiHostTr{
		rcs:              map[string]int{"new-host": 0},
		acceptedHostKeys: map[string]transport.HostKey{"new-host": key},
	}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{
		"--accept-new-host-key", "new-host",
		"new-host", "--", "true",
	}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errB.String())
	}
	for _, want := range []string{
		"accepted-host-key-algorithm=ssh-ed25519",
		"accepted-host-key-fingerprint=SHA256:abc123",
	} {
		if !strings.Contains(out.String(), want) {
			t.Fatalf("passport missing %q: %q", want, out.String())
		}
	}

	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	got, _, err := st.Get("a1")
	if err != nil || got.AcceptedHostKeyAlgorithm != key.Algorithm ||
		got.AcceptedHostKeyFingerprint != key.Fingerprint {
		t.Fatalf("stored host-key evidence=%+v err=%v", got, err)
	}
}

// seedLinuxFacts pre-seeds OS=linux facts for each host so runHost never
// calls session.Probe — keeping multiHostTr's Exec to exactly one call
// per host (the actual command), matching the pattern TestRunLinuxHappyPath
// already establishes for the single-host fakes above.
func seedLinuxFacts(t *testing.T, root string, hosts ...string) {
	t.Helper()
	for _, h := range hosts {
		if err := session.SaveFacts(root, h, session.Facts{OS: "linux"}); err != nil {
			t.Fatalf("SaveFacts(%s): %v", h, err)
		}
	}
}

// hostIndex returns the byte offset of "host=<h>" in s, or -1, used to
// assert argv-order printing.
func hostIndex(s, h string) int {
	return strings.Index(s, "host="+h)
}

// TestRunFanoutTwoHostsAggregateAndArgvOrder covers the brief's Step 1
// first case: two hosts, no transport error. Both passports must appear
// in argv order (h1 before h2), separated by a blank line, followed by
// one aggregate line, and the process exit must be the max remote exit
// across hosts (h1=0, h2=5 -> 5) — no policy denial or transport error
// is present to outrank it.
func TestRunFanoutTwoHostsAggregateAndArgvOrder(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	f := &multiHostTr{rcs: map[string]int{"h1": 0, "h2": 5}}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "--", "true"}, &out, &errB)
	if rc != 5 {
		t.Fatalf("rc=%d, want 5 (max remote exit); stdout=%s stderr=%s", rc, out.String(), errB.String())
	}

	s := out.String()
	i1, i2 := hostIndex(s, "h1"), hostIndex(s, "h2")
	if i1 < 0 || i2 < 0 || i1 > i2 {
		t.Fatalf("passports not present in argv order (h1 then h2): %q", s)
	}
	if !strings.Contains(s, "host=h1 exit=0") {
		t.Fatalf("missing h1 passport with exit=0: %q", s)
	}
	if !strings.Contains(s, "host=h2 exit=5") {
		t.Fatalf("missing h2 passport with exit=5: %q", s)
	}
	// Anchor on the actual junction (h1's body "hello" immediately
	// followed by the blank-line separator), not a bare "\n\n" — that
	// would pass for unrelated reasons, e.g. any body containing a blank
	// line of its own.
	if !strings.Contains(s, "hello\n\n") {
		t.Fatalf("passports must be separated by a blank line: %q", s)
	}
	if !strings.Contains(s, "hosts=2 ok=1 failed=1 transport-errors=0") {
		t.Fatalf("missing aggregate line: %q", s)
	}
}

// TestRunFanoutIgnoresStatusLookingTextInRemoteOutput is the regression
// test for a classification bug found in review: runInvocation must use the
// typed host outcome, never rendered output — otherwise a host whose output
// happens to contain "transport-error=" or " policy-denied" (e.g. grepping
// sshai's own audit.jsonl, or catting another host's earlier passport) would
// be misclassified from its own successful remote output.
func TestRunFanoutIgnoresStatusLookingTextInRemoteOutput(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	f := &multiHostTr{
		rcs:  map[string]int{"h1": 0, "h2": 0},
		body: map[string]string{"h2": "line1 transport-error=ssh h1 policy-denied"},
	}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d, want 0; stdout=%s stderr=%s", rc, out.String(), errB.String())
	}
	if !strings.Contains(out.String(), "hosts=2 ok=2 failed=0 transport-errors=0") {
		t.Fatalf("aggregate line misread status-looking remote output: %q", out.String())
	}
}

// TestRunFanoutTransportErrorForcesExit98 covers the brief's Step 1
// second case: one host fails at the transport level (the fan-out
// equivalent of real ssh's exit 255) rather than returning a remote
// exit code. That must count toward "transport-errors" in the
// aggregate, and force the overall process exit to 98 regardless of any
// other host's outcome.
func TestRunFanoutTransportErrorForcesExit98(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	f := &multiHostTr{
		rcs:          map[string]int{"h1": 0},
		transportErr: map[string]string{"h2": "ssh"},
	}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "--", "true"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitTransport, out.String(), errB.String())
	}

	s := out.String()
	if !strings.Contains(s, "host=h1 exit=0") {
		t.Fatalf("missing h1 passport with exit=0: %q", s)
	}
	if !strings.Contains(s, "host=h2 transport-error=ssh") {
		t.Fatalf("missing h2 transport-error passport: %q", s)
	}
	if !strings.Contains(s, "hosts=2 ok=1 failed=0 transport-errors=1") {
		t.Fatalf("missing aggregate line: %q", s)
	}
}

// TestRunFanoutPolicyDenialOutranksRemoteExit covers the brief's
// worst-exit precedence: a policy denial (97) outranks a remote exit
// even when that remote exit is numerically larger (h3=200 here) — the
// rule is a strict priority tier, not a numeric max across all hosts.
// Only h2 is configured readonly, so the shared command (not on the
// allowlist) is denied for h2 alone while h1 and h3 actually run it.
func TestRunFanoutPolicyDenialOutranksRemoteExit(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2", "h3")
	toml := "[hosts.h2]\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatalf("write config.toml: %v", err)
	}

	f := &multiHostTr{rcs: map[string]int{"h1": 0, "h3": 200}}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "h3", "--", "rm", "-rf", "/tmp/x"}, &out, &errB)
	if rc != exitPolicy {
		t.Fatalf("rc=%d, want %d (policy denial outranks h3's remote exit 200); stdout=%s stderr=%s", rc, exitPolicy, out.String(), errB.String())
	}

	s := out.String()
	if !strings.Contains(s, "h2 policy-denied") {
		t.Fatalf("missing h2 policy-denied status line: %q", s)
	}
	if !strings.Contains(s, "hosts=3 ok=1 failed=2 transport-errors=0") {
		t.Fatalf("missing aggregate line: %q", s)
	}
}

// TestRunFanoutTransportErrorOutranksPolicyDenial covers the top of the
// precedence tier: a transport error (98) wins even over a policy
// denial (97) elsewhere in the same fan-out.
func TestRunFanoutTransportErrorOutranksPolicyDenial(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")
	toml := "[hosts.h2]\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatalf("write config.toml: %v", err)
	}

	f := &multiHostTr{transportErr: map[string]string{"h1": "ssh"}}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "--", "rm", "-rf", "/tmp/x"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitTransport, out.String(), errB.String())
	}
	if !strings.Contains(out.String(), "hosts=2 ok=0 failed=1 transport-errors=1") {
		t.Fatalf("missing aggregate line: %q", out.String())
	}
}

// TestFanoutBudget covers Step 3's per-host budget rule: --budget
// divided evenly across hosts by integer division, floored at 100
// tokens per host.
func TestFanoutBudget(t *testing.T) {
	cases := []struct {
		total, n, want int
	}{
		{300, 3, 100},
		{1000, 3, 333},
		{50, 1, 100},
		{0, 3, 100},
	}
	for _, c := range cases {
		if got := fanoutBudget(c.total, c.n); got != c.want {
			t.Errorf("fanoutBudget(%d, %d) = %d, want %d", c.total, c.n, got, c.want)
		}
	}
}

// TestRunDeltaNoChangeSecondRun covers the brief's Step 1 third case: two
// identical runs with --delta. fakeTr's Exec is deterministic (always
// produces "hello" as the body), so the two runs write byte-identical
// artifacts. The first run has no previous run for the key (exact line
// "delta: no previous run for this key", no "delta=" flag on the status
// line); the second must carry "delta=a1" on its status line and a body of
// "no change since a1".
func TestRunDeltaNoChangeSecondRun(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &fakeTr{rc: 0}
	var out1, errB1 bytes.Buffer
	rc1 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "echo", "hello"}, &out1, &errB1)
	if rc1 != 0 {
		t.Fatalf("first run rc=%d stderr=%s", rc1, errB1.String())
	}
	p1 := out1.String()
	if strings.Contains(p1, "delta=") {
		t.Fatalf("first run must have no previous run to key off of: %q", p1)
	}
	if !strings.Contains(p1, "delta: no previous run for this key") {
		t.Fatalf("first run passport missing the exact no-previous-run line: %q", p1)
	}

	var out2, errB2 bytes.Buffer
	rc2 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "echo", "hello"}, &out2, &errB2)
	if rc2 != 0 {
		t.Fatalf("second run rc=%d stderr=%s", rc2, errB2.String())
	}
	p2 := out2.String()
	if !strings.Contains(p2, "delta=a1") {
		t.Fatalf("second run passport missing delta=a1 on the status line: %q", p2)
	}
	if !strings.Contains(p2, "no change since a1") {
		t.Fatalf("second run passport missing the no-change body: %q", p2)
	}
}

// TestRunHostDeltaKeysDoNotCollideAcrossHosts is the explicit regression
// test for --delta's fan-out interaction: delta.Key includes the host (see
// delta.Key's own signature), so two hosts running the identical
// (ctx, command) pair must never see each other's previous run as their own
// delta base. runHost is called directly (not through runInvocation's
// concurrent goroutines) so artifact IDs are assigned in a known,
// deterministic order: h1's first run -> a1, h2's first run -> a2, h1's
// second run -> a3, h2's second run -> a4.
func TestRunHostDeltaKeysDoNotCollideAcrossHosts(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	seedLinuxFacts(t, root, "h1", "h2")

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	defer store.Close()

	f := &fakeTr{rc: 0}
	deps := Deps{Tr: f, Store: store}
	base := Opts{Ctx: "t1", Command: "echo hello", Budget: 500, Delta: true}

	opts1 := base
	opts1.Host = "h1"
	opts2 := base
	opts2.Host = "h2"

	var out bytes.Buffer
	if outcome := runHost(deps, opts1, &out, &out); outcome.ExitCode() != 0 {
		t.Fatalf("h1 first run rc=%d: %s", outcome.ExitCode(), out.String())
	}
	out.Reset()
	if outcome := runHost(deps, opts2, &out, &out); outcome.ExitCode() != 0 {
		t.Fatalf("h2 first run rc=%d: %s", outcome.ExitCode(), out.String())
	}
	out.Reset()

	// h1's second run must key off its OWN previous run (a1), never h2's
	// (a2) — a cross-host collision would show "delta=a2" here instead.
	if outcome := runHost(deps, opts1, &out, &out); outcome.ExitCode() != 0 {
		t.Fatalf("h1 second run rc=%d: %s", outcome.ExitCode(), out.String())
	}
	p1 := out.String()
	if !strings.Contains(p1, "delta=a1") {
		t.Fatalf("h1 second run must key off its own a1: %q", p1)
	}
	if strings.Contains(p1, "delta=a2") {
		t.Fatalf("h1 second run must not key off h2's a2 (cross-host collision): %q", p1)
	}
	out.Reset()

	// h2's second run must key off its OWN previous run (a2), never h1's
	// rows (a1 or a3).
	if outcome := runHost(deps, opts2, &out, &out); outcome.ExitCode() != 0 {
		t.Fatalf("h2 second run rc=%d: %s", outcome.ExitCode(), out.String())
	}
	p2 := out.String()
	if !strings.Contains(p2, "delta=a2") {
		t.Fatalf("h2 second run must key off its own a2: %q", p2)
	}
}

// TestRunDeltaRenderErrorFallsBackToNormalPassport is the advisor-review
// regression test for the render-error-path fix: LastByKey filters on
// pruned=0 only, so a previous run's artifact file can be gone (gc race,
// manual removal) while its row still looks eligible as a delta base —
// delta.Render then fails to read prevPath. The already-completed remote
// run (and its already-Saved artifact) must not be thrown away over that:
// the run must still succeed (rc==0), print a normal passport for the new
// artifact (host line + file=), and report the render failure on stderr
// rather than silently dropping to a bare exit code with no stdout at all.
func TestRunDeltaRenderErrorFallsBackToNormalPassport(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &fakeTr{rc: 0}
	var out1, errB1 bytes.Buffer
	rc1 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "echo", "hello"}, &out1, &errB1)
	if rc1 != 0 {
		t.Fatalf("first run rc=%d stderr=%s", rc1, errB1.String())
	}

	// Simulate the previous artifact's file being gone despite its row
	// still carrying pruned=0 (a gc race or manual removal), which is
	// exactly what LastByKey does not filter out.
	if err := os.Remove(filepath.Join(root, "art", "a1")); err != nil {
		t.Fatalf("remove previous artifact: %v", err)
	}

	var out2, errB2 bytes.Buffer
	rc2 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "echo", "hello"}, &out2, &errB2)
	if rc2 != 0 {
		t.Fatalf("second run rc=%d, want 0 (render failure must not abort an already-saved run); stdout=%q stderr=%q", rc2, out2.String(), errB2.String())
	}
	p2 := out2.String()
	if !strings.Contains(p2, "a2 host=web01") {
		t.Fatalf("second run stdout missing the normal passport for the new artifact: %q", p2)
	}
	if !strings.Contains(p2, "file=") {
		t.Fatalf("second run stdout missing file= line: %q", p2)
	}
	if !strings.Contains(p2, "delta=a1") {
		t.Fatalf("second run status line must still carry delta=a1 (DeltaBase was set before Save, independent of the render failure): %q", p2)
	}
	if errB2.Len() == 0 {
		t.Fatal("expected the render failure to be reported on stderr")
	}
}

// TestRunDeltaBinaryArtifactSuppressesDiffBody is the task-review
// regression test: a binary artifact (NUL detected within the first 8KiB
// of output — the same rule artifact.RenderPassport already uses to
// suppress its own tail/inline body) must not get a text unified diff of
// raw binary bytes even when a previous run for the key exists. Before
// this fix, the opts.Delta && havePrev branch called delta.Render
// unconditionally, so a binary artifact would get "binary=1" on its status
// line immediately followed by a garbled diff of raw bytes — suppression
// depended on whether a previous run happened to exist, an accident of
// history rather than a deliberate rule. Per the required fix shape, the
// binary case routes through RenderPassport directly instead: no diff
// body, but the status line still carries delta=aN via Meta.DeltaBase (set
// before Save regardless of the branch taken), so "a previous run exists"
// is still communicated.
func TestRunDeltaBinaryArtifactSuppressesDiffBody(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &fakeTr{rc: 0, body: "bin\x00ary-old"}
	var out1, errB1 bytes.Buffer
	rc1 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "cat", "file.bin"}, &out1, &errB1)
	if rc1 != 0 {
		t.Fatalf("first run rc=%d stderr=%s", rc1, errB1.String())
	}
	if !strings.Contains(out1.String(), "binary=1") {
		t.Fatalf("first run must be classified binary: %q", out1.String())
	}

	// Different binary content on the second run — a diff of differing
	// binary bytes is what actually produces garbled unified-diff noise
	// (identical content would just take the "no change" branch, which is
	// short text and would mask the bug this test targets).
	f.body = "bin\x00ary-new"
	var out2, errB2 bytes.Buffer
	rc2 := runWith(f, []string{"--ctx", "t1", "--delta", "web01", "--", "cat", "file.bin"}, &out2, &errB2)
	if rc2 != 0 {
		t.Fatalf("second run rc=%d stderr=%s", rc2, errB2.String())
	}
	p2 := out2.String()
	if !strings.Contains(p2, "binary=1") {
		t.Fatalf("second run must still be classified binary: %q", p2)
	}
	if !strings.Contains(p2, "delta=a1") {
		t.Fatalf("second run status line must carry delta=a1 even without a diff body: %q", p2)
	}
	if strings.Contains(p2, "-bin") || strings.Contains(p2, "+bin") || strings.Contains(p2, "@@") {
		t.Fatalf("binary artifact must not get a text diff body (unified-diff markers found): %q", p2)
	}
	if strings.Contains(p2, "\x00") {
		t.Fatalf("binary artifact passport must not contain raw NUL bytes: %q", p2)
	}
}

// TestRunBodyFilePersistsHashWithoutBodyText protects the body-file
// boundary: arbitrary script text may contain values that a heuristic
// redactor cannot recognize. Long-lived run metadata and audit JSONL must
// therefore keep only the body hash, never a textual preview.
func TestRunBodyFilePersistsHashWithoutBodyText(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}
	bodyFile := filepath.Join(t.TempDir(), "body.sh")
	const body = "echo opaque-value-9e7d"
	if err := os.WriteFile(bodyFile, []byte(body), 0o644); err != nil {
		t.Fatalf("write body file: %v", err)
	}

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "--body-file", bodyFile, "web01"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	defer store.Close()
	m, _, err := store.Get("a1")
	if err != nil {
		t.Fatalf("store.Get: %v", err)
	}

	wantHash := "body:" + sha256Hex(body)[:16]
	if m.Command != wantHash {
		t.Fatalf("Meta.Command = %q, want hash only %q", m.Command, wantHash)
	}

	audit, err := os.ReadFile(filepath.Join(root, "audit.jsonl"))
	if err != nil {
		t.Fatalf("read audit.jsonl: %v", err)
	}
	if strings.Contains(string(audit), body) || strings.Contains(string(audit), "opaque-value-9e7d") {
		t.Fatalf("audit.jsonl persisted body text: %s", audit)
	}
	if !strings.Contains(string(audit), wantHash) {
		t.Fatalf("audit.jsonl = %s, want body hash %q", audit, wantHash)
	}
}

// TestRunOpportunisticGCPrunesWhenOverCap covers the task-15 brief's
// resolution 1: `run` calls gc opportunistically, once per invocation,
// when the artifact store's total non-pruned size has grown past
// RetentionMaxBytes. A tiny cap (100 bytes) and a 500-byte pre-existing
// artifact guarantee the cap is already exceeded before this run even
// starts; the run's own tiny "hello" output (well under the cap on its
// own) must survive the prune while the oversized older artifact does
// not — oldest-first, per gcStore's own contract. Crucially, the run's own
// rc and passport must be entirely unaffected by gc running afterward.
func TestRunOpportunisticGCPrunesWhenOverCap(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("retention_max_bytes = 100\n"), 0o600); err != nil {
		t.Fatalf("write config.toml: %v", err)
	}
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	preexisting, err := store.Save(artifact.Meta{Host: "web01", Ctx: "default", Command: "old", Ts: time.Now()}, "kold", bytes.Repeat([]byte("x"), 500))
	if err != nil {
		t.Fatalf("seed pre-existing artifact: %v", err)
	}
	store.Close()

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "hello"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "a2 host=web01 exit=0") {
		t.Fatalf("run's own passport must be unaffected by opportunistic gc: %q", out.String())
	}

	st2, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("reopen store: %v", err)
	}
	defer st2.Close()
	if _, _, err := st2.Get(preexisting.ID); err == nil || !strings.Contains(err.Error(), "artifact pruned") {
		t.Fatalf("expected opportunistic gc to prune the oversized pre-existing artifact %s, got err=%v", preexisting.ID, err)
	}
	if _, path, err := st2.Get("a2"); err != nil || path == "" {
		t.Fatalf("this run's own artifact a2 must survive (well under the cap): path=%q err=%v", path, err)
	}
}

// TestRunFanoutOpportunisticGCProtectsAllJustWrittenArtifacts is the
// fix-round-1 regression test for Finding 1: runInvocation writes N artifacts
// (one per host) in a single `run` invocation, but maybeGC is called only
// once, after all N have already been saved (run.go:233-235). The
// pre-fix "floor" heuristic in gcStore exempted exactly one row — the
// single newest by ts across the whole store — so a fan-out run's other
// N-1 just-written artifacts could still be pruned straight back out.
// Verified RED against b983023 (the pre-fix commit): this exact test
// failed there with "a1 ... artifact pruned". The fix replaces the
// heuristic with an explicit protect set built from ALL N of this
// invocation's saved ids (protectSet(ids...), run.go), threaded out of
// runInvocation via its second return value. With a cap far smaller than the
// combined output of 3 hosts, none of the 3 artifacts this very
// invocation just wrote may be pruned.
func TestRunFanoutOpportunisticGCProtectsAllJustWrittenArtifacts(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("retention_max_bytes = 1\n"), 0o600); err != nil {
		t.Fatalf("write config.toml: %v", err)
	}
	seedLinuxFacts(t, root, "h1", "h2", "h3")

	f := &multiHostTr{rcs: map[string]int{"h1": 0, "h2": 0, "h3": 0}}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"h1", "h2", "h3", "--", "true"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}

	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	defer store.Close()

	// Exactly 3 Save calls happened (one per host), so ids a1..a3 are
	// each used exactly once, regardless of which host's goroutine
	// committed first under fan-out's concurrent scheduling. ALL THREE
	// must survive.
	for _, id := range []string{"a1", "a2", "a3"} {
		if _, path, err := store.Get(id); err != nil || path == "" {
			t.Fatalf("artifact %s (one of this invocation's own N just-written artifacts) must survive opportunistic gc: path=%q err=%v", id, path, err)
		}
	}
}
