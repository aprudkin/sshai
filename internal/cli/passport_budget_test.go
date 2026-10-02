package cli

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/runner"
	"github.com/aprudkin/sshai/internal/transport"
)

// Only the remote transport is replaced; CLI parsing, epilogue parsing,
// persistence, fan-out budget allocation and rendering all remain real.
type passportBodyTr struct{ body string }

func (f passportBodyTr) Exec(_ string, cmd string, stdin []byte, _ time.Duration) (transport.Result, error) {
	if cmd != "bash -s" {
		return transport.Result{}, fmt.Errorf("unexpected command %q", cmd)
	}
	env := base64.StdEncoding.EncodeToString([]byte("PATH=/usr/bin\x00"))
	out := f.body + "\n" + sentinelFromStdin(stdin) + "\n/tmp\n" + env + "\n"
	return transport.Result{Output: []byte(out)}, nil
}

func (passportBodyTr) Put(_, _, _ string, _ time.Duration) error {
	return fmt.Errorf("unexpected upload")
}

// Catches a renderer bypass or a lost per-host allocation in either caller,
// and ensures clipping cannot masquerade as truncation of the saved stream.
func TestPassportBudgetAcrossExecutionModes(t *testing.T) {
	body := `{"data":"` + strings.Repeat("я日🙂", 3000) + `"}` + "\n"
	for _, tc := range []struct {
		name      string
		shell     string
		hosts     []string
		budget    int
		bodyBytes int
	}{
		{"local bash", "bash", nil, 300, 1200},
		{"local pwsh", "pwsh", nil, 300, 1200},
		{"remote", "", []string{"h1"}, 300, 1200},
		{"divided fanout", "", []string{"h1", "h2", "h3"}, 1000, 1332},
		{"fanout floor", "", []string{"h1", "h2", "h3"}, 150, 400},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			args := []string{"--budget", strconv.Itoa(tc.budget)}
			var out, errOut bytes.Buffer
			var rc int
			wantHosts := tc.hosts
			if tc.shell != "" {
				args = append(args, "--shell", tc.shell, "--", "echo", "synthetic")
				rc = localWithRunner(func(argv []string, stdin []byte, _ time.Duration, _ int64) runner.Result {
					return successfulLocalResult(t, argv, stdin, "/tmp", map[string]string{}, body, 0)
				}, args, &out, &errOut)
				wantHosts = []string{"local-" + tc.shell}
			} else {
				seedLinuxFacts(t, root, tc.hosts...)
				args = append(args, tc.hosts...)
				args = append(args, "--", "echo", "synthetic")
				rc = runWith(passportBodyTr{body}, args, &out, &errOut)
			}
			if rc != 0 || errOut.Len() != 0 {
				t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
			}
			store, err := artifact.OpenStore(root)
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			text := out.String()
			if len(tc.hosts) > 1 {
				var summary string
				text, summary, _ = strings.Cut(text, "\nhosts=")
				if summary != "3 ok=3 failed=0 transport-errors=0\n" {
					t.Fatal("missing successful fan-out summary")
				}
			}
			passports := strings.Split(text, "\n\n")
			if len(passports) != len(wantHosts) {
				t.Fatalf("got %d passports, want %d", len(passports), len(wantHosts))
			}
			for i, passport := range passports {
				id := localArtifactID(t, passport)
				m, path, err := store.Get(id)
				if err != nil {
					t.Fatal(err)
				}
				retained, err := os.ReadFile(path)
				if err != nil || string(retained) != body {
					t.Fatalf("retained artifact changed: bytes=%d err=%v", len(retained), err)
				}
				if m.Host != wantHosts[i] || m.Bytes != int64(len(body)) || m.Lines != 1 ||
					m.SHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(body))) || m.Truncated || m.Binary {
					t.Fatalf("artifact metadata changed: %+v", m)
				}
				parts := strings.SplitN(passport, "\n", 3)
				if len(parts) != 3 || parts[0] != artifact.StatusLine(m) || parts[1] != "file="+path {
					t.Fatal("passport no longer identifies retained artifact")
				}
				if len(parts[2]) > tc.bodyBytes+81 || !utf8.ValidString(passport) {
					t.Errorf("preview/framing=%d bytes, want <=%d and valid UTF-8", len(parts[2]), tc.bodyBytes+81)
				}
				if !strings.Contains(parts[2], "preview omitted") || !strings.Contains(parts[2], "sshai q <id> -- <tool> <args>") || strings.Contains(parts[0], "truncated=1") {
					t.Error("missing omission/query guidance or false capture truncation")
				}
				// Follow the advertised query route, not a renderer helper.
				out.Reset()
				errOut.Reset()
				if rc := Q([]string{id, "--", "wc", "-c"}, &out, &errOut); rc != 0 {
					t.Fatalf("query rc=%d stderr=%q", rc, errOut.String())
				}
				fields := strings.Fields(out.String())
				if len(fields) == 0 || fields[0] != strconv.Itoa(len(body)) {
					t.Fatalf("query did not see complete retained artifact: %q", out.String())
				}
			}
		})
	}
}

// A synthetic one-line command through the real local runner reproduces the
// reported defect without any SSH access or pre-existing user state.
func TestLocalLargeLinePassportRuntime(t *testing.T) {
	for _, tool := range []string{"bash", "tr"} {
		if _, err := exec.LookPath(tool); err != nil {
			t.Skipf("%s is unavailable", tool)
		}
	}
	t.Setenv("SSHAI_ROOT", t.TempDir())
	var out, errOut bytes.Buffer
	rc := Local([]string{"--shell", "bash", "--budget", "300", "--", "printf '%200000s' '' | tr ' ' x"}, &out, &errOut)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
	}
	match := regexp.MustCompile(`(?m)^file=(.+)$`).FindStringSubmatch(out.String())
	if match == nil {
		t.Fatal("missing artifact path")
	}
	retained, err := os.ReadFile(match[1])
	if err != nil || !bytes.Equal(retained, bytes.Repeat([]byte("x"), 200000)) {
		t.Fatalf("retained bytes=%d err=%v", len(retained), err)
	}
	parts := strings.SplitN(out.String(), "\n", 3)
	if len(parts) != 3 || len(parts[2]) > 1281 {
		t.Fatalf("large-line passport still exceeds body/framing budget: total=%d bytes", out.Len())
	}
}
