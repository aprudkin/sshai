package shell

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPwshNoStateWrapper(t *testing.T) {
	for _, marker := range []string{"", "FOLLOW"} {
		script := PwshScriptNoState("Write-Output 'permitted'", "END", marker)
		if !bytes.HasPrefix(script, []byte{0xef, 0xbb, 0xbf}) {
			t.Fatal("missing UTF-8 BOM")
		}
		for _, forbidden := range []string{"Get-Location", "Set-Location", "env:", "ToBase64String", "$__sshai_env"} {
			if bytes.Contains(script, []byte(forbidden)) {
				t.Fatalf("no-state wrapper contains %s", forbidden)
			}
		}
		if !bytes.Contains(script, []byte("finally {")) || !bytes.Contains(script, []byte("exit $__sshai_rc")) {
			t.Fatal("lost exit handling or framing")
		}
	}
}

func TestPwshNoStateParse(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{"permitted\r\n\r\nEND\r\n", "permitted\n"},
		{"permitted\n\nEND\nlate\nmore\n", "permitted\nlate\nmore\n"},
		{"partial\r\n", "partial\r\n"},
		{"\nEND\n", ""},
	} {
		if got := string(PwshParseNoState([]byte(tc.raw), "END")); got != tc.want {
			t.Errorf("parse %q = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

// Each installed interpreter is checked independently. A pwsh pass is not
// Windows PowerShell 5.1 evidence. No SSH or user environment is used.
func TestPwshNoStateRuntime(t *testing.T) {
	for _, executable := range []string{"pwsh", "powershell"} {
		t.Run(executable, func(t *testing.T) {
			path, err := exec.LookPath(executable)
			if err != nil {
				t.Skip(executable + " unavailable; runtime semantics not verified")
			}
			for _, follow := range []bool{false, true} {
				for _, tc := range []struct {
					name, body, want string
					exit             int
				}{
					{"success", "Write-Output 'permitted'", "permitted", 0},
					{"nonzero", "Write-Output 'permitted'; exit 7", "permitted", 7},
					{"throw", "throw 'synthetic-exception'", "synthetic-exception", 1},
					{"strict", "Set-StrictMode -Version Latest; Write-Output 'strict-ok'", "strict-ok", 0},
					{"native", "$global:LASTEXITCODE = 9; Write-Output 'native-code'", "native-code", 9},
				} {
					t.Run(tc.name+map[bool]string{false: "/normal", true: "/follow"}[follow], func(t *testing.T) {
						dir := t.TempDir()
						marker := ""
						if follow {
							marker = "FOLLOW"
						}
						script := PwshScriptNoState(tc.body, "END", marker)
						file := filepath.Join(dir, "body.ps1")
						if err := os.WriteFile(file, script, 0o600); err != nil {
							t.Fatal(err)
						}
						ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
						defer cancel()
						cmd := exec.CommandContext(ctx, path, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", file)
						cwd := t.TempDir()
						cmd.Dir = cwd
						cmd.Env = []string{"HOME=" + dir, "USERPROFILE=" + dir, "PATH=" + filepath.Dir(path), "SSHAI_ENV_CANARY=synthetic-environment-only-value"}
						// Windows PowerShell needs the OS path, not the user's environment.
						if executable == "powershell" {
							cmd.Env = append(cmd.Env, `SystemRoot=C:\Windows`)
						}
						raw, runErr := cmd.CombinedOutput()
						if ctx.Err() != nil || cmd.ProcessState == nil || cmd.ProcessState.ExitCode() != tc.exit {
							t.Fatalf("exit: %v, output=%s", runErr, raw)
						}
						if bytes.Contains(raw, []byte("synthetic-environment-only-value")) || bytes.Contains(raw, []byte("SSHAI_ENV_CANARY")) || bytes.Contains(raw, []byte(cwd)) {
							t.Fatalf("environment/cwd captured: %s", raw)
						}
						out := string(PwshParseNoState(raw, "END"))
						if !strings.Contains(out, tc.want) || strings.Contains(out, "END") || (follow && !strings.Contains(out, marker)) {
							t.Fatalf("output/framing mismatch: %q", out)
						}
					})
				}
			}
		})
	}
}
