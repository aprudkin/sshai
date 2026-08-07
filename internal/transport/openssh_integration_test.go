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
	tr := NewOpenSSH(t.TempDir(), "5m", 1<<20)
	res, err := tr.Exec(host, "echo sshai-$((6*7))", nil, 30*time.Second)
	if err != nil || res.ExitCode != 0 || !strings.Contains(string(res.Output), "sshai-42") {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	res, err = tr.Exec(host, "exit 7", nil, 30*time.Second)
	if err != nil || res.ExitCode != 7 {
		t.Fatalf("want honest exit 7: res=%+v err=%v", res, err)
	}
}
