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

// HostKey is the bounded evidence returned when an explicitly scoped
// accept-new connection writes a previously unknown server key.
type HostKey struct {
	Algorithm   string
	Fingerprint string
}

// HostKeyReporter is an optional Transport capability. Implementations return
// a key only when that exact host alias caused a new known_hosts entry during
// the current invocation; normal strict connections report no key.
type HostKeyReporter interface {
	AcceptedHostKey(host string) (HostKey, bool, error)
}

// TransportError reports a failure of the transport itself, as opposed to
// an honest exit code from the remote command. Reason is the stable transport
// class ("ssh", "scp", or "timeout"). Diagnostic returns a canonical,
// allowlisted explanation derived from transport output; raw output is never
// retained on the error.
type TransportError struct {
	Reason     string
	diagnostic string
}

// NewTransportError builds a transport error while reducing raw process
// output to a safe, canonical diagnostic. Unknown output produces no
// diagnostic rather than exposing arbitrary SSH configuration or secrets.
func NewTransportError(reason string, output []byte) *TransportError {
	return newTransportError(reason, safeTransportDiagnostic(output))
}

func newTransportError(reason, diagnostic string) *TransportError {
	return &TransportError{Reason: reason, diagnostic: diagnostic}
}

// Diagnostic returns the safe canonical explanation, or "" when the raw
// failure did not match the allowlist.
func (e *TransportError) Diagnostic() string {
	return e.diagnostic
}

func (e *TransportError) Error() string {
	if e.diagnostic != "" {
		return "transport: " + e.Reason + ": " + e.diagnostic
	}
	return "transport: " + e.Reason
}

// Transport runs commands on a remote host and copies files to it. Exec
// returns a TransportError (never a plain error) when the transport
// itself failed rather than the remote command; a non-nil Result paired
// with a nil error means the remote command ran and ExitCode is its
// honest exit status, whatever that status is.
// StreamingTransport is an optional additive capability for transports that can
// deliver the remote combined stream as it arrives. Implementations must
// preserve its pipe order for wrapper parsing. Transport remains unchanged so
// existing callers and fakes do not need to implement streaming.
type StreamingTransport interface {
	ExecStream(host, command string, stdin []byte, timeout time.Duration, output func([]byte)) (Result, error)
}

type Transport interface {
	// Exec runs command on host, feeding it stdin, and returns once the
	// command exits, timeout elapses, or the transport fails outright.
	Exec(host, command string, stdin []byte, timeout time.Duration) (Result, error)

	// Put copies the local file at localPath to remotePath on host.
	Put(host, localPath, remotePath string) error
}
