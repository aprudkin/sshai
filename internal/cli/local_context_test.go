package cli

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
)

// Run the real local entry point in a disposable process, so cwd/environment
// changes cannot affect the test runner and user shell state is never loaded.
func TestLocalContextProcess(t *testing.T) {
	if os.Getenv("SSHAI_CONTEXT_HELPER") != "1" {
		return
	}
	for i, arg := range os.Args {
		if arg == "--" {
			os.Exit(Local(os.Args[i+1:], os.Stdout, os.Stderr))
		}
	}
	t.Fatal("missing local arguments")
}

func TestLocalProjectContexts(t *testing.T) {
	for _, interpreter := range []string{"bash", "pwsh"} {
		t.Run(interpreter, func(t *testing.T) {
			if _, err := exec.LookPath(interpreter); err != nil {
				t.Skip(interpreter + " is unavailable")
			}
			dir, err := filepath.EvalSymlinks(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			projectA := filepath.Join(dir, "project a")
			projectB := filepath.Join(dir, "project b")
			home := filepath.Join(dir, "home")
			root := filepath.Join(dir, "sshai")
			for _, path := range []string{projectA, projectB, home, root} {
				if err := os.Mkdir(path, 0o700); err != nil {
					t.Fatal(err)
				}
			}
			bodyPath := filepath.Join(dir, "body.txt")
			// Inherit only interpreter discovery and Windows runtime essentials.
			// In particular, do not inherit BASH_ENV, SSHAI_CTX or user credentials.
			env := []string{
				"PATH=" + os.Getenv("PATH"), "HOME=" + home, "USERPROFILE=" + home,
				"SSHAI_ROOT=" + root, "SSHAI_CONTEXT_HELPER=1", "SSHAI_CONTEXT_TEST=caller",
			}
			if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
				env = append(env, "SystemRoot="+systemRoot)
			}
			executable, err := os.Executable()
			if err != nil {
				t.Fatal(err)
			}
			observe := "printf 'cwd=%s\\nmarker=%s\\n' \"$PWD\" \"$SSHAI_CONTEXT_TEST\"\n"
			setMarker := func(value string) string { return "export SSHAI_CONTEXT_TEST='" + value + "'\n" }
			setCwd := func(path string) string {
				return "cd -- '" + strings.ReplaceAll(path, "'", `'\''`) + "' || exit 1\n"
			}
			if interpreter == "pwsh" {
				observe = "Write-Output ('cwd=' + (Get-Location).Path)\nWrite-Output ('marker=' + $env:SSHAI_CONTEXT_TEST)\n"
				setMarker = func(value string) string { return "$env:SSHAI_CONTEXT_TEST = '" + value + "'\n" }
				setCwd = func(path string) string {
					return "Set-Location -LiteralPath '" + strings.ReplaceAll(path, "'", "''") + "' -ErrorAction Stop\n"
				}
			}
			run := func(name, callerDir, ctx, body, wantDir, wantMarker string, wantExit int) {
				t.Helper()
				if err := os.WriteFile(bodyPath, []byte(body), 0o600); err != nil {
					t.Fatal(err)
				}
				deadline, cancel := context.WithTimeout(context.Background(), 20*time.Second)
				defer cancel()
				cmd := exec.CommandContext(deadline, executable, "-test.run=^TestLocalContextProcess$", "--",
					"--shell", interpreter, "--ctx", ctx, "--timeout", "10", "--result-format=json", "--body-file", bodyPath)
				cmd.Dir, cmd.Env = callerDir, env
				output, err := cmd.Output()
				if deadline.Err() != nil || cmd.ProcessState == nil || cmd.ProcessState.ExitCode() != wantExit || (err != nil && wantExit == 0) {
					t.Fatalf("%s: exit mismatch or deadline: %v", name, err)
				}
				var result struct {
					Runs []artifact.ResultEntry `json:"runs"`
				}
				if err := json.Unmarshal(output, &result); err != nil {
					t.Fatalf("%s: decode result: %v", name, err)
				}
				if len(result.Runs) != 1 || result.Runs[0].Exit != wantExit || result.Runs[0].LocalError != "" || result.Runs[0].Truncated {
					t.Fatalf("%s: expected a normal, complete shell result", name)
				}
				captured, err := os.ReadFile(result.Runs[0].ArtifactPath)
				if err != nil {
					t.Fatal(err)
				}
				if wantExit != 0 {
					if strings.Contains(string(captured), "UNEXPECTED-BODY") {
						t.Fatalf("%s: continued after failed cwd selection", name)
					}
					return
				}
				want := "cwd=" + wantDir + "\nmarker=" + wantMarker + "\n"
				if got := strings.ReplaceAll(string(captured), "\r\n", "\n"); got != want {
					t.Fatalf("%s: captured=%q, want %q", name, got, want)
				}
			}
			// Establish the baseline before changing a variable: restoration is a
			// delta from that baseline, not a replay of the entire environment.
			run("initial cwd", projectA, "project-a", observe, projectA, "caller", 0)
			run("save environment delta", projectA, "project-a", setMarker("saved-a")+observe, projectA, "saved-a", 0)
			run("saved state overrides caller", projectB, "project-a", observe, projectA, "saved-a", 0)
			run("explicit cwd wins", projectA, "project-a", setCwd(projectB)+observe, projectB, "saved-a", 0)
			run("new context starts from caller", projectA, "project-b", observe, projectA, "caller", 0)
			run("initialize second project", projectB, "project-b", setCwd(projectA)+setMarker("saved-b")+observe, projectA, "saved-b", 0)
			run("original context remains separate", projectA, "project-a", observe, projectB, "saved-a", 0)
			run("second context restores its own state", projectB, "project-b", observe, projectA, "saved-b", 0)
			run("invalid explicit cwd stops body", projectA, "project-a", setCwd(filepath.Join(dir, "missing"))+"echo UNEXPECTED-BODY\n", "", "", 1)
		})
	}
}
