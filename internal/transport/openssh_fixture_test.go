package transport

import (
	"os"
	"testing"
)

// Offline process fixtures own both their original temporary root and any
// fallback selected outside it. This helper never removes production sockets.
func newTestOpenSSH(t *testing.T, persist string, cap int64, options OpenSSHOptions) *OpenSSH {
	t.Helper()
	dir := t.TempDir()
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
