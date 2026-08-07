// internal/transport/openssh_test.go
package transport

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func fake(rc int, out string, timedOut bool) func([]string, []byte, time.Duration) (int, []byte, bool) {
	return func([]string, []byte, time.Duration) (int, []byte, bool) { return rc, []byte(out), timedOut }
}

func TestExecMirrorsRemoteExit(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	tr.Runner = fake(3, "boom\n", false)
	res, err := tr.Exec("h1", "false", nil, time.Minute)
	if err != nil || res.ExitCode != 3 || string(res.Output) != "boom\n" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestExec255IsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	tr.Runner = fake(255, "Connection refused", false)
	_, err := tr.Exec("h1", "true", nil, time.Minute)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}

func TestExecTimeout(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	tr.Runner = fake(0, "", true)
	_, err := tr.Exec("h1", "sleep 999", nil, time.Millisecond)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "timeout" {
		t.Fatalf("want TransportError{timeout}, got %v", err)
	}
}

func TestArgvDiscipline(t *testing.T) {
	tr := NewOpenSSH("/tmp/cm", "15m", 1<<20)
	var captured []string
	tr.Runner = func(argv []string, _ []byte, _ time.Duration) (int, []byte, bool) {
		captured = argv
		return 0, nil, false
	}
	tr.Exec("h1", "df -h", nil, time.Minute)
	// Every option is its own element; no element may contain two options glued together.
	for _, a := range captured {
		if a != "-o" && len(a) > 2 && a[0] == '-' && a[1] == 'o' {
			t.Fatalf("glued option: %q in %q", a, captured)
		}
	}
	if captured[0] != "ssh" || captured[len(captured)-2] != "h1" || captured[len(captured)-1] != "df -h" {
		t.Fatalf("argv shape: %q", captured)
	}
}

// TestPutNonZeroIsTransportError covers Put's discrimination rule, which
// (unlike Exec's) does not special-case 255: any non-zero rc from scp is
// reported as TransportError{"scp"}.
func TestPutNonZeroIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	tr.Runner = fake(1, "scp: No such file or directory", false)
	err := tr.Put("h1", "/local/script.sh", "/remote/script.sh")
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "scp" {
		t.Fatalf("want TransportError{scp}, got %v", err)
	}
}

// --- capWriter: regression net for the exact-cap off-by-one and the
// overflow/kill/truncate contract (task-7 review finding 1 and 3). ---

func TestCapWriterUnderCapNotKilled(t *testing.T) {
	canceled := false
	w := newCapWriter(10, func() { canceled = true })
	n, err := w.Write([]byte("hello")) // 5 bytes, cap 10
	if err != nil || n != 5 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if canceled {
		t.Fatal("must not kill a write under the cap")
	}
	if got := string(w.Bytes()); got != "hello" {
		t.Fatalf("Bytes()=%q", got)
	}
}

// TestCapWriterExactCapRetainedNotTruncated is the regression net for the
// off-by-one the reviewer found: a write that fits exactly at the cap
// must be retained in full, with no kill and no truncation.
func TestCapWriterExactCapRetainedNotTruncated(t *testing.T) {
	canceled := false
	w := newCapWriter(10, func() { canceled = true })
	n, err := w.Write([]byte("1234567890")) // exactly 10 bytes, cap 10
	if err != nil || n != 10 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if canceled {
		t.Fatal("an exact-fit write must not kill the process")
	}
	if got := string(w.Bytes()); got != "1234567890" {
		t.Fatalf("Bytes()=%q, want all 10 bytes retained", got)
	}
}

// TestCapWriterOneByteOverCapKills checks the other edge of the same
// boundary: a write of exactly max+1 bytes is genuine overflow and must
// kill, even though it stops just one byte past the cap.
func TestCapWriterOneByteOverCapKills(t *testing.T) {
	canceled := false
	w := newCapWriter(10, func() { canceled = true })
	n, err := w.Write([]byte("12345678901")) // 11 bytes, cap 10
	if err == nil {
		t.Fatal("want overflow error")
	}
	if !canceled {
		t.Fatal("must kill the process the moment output exceeds the cap")
	}
	if n != 11 {
		t.Fatalf("n=%d, want 11 (all bytes retained as the overflow sentinel)", n)
	}
	if got := len(w.Bytes()); got != 11 {
		t.Fatalf("Bytes() len=%d, want 11 (max+1, the overflow sentinel)", got)
	}
}

// TestCapWriterOverCapTruncatesAndKills covers a write well past the cap,
// spread realistically the way os/exec would deliver it in one chunk.
func TestCapWriterOverCapTruncatesAndKills(t *testing.T) {
	canceled := false
	w := newCapWriter(10, func() { canceled = true })
	n, err := w.Write([]byte(strings.Repeat("x", 14))) // 14 bytes, cap 10
	if err == nil {
		t.Fatal("want overflow error")
	}
	if !canceled {
		t.Fatal("must kill the process on overflow")
	}
	if n != 11 {
		t.Fatalf("n=%d, want 11 (bytes retained, capped at max+1)", n)
	}
	if got := len(w.Bytes()); got != 11 {
		t.Fatalf("Bytes() len=%d, want 11", got)
	}
}

// TestCapWriterOverCapAcrossWrites confirms the kill fires on cumulative
// overflow spread across multiple Write calls, not just a single big one.
func TestCapWriterOverCapAcrossWrites(t *testing.T) {
	canceled := false
	w := newCapWriter(10, func() { canceled = true })
	if n, err := w.Write([]byte("123456")); err != nil || n != 6 {
		t.Fatalf("first write: n=%d err=%v", n, err)
	}
	if canceled {
		t.Fatal("must not kill before the cumulative total exceeds the cap")
	}
	n, err := w.Write([]byte("789012")) // 6+6=12 total, cap 10
	if err == nil {
		t.Fatal("want overflow error")
	}
	if !canceled {
		t.Fatal("must kill once the cumulative total exceeds the cap")
	}
	if n != 5 {
		t.Fatalf("n=%d, want 5 (bytes retained from this write, 11-6)", n)
	}
	if got := len(w.Bytes()); got != 11 {
		t.Fatalf("Bytes() len=%d, want 11", got)
	}
}

// --- Exec's truncation derivation from capWriter's sentinel-inclusive
// output (task-7 review finding 1, consumer side). ---

func TestExecExactCapOutputNotTruncated(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 5) // streamCap=5
	tr.Runner = fake(0, "hello", false)     // exactly 5 bytes
	res, err := tr.Exec("h1", "echo -n hello", nil, time.Minute)
	if err != nil || res.Truncated || string(res.Output) != "hello" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestExecOverCapOutputTruncated(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 5) // streamCap=5
	tr.Runner = fake(0, "hello world", false)
	res, err := tr.Exec("h1", "echo -n hello world", nil, time.Minute)
	if err != nil || !res.Truncated || string(res.Output) != "hello" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

// --- Local exec-start failure (task-7 review finding 2): a missing
// binary or a denied exec permission must surface as a TransportError,
// never as a fabricated honest exit. ---

// TestRunSurfacesStartFailure drives the default Runner (run) directly
// with a nonexistent binary path — no network involved, just a local
// exec(2) failure — and checks it reports execStartFailedRC rather than
// some ordinary-looking exit code.
func TestRunSurfacesStartFailure(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	rc, out, timedOut := tr.run([]string{"/nonexistent/path/sshai-missing-binary", "x"}, nil, 5*time.Second)
	if rc != execStartFailedRC {
		t.Fatalf("rc=%d, want execStartFailedRC (%d)", rc, execStartFailedRC)
	}
	if timedOut {
		t.Fatal("a start failure must not be reported as a timeout")
	}
	if len(out) != 0 {
		t.Fatalf("out=%q, want empty — the child never ran", out)
	}
}

// TestExecStartFailureIsTransportError checks Exec's discrimination in
// isolation: given execStartFailedRC from the Runner (fake or real), it
// must produce TransportError{"ssh"}, the same as a real ssh exit 255.
func TestExecStartFailureIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	tr.Runner = fake(execStartFailedRC, "", false)
	_, err := tr.Exec("h1", "true", nil, time.Minute)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}

// TestExecSurfacesLocalStartFailureAsTransportError closes the seam
// between the two tests above: it leaves the default Runner in place
// (never overrides tr.Runner) and forces a real local start failure by
// making "ssh" unresolvable on PATH, proving execStartFailedRC actually
// reaches Exec's discrimination end to end through the production code
// path — not just through the fake. Network-free: PATH is emptied before
// ssh would ever be invoked, so nothing is dialed.
func TestExecSurfacesLocalStartFailureAsTransportError(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // a directory with no "ssh" binary in it
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20)
	_, err := tr.Exec("h1", "true", nil, 5*time.Second)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}
