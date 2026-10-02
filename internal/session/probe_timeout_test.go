package session

import (
	"errors"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/shell"
	"github.com/aprudkin/sshai/internal/transport"
)

// The transport is the external seam: Probe must supply decreasing budgets
// and refuse later candidates, rather than relying on each Exec's timer alone.
type budgetProbeTransport struct {
	budgets []time.Duration
	exhaust bool
}

func (f *budgetProbeTransport) Exec(_, _ string, _ []byte, timeout time.Duration) (transport.Result, error) {
	f.budgets = append(f.budgets, timeout)
	if f.exhaust {
		// Model an unsuccessful candidate returning during local cleanup.
		time.Sleep(max(timeout, 0) + time.Millisecond)
	} else {
		time.Sleep(10 * time.Millisecond)
	}
	return transport.Result{ExitCode: 1}, nil
}

func (*budgetProbeTransport) Put(_, _, _ string, _ time.Duration) error { panic("unexpected staging") }

func TestProbeSharesBudgetAcrossCandidates(t *testing.T) {
	tr := &budgetProbeTransport{}
	_, err := Probe(tr, "windows01", shell.PwshDefaultShell, true, time.Second)
	var setup *RemoteSetupError
	if !errors.As(err, &setup) || len(tr.budgets) != 5 {
		t.Fatalf("expected exhausted shell candidates, calls=%d err=%v", len(tr.budgets), err)
	}
	for i := 1; i < len(tr.budgets); i++ {
		if tr.budgets[i] > tr.budgets[i-1]-10*time.Millisecond {
			t.Fatalf("candidate %d received a renewed budget: %v", i, tr.budgets)
		}
	}
}

func TestProbeDoesNotStartCandidateAfterDeadline(t *testing.T) {
	for _, timeout := range []time.Duration{0, -time.Second, 10 * time.Millisecond} {
		t.Run(timeout.String(), func(t *testing.T) {
			tr := &budgetProbeTransport{exhaust: true}
			_, err := Probe(tr, "windows01", shell.PwshDefaultShell, true, timeout)
			var te *transport.TransportError
			if !errors.As(err, &te) || te.Reason != "timeout" || te.Diagnostic() != "operation timed out" {
				t.Fatalf("want canonical timeout, got %v", err)
			}
			wantCalls := 0
			if timeout > 0 {
				wantCalls = 1
			}
			if len(tr.budgets) != wantCalls {
				t.Fatalf("started %d candidates, want %d", len(tr.budgets), wantCalls)
			}
		})
	}
}
