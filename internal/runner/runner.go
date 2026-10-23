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

// CaptureSetupError distinguishes failure to prepare output capture from
// failure to start the process. It is returned in StartErr so existing callers
// retain their start-failure handling and can still inspect the underlying error.
type CaptureSetupError struct {
	cause error
}

func (e *CaptureSetupError) Error() string { return e.cause.Error() }
func (e *CaptureSetupError) Unwrap() error { return e.cause }

// Run executes argv directly (without a shell), supplies stdin, and captures
// its combined stdout and stderr, draining inherited pipes to EOF within the
// same timeout even after the direct child exits. It kills only the direct
// child and stops capture when timeout elapses or output exceeds cap. A cap of
// zero retains no output; negative caps are treated as zero.
func Run(argv []string, stdin []byte, timeout time.Duration, cap int64) Result {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	return run(ctx, cancel, argv, stdin, cap, nil, true)
}

// RunObservedUntil executes argv directly and synchronously reports each
// retained output chunk to observe. The absolute deadline includes caller
// preparation performed before this call. Capture read errors do not cancel
// the child, matching the historical streaming transport behavior.
func RunObservedUntil(argv []string, stdin []byte, deadline time.Time, cap int64, observe func([]byte)) Result {
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	return run(ctx, cancel, argv, stdin, cap, observe, false)
}

func run(ctx context.Context, cancel context.CancelFunc, argv []string, stdin []byte, cap int64, observe func([]byte), cancelOnCaptureError bool) Result {
	if len(argv) == 0 {
		return Result{StartErr: errors.New("runner: empty argv")}
	}
	if cap < 0 {
		cap = 0
	}

	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) // #nosec G204 -- caller supplies discrete argv; no shell is used.
	// Bound os/exec's stdin cleanup, but own output separately: WaitDelay
	// otherwise closes inherited output soon after child exit, before EOF.
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Stdin = bytes.NewReader(stdin)
	readPipe, writePipe, err := os.Pipe()
	if err != nil {
		return Result{StartErr: &CaptureSetupError{cause: err}}
	}
	defer readPipe.Close()
	defer writePipe.Close()
	out := newCapWriter(cap, cancel, observe)
	// One pipe preserves the observed combined stdout/stderr ordering.
	cmd.Stdout, cmd.Stderr = writePipe, writePipe
	if err := cmd.Start(); err != nil {
		result := Result{StartErr: err}
		if !cancelOnCaptureError {
			result.TimedOut = ctx.Err() == context.DeadlineExceeded
		}
		return result
	}
	_ = writePipe.Close() // only the child/descendants may now keep output open
	stopClose := context.AfterFunc(ctx, func() { _ = readPipe.Close() })
	defer stopClose()
	drained := make(chan error, 1)
	go func() {
		_, err := io.Copy(out, readPipe)
		if err != nil && cancelOnCaptureError {
			cancel() // buffered/local capture failure must not leave the child running
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
	observe   func([]byte)
}

func newCapWriter(cap int64, cancel context.CancelFunc, observe func([]byte)) *capWriter {
	return &capWriter{cap: cap, cancel: cancel, observe: observe}
}

func (w *capWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	if len(p) == 0 {
		w.mu.Unlock()
		return 0, nil
	}
	if w.truncated {
		w.mu.Unlock()
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
	var accepted []byte
	if w.observe != nil && take > 0 {
		accepted = append([]byte(nil), p[:take]...)
	}
	overflow := take != int64(len(p))
	if overflow {
		w.truncated = true
		w.cancel()
	}
	observe := w.observe
	w.mu.Unlock()

	if len(accepted) > 0 && observe != nil {
		observe(accepted)
	}
	if overflow {
		return int(take), errOutputLimit
	}
	return int(take), nil
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
