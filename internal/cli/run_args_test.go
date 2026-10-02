package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aprudkin/sshai/internal/session"
)

// These errors must identify the invalid invocation shape, without consuming
// a body or starting transport. In particular, flag.Parse consumes a leading
// -- when the caller has omitted the host.
func TestRunArgumentShapeErrors(t *testing.T) {
	for _, tt := range []struct {
		name string
		args []string
		want []string
	}{
		{"body file after host", []string{"web01", "--body-file", "missing.ps1"}, []string{"--body-file", "before hosts", "sshai run [flags] --body-file <file|-> <host...>"}},
		{"body file equals after host", []string{"web01", "--body-file=missing.ps1"}, []string{"--body-file", "before hosts"}},
		{"single dash flag after host", []string{"web01", "-timeout", "30", "--", "true"}, []string{"-timeout", "before hosts", "sshai run [flags] <host...> -- <command>"}},
		{"bool after multiple hosts", []string{"web01", "web02", "--delta", "--", "true"}, []string{"--delta", "before hosts"}},
		{"flag in body file host list", []string{"--body-file", "missing.ps1", "web01", "--timeout=30"}, []string{"--timeout", "before hosts"}},
		{"no args", nil, []string{"at least one host is required"}},
		{"command without host", []string{"--", "echo", "hello"}, []string{"at least one host is required"}},
		{"flag and command without host", []string{"--timeout", "30", "--", "echo", "hello"}, []string{"at least one host is required"}},
		{"command option without host", []string{"--", "--body-file", "missing.ps1"}, []string{"at least one host is required"}},
		{"mixed flags without host", []string{"--delta", "--budget=100", "--posix-shell", "--", "--", "echo"}, []string{"at least one host is required"}},
		{"two separators without host", []string{"--", "--", "echo"}, []string{"at least one host is required"}},
		{"body file without host", []string{"--body-file", "missing.ps1"}, []string{"at least one host is required"}},
		{"stdin without host", []string{"--body-file", "-"}, []string{"at least one host is required"}},
		{"host without command", []string{"web01"}, []string{"a command is required"}},
		{"separator without command", []string{"web01", "--"}, []string{"a command is required"}},
		{"empty command", []string{"web01", "--", ""}, []string{"a command is required"}},
		{"separator as flag value", []string{"--posix-shell", "--", "web01"}, []string{"a command is required"}},
	} {
		t.Run(tt.name, func(t *testing.T) {
			t.Setenv("SSHAI_ROOT", t.TempDir())
			// A closed stdin makes accidental reads fail deterministically,
			// rather than hanging the no-host stdin regression.
			stdin, err := os.Open(os.DevNull)
			if err != nil {
				t.Fatal(err)
			}
			stdin.Close()
			oldStdin := os.Stdin
			os.Stdin = stdin
			t.Cleanup(func() { os.Stdin = oldStdin })
			tr := &fakeTr{}
			var out, errOut bytes.Buffer
			rc := runWith(tr, tt.args, &out, &errOut)
			if rc != exitUsage || tr.calls != 0 || out.Len() != 0 {
				t.Fatalf("rc=%d calls=%d stdout=%q stderr=%q", rc, tr.calls, out.String(), errOut.String())
			}
			for _, want := range tt.want {
				if !strings.Contains(errOut.String(), want) {
					t.Errorf("stderr=%q, want %q", errOut.String(), want)
				}
			}
		})
	}
}

// Positive controls protect the existing inline/body forms and prove that
// option-looking command words still reach the wrapper unchanged.
func TestRunArgumentShapeValidControls(t *testing.T) {
	for _, tt := range []struct {
		name string
		args []string
		body string
	}{
		{"inline", []string{"--timeout=30", "web01", "--", "echo", "hello"}, "echo hello"},
		{"command options", []string{"web01", "--", "printf", "--body-file", "--timeout=30", "-delta", "--"}, "printf --body-file --timeout=30 -delta --"},
		{"command starts with option", []string{"web01", "--", "--body-file", "--ctx=x"}, "--body-file --ctx=x"},
		{"flag terminator before host", []string{"--", "web01", "--", "echo", "hello"}, "echo hello"},
		{"separator flag value", []string{"--posix-shell", "--", "web01", "--", "echo", "hello"}, "echo hello"},
		{"file", []string{"--body-file", "BODY", "web01"}, "echo file\n"},
		{"stdin", []string{"--body-file", "-", "web01"}, "echo stdin\n"},
	} {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux"}); err != nil {
				t.Fatal(err)
			}
			bodyPath := filepath.Join(t.TempDir(), "body.sh")
			if err := os.WriteFile(bodyPath, []byte(tt.body), 0o600); err != nil {
				t.Fatal(err)
			}
			args := append([]string(nil), tt.args...)
			for i, arg := range args {
				if arg == "BODY" {
					args[i] = bodyPath
				}
			}
			stdin, err := os.Open(bodyPath)
			if err != nil {
				t.Fatal(err)
			}
			defer stdin.Close()
			oldStdin := os.Stdin
			os.Stdin = stdin
			t.Cleanup(func() { os.Stdin = oldStdin })
			tr := &fakeTr{}
			var out, errOut bytes.Buffer
			if rc := runWith(tr, args, &out, &errOut); rc != 0 {
				t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
			}
			if tr.calls != 1 || !bytes.HasSuffix(tr.lastStdin, []byte("\n"+tt.body)) {
				t.Fatalf("calls=%d wrapper missing unchanged body %q: %q", tr.calls, tt.body, tr.lastStdin)
			}
			if !strings.Contains(out.String(), "host=web01 exit=0") {
				t.Fatalf("missing successful passport: %q", out.String())
			}
		})
	}
}

func TestRunArgumentShapeFanout(t *testing.T) {
	for _, form := range []string{"inline", "file", "stdin"} {
		t.Run(form, func(t *testing.T) {
			root := t.TempDir()
			t.Setenv("SSHAI_ROOT", root)
			seedLinuxFacts(t, root, "web01", "web02")
			bodyPath := filepath.Join(t.TempDir(), "body.sh")
			if err := os.WriteFile(bodyPath, []byte("echo hello\n"), 0o600); err != nil {
				t.Fatal(err)
			}
			stdin, err := os.Open(bodyPath)
			if err != nil {
				t.Fatal(err)
			}
			defer stdin.Close()
			oldStdin := os.Stdin
			os.Stdin = stdin
			t.Cleanup(func() { os.Stdin = oldStdin })
			args := []string{"--budget", "300", "web01", "web02", "--", "echo", "hello"}
			if form != "inline" {
				if form == "stdin" {
					bodyPath = "-"
				}
				args = []string{"--budget", "300", "--body-file", bodyPath, "web01", "web02"}
			}
			tr := &multiHostTr{rcs: map[string]int{"web01": 0, "web02": 5}}
			var out, errOut bytes.Buffer
			if rc := runWith(tr, args, &out, &errOut); rc != 5 {
				t.Fatalf("rc=%d stderr=%q", rc, errOut.String())
			}
			for _, want := range []string{"host=web01 exit=0", "host=web02 exit=5", "hosts=2 ok=1 failed=1"} {
				if !strings.Contains(out.String(), want) {
					t.Errorf("stdout=%q, want %q", out.String(), want)
				}
			}
		})
	}
}
