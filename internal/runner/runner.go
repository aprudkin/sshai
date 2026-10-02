// Package runner executes a single local process with bounded combined output.
package runner

import (
	"bytes"
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

// Result describes one bounded process execution. StartErr is non-nil only
// when the process could not be started; in that case ExitCode is not an
// execution result. Output contains stdout and stderr in their observed
// combined order and never exceeds the requested cap. ExitCode is the direct
// child's status, not proof of complete capture: check TimedOut, Truncated and
// CaptureErr too. CaptureErr reports an output read failure; cancellation can
// set it alongside TimedOut or Truncated, which take precedence.
type Result struct {
	ExitCode   int
	Output     []byte
	Truncated  bool
	TimedOut   bool
	StartErr   error
	CaptureErr error
}

// Run executes argv directly (without a shell), supplies stdin, and captures
// its combined stdout and stderr, draining inherited pipes to EOF within the
// same timeout even after the direct child exits. It kills only the direct
// child and stops capture when timeout elapses or output exceeds cap. A cap of
// zero retains no output; negative caps are treated as zero.
func Run(argv []string, stdin []byte, timeout time.Duration, cap int64) Result {
	if len(argv) == 0 {
		return Result{StartErr: errors.New("runner: empty argv")}
	}
	if cap < 0 {
		cap = 0
	}

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) // #nosec G204 -- caller supplies discrete argv; no shell is used.
	// Bound os/exec's stdin cleanup, but own output separately: WaitDelay
	// otherwise closes inherited output soon after child exit, before EOF.
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Stdin = bytes.NewReader(stdin)
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		return Result{StartErr: err}
	}
	defer readPipe.Close()
	defer writePipe.Close()
	out := newCapWriter(cap, cancel)
	// One pipe preserves the observed combined stdout/stderr ordering.
	cmd.Stdout, cmd.Stderr = writePipe, writePipe
	if err := cmd.Start(); err != nil {
		return Result{StartErr: err}
	}
	_ = writePipe.Close() // only the child/descendants may now keep output open
	stopClose := context.AfterFunc(ctx, func() { _ = readPipe.Close() })
	defer stopClose()
	drained := make(chan error, 1)
	go func() {
		_, err := io.Copy(out, readPipe)
		if err != nil {
			cancel() // a capture failure must not leave the child running
		}
		drained <- err
	}()
	_ = cmd.Wait()
	captureErr := <-drained
	return Result{
		ExitCode:   cmd.ProcessState.ExitCode(),
		Output:     out.Bytes(),
		Truncated:  out.Truncated(),
		TimedOut:   ctx.Err() == context.DeadlineExceeded,
		CaptureErr: captureErr,
	}
}

var errOutputLimit = errors.New("runner: output limit exceeded")

type capWriter struct {
	mu        sync.Mutex
	cap       int64
	output    []byte
	truncated bool
	cancel    context.CancelFunc
}

func newCapWriter(cap int64, cancel context.CancelFunc) *capWriter {
	return &capWriter{cap: cap, cancel: cancel}
}

func (w *capWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if len(p) == 0 {
		return 0, nil
	}
	if w.truncated {
		return 0, errOutputLimit
	}

	room := w.cap - int64(len(w.output))
	if room < 0 {
		room = 0
	}
	take := int64(len(p))
	if take > room {
		take = room
	}
	w.output = append(w.output, p[:take]...)
	if take == int64(len(p)) {
		return int(take), nil
	}

	w.truncated = true
	w.cancel()
	return int(take), errOutputLimit
}

func (w *capWriter) Bytes() []byte {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]byte(nil), w.output...)
}

func (w *capWriter) Truncated() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.truncated
}
