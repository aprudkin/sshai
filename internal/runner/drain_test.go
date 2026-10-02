package runner

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"testing"
	"time"
)

// Exiting the direct child must not silently drop inherited-pipe output or
// disguise a drain deadline as success, even when the child's exit is nonzero.
func TestRunInheritedOutput(t *testing.T) {
	for _, exitCode := range []int{0, 3} {
		for _, tc := range []struct {
			name    string
			timeout time.Duration
			cap     int64
			output  string
			timed   bool
			trunc   bool
		}{
			{"complete", 2 * time.Second, 64, "before\nafter\n", false, false},
			{"deadline", 250 * time.Millisecond, 64, "before\n", true, false},
			{"cap", 2 * time.Second, 8, "before\na", false, true},
		} {
			t.Run(fmt.Sprintf("exit%d/%s", exitCode, tc.name), func(t *testing.T) {
				t.Setenv("RUNNER_DRAIN_HELPER", "parent")
				t.Setenv("RUNNER_DRAIN_EXIT", strconv.Itoa(exitCode))
				if tc.trunc {
					t.Setenv("RUNNER_DRAIN_HOLD", "1")
				}
				started := time.Now()
				res := Run([]string{os.Args[0], "-test.run=^TestRunnerDrainHelper$"}, nil, tc.timeout, tc.cap)
				if res.StartErr != nil || res.ExitCode != exitCode || string(res.Output) != tc.output || res.TimedOut != tc.timed || res.Truncated != tc.trunc {
					t.Fatalf("result=%+v output=%q, want exit=%d output=%q timed=%v trunc=%v", res, res.Output, exitCode, tc.output, tc.timed, tc.trunc)
				}
				if !tc.timed && !tc.trunc && res.CaptureErr != nil {
					t.Fatalf("complete output reported as a capture failure: %v", res.CaptureErr)
				}
				limit := tc.timeout + 400*time.Millisecond
				if tc.trunc {
					limit = time.Second // descendant still holds the pipe until 1.5s
				}
				if elapsed := time.Since(started); elapsed > limit {
					t.Fatalf("drain exceeded cancellation allowance: %v", elapsed)
				}
			})
		}
	}
}

func TestRunnerDrainHelper(t *testing.T) {
	switch os.Getenv("RUNNER_DRAIN_HELPER") {
	case "parent":
		fmt.Fprint(os.Stdout, "before\n")
		child := exec.Command(os.Args[0], "-test.run=^TestRunnerDrainHelper$") // #nosec G204 -- fixed test helper.
		child.Env = append(os.Environ(), "RUNNER_DRAIN_HELPER=child")
		child.Stdout, child.Stderr = os.Stdout, os.Stderr
		if err := child.Start(); err != nil {
			os.Exit(2)
		}
		rc, _ := strconv.Atoi(os.Getenv("RUNNER_DRAIN_EXIT"))
		os.Exit(rc)
	case "child":
		time.Sleep(500 * time.Millisecond)
		fmt.Fprint(os.Stderr, "after\n")
		// Keep the inherited pipe open after overflow, proving cancellation
		// does not wait for EOF. The disposable helper exits on its own.
		if os.Getenv("RUNNER_DRAIN_HOLD") == "1" {
			time.Sleep(time.Second)
		}
		os.Exit(0)
	}
}
