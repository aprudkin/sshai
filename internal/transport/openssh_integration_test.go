//go:build integration

// internal/transport/openssh_integration_test.go
package transport

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestExecAgainstRealLinuxHost(t *testing.T) {
	host := os.Getenv("SSHAI_TEST_LINUX_HOST")
	if host == "" {
		t.Skip("SSHAI_TEST_LINUX_HOST not set")
	}
	// A short control dir, not t.TempDir(): OpenSSH's ControlPath (%C's
	// 40-char host hash plus its own internal mkstemp-style suffix) can
	// exceed macOS's 104-byte AF_UNIX sun_path limit when built under
	// t.TempDir()'s long, test-name-derived path — a platform ceiling the
	// code under test has no way to signal.
	controlDir, err := os.MkdirTemp("/tmp", "sshai-cm-")
	if err != nil {
		t.Fatalf("MkdirTemp: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(controlDir) })

	tr := NewOpenSSH(controlDir, "5m", 1<<20, OpenSSHOptions{})
	res, err := tr.Exec(host, "echo sshai-$((6*7))", nil, 30*time.Second)
	if err != nil || res.ExitCode != 0 || !strings.Contains(string(res.Output), "sshai-42") {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	res, err = tr.Exec(host, "exit 7", nil, 30*time.Second)
	if err != nil || res.ExitCode != 7 {
		t.Fatalf("want honest exit 7: res=%+v err=%v", res, err)
	}
}
