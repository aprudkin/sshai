//go:build integration

// internal/cli/run_integration_test.go
package cli

import (
	"bytes"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// repoRoot returns the module root: internal/cli is two directories below
// it.
func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatalf("getwd: %v", err)
	}
	return filepath.Join(wd, "..", "..")
}

// buildSshai builds the sshai binary fresh into a temp directory, so this
// test never depends on a stale pre-built binary lying around — self
// contained, per Step 5's "build the binary first".
func buildSshai(t *testing.T) string {
	t.Helper()
	bin := filepath.Join(t.TempDir(), "sshai")
	cmd := exec.Command("go", "build", "-o", bin, "./cmd/sshai")
	cmd.Dir = repoRoot(t)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("go build ./cmd/sshai: %v\n%s", err, out)
	}
	return bin
}

// TestRunIntegration drives the real sshai binary as a subprocess against
// SSHAI_TEST_LINUX_HOST — the only way to observe the process's actual
// exit code (as opposed to cli.Run's in-process return value) and prove
// the main.go wiring, not just runHost in isolation. Skip pattern
// identical to Task 7 (internal/transport/openssh_integration_test.go).
func TestRunIntegration(t *testing.T) {
	host := os.Getenv("SSHAI_TEST_LINUX_HOST")
	if host == "" {
		t.Skip("SSHAI_TEST_LINUX_HOST not set")
	}

	bin := buildSshai(t)

	// A short root, not t.TempDir(): production run.go derives the
	// transport's ControlPath from SSHAI_ROOT (<root>/cm/%C), and
	// t.TempDir()'s long, test-name-derived path can push %C's 40-char
	// hash plus OpenSSH's own internal mkstemp-style suffix past macOS's
	// 104-byte AF_UNIX sun_path limit — breaking silently (see
	// internal/transport/openssh_integration_test.go's comment on the
	// same constraint). Shared across the subtests below (not
	// re-created per subtest) so (a)'s state re-injection can observe
	// the first run's effect on the second.
	root, err := os.MkdirTemp("/tmp", "sshai-cm-")
	if err != nil {
		t.Fatalf("MkdirTemp: %v", err)
	}
	t.Cleanup(func() { os.RemoveAll(root) })

	run := func(args ...string) (stdout, stderr string, exitCode int) {
		cmd := exec.Command(bin, args...)
		cmd.Env = append(os.Environ(), "SSHAI_ROOT="+root)
		var outBuf, errBuf bytes.Buffer
		cmd.Stdout = &outBuf
		cmd.Stderr = &errBuf
		runErr := cmd.Run()
		code := 0
		if runErr != nil {
			ee, ok := runErr.(*exec.ExitError)
			if !ok {
				t.Fatalf("run %v: %v (stderr=%s)", args, runErr, errBuf.String())
			}
			code = ee.ExitCode()
		}
		return outBuf.String(), errBuf.String(), code
	}

	t.Run("state re-injection", func(t *testing.T) {
		bodyFile := filepath.Join(t.TempDir(), "body.sh")
		if err := os.WriteFile(bodyFile, []byte("cd /tmp"), 0o644); err != nil {
			t.Fatalf("write body file: %v", err)
		}

		out1, err1, rc1 := run("run", "--ctx", "it", "--body-file", bodyFile, host)
		if rc1 != 0 {
			t.Fatalf("first run rc=%d stdout=%s stderr=%s", rc1, out1, err1)
		}

		out2, err2, rc2 := run("run", "--ctx", "it", host, "--", "pwd")
		if rc2 != 0 {
			t.Fatalf("second run rc=%d stdout=%s stderr=%s", rc2, out2, err2)
		}
		if !strings.Contains(out2, "/tmp") {
			t.Fatalf("second run's passport does not show /tmp (state re-injection failed): %s", out2)
		}
	})

	t.Run("honest exit code", func(t *testing.T) {
		out, errOut, rc := run("run", "--ctx", "it", host, "--", "exit", "3")
		if rc != 3 {
			t.Fatalf("rc=%d, want 3; stdout=%s stderr=%s", rc, out, errOut)
		}
		if !strings.Contains(out, "exit=3") {
			t.Fatalf("passport missing exit=3: %s", out)
		}
	})

	t.Run("timeout", func(t *testing.T) {
		out, errOut, rc := run("run", "--ctx", "it", "--timeout", "2", host, "--", "sleep", "30")
		if rc != 98 {
			t.Fatalf("rc=%d, want 98; stdout=%s stderr=%s", rc, out, errOut)
		}
		if !strings.Contains(out, "transport-error=timeout") {
			t.Fatalf("passport missing transport-error=timeout: %s", out)
		}
	})
}
