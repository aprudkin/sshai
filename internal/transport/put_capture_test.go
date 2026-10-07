package transport

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

// Put has no truncated-result surface: a late overflow after a successful SCP
// child exit must fail staging, not silently discard its incomplete capture.
func TestPutInheritedOutputCap(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake SCP fixture")
	}
	for _, cap := range []int64{8, 13} {
		t.Run(fmt.Sprintf("cap%d", cap), func(t *testing.T) {
			dir := t.TempDir()
			body := "#!/bin/sh\nprintf 'before\\n'\n(sleep 0.4; printf 'after\\n') &\nexit 0\n"
			if err := os.WriteFile(filepath.Join(dir, "scp"), []byte(body), 0o700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
			tr := newTestOpenSSH(t, "15m", cap, OpenSSHOptions{})
			err := tr.Put("synthetic01", "source", "target", 2*time.Second)
			if cap == 13 {
				if err != nil {
					t.Fatalf("exact-cap complete SCP output failed: %v", err)
				}
			} else {
				var te *TransportError
				if !errors.As(err, &te) || te.Reason != "scp" || te.Diagnostic() != "scp output capture incomplete" {
					t.Fatalf("incomplete SCP capture returned unqualified success or raw diagnostic: %v", err)
				}
			}
		})
	}
}
