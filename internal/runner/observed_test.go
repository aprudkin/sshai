package runner

import (
	"os"
	"testing"
	"time"
)

func TestRunObservedCapture(t *testing.T) {
	for _, tc := range []struct {
		name      string
		mode      string
		cap       int64
		wantOut   string
		wantRC    int
		wantFull  string
		truncated bool
	}{
		{name: "normal", mode: "output", cap: 8, wantOut: "hello", wantRC: 0, wantFull: "hello"},
		{name: "nonzero combined output", mode: "ordered", cap: 8, wantOut: "outerr", wantRC: 7, wantFull: "outerr"},
		{name: "overflow", mode: "overflow", cap: 5, wantOut: "hello", wantFull: "hello", truncated: true},
		{name: "zero cap", mode: "output", cap: 0, wantFull: "", truncated: true},
		{name: "negative cap", mode: "output", cap: -1, wantFull: "", truncated: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("RUNNER_HELPER", tc.mode)
			var observed []byte
			result := RunObservedUntil(helperArgv(), nil, time.Now().Add(5*time.Second), tc.cap, func(p []byte) {
				observed = append(observed, p...)
				for i := range p {
					p[i] = '!'
				}
			})
			if result.StartErr != nil || result.TimedOut || (result.CaptureErr != nil && !tc.truncated) {
				t.Fatalf("unexpected capture failure: %+v", result)
			}
			if string(result.Output) != tc.wantFull || result.Truncated != tc.truncated {
				t.Fatalf("result=%+v", result)
			}
			if string(observed) != tc.wantFull {
				t.Fatalf("observed=%q, want accepted bytes %q", observed, tc.wantFull)
			}
			if !tc.truncated && result.ExitCode != tc.wantRC {
				t.Fatalf("ExitCode=%d, want %d", result.ExitCode, tc.wantRC)
			}
		})
	}
}

func TestRunObservedStartFailure(t *testing.T) {
	result := RunObservedUntil([]string{t.TempDir() + "/missing-runner-binary"}, nil, time.Now().Add(time.Second), 10, nil)
	if result.StartErr == nil || result.TimedOut || result.Truncated || len(result.Output) != 0 {
		t.Fatalf("result=%+v, want only StartErr", result)
	}
}

func TestRunObservedInheritedPipe(t *testing.T) {
	for _, tc := range []struct {
		name    string
		timeout time.Duration
		hold    bool
		want    string
		timed   bool
	}{
		{name: "delayed output after direct child exit", timeout: 2 * time.Second, want: "before\nafter\n"},
		{name: "deadline while inherited pipe remains open", timeout: 250 * time.Millisecond, hold: true, want: "before\n", timed: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("RUNNER_DRAIN_HELPER", "parent")
			t.Setenv("RUNNER_DRAIN_EXIT", "3")
			if tc.hold {
				t.Setenv("RUNNER_DRAIN_HOLD", "1")
			} else {
				t.Setenv("RUNNER_DRAIN_HOLD", "")
			}
			var observed []byte
			started := time.Now()
			result := RunObservedUntil([]string{os.Args[0], "-test.run=^TestRunnerDrainHelper$"}, nil, started.Add(tc.timeout), 64, func(p []byte) {
				observed = append(observed, p...)
			})
			if result.StartErr != nil || result.TimedOut != tc.timed || result.ExitCode != 3 || string(result.Output) != tc.want || string(observed) != tc.want {
				t.Fatalf("result=%+v observed=%q", result, observed)
			}
			if elapsed := time.Since(started); elapsed > tc.timeout+400*time.Millisecond {
				t.Fatalf("drain exceeded deadline cleanup bound: %v", elapsed)
			}
		})
	}
}

func helperArgv() []string {
	return []string{os.Args[0], "-test.run=TestRunnerHelperProcess", "--"}
}
