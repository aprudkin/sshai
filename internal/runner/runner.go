// Package runner executes a single local process with bounded combined output.
package runner

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
	"sync"
	"time"
)

// Result describes one bounded process execution. StartErr is non-nil only
// when the process could not be started; in that case ExitCode is not an
// execution result. Output contains stdout and stderr in their observed
// combined order and never exceeds the requested cap.
type Result struct {
	ExitCode  int
	Output    []byte
	Truncated bool
	TimedOut  bool
	StartErr  error
}

// Run executes argv directly (without a shell), supplies stdin, and captures
// its combined stdout and stderr. It kills the direct child when timeout
// elapses or output exceeds cap. A cap of zero retains no output; negative
// caps are treated as zero.
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
	// A killed interpreter can leave descendants holding inherited pipe file
	// descriptors. Process-tree cleanup is intentionally out of scope, but the
	// runner must still return near its deadline rather than wait for those
	// descendants to close the pipes.
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Stdin = bytes.NewReader(stdin)
	out := newCapWriter(cap, cancel)
	// Assigning the same writer preserves the ordering observed by os/exec.
	cmd.Stdout, cmd.Stderr = out, out

	err := cmd.Run()
	if cmd.ProcessState == nil {
		return Result{Output: out.Bytes(), Truncated: out.Truncated(), StartErr: err}
	}
	return Result{
		ExitCode:  cmd.ProcessState.ExitCode(),
		Output:    out.Bytes(),
		Truncated: out.Truncated(),
		TimedOut:  ctx.Err() == context.DeadlineExceeded,
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
