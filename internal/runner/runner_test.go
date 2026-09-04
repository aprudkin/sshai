package runner

import (
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
	"testing"
	"time"
)

func TestRun(t *testing.T) {
	cases := []struct {
		name    string
		mode    string
		argv    []string
		stdin   []byte
		cap     int64
		timeout time.Duration
		wantOut string
		wantRC  int
		trunc   bool
		timed   bool
	}{
		{"exit and combined order", "ordered", nil, nil, 64, 5 * time.Second, "outerr", 7, false, false},
		{"stdin", "stdin", nil, []byte("input bytes"), 64, 5 * time.Second, "input bytes", 0, false, false},
		{"discrete argv", "argv", []string{"one two", "three"}, nil, 64, 5 * time.Second, "one two\x00three", 0, false, false},
		{"under cap", "output", nil, nil, 6, 5 * time.Second, "hello", 0, false, false},
		{"exact cap", "output", nil, nil, 5, 5 * time.Second, "hello", 0, false, false},
		{"overflow", "overflow", nil, nil, 5, 5 * time.Second, "hello", -1, true, false},
		{"cumulative overflow", "cumulative", nil, nil, 5, 5 * time.Second, "hello", -1, true, false},
		{"zero cap overflows", "output", nil, nil, 0, 5 * time.Second, "", -1, true, false},
		{"timeout", "sleep", nil, nil, 64, 20 * time.Millisecond, "", -1, false, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			result := runHelper(t, tc.mode, tc.argv, tc.stdin, tc.timeout, tc.cap)
			if result.StartErr != nil {
				t.Fatalf("StartErr=%v", result.StartErr)
			}
			if string(result.Output) != tc.wantOut || result.Truncated != tc.trunc || result.TimedOut != tc.timed {
				t.Fatalf("result=%+v, want output=%q truncated=%v timedOut=%v", result, tc.wantOut, tc.trunc, tc.timed)
			}
			if !tc.trunc && !tc.timed && result.ExitCode != tc.wantRC {
				t.Fatalf("ExitCode=%d, want %d", result.ExitCode, tc.wantRC)
			}
		})
	}
}

func TestRunTimeoutReturnsWhenDescendantKeepsPipesOpen(t *testing.T) {
	started := time.Now()
	result := runHelper(t, "descendant", nil, nil, 20*time.Millisecond, 64)
	if !result.TimedOut || time.Since(started) > time.Second {
		t.Fatalf("result=%+v elapsed=%v", result, time.Since(started))
	}
}

func TestRunStartFailure(t *testing.T) {
	result := Run([]string{t.TempDir() + "/missing-runner-binary"}, nil, time.Second, 10)
	if result.StartErr == nil || result.TimedOut || result.Truncated || len(result.Output) != 0 {
		t.Fatalf("result=%+v, want only StartErr", result)
	}
}

func runHelper(t *testing.T, mode string, args []string, stdin []byte, timeout time.Duration, cap int64) Result {
	t.Helper()
	argv := append([]string{os.Args[0], "-test.run=TestRunnerHelperProcess", "--"}, args...)
	t.Setenv("RUNNER_HELPER", mode)
	return Run(argv, stdin, timeout, cap)
}

func TestRunnerHelperProcess(t *testing.T) {
	if mode := os.Getenv("RUNNER_HELPER"); mode != "" {
		switch mode {
		case "ordered":
			fmt.Fprint(os.Stdout, "out")
			fmt.Fprint(os.Stderr, "err")
			os.Exit(7)
		case "stdin":
			body, _ := io.ReadAll(os.Stdin)
			_, _ = os.Stdout.Write(body)
		case "argv":
			fmt.Fprint(os.Stdout, strings.Join(os.Args[3:], "\x00"))
		case "output":
			fmt.Fprint(os.Stdout, "hello")
		case "overflow":
			fmt.Fprint(os.Stdout, "hello world")
		case "cumulative":
			fmt.Fprint(os.Stdout, "he")
			time.Sleep(10 * time.Millisecond)
			fmt.Fprint(os.Stdout, "llo")
			time.Sleep(10 * time.Millisecond)
			fmt.Fprint(os.Stdout, "!")
		case "sleep":
			time.Sleep(time.Second)
		case "descendant":
			child := exec.Command(os.Args[0], "-test.run=TestRunnerHelperProcess", "--") // #nosec G204 -- test helper invokes its own fixed executable.
			child.Env = append(os.Environ(), "RUNNER_HELPER=grandchild")
			child.Stdout, child.Stderr = os.Stdout, os.Stderr
			if err := child.Start(); err != nil {
				os.Exit(2)
			}
			time.Sleep(3 * time.Second)
		case "grandchild":
			time.Sleep(3 * time.Second)
		}
		os.Exit(0)
	}
}
