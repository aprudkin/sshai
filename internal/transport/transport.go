// internal/transport/transport.go
package transport

import "time"

// Result is the outcome of a remote command run through Exec: the exit
// code the remote command itself returned, its combined stdout+stderr
// (interleaved as produced), and whether that output was cut off at the
// implementation's stream cap.
type Result struct {
	ExitCode  int
	Output    []byte
	Truncated bool
}

// TransportError reports a failure of the transport itself, as opposed to
// an honest exit code from the remote command. Reason is one of "ssh",
// "scp", or "timeout".
type TransportError struct {
	Reason string
}

func (e *TransportError) Error() string {
	return "transport: " + e.Reason
}

// Transport runs commands on a remote host and copies files to it. Exec
// returns a TransportError (never a plain error) when the transport
// itself failed rather than the remote command; a non-nil Result paired
// with a nil error means the remote command ran and ExitCode is its
// honest exit status, whatever that status is.
type Transport interface {
	// Exec runs command on host, feeding it stdin, and returns once the
	// command exits, timeout elapses, or the transport fails outright.
	Exec(host, command string, stdin []byte, timeout time.Duration) (Result, error)

	// Put copies the local file at localPath to remotePath on host.
	Put(host, localPath, remotePath string) error
}
