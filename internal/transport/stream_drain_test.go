package transport

import (
	"fmt"
	"strings"
	"testing"
	"time"
)

// A direct SSH child's exit does not prove EOF. Keep draining within the host
// budget, regardless of exit code, and report timeout if that budget expires.
func TestExecInheritedOutputRespectsRemainingBudget(t *testing.T) {
	for _, follow := range []bool{false, true} {
		for _, exitCode := range []int{0, 3} {
			for _, timeout := range []time.Duration{time.Second, 100 * time.Millisecond} {
				t.Run(fmt.Sprintf("follow%v/exit%d/%s", follow, exitCode, timeout), func(t *testing.T) {
					writeBudgetSSH(t, fmt.Sprintf("printf 'before\\n'\n(sleep 0.4; printf 'after\\n') &\nexit %d\n", exitCode))
					tr := NewOpenSSH(t.TempDir(), "15m", 1024, OpenSSHOptions{})
					var preview strings.Builder
					started := time.Now()
					var res Result
					var err error
					if follow {
						res, err = tr.ExecStream("synthetic01", "true", nil, timeout, func(p []byte) { preview.Write(p) })
					} else {
						res, err = tr.Exec("synthetic01", "true", nil, timeout)
					}
					if timeout == time.Second {
						if err != nil || res.ExitCode != exitCode || res.Truncated || string(res.Output) != "before\nafter\n" || (follow && preview.String() != string(res.Output)) {
							t.Fatalf("inherited output lost or command exit changed: res=%+v err=%v preview=%q", res, err, preview.String())
						}
					} else {
						requireTransportTimeout(t, err)
						if len(res.Output) != 0 {
							t.Fatalf("partial result presented as authoritative on timeout: %+v", res)
						}
					}
					if elapsed := time.Since(started); elapsed > timeout+400*time.Millisecond {
						t.Fatalf("draining exceeded budget plus cleanup/scheduling allowance: %v", elapsed)
					}
				})
			}
		}
	}
}
