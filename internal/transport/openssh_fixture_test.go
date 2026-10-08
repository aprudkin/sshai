package transport

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// Offline process fixtures own both their original temporary root and any
// fallback selected outside it. This helper never removes production sockets.
func newTestOpenSSH(t *testing.T, persist string, cap int64, options OpenSSHOptions) *OpenSSH {
	t.Helper()
	// Go's numbered t.TempDir child can be 0755. Create a separate private
	// socket directory instead of passing that existing child to the guard.
	dir := filepath.Join(t.TempDir(), "cm")
	tr := NewOpenSSH(dir, persist, cap, options)
	if tr.controlDir != dir && tr.controlDir != "" {
		t.Cleanup(func() {
			if err := os.Remove(tr.controlDir); err != nil && !os.IsNotExist(err) {
				t.Errorf("remove test control directory: %v", err)
			}
		})
	}
	return tr
}

// Keep the name short so Go's temporary directory fits the socket budget.
// Long macOS temporary roots otherwise conceal a non-private direct fixture.
func TestShortRootFixture(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	t.Setenv("TMPDIR", "/tmp")
	tr := newTestOpenSSH(t, "15m", 64, OpenSSHOptions{})
	if !strings.HasPrefix(tr.controlDir, "/tmp/TestShortRootFixture") {
		t.Fatalf("fixture did not exercise a retained short path: %q", tr.controlDir)
	}
	rc, out, timedOut := tr.run([]string{filepath.Join(t.TempDir(), "missing")}, nil, time.Second)
	if rc != execStartFailedRC || len(out) != 0 || timedOut {
		t.Fatalf("missing binary was masked by fixture setup: rc=%d out=%q timedOut=%v", rc, out, timedOut)
	}
	info, err := os.Lstat(tr.controlDir)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 || !controlDirOwned(info) {
		t.Fatalf("fixture control directory is not private: info=%v err=%v", info, err)
	}
}
