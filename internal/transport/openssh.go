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

// execStartFailedRC is the out-of-band sentinel run() returns when the
// local ssh/scp process never started at all (binary missing, exec
// permission denied, ...). It is deliberately distinct from -1, which
// ProcessState.ExitCode() itself returns for "started but has no clean
// exit status" (killed by our own cap-overflow or timeout logic) — a
// real, if uninformative, outcome. execStartFailedRC means there is no
// remote exit code whatsoever to report; Exec and Put both escalate it
// to a TransportError rather than passing it through as an honest exit.
const execStartFailedRC = -2

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
// ps_ssh.py treats it: as a transport failure, not an honest exit. The
// local ssh process failing to start at all (missing binary, exec
// permission denied — see run's execStartFailedRC) is reported the same
// way, since there is no remote exit code to speak of either. Any other
// exit code, including 0, is the remote command's own honest status. A
// context deadline exceeded before the process finished maps to
// TransportError{"timeout"} regardless of rc.
func (tr *OpenSSH) Exec(host, command string, stdin []byte, timeout time.Duration) (Result, error) {
	argv := tr.sshArgv(host, command)
	rc, out, timedOut := tr.Runner(argv, stdin, timeout)

	if timedOut {
		return Result{}, &TransportError{Reason: "timeout"}
	}
	if rc == 255 || rc == execStartFailedRC {
		return Result{}, &TransportError{Reason: "ssh"}
	}

	// The default Runner's capWriter retains at most streamCap+1 bytes:
	// one byte past the advertised cap exists purely as an overflow
	// sentinel (see capWriter.Write), so a write landing exactly at the
	// cap is never mistaken for truncation. Output longer than streamCap
	// here is proof of genuine overflow — trim the sentinel (and
	// whatever else capWriter had buffered before the kill landed) back
	// off so callers never see more than the cap they asked for.
	truncated := false
	if tr.streamCap > 0 && int64(len(out)) > tr.streamCap {
		truncated = true
		out = out[:tr.streamCap]
	}

	return Result{
		ExitCode:  rc,
		Output:    out,
		Truncated: truncated,
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

	// cmd.Run()'s error is redundant with cmd.ProcessState/ctx.Err() below
	// for every case except one: a non-zero exit, a kill triggered by
	// capWriter on overflow, and a kill triggered by the context deadline
	// all report themselves through those two regardless of what Run()
	// returned. The one case they can't cover is the child never having
	// started at all (missing binary, exec permission denied, ...): then
	// ProcessState stays nil and there is no remote exit code whatsoever
	// — that's the execStartFailedRC branch below, which Exec/Put escalate
	// to a TransportError instead of passing through as a fabricated
	// honest exit.
	_ = cmd.Run()

	rc := execStartFailedRC
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

// capWriter accumulates combined stdout+stderr up to max+1 bytes: max is
// the caller's advertised cap, and the one extra byte is kept purely as
// an overflow sentinel — proof that real output exceeded max — so that a
// write landing exactly at max is retained in full rather than mistaken
// for truncation (that off-by-one was the bug: triggering on "reaches
// max" rather than "exceeds max" killed processes whose output fit
// exactly). The kill trigger is "buffered bytes exceed max", checked
// after every write, so it fires the moment real output crosses the cap
// regardless of whether that happens within one write or is spread
// across several — including a single write of exactly max+1 bytes.
// cancel is invoked exactly once. Exec derives the caller-visible
// Truncated fact from output length (len(out) > max) and trims the
// sentinel byte back off — see Exec. Each Exec/Put call constructs its
// own capWriter inside run(), so there is no mutable state shared
// between concurrent calls on one OpenSSH instance.
type capWriter struct {
	mu     sync.Mutex
	max    int64
	buf    []byte
	killed bool
	cancel context.CancelFunc
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
	if w.killed {
		return 0, errStreamCapExceeded
	}

	hardCap := w.max + 1
	room := hardCap - int64(len(w.buf))
	if room < 0 {
		room = 0
	}
	take := int64(len(p))
	if take > room {
		take = room
	}
	w.buf = append(w.buf, p[:take]...)

	if int64(len(w.buf)) <= w.max {
		// Still at or under the advertised cap: no overflow yet.
		return int(take), nil
	}

	// Buffered output now exceeds max — genuine overflow, whether that
	// happened within this write or was the last of several. Kill the
	// process and stop accepting further output.
	w.killed = true
	if w.cancel != nil {
		w.cancel()
	}
	return int(take), errStreamCapExceeded
}

func (w *capWriter) Bytes() []byte {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := make([]byte, len(w.buf))
	copy(out, w.buf)
	return out
}
