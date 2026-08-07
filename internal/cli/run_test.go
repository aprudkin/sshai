// internal/cli/run_test.go
package cli

import (
	"bytes"
	"encoding/base64"
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
type probeFailsTr struct{ calls []string }

func (f *probeFailsTr) Exec(host, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	f.calls = append(f.calls, cmd)
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

	f := &probeFailsTr{}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "hello"}, &out, &errB)
	if rc != exitTransport {
		t.Fatalf("rc=%d, want %d; stdout=%s stderr=%s", rc, exitTransport, out.String(), errB.String())
	}
	if !strings.Contains(out.String(), "transport-error=ssh") {
		t.Fatalf("passport missing transport-error=ssh: %q", out.String())
	}
	if len(f.calls) != 1 || f.calls[0] != "uname -s" {
		t.Fatalf("expected exactly one \"uname -s\" probe call, got calls=%v", f.calls)
	}
	if _, ok, _ := session.LoadFacts(root, "web01"); ok {
		t.Fatal("facts must not be cached when the probe itself fails")
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

// fakeTr is a network-free transport.Transport: Exec synthesizes a
// BashWrap-shaped reply directly from the stdin it receives, rather than
// relying on a second-phase hook — the sentinel is random per run, but it
// travels to Exec inside stdin, so Exec can parse it out and echo it back
// in the reply it fabricates, entirely within one call. This keeps the
// test free of any coordination between "capture the sentinel" and "build
// the response" phases.
type fakeTr struct {
	lastCmd   string
	lastStdin []byte
	rc        int
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
	f.lastCmd, f.lastStdin = cmd, stdin
	sent := sentinelFromStdin(stdin)
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	out := []byte("hello\n\n" + sent + "\n/tmp\n" + env + "\n")
	return transport.Result{ExitCode: f.rc, Output: out}, nil
}

func (f *fakeTr) Put(host, l, r string) error { return nil }

func TestRunLinuxHappyPath(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	// Pre-seed facts so runHost never calls Probe — leaves f.lastCmd/lastStdin
	// holding the actual run's exec, not a preceding "uname -s" probe call.
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"--ctx", "t1", "web01", "--", "echo", "hello"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	p := out.String()
	if !strings.Contains(p, "a1 host=web01 exit=0") || !strings.Contains(p, "hello") {
		t.Fatalf("passport: %q", p)
	}
	if !strings.Contains(f.lastCmd, "bash -s") {
		t.Fatalf("command must be bash -s, got %q", f.lastCmd)
	}
	if !strings.Contains(string(f.lastStdin), "echo hello") {
		t.Fatal("body must travel on stdin, not argv")
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

// TestRunRejectsMultipleHosts covers the brief's "reject >1 host" rule —
// fan-out is Task 12, not this one.
func TestRunRejectsMultipleHosts(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	f := &fakeTr{rc: 0}
	var out, errB bytes.Buffer
	rc := runWith(f, []string{"web01", "web02", "--", "echo", "hi"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitUsage, errB.String())
	}
	if errB.Len() == 0 {
		t.Fatal("expected a usage error on stderr")
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

// TestRunBodyFileMetaCommandIncludesHashAndPreview covers Finding 1's
// fix: the design doc's run-log row description is "command (or body
// hash + first 80 chars)", so a --body-file run's stored Meta.Command
// must combine deltaKeyCommand's hash form with a redacted preview of
// the body — the hash alone would make the row opaque, and the raw body
// alone would defeat the point of hashing it for the DB in the first
// place.
func TestRunBodyFileMetaCommandIncludesHashAndPreview(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
		t.Fatalf("SaveFacts: %v", err)
	}
	bodyFile := filepath.Join(t.TempDir(), "body.sh")
	if err := os.WriteFile(bodyFile, []byte("echo hello"), 0o644); err != nil {
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

	wantHash := "body:" + sha256Hex("echo hello")[:16]
	if !strings.HasPrefix(m.Command, wantHash+" ") {
		t.Fatalf("Meta.Command = %q, want it to start with %q", m.Command, wantHash+" ")
	}
	if !strings.Contains(m.Command, "echo hello") {
		t.Fatalf("Meta.Command = %q, want it to contain the redacted preview %q", m.Command, "echo hello")
	}
}
