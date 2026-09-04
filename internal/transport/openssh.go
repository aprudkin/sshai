// internal/transport/openssh.go
package transport

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha1" // #nosec G505 -- required to match OpenSSH's documented |1| hashed-host format.
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/aprudkin/sshai/internal/runner"
)

// defaultPutTimeout bounds the underlying scp invocation for Put. Unlike
// Exec, Put's signature (see Transport) carries no caller-supplied
// timeout, so a fixed budget is used — generous enough for pushing a
// small script body, not meant for bulk file transfer.
const defaultPutTimeout = 2 * time.Minute

const (
	hostKeyConfigCap     = 256 << 10
	knownHostsReadCap    = 8 << 20
	hostKeyLookupTimeout = 10 * time.Second
)

// execStartFailedRC is the out-of-band sentinel run() returns when the
// local ssh/scp process never started at all (binary missing, exec
// permission denied, ...). It is deliberately distinct from -1, which
// ProcessState.ExitCode() itself returns for "started but has no clean
// exit status" (killed by our own cap-overflow or timeout logic) — a
// real, if uninformative, outcome. execStartFailedRC means there is no
// remote exit code whatsoever to report; Exec and Put both escalate it
// to a TransportError rather than passing it through as an honest exit.
const execStartFailedRC = -2

// Transport diagnostics are deliberately an allowlist of fixed phrases.
// ssh/scp stderr may contain hostnames, key fingerprints, algorithm offers,
// paths, or configuration excerpts; none of that raw text may cross the
// transport boundary. Matching is case-insensitive and bounded because this
// only needs the short terminal error emitted by OpenSSH.
const maxTransportDiagnosticBytes = 64 << 10

var transportDiagnosticPatterns = []struct {
	needle     []byte
	diagnostic string
}{
	{[]byte("remote host identification has changed"), "remote host identification changed"},
	{[]byte("host key verification failed"), "host key verification failed"},
	{[]byte("no matching host key type"), "no matching host key type"},
	{[]byte("no matching key exchange method"), "no matching key exchange method"},
	{[]byte("no matching cipher found"), "no matching cipher"},
	{[]byte("connection timed out"), "connection timed out"},
	{[]byte("operation timed out"), "connection timed out"},
	{[]byte("connection refused"), "connection refused"},
	{[]byte("could not resolve hostname"), "could not resolve hostname"},
	{[]byte("name or service not known"), "could not resolve hostname"},
	{[]byte("nodename nor servname provided"), "could not resolve hostname"},
	{[]byte("no route to host"), "no route to host"},
	{[]byte("network is unreachable"), "network is unreachable"},
	{[]byte("too many authentication failures"), "too many authentication failures"},
	{[]byte("permission denied"), "permission denied"},
	{[]byte("connection reset"), "connection reset"},
	{[]byte("connection closed"), "connection closed"},
}

var openSSHControlMasterSupported = runtime.GOOS != "windows"

func safeTransportDiagnostic(output []byte) string {
	if len(output) > maxTransportDiagnosticBytes {
		output = output[len(output)-maxTransportDiagnosticBytes:]
	}
	lower := bytes.ToLower(output)
	for _, pattern := range transportDiagnosticPatterns {
		if bytes.Contains(lower, pattern.needle) {
			return pattern.diagnostic
		}
	}
	return ""
}

// OpenSSH is a Transport that shells out to the system ssh(1) and scp(1)
// binaries, using one ControlMaster socket per host on clients that support
// connection sharing to avoid paying a fresh TCP+auth handshake on every call.
type OpenSSH struct {
	controlDir     string
	controlPersist string
	streamCap      int64
	options        OpenSSHOptions

	hostKeyMu       sync.Mutex
	hostKeyBefore   map[string]HostKey
	acceptedHostKey HostKey
	hostKeyPrepared bool
	hostKeyErr      error
	hostKeyLookup   func(host string, sshOpts []string) (map[string]HostKey, error)

	// Runner executes argv (argv[0] is "ssh" or "scp"), feeding it stdin
	// and enforcing timeout. It returns the remote process's exit code,
	// its combined stdout+stderr, and whether timeout elapsed before the
	// process exited. Exposed for injection in tests; NewOpenSSH wires it
	// to a default backed by os/exec.
	Runner func(argv []string, stdin []byte, timeout time.Duration) (rc int, out []byte, timedOut bool)
}

// OpenSSHOptions contains invocation-scoped SSH policy exceptions. The empty
// value preserves the strict ssh_config-derived route and host-key behavior.
type OpenSSHOptions struct {
	AcceptNewHostKey string
	ProxyJumpNone    bool
}

// NewOpenSSH builds an OpenSSH transport. On OpenSSH clients that support
// connection sharing, every Exec and Put call carries the same ControlMaster
// options — each its own argv element — pointed at a socket directory under
// controlDir and persisted for controlPersist after the last client
// disconnects. Windows OpenSSH clients do not support that Unix socket shape,
// so they keep the same transport semantics without ControlMaster options.
// streamCap bounds the combined stdout+stderr captured per call; a remote
// command that writes past it is killed and its Result reports Truncated.
// options apply only to this transport instance.
func NewOpenSSH(controlDir, controlPersist string, streamCap int64, options OpenSSHOptions) *OpenSSH {
	tr := &OpenSSH{
		controlDir:     controlDir,
		controlPersist: controlPersist,
		streamCap:      streamCap,
		options:        options,
	}
	tr.Runner = tr.run
	tr.hostKeyLookup = tr.lookupHostKeys
	return tr
}

// sshOpts returns the fixed options shared by every ssh and scp invocation,
// plus only the explicitly requested invocation-scoped overrides. Each -o
// and value remains a separate argv element (see TestArgvDiscipline).
func (tr *OpenSSH) sshOpts(host string) []string {
	opts := []string{
		"-o", "BatchMode=yes",
		"-o", "ConnectTimeout=10",
		"-o", "LogLevel=ERROR",
	}
	if openSSHControlMasterSupported {
		opts = append(opts,
			"-o", "ControlMaster=auto",
			"-o", "ControlPath="+tr.controlDir+"/%C",
			"-o", "ControlPersist="+tr.controlPersist,
		)
	}
	if tr.options.ProxyJumpNone {
		opts = append(opts, "-o", "ProxyJump=none")
	}
	if host == tr.options.AcceptNewHostKey {
		opts = append(opts,
			"-o", "StrictHostKeyChecking=accept-new",
			"-o", "UpdateHostKeys=no",
		)
	}
	return opts
}

// sshArgv builds the argv for a remote command: ssh <opts...> host command.
// command travels as a single argv element — the shell on the remote end
// parses it, never this process's own argv.
func (tr *OpenSSH) sshArgv(host, command string) []string {
	argv := append([]string{"ssh"}, tr.sshOpts(host)...)
	return append(argv, host, command)
}

// scpArgv builds the argv for a file copy: scp -q <opts...> local host:remote.
func (tr *OpenSSH) scpArgv(host, localPath, remotePath string) []string {
	argv := append([]string{"scp", "-q"}, tr.sshOpts(host)...)
	return append(argv, localPath, host+":"+remotePath)
}

var (
	errHostKeyInspection = errors.New("host key inspection failed")
	errHostKeyAmbiguous  = errors.New("multiple new host keys recorded")
)

// prepareAcceptedHostKey snapshots the exact alias's known keys before the
// first connection that carries accept-new. Failure is fail-closed: ssh/scp
// is not started unless ssh_config and known_hosts can be inspected.
func (tr *OpenSSH) prepareAcceptedHostKey(host string) error {
	if host != tr.options.AcceptNewHostKey {
		return nil
	}

	tr.hostKeyMu.Lock()
	defer tr.hostKeyMu.Unlock()
	if tr.hostKeyPrepared {
		return tr.hostKeyErr
	}
	tr.hostKeyPrepared = true
	before, err := tr.hostKeyLookup(host, tr.sshOpts(host))
	if err != nil {
		tr.hostKeyErr = errHostKeyInspection
		return tr.hostKeyErr
	}
	tr.hostKeyBefore = before
	return nil
}

// observeAcceptedHostKey compares known_hosts after a connection attempt.
// StrictHostKeyChecking=accept-new itself refuses changed keys; this records
// only a genuinely new entry and never retains raw ssh output or key bytes.
func (tr *OpenSSH) observeAcceptedHostKey(host string) {
	if host != tr.options.AcceptNewHostKey {
		return
	}

	tr.hostKeyMu.Lock()
	defer tr.hostKeyMu.Unlock()
	if tr.hostKeyErr != nil || tr.acceptedHostKey.Fingerprint != "" {
		return
	}
	after, err := tr.hostKeyLookup(host, tr.sshOpts(host))
	if err != nil {
		tr.hostKeyErr = errHostKeyInspection
		return
	}

	var added []HostKey
	for id, key := range after {
		if _, existed := tr.hostKeyBefore[id]; !existed {
			added = append(added, key)
		}
	}
	switch len(added) {
	case 0:
		return
	case 1:
		tr.acceptedHostKey = added[0]
	default:
		tr.hostKeyErr = errHostKeyAmbiguous
	}
}

// AcceptedHostKey implements HostKeyReporter.
func (tr *OpenSSH) AcceptedHostKey(host string) (HostKey, bool, error) {
	if host != tr.options.AcceptNewHostKey {
		return HostKey{}, false, nil
	}
	tr.hostKeyMu.Lock()
	defer tr.hostKeyMu.Unlock()
	if tr.hostKeyErr != nil {
		return HostKey{}, false, tr.hostKeyErr
	}
	if tr.acceptedHostKey.Fingerprint == "" {
		return HostKey{}, false, nil
	}
	return tr.acceptedHostKey, true, nil
}

func (tr *OpenSSH) lookupHostKeys(host string, sshOpts []string) (map[string]HostKey, error) {
	argv := append([]string{"ssh", "-G"}, sshOpts...)
	argv = append(argv, host)
	result := runner.Run(argv, nil, hostKeyLookupTimeout, hostKeyConfigCap)
	if result.StartErr != nil || result.TimedOut || result.Truncated || result.ExitCode != 0 {
		return nil, errHostKeyInspection
	}
	out := result.Output
	if int64(len(out)) > hostKeyConfigCap {
		return nil, errHostKeyInspection
	}
	lookup, paths, err := parseKnownHostsConfig(out)
	if err != nil {
		return nil, err
	}
	return readKnownHostKeys(lookup, paths)
}

func parseKnownHostsConfig(output []byte) (string, []string, error) {
	var hostname, port, alias string
	var paths []string
	for _, line := range bytes.Split(output, []byte{'\n'}) {
		fields := strings.Fields(string(line))
		if len(fields) < 2 {
			continue
		}
		switch fields[0] {
		case "hostname":
			hostname = fields[1]
		case "port":
			port = fields[1]
		case "hostkeyalias":
			alias = fields[1]
		case "userknownhostsfile":
			paths = append(paths, fields[1:]...)
		}
	}
	if hostname == "" {
		return "", nil, errHostKeyInspection
	}

	lookup := alias
	if lookup == "" || lookup == "none" {
		lookup = hostname
		if port != "" && port != "22" {
			lookup = "[" + hostname + "]:" + port
		}
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return "", nil, errHostKeyInspection
	}
	resolved := make([]string, 0, len(paths))
	for _, path := range paths {
		if path == "none" {
			continue
		}
		if path == "~" {
			path = home
		} else if strings.HasPrefix(path, "~/") {
			path = filepath.Join(home, path[2:])
		}
		if strings.Contains(path, "%") {
			return "", nil, errHostKeyInspection
		}
		resolved = append(resolved, path)
	}
	if len(resolved) == 0 {
		return "", nil, errHostKeyInspection
	}
	return lookup, resolved, nil
}

func readKnownHostKeys(lookup string, paths []string) (map[string]HostKey, error) {
	keys := make(map[string]HostKey)
	for _, path := range paths {
		f, err := os.Open(path) // #nosec G304 -- paths come from parsed UserKnownHostsFile configuration.
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return nil, errHostKeyInspection
		}
		body, readErr := io.ReadAll(io.LimitReader(f, knownHostsReadCap+1))
		closeErr := f.Close()
		if readErr != nil || closeErr != nil || len(body) > knownHostsReadCap {
			return nil, errHostKeyInspection
		}
		for _, rawLine := range bytes.Split(body, []byte{'\n'}) {
			id, key, ok := knownHostKey(string(rawLine), lookup)
			if ok {
				keys[id] = key
			}
		}
	}
	return keys, nil
}

func knownHostKey(line, lookup string) (string, HostKey, bool) {
	fields := strings.Fields(line)
	if len(fields) == 0 || strings.HasPrefix(fields[0], "#") {
		return "", HostKey{}, false
	}
	hostIndex := 0
	if strings.HasPrefix(fields[0], "@") {
		hostIndex = 1
	}
	if len(fields) <= hostIndex+2 || !knownHostListMatches(fields[hostIndex], lookup) {
		return "", HostKey{}, false
	}
	algorithm, encoded := fields[hostIndex+1], fields[hostIndex+2]
	if !safeHostKeyAlgorithm(algorithm) {
		return "", HostKey{}, false
	}
	keyBytes, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		keyBytes, err = base64.RawStdEncoding.DecodeString(encoded)
	}
	if err != nil {
		return "", HostKey{}, false
	}
	sum := sha256.Sum256(keyBytes)
	key := HostKey{
		Algorithm:   algorithm,
		Fingerprint: "SHA256:" + base64.RawStdEncoding.EncodeToString(sum[:]),
	}
	return algorithm + "\x00" + encoded, key, true
}

func knownHostListMatches(hosts, lookup string) bool {
	for _, host := range strings.Split(hosts, ",") {
		if strings.EqualFold(host, lookup) || hashedKnownHostMatches(host, lookup) {
			return true
		}
	}
	return false
}

func hashedKnownHostMatches(host, lookup string) bool {
	parts := strings.Split(host, "|")
	if len(parts) != 4 || parts[0] != "" || parts[1] != "1" {
		return false
	}
	salt, err := base64.StdEncoding.DecodeString(parts[2])
	if err != nil {
		return false
	}
	want, err := base64.StdEncoding.DecodeString(parts[3])
	if err != nil {
		return false
	}
	mac := hmac.New(sha1.New, salt)
	_, _ = mac.Write([]byte(lookup))
	return hmac.Equal(mac.Sum(nil), want)
}

func safeHostKeyAlgorithm(algorithm string) bool {
	if len(algorithm) == 0 || len(algorithm) > 128 {
		return false
	}
	for _, c := range []byte(algorithm) {
		if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') || strings.ContainsRune("@._+-", rune(c)) {
			continue
		}
		return false
	}
	return true
}

// Exec runs command on host. ssh reserves exit 255 for its own transport
// failures (connection refused, auth failure, DNS) — never the remote
// command's, since a well-behaved remote command that itself wants to
// exit 255 is indistinguishable at this layer and treated the same way
// ps_ssh.py treats it: as a transport failure, not an honest exit. The
// local ssh process failing to start at all (missing binary, exec
// permission denied — see run's execStartFailedRC) or ending without a
// clean local process exit is reported the same way, since there is no
// remote exit code to speak of either. Any other exit code, including 0,
// is the remote command's own honest status. A context deadline exceeded
// before the process finished maps to TransportError{"timeout"} regardless
// of rc.
func (tr *OpenSSH) Exec(host, command string, stdin []byte, timeout time.Duration) (Result, error) {
	if err := tr.prepareAcceptedHostKey(host); err != nil {
		return Result{}, newTransportError("ssh", "host key inspection failed")
	}
	argv := tr.sshArgv(host, command)
	rc, out, timedOut := tr.Runner(argv, stdin, timeout)
	tr.observeAcceptedHostKey(host)

	if timedOut {
		return Result{}, newTransportError("timeout", "operation timed out")
	}
	if rc == execStartFailedRC {
		return Result{}, newTransportError("ssh", "ssh process failed to start")
	}
	if rc == 255 {
		return Result{}, NewTransportError("ssh", out)
	}

	// The injectable Runner predates runner.Result and reports truncation by
	// returning more than the configured cap. The production runner itself
	// retains exactly cap bytes; preserve this compatibility for test runners.
	streamCap := max(tr.streamCap, 0)
	truncated := false
	if int64(len(out)) > streamCap {
		truncated = true
		out = out[:streamCap]
	}
	if rc < 0 && !truncated {
		return Result{}, NewTransportError("ssh", out)
	}

	return Result{
		ExitCode:  rc,
		Output:    out,
		Truncated: truncated,
	}, nil
}

// ExecStream is Exec with live delivery of the remote combined stream. SSH's
// own diagnostics are diverted to a private log so they cannot enter events.
func (tr *OpenSSH) ExecStream(host, command string, stdin []byte, timeout time.Duration, output func([]byte)) (Result, error) {
	if err := tr.prepareAcceptedHostKey(host); err != nil {
		return Result{}, newTransportError("ssh", "host key inspection failed")
	}
	logDir, err := os.MkdirTemp("", "sshai-ssh-*")
	if err != nil {
		return Result{}, newTransportError("ssh", "ssh diagnostics unavailable")
	}
	defer os.Remove(logDir)
	logName := filepath.Join(logDir, "diagnostic.log")
	log, err := os.OpenFile(logName, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o600) // #nosec G304 -- logName is fixed beneath a fresh private temp directory.
	if err != nil {
		return Result{}, newTransportError("ssh", "ssh diagnostics unavailable")
	}
	if err := log.Close(); err != nil {
		_ = os.Remove(logName)
		return Result{}, newTransportError("ssh", "ssh diagnostics unavailable")
	}
	defer os.Remove(logName)
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	argv := tr.sshArgv(host, command)
	argv = append([]string{argv[0], "-E", logName}, argv[1:]...)
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...) // #nosec G204 -- argv is built for the fixed system ssh executable without a shell.
	cmd.Stdin = bytes.NewReader(stdin)
	w := newObservingWriter(newStreamCapWriter(tr.streamCap, cancel), output)
	// Identical writers make os/exec retain one pipe and its actual byte order.
	cmd.Stdout, cmd.Stderr = w, w
	_ = cmd.Run()
	tr.observeAcceptedHostKey(host)
	if ctx.Err() == context.DeadlineExceeded {
		return Result{}, newTransportError("timeout", "operation timed out")
	}
	rc := execStartFailedRC
	if cmd.ProcessState != nil {
		rc = cmd.ProcessState.ExitCode()
	}
	if rc == execStartFailedRC {
		return Result{}, newTransportError("ssh", "ssh process failed to start")
	}
	if rc == 255 {
		diagnostic, err := boundedSSHLog(logName)
		if err != nil {
			return Result{}, newTransportError("ssh", "ssh diagnostics unavailable")
		}
		return Result{}, NewTransportError("ssh", diagnostic)
	}
	out, truncated := w.Bytes()
	if rc < 0 && !truncated {
		diagnostic, err := boundedSSHLog(logName)
		if err != nil {
			return Result{}, newTransportError("ssh", "ssh diagnostics unavailable")
		}
		return Result{}, NewTransportError("ssh", diagnostic)
	}
	return Result{ExitCode: rc, Output: out, Truncated: truncated}, nil
}

const sshDiagnosticLogCap = 64 << 10

var errStreamCapExceeded = errors.New("transport: stream cap exceeded")

func boundedSSHLog(path string) ([]byte, error) {
	f, err := os.Open(path) // #nosec G304 -- caller passes the fixed log path created in ExecStream's private temp directory.
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return io.ReadAll(io.LimitReader(f, sshDiagnosticLogCap))
}

type streamCapWriter struct {
	mu     sync.Mutex
	max    int64
	buf    []byte
	killed bool
	cancel context.CancelFunc
}

func newStreamCapWriter(max int64, cancel context.CancelFunc) *streamCapWriter {
	return &streamCapWriter{max: max, cancel: cancel}
}

// Write retains at most max bytes and uses a private overflow sentinel only to
// cancel the process. The callback receives no sentinel byte.
func (w *streamCapWriter) Write(p []byte) (int, []byte, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(p) == 0 || w.killed {
		if w.killed {
			return 0, nil, errStreamCapExceeded
		}
		return 0, nil, nil
	}
	room := w.max - int64(len(w.buf))
	if room < 0 {
		room = 0
	}
	take := int64(len(p))
	if take > room {
		take = room
	}
	if take > 0 {
		w.buf = append(w.buf, p[:take]...)
	}
	if int64(len(p)) > take {
		w.killed = true
		if w.cancel != nil {
			w.cancel()
		}
		return int(take), append([]byte(nil), p[:take]...), errStreamCapExceeded
	}
	return int(take), append([]byte(nil), p[:take]...), nil
}

func (w *streamCapWriter) Bytes() ([]byte, bool) {
	w.mu.Lock()
	defer w.mu.Unlock()
	return append([]byte(nil), w.buf...), w.killed
}

type observingWriter struct {
	w      *streamCapWriter
	output func([]byte)
}

func newObservingWriter(w *streamCapWriter, output func([]byte)) *observingWriter {
	return &observingWriter{w: w, output: output}
}

func (w *observingWriter) Write(p []byte) (int, error) {
	n, observed, err := w.w.Write(p)
	if len(observed) > 0 && w.output != nil {
		w.output(observed)
	}
	return n, err
}

func (w *observingWriter) Bytes() ([]byte, bool) { return w.w.Bytes() }

// Put copies the local file at localPath to remotePath on host via scp.
// Unlike ssh's Exec discrimination, scp's exit 255 is not special-cased
// here — per the ported semantics, any non-zero rc from scp (255
// included) is reported as TransportError{"scp"}.
func (tr *OpenSSH) Put(host, localPath, remotePath string) error {
	if err := tr.prepareAcceptedHostKey(host); err != nil {
		return newTransportError("scp", "host key inspection failed")
	}
	argv := tr.scpArgv(host, localPath, remotePath)
	rc, out, timedOut := tr.Runner(argv, nil, defaultPutTimeout)
	tr.observeAcceptedHostKey(host)

	if timedOut {
		return newTransportError("timeout", "operation timed out")
	}
	if rc == execStartFailedRC {
		return newTransportError("scp", "scp process failed to start")
	}
	if rc < 0 {
		return NewTransportError("scp", out)
	}
	if rc != 0 {
		return NewTransportError("scp", out)
	}
	return nil
}

// run is the default Runner, backed by os/exec. It feeds stdin to the
// child and captures combined stdout+stderr through tr.streamCap.
func (tr *OpenSSH) run(argv []string, stdin []byte, timeout time.Duration) (int, []byte, bool) {
	result := runner.Run(argv, stdin, timeout, tr.streamCap)
	if result.StartErr != nil {
		return execStartFailedRC, result.Output, result.TimedOut
	}
	if result.Truncated {
		// Runner's legacy function shape has no truncation result. Preserve it
		// for OpenSSH callers with a private sentinel which Exec strips.
		return result.ExitCode, append(result.Output, 0), result.TimedOut
	}
	return result.ExitCode, result.Output, result.TimedOut
}
