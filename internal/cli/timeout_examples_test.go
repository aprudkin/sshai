package cli

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
	"github.com/aprudkin/sshai/internal/transport"
)

// Model a remote check's required time without sleeping for minutes or using SSH.
// The real CLI still parses the example, chooses its deadline, saves evidence,
// and renders both the passport and follow completion event.
type exampleDeadlineTr struct {
	followTr
	required time.Duration
	budget   time.Duration
}

func (f *exampleDeadlineTr) ExecStream(host, command string, stdin []byte, timeout time.Duration, out func([]byte)) (transport.Result, error) {
	f.budget = timeout
	if timeout < f.required {
		return transport.Result{}, &transport.TransportError{Reason: "timeout"}
	}
	return f.followTr.ExecStream(host, command, stdin, timeout, out)
}

// Removing --timeout from a long-running example (or putting it after the host)
// must fail with a short configured default rather than silently demonstrating
// a command that cannot finish. Only simple, single-line examples are consumed.
func TestLongRunningExamples(t *testing.T) {
	var help, helpErr bytes.Buffer
	if rc := Help([]string{"run"}, &help, &helpErr); rc != 0 {
		t.Fatalf("help rc=%d stderr=%s", rc, &helpErr)
	}
	sources := map[string]string{"help run": help.String()}
	for _, path := range []string{"README.md", "docs/agent-usage.md", "skills/sshai/SKILL.md"} {
		body, err := os.ReadFile(filepath.Join("..", "..", path))
		if err != nil {
			t.Fatal(err)
		}
		sources[path] = string(body)
	}
	for name, source := range sources {
		t.Run(name, func(t *testing.T) {
			count := 0
			for _, line := range strings.Split(source, "\n") {
				line = strings.TrimSpace(line)
				if !strings.HasPrefix(line, "sshai run ") || !strings.Contains(line, "--follow") {
					continue
				}
				count++
				t.Run(fmt.Sprint(count), func(t *testing.T) {
					root := t.TempDir()
					t.Setenv("SSHAI_ROOT", root)
					if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("timeout_sec = 7\n"), 0o600); err != nil {
						t.Fatal(err)
					}
					// Replace only the documented host/command placeholders; the
					// CLI receives every published flag and its original ordering.
					line = strings.NewReplacer("<host>", "web01", "<command>", "long-running-check").Replace(line)
					args := strings.Fields(line)[2:]
					if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
						t.Fatal(err)
					}
					tr := &exampleDeadlineTr{required: 4 * time.Minute}
					var stdout, stderr bytes.Buffer
					if rc := runWith(tr, args, &stdout, &stderr); rc != 0 {
						t.Fatalf("example %q cannot complete a four-minute check: rc=%d budget=%s stdout=%s stderr=%s", line, rc, tr.budget, &stdout, &stderr)
					}
					store, err := artifact.OpenStore(root)
					if err != nil {
						t.Fatal(err)
					}
					defer store.Close()
					meta, path, err := store.Get("a1")
					if err != nil {
						t.Fatal(err)
					}
					body, err := os.ReadFile(path)
					if err != nil || string(body) != "first\nsecond\n" || meta.TransportErr != "" || meta.Exit != 0 || meta.Command != "long-running-check" {
						t.Fatalf("example evidence: meta=%+v body=%q err=%v", meta, body, err)
					}
					events := followEvents(t, &stderr)
					last := events[len(events)-1]
					if last["type"] != "completed" || last["process_exit"] != float64(0) {
						t.Fatalf("example completion: %v", events)
					}
				})
			}
			if count == 0 {
				t.Fatal("no executable long-running follow example found")
			}
		})
	}
}

// Changing the follow interval must not replace the selected CLI budget. This
// also checks the documented factory/config precedence through the real parser.
func TestFollowTimeoutSelection(t *testing.T) {
	for _, tc := range []struct {
		name   string
		config string
		flags  []string
		want   time.Duration
	}{
		{"factory", "", nil, 60 * time.Second},
		{"configured", "timeout_sec = 90\n", []string{"--follow-interval", "1"}, 90 * time.Second},
		{"explicit", "timeout_sec = 7\n", []string{"--timeout", "300", "--follow-interval", "5"}, 300 * time.Second},
	} {
		t.Run(tc.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(tc.config), 0o600); err != nil {
				t.Fatal(err)
			}
			if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
				t.Fatal(err)
			}
			args := append([]string{"--follow"}, tc.flags...)
			args = append(args, "web01", "--", "echo", "check")
			tr := &exampleDeadlineTr{}
			var stdout, stderr bytes.Buffer
			if rc := runWith(tr, args, &stdout, &stderr); rc != 0 {
				t.Fatalf("rc=%d stderr=%s", rc, &stderr)
			}
			if tr.budget > tc.want || tr.budget < tc.want-time.Second {
				t.Fatalf("execution budget=%s, want remaining part of %s", tr.budget, tc.want)
			}
		})
	}
}
