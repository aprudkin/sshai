package transport

import (
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func TestLongRootControlSocketReachesTCP(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	if _, err := exec.LookPath("ssh"); err != nil {
		t.Skip("system OpenSSH unavailable")
	}
	root := filepath.Join(t.TempDir(), strings.Repeat("x", 90))
	tr := NewOpenSSH(filepath.Join(root, "cm"), "15m", 4096, OpenSSHOptions{})
	// Isolate system OpenSSH from user configuration; a closed loopback port
	// needs neither credentials nor a host-key handshake or remote command.
	tr.Runner = func(argv []string, stdin []byte, timeout time.Duration) (int, []byte, bool) {
		argv = append([]string{argv[0], "-F", os.DevNull, "-p", "9"}, argv[1:]...)
		return tr.run(argv, stdin, timeout)
	}
	_, err := tr.Exec("127.0.0.1", "true", nil, 3*time.Second)
	var te *TransportError
	if !errors.As(err, &te) || te.Diagnostic() != "connection refused" {
		t.Fatalf("long-root connection should reach TCP: %v", err)
	}
	if strings.HasPrefix(tr.controlDir, root) {
		t.Fatalf("long socket directory retained: %q", tr.controlDir)
	}
	t.Cleanup(func() { os.Remove(tr.controlDir) })
	// Exercise the longest listener shape locally, without any SSH server.
	listenerPath := filepath.Join(tr.controlDir, strings.Repeat("c", 40)+"."+strings.Repeat("r", 16))
	listener, err := net.Listen("unix", listenerPath)
	if err != nil {
		t.Fatalf("temporary control listener cannot bind: %v", err)
	}
	if err := listener.Close(); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(tr.controlDir)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		t.Fatalf("control directory must be private: %v %v", info, err)
	}
	same := NewOpenSSH(filepath.Join(root, "cm"), "15m", 4096, OpenSSHOptions{})
	other := NewOpenSSH(filepath.Join(root, "other", "cm"), "15m", 4096, OpenSSHOptions{})
	if same.controlDir != tr.controlDir || other.controlDir == tr.controlDir {
		t.Fatal("control directories must be stable and root-scoped")
	}
	for _, argv := range [][]string{tr.sshArgv("h", "true"), tr.scpArgv("h", "/local", "/remote")} {
		if !argvContains(argv, "ControlPath="+tr.controlDir+"/%C") || !argvContains(argv, "ControlMaster=auto") || argvContains(argv, "ProxyJump=none") || argvContains(argv, "StrictHostKeyChecking=accept-new") {
			t.Fatalf("socket fallback changed transport policy: %q", argv)
		}
	}
}

func TestControlSocketPathBudget(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	withControlMasterSupport(t, true)
	for _, tc := range []struct {
		name, dir string
		retain    bool
	}{
		{"ordinary", "/tmp/cm", true},
		{"boundary", "/tmp/" + strings.Repeat("x", 40), true},
		{"temporary listener overflow", "/tmp/" + strings.Repeat("x", 41), false},
		{"utf8 bytes", "/tmp/" + strings.Repeat("é", 21), false},
		{"percent expansion", "/tmp/%C", false},
		{"environment expansion", "/tmp/${HOME}", false},
		{"quoting", "/tmp/space dir", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tr := NewOpenSSH(tc.dir, "15m", 4096, OpenSSHOptions{})
			if (tr.controlDir == tc.dir) != tc.retain {
				t.Fatalf("selected %q from %q", tr.controlDir, tc.dir)
			}
			// %C expands to 40 bytes; OpenSSH adds . plus 16 bytes while binding.
			if len(tr.controlDir)+1+40+17 >= 104 {
				t.Fatalf("listener path cannot fit: %q", tr.controlDir)
			}
		})
	}
}

func TestControlSocketSetupFailureIsSafe(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	for _, fixture := range []string{"file", "symlink", "public directory"} {
		t.Run(fixture, func(t *testing.T) {
			root := filepath.Join(t.TempDir(), strings.Repeat("x", 90))
			tr := NewOpenSSH(filepath.Join(root, "cm"), "15m", 4096, OpenSSHOptions{})
			if err := os.MkdirAll(filepath.Dir(tr.controlDir), 0700); err != nil {
				t.Fatal(err)
			}
			switch fixture {
			case "file":
				if err := os.WriteFile(tr.controlDir, nil, 0600); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Symlink(t.TempDir(), tr.controlDir); err != nil {
					t.Fatal(err)
				}
			default:
				if err := os.Mkdir(tr.controlDir, 0700); err != nil {
					t.Fatal(err)
				}
				if err := os.Chmod(tr.controlDir, 0755); err != nil {
					t.Fatal(err)
				}
			}
			t.Cleanup(func() { os.Remove(tr.controlDir) })
			check := func(err error, reason string) {
				t.Helper()
				var te *TransportError
				if !errors.As(err, &te) || te.Reason != reason || te.Diagnostic() != "control socket unavailable" || strings.Contains(te.Error(), tr.controlDir) {
					t.Fatalf("unsafe/unclassified setup error: %v", err)
				}
			}
			_, err := tr.Exec("unused", "true", nil, time.Second)
			check(err, "ssh")
			check(tr.Put("unused", "/unused", "/unused", time.Second), "scp")
			_, err = tr.ExecStream("unused", "true", nil, time.Second, func([]byte) { t.Error("setup error leaked to preview") })
			check(err, "ssh")
		})
	}
}

// Test fixture cleanup must cover the selected socket directory, which can
// live outside t.TempDir. A missing child still prepares that directory.
func TestControlSocketFixtureCleanup(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	var selected string
	t.Run("fixture", func(t *testing.T) {
		tr := newTestOpenSSH(t, "15m", 64, OpenSSHOptions{})
		selected = tr.controlDir
		_, _, _ = tr.run([]string{filepath.Join(t.TempDir(), "missing")}, nil, time.Second)
		if _, err := os.Stat(selected); err != nil {
			t.Fatal(err)
		}
	})
	// Remove this test's directory even on the unfixed implementation.
	t.Cleanup(func() { os.Remove(selected) })
	if _, err := os.Stat(selected); !os.IsNotExist(err) {
		t.Fatalf("test-owned socket directory survived fixture cleanup: %v", err)
	}
}

func TestControlSocketTooLongDiagnosticIsCanonical(t *testing.T) {
	te := NewTransportError("ssh", []byte("ControlPath too long ('/private/TOPSECRET/socket' >= 104 bytes)"))
	if te.Diagnostic() != "control socket path too long" || strings.Contains(te.Error(), "TOPSECRET") {
		t.Fatalf("diagnostic: %v", te)
	}
}
