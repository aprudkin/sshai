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
	rcs          map[string]int
	transportErr map[string]string
	body         map[string]string
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
	return transport.Result{ExitCode: f.rcs[host], Output: out}, nil
}

func (f *multiHostTr) Put(host, l, r string) error { return nil }

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
// test for a classification bug found in review: runFanout must inspect
// only a host's passport *first line* (its own status line), never the
// whole buffer — otherwise a host whose command output happens to
// contain "transport-error=" or " policy-denied" (e.g. grepping sshai's
// own audit.jsonl, or catting another host's earlier passport) would be
// misclassified from its own successful remote output.
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
// delta base. runHost is called directly (not through runFanout's
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
	if rc := runHost(deps, opts1, &out, &out); rc != 0 {
		t.Fatalf("h1 first run rc=%d: %s", rc, out.String())
	}
	out.Reset()
	if rc := runHost(deps, opts2, &out, &out); rc != 0 {
		t.Fatalf("h2 first run rc=%d: %s", rc, out.String())
	}
	out.Reset()

	// h1's second run must key off its OWN previous run (a1), never h2's
	// (a2) — a cross-host collision would show "delta=a2" here instead.
	if rc := runHost(deps, opts1, &out, &out); rc != 0 {
		t.Fatalf("h1 second run rc=%d: %s", rc, out.String())
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
	if rc := runHost(deps, opts2, &out, &out); rc != 0 {
		t.Fatalf("h2 second run rc=%d: %s", rc, out.String())
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
