//go:build darwin || linux

package transport

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"syscall"
	"testing"
	"time"
)

// Keep descriptor exhaustion inside a disposable process. One remaining file
// descriptor permits the private SSH diagnostic file, but not a capture pipe.
func TestExecStreamCaptureSetupFailure(t *testing.T) {
	if os.Getenv("SSHAI_TEST_CAPTURE_SETUP_FAILURE") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestExecStreamCaptureSetupFailure$", "-test.v")
		cmd.Env = append(os.Environ(), "SSHAI_TEST_CAPTURE_SETUP_FAILURE=1")
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("isolated capture-setup regression: %v\n%s", err, output)
		}
		return
	}

	// A successful capture must never reach a real SSH executable either.
	writeBudgetSSH(t, "exit 0\n")
	tr := newTestOpenSSH(t, "15m", 64, OpenSSHOptions{AcceptNewHostKey: "synthetic01"})
	if err := tr.prepareControlDir(); err != nil {
		t.Fatal(err)
	}
	lookups := 0
	tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
		lookups++
		return map[string]HostKey{}, nil
	}

	var original syscall.Rlimit
	if err := syscall.Getrlimit(syscall.RLIMIT_NOFILE, &original); err != nil {
		t.Fatal(err)
	}
	limited := original
	limited.Cur = min(limited.Cur, 256)
	if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &limited); err != nil {
		t.Fatal(err)
	}
	defer func() {
		if err := syscall.Setrlimit(syscall.RLIMIT_NOFILE, &original); err != nil {
			t.Errorf("restore descriptor limit: %v", err)
		}
	}()
	var held []*os.File
	defer func() {
		for _, f := range held {
			_ = f.Close()
		}
	}()
	for {
		f, err := os.Open(os.DevNull)
		if errors.Is(err, syscall.EMFILE) {
			break
		}
		if err != nil {
			t.Fatal(err)
		}
		held = append(held, f)
	}
	if len(held) == 0 {
		t.Fatal("no fixture-owned descriptor available")
	}
	if err := held[len(held)-1].Close(); err != nil {
		t.Fatal(err)
	}
	held = held[:len(held)-1]

	observed := false
	_, err := tr.ExecStream("synthetic01", "true", nil, time.Second, func([]byte) { observed = true })
	var transportErr *TransportError
	if !errors.As(err, &transportErr) || transportErr.Reason != "ssh" || transportErr.Diagnostic() != "ssh output capture unavailable" {
		t.Errorf("capture setup failure changed its canonical diagnostic: %v", err)
	}
	if observed || lookups != 1 {
		t.Errorf("capture setup failure must stop before observation: output=%v host-key-lookups=%d", observed, lookups)
	}
}
