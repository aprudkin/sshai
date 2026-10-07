package transport

import (
	"errors"
	"testing"
	"time"
)

// The legacy runner seam must not turn capture failure into a command exit or
// publish the partial raw SSH/SCP diagnostics, even when they match an allowlist.
func TestCaptureFailureDiagnostics(t *testing.T) {
	for _, copyFile := range []bool{false, true} {
		tr := newTestOpenSSH(t, "15m", 64, OpenSSHOptions{})
		tr.Runner = fake(execCaptureFailedRC, "permission denied: private fixture data", false)
		var err error
		wantClass, wantDiagnostic := "ssh", "ssh output capture incomplete"
		if copyFile {
			err = tr.Put("synthetic01", "source", "target", time.Second)
			wantClass, wantDiagnostic = "scp", "scp output capture incomplete"
		} else {
			res, execErr := tr.Exec("synthetic01", "true", nil, time.Second)
			err = execErr
			if len(res.Output) != 0 {
				t.Fatalf("raw partial output escaped: %q", res.Output)
			}
		}
		var te *TransportError
		if !errors.As(err, &te) || te.Reason != wantClass || te.Diagnostic() != wantDiagnostic {
			t.Fatalf("capture failure not canonical: %v", err)
		}
	}
}
