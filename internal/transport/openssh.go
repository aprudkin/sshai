// internal/transport/openssh.go
package transport

import (
	"bytes"
	"context"
	"errors"
	"os/exec"
	"sync"
	"time"
)

// defaultPutTimeout bounds the underlying scp invocation for Put. Unlike
// Exec, Put's signature (see Transport) carries no caller-supplied
// timeout, so a fixed budget is used — generous enough for pushing a
// small script body, not meant for bulk file transfer.
const defaultPutTimeout = 2 * time.Minute

// OpenSSH is a Transport that shells out to the system ssh(1) and scp(1)
// binaries, sharing one ControlMaster socket per host to avoid paying a
// fresh TCP+auth handshake on every call.
type OpenSSH struct {
	controlDir     string
	controlPersist string
	streamCap      int64

	// Runner executes argv (argv[0] is "ssh" or "scp"), feeding it stdin
	// and enforcing timeout. It returns the remote process's exit code,
	// its combined stdout+stderr, and whether timeout elapsed before the
	// process exited. Exposed for injection in tests; NewOpenSSH wires it
	// to a default backed by os/exec.
	Runner func(argv []string, stdin []byte, timeout time.Duration) (rc int, out []byte, timedOut bool)
}

// NewOpenSSH builds an OpenSSH transport whose every Exec and Put call
// carries the same ControlMaster options — each its own argv element —
// pointed at a socket directory under controlDir and persisted for
// controlPersist after the last client disconnects. streamCap bounds the
// combined stdout+stderr captured per call; a remote command that writes
// past it is killed and its Result reports Truncated.
func NewOpenSSH(controlDir, controlPersist string, streamCap int64) *OpenSSH {
	tr := &OpenSSH{
		controlDir:     controlDir,
		controlPersist: controlPersist,
		streamCap:      streamCap,
	}
	tr.Runner = tr.run
	return tr
}

// sshOpts is the fixed set of -o options shared by every ssh and scp
// invocation this transport makes, each option its own argv element (see
// TestArgvDiscipline): never glue "-o" to its value, and never combine
// two options into one element — a historical bug ("keyword batchmode
// extra arguments") came from doing exactly that.
func (tr *OpenSSH) sshOpts() []string {
	return []string{
		"-o", "BatchMode=yes",
		"-o", "ConnectTimeout=10",
		"-o", "LogLevel=ERROR",
		"-o", "ControlMaster=auto",
		"-o", "ControlPath=" + tr.controlDir + "/%C",
		"-o", "ControlPersist=" + tr.controlPersist,
	}
}

// sshArgv builds the argv for a remote command: ssh <opts...> host command.
// command travels as a single argv element — the shell on the remote end
// parses it, never this process's own argv.
func (tr *OpenSSH) sshArgv(host, command string) []string {
	argv := append([]string{"ssh"}, tr.sshOpts()...)
	return append(argv, host, command)
}

// scpArgv builds the argv for a file copy: scp -q <opts...> local host:remote.
func (tr *OpenSSH) scpArgv(host, localPath, remotePath string) []string {
	argv := append([]string{"scp", "-q"}, tr.sshOpts()...)
	return append(argv, localPath, host+":"+remotePath)
}

// Exec runs command on host. ssh reserves exit 255 for its own transport
// failures (connection refused, auth failure, DNS) — never the remote
// command's, since a well-behaved remote command that itself wants to
// exit 255 is indistinguishable at this layer and treated the same way
// ps_ssh.py treats it: as a transport failure, not an honest exit. Any
// other exit code, including 0, is the remote command's own honest
// status. A context deadline exceeded before the process finished maps
// to TransportError{"timeout"} regardless of rc.
func (tr *OpenSSH) Exec(host, command string, stdin []byte, timeout time.Duration) (Result, error) {
	argv := tr.sshArgv(host, command)
	rc, out, timedOut := tr.Runner(argv, stdin, timeout)

	if timedOut {
		return Result{}, &TransportError{Reason: "timeout"}
	}
	if rc == 255 {
		return Result{}, &TransportError{Reason: "ssh"}
	}
	return Result{
		ExitCode: rc,
		Output:   out,
		// The default Runner never returns more than streamCap bytes; if
		// it returned exactly that many, treat the boundary as a cap hit
		// rather than a coincidence, consistent with capWriter's own
		// truncation trigger (see run and capWriter.Write below).
		Truncated: tr.streamCap > 0 && int64(len(out)) >= tr.streamCap,
	}, nil
}

// Put copies the local file at localPath to remotePath on host via scp.
// Unlike ssh's Exec discrimination, scp's exit 255 is not special-cased
// here — per the ported semantics, any non-zero rc from scp (255
// included) is reported as TransportError{"scp"}.
func (tr *OpenSSH) Put(host, localPath, remotePath string) error {
	argv := tr.scpArgv(host, localPath, remotePath)
	rc, _, timedOut := tr.Runner(argv, nil, defaultPutTimeout)

	if timedOut {
		return &TransportError{Reason: "timeout"}
	}
	if rc != 0 {
		return &TransportError{Reason: "scp"}
	}
	return nil
}

// run is the default Runner, backed by os/exec. It feeds stdin to the
// child, captures combined stdout+stderr through a capWriter bounded at
// tr.streamCap, and reports rc from the process's own exit status once
// it has actually run.
func (tr *OpenSSH) run(argv []string, stdin []byte, timeout time.Duration) (int, []byte, bool) {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Stdin = bytes.NewReader(stdin)

	w := newCapWriter(tr.streamCap, cancel)
	cmd.Stdout = w
	cmd.Stderr = w

	// The error from Run is deliberately not surfaced: a non-zero exit,
	// a kill triggered by capWriter on overflow, and a kill triggered by
	// the context deadline all report themselves through
	// cmd.ProcessState and ctx.Err() below, which is what the caller
	// (Exec/Put) actually discriminates on.
	_ = cmd.Run()

	rc := -1
	if cmd.ProcessState != nil {
		rc = cmd.ProcessState.ExitCode()
	}
	timedOut := ctx.Err() == context.DeadlineExceeded
	return rc, w.Bytes(), timedOut
}

// errStreamCapExceeded is capWriter's sentinel Write error once the cap
// is reached: it exists to stop os/exec's internal copy loop promptly
// (a Write returning a non-nil error halts further copying), not to be
// inspected by callers.
var errStreamCapExceeded = errors.New("transport: stream cap exceeded")

// capWriter accumulates up to max bytes of combined stdout+stderr. The
// write that reaches or crosses max is truncated to fit, Truncated is
// recorded, and cancel is invoked exactly once to kill the underlying
// process — reaching the cap is treated as the truncation point outright,
// not merely a hint to keep watching, so that a process is never left
// running unbounded just because its output is large.
type capWriter struct {
	mu        sync.Mutex
	max       int64
	buf       []byte
	truncated bool
	cancel    context.CancelFunc
}

func newCapWriter(max int64, cancel context.CancelFunc) *capWriter {
	return &capWriter{max: max, cancel: cancel}
}

func (w *capWriter) Write(p []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()

	if len(p) == 0 {
		return 0, nil
	}
	if w.truncated {
		return 0, errStreamCapExceeded
	}

	remaining := w.max - int64(len(w.buf))
	if remaining < 0 {
		remaining = 0
	}
	if int64(len(p)) < remaining {
		w.buf = append(w.buf, p...)
		return len(p), nil
	}

	w.buf = append(w.buf, p[:remaining]...)
	w.truncated = true
	if w.cancel != nil {
		w.cancel()
	}
	return int(remaining), errStreamCapExceeded
}

func (w *capWriter) Bytes() []byte {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := make([]byte, len(w.buf))
	copy(out, w.buf)
	return out
}
