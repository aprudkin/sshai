package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// Capture fixtures do not exercise fallback selection. Keep their socket
// directories short and explicitly cleaned so no fallback directory survives.
func testControlDir(t *testing.T) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		return t.TempDir()
	}
	dir, err := os.MkdirTemp("/tmp", "sshai-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(dir); err != nil {
			t.Errorf("remove test control directory: %v", err)
		}
	})
	return dir
}

func TestRunLongRootControlSocketKeepsArtifactRoot(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Unix control sockets")
	}
	ssh, err := exec.LookPath("ssh")
	if err != nil {
		t.Skip("system OpenSSH unavailable")
	}
	for _, long := range []bool{false, true} {
		name := "short"
		if long {
			name = "long"
		}
		t.Run(name, func(t *testing.T) {
			root, err := os.MkdirTemp("/tmp", "s38-")
			if err != nil {
				t.Fatal(err)
			}
			baseRoot := root
			t.Cleanup(func() { os.RemoveAll(baseRoot) })
			if long {
				root = filepath.Join(root, strings.Repeat("x", 90))
			}
			t.Setenv("SSHAI_ROOT", root)
			fixture := t.TempDir()
			trace := filepath.Join(fixture, "trace")
			t.Setenv("SSHAI_TEST_TRACE", trace)
			// Route only to a closed loopback port through the real local OpenSSH.
			// Record its ControlPath so this test can remove its own empty directory.
			script := "#!/bin/sh\nfor arg do\n case \"$arg\" in ControlPath=*) printf '%s' \"$arg\" >\"$SSHAI_TEST_TRACE\";; esac\ndone\nexec '" + strings.ReplaceAll(ssh, "'", "'\\''") + "' -F /dev/null -p 9 \"$@\"\n"
			if err := os.WriteFile(filepath.Join(fixture, "ssh"), []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", fixture+string(os.PathListSeparator)+os.Getenv("PATH"))
			var stdout, stderr bytes.Buffer
			rc := runWith(nil, []string{"--timeout", "3", "--result-format=json", "127.0.0.1", "--", "true"}, &stdout, &stderr)
			path, err := os.ReadFile(trace)
			if err != nil {
				t.Fatal(err)
			}
			dir := strings.TrimSuffix(strings.TrimPrefix(string(path), "ControlPath="), "/%C")
			if long {
				t.Cleanup(func() { os.Remove(dir) })
			}
			if rc != exitTransport {
				t.Fatalf("rc=%d stderr=%s stdout=%s", rc, stderr.String(), stdout.String())
			}
			var env struct {
				Runs []struct {
					ArtifactPath        string `json:"artifact_path"`
					TransportDiagnostic string `json:"transport_diagnostic"`
					FailurePhase        string `json:"failure_phase"`
					RemoteCompletion    string `json:"remote_completion"`
				} `json:"runs"`
			}
			if err := json.Unmarshal(stdout.Bytes(), &env); err != nil {
				t.Fatal(err)
			}
			if len(env.Runs) != 1 {
				t.Fatalf("runs=%+v", env.Runs)
			}
			result := env.Runs[0]
			if result.TransportDiagnostic != "connection refused" || result.FailurePhase != "probe" || result.RemoteCompletion != "not_started" {
				t.Fatalf("result=%+v", result)
			}
			if !strings.HasPrefix(result.ArtifactPath, filepath.Join(root, "art")+string(os.PathSeparator)) {
				t.Fatalf("artifact relocated: %s", result.ArtifactPath)
			}
			body, err := os.ReadFile(result.ArtifactPath)
			if err != nil || string(body) != "transport diagnostic: connection refused\n" {
				t.Fatalf("artifact=%q err=%v", body, err)
			}
			if len(dir)+1+40+17 >= 104 || (!long && dir != filepath.Join(root, "cm")) || (long && strings.HasPrefix(dir, root)) {
				t.Fatalf("invalid socket selection: %q", dir)
			}
		})
	}
}
