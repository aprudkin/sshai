// internal/transport/openssh_test.go
package transport

import (
	"errors"
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
