// internal/cli/run_test.go
package cli

import (
	"bytes"
	"encoding/base64"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/session"
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
