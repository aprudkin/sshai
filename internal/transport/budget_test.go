package transport

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func requireTransportTimeout(t *testing.T, err error) {
	t.Helper()
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "timeout" || te.Diagnostic() != "operation timed out" {
		t.Fatalf("want canonical timeout, got %v", err)
	}
}

func TestRemaining(t *testing.T) {
	remaining, err := Remaining(time.Now().Add(time.Second))
	if err != nil || remaining <= 0 || remaining > time.Second {
		t.Fatalf("remaining=%v err=%v", remaining, err)
	}
	for _, deadline := range []time.Time{time.Now(), time.Now().Add(-time.Second), {}} {
		remaining, err := Remaining(deadline)
		if remaining != 0 {
			t.Fatalf("expired remaining=%v", remaining)
		}
		requireTransportTimeout(t, err)
	}
}

func TestExecPreparationConsumesBudget(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	calls := 0
	tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
		calls++
		if calls == 1 {
			time.Sleep(40 * time.Millisecond)
		}
		return map[string]HostKey{}, nil
	}
	tr.Runner = func(_ []string, _ []byte, timeout time.Duration) (int, []byte, bool) {
		if timeout <= 0 || timeout > 80*time.Millisecond {
			t.Errorf("execution received fresh budget %v after preparation", timeout)
		}
		return 3, []byte("done"), false
	}
	res, err := tr.Exec("h", "true", nil, 100*time.Millisecond)
	if err != nil || res.ExitCode != 3 || string(res.Output) != "done" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestPutPreparationConsumesBudget(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	calls := 0
	tr.hostKeyLookup = func(_ string, _ []string, timeout time.Duration) (map[string]HostKey, error) {
		calls++
		if timeout <= 0 || timeout > 100*time.Millisecond {
			t.Errorf("host-key inspection received independent budget %v", timeout)
		}
		if calls == 1 {
			time.Sleep(40 * time.Millisecond)
		}
		return map[string]HostKey{}, nil
	}
	tr.Runner = func(argv []string, _ []byte, timeout time.Duration) (int, []byte, bool) {
		if argv[0] != "scp" || timeout <= 0 || timeout > 80*time.Millisecond {
			t.Errorf("upload argv=%q budget=%v", argv, timeout)
		}
		return 0, nil, false
	}
	if err := tr.Put("h", "local", "remote", 100*time.Millisecond); err != nil {
		t.Fatal(err)
	}
}

func TestTransportRefusesExpiredPreparation(t *testing.T) {
	t.Setenv("PATH", t.TempDir())
	for _, operation := range []string{"put", "stream"} {
		t.Run(operation, func(t *testing.T) {
			tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
			tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
				time.Sleep(30 * time.Millisecond)
				return map[string]HostKey{}, nil
			}
			tr.Runner = func([]string, []byte, time.Duration) (int, []byte, bool) {
				t.Error("upload started after preparation exhausted budget")
				return 0, nil, false
			}
			var err error
			if operation == "put" {
				err = tr.Put("h", "local", "remote", 10*time.Millisecond)
			} else {
				_, err = tr.ExecStream("h", "true", nil, 10*time.Millisecond, nil)
			}
			requireTransportTimeout(t, err)
		})
	}
}

func TestExecRefusesExpiredPreparation(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
		time.Sleep(30 * time.Millisecond)
		return map[string]HostKey{}, nil
	}
	tr.Runner = func([]string, []byte, time.Duration) (int, []byte, bool) {
		t.Error("execution started after preparation exhausted budget")
		return 0, nil, false
	}
	_, err := tr.Exec("h", "true", nil, 10*time.Millisecond)
	requireTransportTimeout(t, err)
}

func TestTransportRefusesNonPositiveBudget(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // even a regression cannot invoke live SSH
	for _, operation := range []string{"exec", "put", "stream"} {
		t.Run(operation, func(t *testing.T) {
			for _, timeout := range []time.Duration{0, -time.Second} {
				tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
				tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
					t.Error("lookup started without budget")
					return nil, nil
				}
				tr.Runner = func([]string, []byte, time.Duration) (int, []byte, bool) {
					t.Error("execution started without budget")
					return 0, nil, false
				}
				var err error
				switch operation {
				case "exec":
					_, err = tr.Exec("h", "true", nil, timeout)
				case "put":
					err = tr.Put("h", "local", "remote", timeout)
				case "stream":
					_, err = tr.ExecStream("h", "true", nil, timeout, nil)
				}
				requireTransportTimeout(t, err)
			}
		})
	}
}

func writeBudgetSSH(t *testing.T, body string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake ssh fixture")
	}
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "ssh"), []byte("#!/bin/sh\n"+body), 0o700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
}

func TestObservationDoesNotRestartExpiredBudget(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	calls := 0
	tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
		calls++
		return map[string]HostKey{}, nil
	}
	tr.Runner = func([]string, []byte, time.Duration) (int, []byte, bool) {
		time.Sleep(30 * time.Millisecond)
		return -1, nil, true
	}
	_, err := tr.Exec("h", "true", nil, 10*time.Millisecond)
	requireTransportTimeout(t, err)
	if calls != 1 {
		t.Fatalf("inspection started after execution exhausted budget: %d lookups", calls)
	}
	if _, _, err := tr.AcceptedHostKey("h"); err == nil {
		t.Fatal("missing unavailable host-key evidence")
	}
}

func TestHostKeyLookupConsumesSuppliedBudget(t *testing.T) {
	writeBudgetSSH(t, "exec sleep 1\n")
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	started := time.Now()
	_, err := tr.Exec("h", "true", nil, 30*time.Millisecond)
	requireTransportTimeout(t, err)
	if elapsed := time.Since(started); elapsed > 500*time.Millisecond {
		t.Fatalf("host-key lookup exceeded supplied budget: %v", elapsed)
	}
}

func TestHostKeyObservationConsumesRemainingBudget(t *testing.T) {
	for _, operation := range []string{"exec", "put", "stream"} {
		t.Run(operation, func(t *testing.T) {
			t.Setenv("SSHAI_TEST_LOOKUP_MARKER", filepath.Join(t.TempDir(), "prepared"))
			t.Setenv("SSHAI_TEST_KNOWN_HOSTS", filepath.Join(t.TempDir(), "known_hosts"))
			writeBudgetSSH(t, `if [ "$1" = -G ]; then
  if [ -f "$SSHAI_TEST_LOOKUP_MARKER" ]; then
    exec sleep 1
  fi
  : >"$SSHAI_TEST_LOOKUP_MARKER"
  printf 'hostname h\nuserknownhostsfile %s\n' "$SSHAI_TEST_KNOWN_HOSTS"
  exit 0
fi
printf done
exit 3
`)
			tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
			started := time.Now()
			var res Result
			var err error
			switch operation {
			case "exec":
				res, err = tr.Exec("h", "true", nil, 300*time.Millisecond)
			case "stream":
				res, err = tr.ExecStream("h", "true", nil, 300*time.Millisecond, nil)
			case "put":
				tr.Runner = fake(0, "", false) // no scp process or live network
				err = tr.Put("h", "local", "remote", 300*time.Millisecond)
			}
			if err != nil || (operation != "put" && (res.ExitCode != 3 || string(res.Output) != "done")) {
				t.Fatalf("completed outcome lost: res=%+v err=%v", res, err)
			}
			if elapsed := time.Since(started); elapsed > 700*time.Millisecond {
				t.Fatalf("observation added a new budget: %v", elapsed)
			}
			if _, _, err := tr.AcceptedHostKey("h"); err == nil {
				t.Fatal("missing inspection failure evidence")
			}
		})
	}
}

// Run the inherited-pipe reproduction in a separate test process so a broken
// drain cannot hang the suite. The fixture's descendant exits after two seconds;
// neither the parent nor helper invokes a real SSH client or network host.
func TestExecStreamInheritedPipeDeadline(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake ssh fixture")
	}
	if os.Getenv("SSHAI_TEST_STREAM_PIPE_HELPER") == "1" {
		t.Setenv("SSHAI_TEST_PIPE_BINARY", os.Args[0])
		t.Setenv("SSHAI_TEST_PIPE_ROLE", "ssh")
		writeBudgetSSH(t, "exec \"$SSHAI_TEST_PIPE_BINARY\" -test.run=^TestStreamPipeProcess$\n")
		tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{})
		started := time.Now()
		var observed []byte
		_, err := tr.ExecStream("h", "true", nil, 300*time.Millisecond, func(p []byte) { observed = append(observed, p...) })
		requireTransportTimeout(t, err)
		if !strings.Contains(string(observed), "pipe-held") {
			t.Fatalf("fixture did not establish inherited pipes before cancellation: %q", observed)
		}
		if elapsed := time.Since(started); elapsed > 800*time.Millisecond {
			t.Fatalf("stream waited for inherited pipe after deadline: %v", elapsed)
		}
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestExecStreamInheritedPipeDeadline$", "-test.v")
	cmd.Env = append(os.Environ(), "SSHAI_TEST_STREAM_PIPE_HELPER=1")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("isolated pipe regression: %v\n%s", err, out)
	}
}

// A Go descendant deliberately retains both inherited pipes; unlike shell job
// control, its lifetime is independent of the fake SSH parent's cancellation.
func TestStreamPipeProcess(t *testing.T) {
	switch os.Getenv("SSHAI_TEST_PIPE_ROLE") {
	case "holder":
		if _, err := os.Stdout.WriteString("pipe-held"); err != nil {
			t.Fatal(err)
		}
		time.Sleep(2 * time.Second)
	case "ssh":
		cmd := exec.Command(os.Args[0], "-test.run=^TestStreamPipeProcess$")
		cmd.Env = append(os.Environ(), "SSHAI_TEST_PIPE_ROLE=holder")
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		time.Sleep(2 * time.Second)
	}
}

func TestExecStreamObservationFailurePreservesCompletedResult(t *testing.T) {
	writeBudgetSSH(t, "printf done\nexit 3\n")
	tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{AcceptNewHostKey: "h"})
	calls := 0
	tr.hostKeyLookup = func(string, []string, time.Duration) (map[string]HostKey, error) {
		calls++
		if calls == 2 {
			time.Sleep(350 * time.Millisecond)
			return nil, errHostKeyInspection
		}
		return map[string]HostKey{}, nil
	}
	res, err := tr.ExecStream("h", "true", nil, 300*time.Millisecond, nil)
	if err != nil || res.ExitCode != 3 || string(res.Output) != "done" {
		t.Fatalf("completed command lost to evidence failure: res=%+v err=%v", res, err)
	}
	if _, _, err := tr.AcceptedHostKey("h"); err == nil {
		t.Fatal("missing inspection error evidence")
	}
}
