// internal/transport/openssh_test.go
package transport

import (
	"crypto/hmac"
	"crypto/sha1"
	"crypto/sha256"
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func fake(rc int, out string, timedOut bool) func([]string, []byte, time.Duration) (int, []byte, bool) {
	return func([]string, []byte, time.Duration) (int, []byte, bool) { return rc, []byte(out), timedOut }
}

func argvContains(argv []string, want string) bool {
	for _, arg := range argv {
		if arg == want {
			return true
		}
	}
	return false
}

func withControlMasterSupport(t *testing.T, supported bool) {
	t.Helper()
	original := openSSHControlMasterSupported
	openSSHControlMasterSupported = supported
	t.Cleanup(func() { openSSHControlMasterSupported = original })
}

func setTransportTestUserHome(t *testing.T, home string) {
	t.Helper()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	if runtime.GOOS == "windows" {
		volume := filepath.VolumeName(home)
		if volume != "" {
			t.Setenv("HOMEDRIVE", volume)
			t.Setenv("HOMEPATH", strings.TrimPrefix(home, volume))
		}
	}
}

func TestExecMirrorsRemoteExit(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(3, "boom\n", false)
	res, err := tr.Exec("h1", "false", nil, time.Minute)
	if err != nil || res.ExitCode != 3 || string(res.Output) != "boom\n" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestExec255IsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(255, "ssh: connect to host private.example port 22: Connection refused", false)
	_, err := tr.Exec("h1", "true", nil, time.Minute)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" || te.Diagnostic() != "connection refused" {
		t.Fatalf("want safe ssh connection-refused diagnostic, got %v", err)
	}
	if strings.Contains(te.Error(), "private.example") {
		t.Fatalf("raw hostname leaked through error: %q", te.Error())
	}
}

func TestTransportDiagnosticsAreCanonicalAndSafe(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		want string
	}{
		{"host key", "WARNING SHA256:TOPSECRET\nHost key verification failed.", "host key verification failed"},
		{"host key algorithm", "Unable to negotiate with private.example: no matching host key type found. Their offer: ssh-rsa TOPSECRET", "no matching host key type"},
		{"timeout", "ssh: connect to host private.example port 22: Connection timed out TOPSECRET", "connection timed out"},
		{"unknown", "debug1: identity file /private/TOPSECRET type 0", ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			te := NewTransportError("ssh", []byte(tc.raw))
			if got := te.Diagnostic(); got != tc.want {
				t.Fatalf("Diagnostic()=%q, want %q", got, tc.want)
			}
			if strings.Contains(te.Error(), "TOPSECRET") || strings.Contains(te.Error(), "private.example") {
				t.Fatalf("raw SSH detail leaked through error: %q", te.Error())
			}
		})
	}
}

func TestExecTimeout(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(0, "", true)
	_, err := tr.Exec("h1", "sleep 999", nil, time.Millisecond)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "timeout" || te.Diagnostic() != "operation timed out" {
		t.Fatalf("want timeout diagnostic, got %v", err)
	}
}

func TestArgvDiscipline(t *testing.T) {
	withControlMasterSupport(t, true)
	tr := NewOpenSSH("/tmp/cm", "15m", 1<<20, OpenSSHOptions{})
	var captured []string
	tr.Runner = func(argv []string, _ []byte, _ time.Duration) (int, []byte, bool) {
		captured = argv
		return 0, nil, false
	}
	tr.Exec("h1", "df -h", nil, time.Minute)
	// Every option is its own element; no element may contain two options glued together.
	for _, a := range captured {
		if a != "-o" && len(a) > 2 && a[0] == '-' && a[1] == 'o' {
			t.Fatalf("glued option: %q in %q", a, captured)
		}
	}
	if captured[0] != "ssh" || captured[len(captured)-2] != "h1" || captured[len(captured)-1] != "df -h" {
		t.Fatalf("argv shape: %q", captured)
	}
	if argvContains(captured, "StrictHostKeyChecking=accept-new") || argvContains(captured, "ProxyJump=none") {
		t.Fatalf("default argv must preserve ssh_config host-key and route behavior: %q", captured)
	}
}

func TestControlMasterOptionsOmittedWhenUnsupported(t *testing.T) {
	withControlMasterSupport(t, false)
	tr := NewOpenSSH("/tmp/cm", "15m", 1<<20, OpenSSHOptions{})
	var captured []string
	tr.Runner = func(argv []string, _ []byte, _ time.Duration) (int, []byte, bool) {
		captured = argv
		return 0, nil, false
	}

	if _, err := tr.Exec("h1", "true", nil, time.Minute); err != nil {
		t.Fatal(err)
	}
	for _, forbidden := range []string{"ControlMaster=auto", "ControlPath=/tmp/cm/%C", "ControlPersist=15m"} {
		if argvContains(captured, forbidden) {
			t.Fatalf("unsupported ControlMaster option leaked into argv: %q", captured)
		}
	}
	for _, want := range []string{"BatchMode=yes", "ConnectTimeout=10", "LogLevel=ERROR"} {
		if !argvContains(captured, want) {
			t.Fatalf("base ssh option %q missing: %q", want, captured)
		}
	}
}

func TestInvocationOverridesAreScopedToConfiguredAlias(t *testing.T) {
	withControlMasterSupport(t, true)
	tr := NewOpenSSH("/tmp/cm", "15m", 1<<20, OpenSSHOptions{
		AcceptNewHostKey: "new-host",
		ProxyJumpNone:    true,
	})
	accepted := HostKey{Algorithm: "ssh-ed25519", Fingerprint: "SHA256:accepted"}
	lookupCalls := 0
	tr.hostKeyLookup = func(host string, _ []string) (map[string]HostKey, error) {
		lookupCalls++
		if host != "new-host" {
			t.Fatalf("host-key lookup called for unscoped host %q", host)
		}
		if lookupCalls == 1 {
			return map[string]HostKey{}, nil
		}
		return map[string]HostKey{"new": accepted}, nil
	}
	var captured [][]string
	tr.Runner = func(argv []string, _ []byte, _ time.Duration) (int, []byte, bool) {
		captured = append(captured, append([]string(nil), argv...))
		return 0, nil, false
	}

	if _, err := tr.Exec("known-host", "true", nil, time.Minute); err != nil {
		t.Fatal(err)
	}
	if _, err := tr.Exec("new-host", "true", nil, time.Minute); err != nil {
		t.Fatal(err)
	}
	if len(captured) != 2 {
		t.Fatalf("captured %d argv sets, want 2", len(captured))
	}
	if !argvContains(captured[0], "ProxyJump=none") ||
		argvContains(captured[0], "StrictHostKeyChecking=accept-new") {
		t.Fatalf("unscoped host argv=%q", captured[0])
	}
	for _, want := range []string{
		"ProxyJump=none",
		"StrictHostKeyChecking=accept-new",
		"UpdateHostKeys=no",
	} {
		if !argvContains(captured[1], want) {
			t.Fatalf("scoped host argv missing %q: %q", want, captured[1])
		}
	}
	got, ok, err := tr.AcceptedHostKey("new-host")
	if err != nil || !ok || got != accepted {
		t.Fatalf("AcceptedHostKey=(%+v,%v,%v), want %+v", got, ok, err, accepted)
	}
	if _, ok, err := tr.AcceptedHostKey("known-host"); err != nil || ok {
		t.Fatalf("unscoped AcceptedHostKey ok=%v err=%v", ok, err)
	}
}

func TestInvocationOverridesApplyToSCP(t *testing.T) {
	withControlMasterSupport(t, true)
	tr := NewOpenSSH("/tmp/cm", "15m", 1<<20, OpenSSHOptions{
		AcceptNewHostKey: "win01",
		ProxyJumpNone:    true,
	})
	lookupCalls := 0
	tr.hostKeyLookup = func(string, []string) (map[string]HostKey, error) {
		lookupCalls++
		if lookupCalls == 1 {
			return map[string]HostKey{}, nil
		}
		return map[string]HostKey{
			"new": {Algorithm: "ssh-ed25519", Fingerprint: "SHA256:accepted"},
		}, nil
	}
	var captured []string
	tr.Runner = func(argv []string, _ []byte, _ time.Duration) (int, []byte, bool) {
		captured = append([]string(nil), argv...)
		return 0, nil, false
	}
	if err := tr.Put("win01", "/local/script.ps1", "/remote/script.ps1"); err != nil {
		t.Fatal(err)
	}
	if captured[0] != "scp" {
		t.Fatalf("argv=%q, want scp", captured)
	}
	for _, want := range []string{"ProxyJump=none", "StrictHostKeyChecking=accept-new", "UpdateHostKeys=no"} {
		if !argvContains(captured, want) {
			t.Fatalf("scp argv missing %q: %q", want, captured)
		}
	}
}

func TestReadKnownHostKeysMatchesPlainAndHashedAliases(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "known_hosts")
	plainBlob := base64.StdEncoding.EncodeToString([]byte("plain-key"))
	hashedBlob := base64.StdEncoding.EncodeToString([]byte("hashed-key"))
	salt := []byte("01234567890123456789")
	mac := hmac.New(sha1.New, salt)
	_, _ = mac.Write([]byte("[private.example]:2222"))
	hashedAlias := "|1|" + base64.StdEncoding.EncodeToString(salt) + "|" +
		base64.StdEncoding.EncodeToString(mac.Sum(nil))
	body := "plain.example ssh-ed25519 " + plainBlob + "\n" +
		hashedAlias + " ssh-rsa " + hashedBlob + "\n"
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}

	plain, err := readKnownHostKeys("plain.example", []string{path})
	if err != nil || len(plain) != 1 {
		t.Fatalf("plain keys=%v err=%v", plain, err)
	}
	hashed, err := readKnownHostKeys("[private.example]:2222", []string{path})
	if err != nil || len(hashed) != 1 {
		t.Fatalf("hashed keys=%v err=%v", hashed, err)
	}
	wantSum := sha256.Sum256([]byte("hashed-key"))
	wantFingerprint := "SHA256:" + base64.RawStdEncoding.EncodeToString(wantSum[:])
	for _, key := range hashed {
		if key.Algorithm != "ssh-rsa" || key.Fingerprint != wantFingerprint {
			t.Fatalf("hashed key=%+v, want ssh-rsa %s", key, wantFingerprint)
		}
	}
}

func TestParseKnownHostsConfigUsesAliasAndNonDefaultPort(t *testing.T) {
	home := t.TempDir()
	setTransportTestUserHome(t, home)
	lookup, paths, err := parseKnownHostsConfig([]byte(
		"hostname private.example\nport 2222\nuserknownhostsfile ~/.ssh/known_hosts\n",
	))
	if err != nil || lookup != "[private.example]:2222" ||
		len(paths) != 1 || paths[0] != filepath.Join(home, ".ssh", "known_hosts") {
		t.Fatalf("lookup=%q paths=%q err=%v", lookup, paths, err)
	}

	lookup, _, err = parseKnownHostsConfig([]byte(
		"hostname private.example\nport 2222\nhostkeyalias exact-alias\nuserknownhostsfile /tmp/known_hosts\n",
	))
	if err != nil || lookup != "exact-alias" {
		t.Fatalf("hostkeyalias lookup=%q err=%v", lookup, err)
	}
}

// TestPutNonZeroIsTransportError covers Put's discrimination rule, which
// (unlike Exec's) does not special-case 255: any non-zero rc from scp is
// reported as TransportError{"scp"}.
func TestPutNonZeroIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(1, "scp: No such file or directory", false)
	err := tr.Put("h1", "/local/script.sh", "/remote/script.sh")
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "scp" {
		t.Fatalf("want TransportError{scp}, got %v", err)
	}
}

// --- Exec's truncation derivation from capWriter's sentinel-inclusive
// output (task-7 review finding 1, consumer side). ---

func TestObservingWriterStreamsCombinedRemoteOrderAndHidesSentinel(t *testing.T) {
	cap := newStreamCapWriter(5, nil)
	var observed []byte
	w := newObservingWriter(cap, func(p []byte) { observed = append(observed, p...) })
	if _, err := w.Write([]byte("out")); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write([]byte("err")); err == nil {
		t.Fatal("shared cap overflow must cancel")
	}
	if got := string(observed); got != "outer" {
		t.Fatalf("callback included overflow sentinel or lost order: %q", got)
	}
	captured, truncated := w.Bytes()
	if !truncated || string(captured) != "outer" {
		t.Fatalf("capture=%q truncated=%v", captured, truncated)
	}
}

func TestExecStreamUsesUnifiedRemotePipeAndPrivateDiagnosticLog(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake ssh fixture")
	}

	dir := t.TempDir()
	ssh := filepath.Join(dir, "ssh")
	writeSSH := func(body string) {
		t.Helper()
		if err := os.WriteFile(ssh, []byte("#!/bin/sh\n"+body), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))

	t.Run("remote streams", func(t *testing.T) {
		writeSSH("printf out\nprintf err >&2\nexit 0\n")
		tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{})
		var observed []byte
		res, err := tr.ExecStream("h", "true", nil, time.Second, func(p []byte) { observed = append(observed, p...) })
		if err != nil || string(res.Output) != "outerr" || string(observed) != "outerr" {
			t.Fatalf("res=%+v observed=%q err=%v", res, observed, err)
		}
	})

	t.Run("transport diagnostic", func(t *testing.T) {
		trace := filepath.Join(t.TempDir(), "trace")
		t.Setenv("SSHAI_TEST_TRACE", trace)
		writeSSH("while [ $# -gt 0 ]; do\n  if [ \"$1\" = -E ]; then\n    printf %s \"$2\" >\"$SSHAI_TEST_TRACE\"\n    printf 'Permission denied for secret-host.example\\n' >\"$2\"\n    exit 255\n  fi\n  shift\ndone\nexit 255\n")
		tr := NewOpenSSH(t.TempDir(), "15m", 64, OpenSSHOptions{})
		var observed []byte
		_, err := tr.ExecStream("h", "true", nil, time.Second, func(p []byte) { observed = append(observed, p...) })
		var te *TransportError
		if !errors.As(err, &te) || te.Diagnostic() != "permission denied" || len(observed) != 0 || strings.Contains(te.Error(), "secret-host") {
			t.Fatalf("err=%v observed=%q", err, observed)
		}
		logPath, readErr := os.ReadFile(trace)
		if readErr != nil {
			t.Fatal(readErr)
		}
		if _, statErr := os.Stat(string(logPath)); !os.IsNotExist(statErr) {
			t.Fatalf("diagnostic log remains: %q err=%v", logPath, statErr)
		}
	})
}

func TestExecExactCapOutputNotTruncated(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 5, OpenSSHOptions{}) // streamCap=5
	tr.Runner = fake(0, "hello", false)                       // exactly 5 bytes
	res, err := tr.Exec("h1", "echo -n hello", nil, time.Minute)
	if err != nil || res.Truncated || string(res.Output) != "hello" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestExecZeroCapOutputTruncated(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 0, OpenSSHOptions{})
	tr.Runner = fake(0, "x", false)
	res, err := tr.Exec("h1", "printf x", nil, time.Minute)
	if err != nil || !res.Truncated || len(res.Output) != 0 {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

func TestExecOverCapOutputTruncated(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 5, OpenSSHOptions{}) // streamCap=5
	tr.Runner = fake(0, "hello world", false)
	res, err := tr.Exec("h1", "echo -n hello world", nil, time.Minute)
	if err != nil || !res.Truncated || string(res.Output) != "hello" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
}

// --- Local exec-start failure (task-7 review finding 2): a missing
// binary or a denied exec permission must surface as a TransportError,
// never as a fabricated honest exit. ---

// TestRunSurfacesStartFailure drives the default Runner (run) directly
// with a nonexistent binary path — no network involved, just a local
// exec(2) failure — and checks it reports execStartFailedRC rather than
// some ordinary-looking exit code.
func TestRunSurfacesStartFailure(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	rc, out, timedOut := tr.run([]string{"/nonexistent/path/sshai-missing-binary", "x"}, nil, 5*time.Second)
	if rc != execStartFailedRC {
		t.Fatalf("rc=%d, want execStartFailedRC (%d)", rc, execStartFailedRC)
	}
	if timedOut {
		t.Fatal("a start failure must not be reported as a timeout")
	}
	if len(out) != 0 {
		t.Fatalf("out=%q, want empty — the child never ran", out)
	}
}

// TestExecStartFailureIsTransportError checks Exec's discrimination in
// isolation: given execStartFailedRC from the Runner (fake or real), it
// must produce TransportError{"ssh"}, the same as a real ssh exit 255.
func TestExecStartFailureIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(execStartFailedRC, "", false)
	_, err := tr.Exec("h1", "true", nil, time.Minute)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}

func TestExecNegativeLocalExitIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(-1, "Read from remote host h1: Unknown error", false)
	_, err := tr.Exec("h1", "true", nil, time.Minute)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}

func TestPutNegativeLocalExitIsTransportError(t *testing.T) {
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	tr.Runner = fake(-1, "Read from remote host h1: Unknown error", false)
	err := tr.Put("h1", "/local/script.sh", "/remote/script.sh")
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "scp" {
		t.Fatalf("want TransportError{scp}, got %v", err)
	}
}

// TestExecSurfacesLocalStartFailureAsTransportError closes the seam
// between the two tests above: it leaves the default Runner in place
// (never overrides tr.Runner) and forces a real local start failure by
// making "ssh" unresolvable on PATH, proving execStartFailedRC actually
// reaches Exec's discrimination end to end through the production code
// path — not just through the fake. Network-free: PATH is emptied before
// ssh would ever be invoked, so nothing is dialed.
func TestExecSurfacesLocalStartFailureAsTransportError(t *testing.T) {
	t.Setenv("PATH", t.TempDir()) // a directory with no "ssh" binary in it
	tr := NewOpenSSH(t.TempDir(), "15m", 1<<20, OpenSSHOptions{})
	_, err := tr.Exec("h1", "true", nil, 5*time.Second)
	var te *TransportError
	if !errors.As(err, &te) || te.Reason != "ssh" {
		t.Fatalf("want TransportError{ssh}, got %v", err)
	}
}
