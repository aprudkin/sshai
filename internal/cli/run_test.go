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

	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

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
