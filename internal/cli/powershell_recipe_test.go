package cli

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
)

// Extract the shipped example, not a second copy that can drift from the skill.
func powerShellRecipe(t *testing.T) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "skills", "sshai", "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	section := strings.SplitN(string(data), "### PowerShell required steps and optional probes\n", 2)
	if len(section) != 2 {
		t.Fatal("missing PowerShell recipe section")
	}
	match := regexp.MustCompile("(?s)```bash\\nsshai run --body-file - windows01 <<'POWERSHELL'\\n(.*?)\\nPOWERSHELL\\n```").FindStringSubmatch(section[1])
	if len(match) != 2 {
		t.Fatal("missing quoted stdin recipe")
	}
	return match[1] + "\n"
}

// These cases catch invalid foreach/interpolation syntax, continued execution
// after a required cmdlet failure, and an optional missing file aborting the run.
// Only local pwsh and disposable files are used, not Windows or SSH targets.
func TestPowerShellRecipe(t *testing.T) {
	body := powerShellRecipe(t)
	powerShell, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh is unavailable; recipe execution was not checked")
	}
	dir := t.TempDir()
	home := filepath.Join(dir, "home")
	if err := os.Mkdir(home, 0o700); err != nil {
		t.Fatal(err)
	}
	env := []string{"PATH=" + os.Getenv("PATH"), "HOME=" + home, "USERPROFILE=" + home}
	if systemRoot := os.Getenv("SystemRoot"); systemRoot != "" {
		env = append(env, "SystemRoot="+systemRoot)
	}
	parserPath := filepath.Join(dir, "parse.ps1")
	parser := `param($Path)
$tokens = $null
$errors = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
if ($errors.Count -ne 0) { $errors | Out-String | Write-Output; exit 1 }
$PSVersionTable.PSVersion.ToString()
`
	if err := os.WriteFile(parserPath, []byte(parser), 0o600); err != nil {
		t.Fatal(err)
	}
	bodyPath := filepath.Join(dir, "recipe.ps1")
	if err := os.WriteFile(bodyPath, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	parse := exec.CommandContext(ctx, powerShell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", parserPath, bodyPath)
	parse.Env, parse.Dir = env, dir
	version, err := parse.CombinedOutput()
	if err != nil {
		t.Fatalf("recipe parser failed: %v\n%s", err, version)
	}
	t.Logf("recipe parsed by PowerShell %s (local host only)", strings.TrimSpace(string(version)))

	executable, err := os.Executable()
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name       string
		required   bool
		optional   bool
		wantExit   int
		wantStatus string
	}{
		{"success", true, true, 0, "present"},
		{"required failure", false, true, 1, ""},
		{"optional missing", true, false, 0, "not_found"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			work := t.TempDir()
			for name, content := range map[string]string{"report-a.txt": "alpha", "report-b.txt": "beta", "optional.txt": "note"} {
				if (name == "report-b.txt" && !tc.required) || (name == "optional.txt" && !tc.optional) {
					continue
				}
				if err := os.WriteFile(filepath.Join(work, name), []byte(content), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			const rootLiteral = `$root = 'C:\Reports'`
			if strings.Count(body, rootLiteral) != 1 {
				t.Fatal("expected one documented root placeholder")
			}
			// Replace only the documented path placeholder; all logic is unchanged.
			input := strings.Replace(body, rootLiteral, "$root = '"+strings.ReplaceAll(work, "'", "''")+"'", 1)
			deadline, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			cmd := exec.CommandContext(deadline, executable, "-test.run=^TestLocalContextProcess$", "--",
				"--shell", "pwsh", "--ctx", "recipe", "--timeout", "10", "--result-format=json", "--body-file", "-")
			cmd.Dir = work
			cmd.Env = append(append([]string{}, env...), "SSHAI_CONTEXT_HELPER=1", "SSHAI_ROOT="+filepath.Join(work, "sshai"))
			cmd.Stdin = strings.NewReader(input)
			output, err := cmd.Output()
			if deadline.Err() != nil || cmd.ProcessState == nil || cmd.ProcessState.ExitCode() != tc.wantExit || (err != nil && tc.wantExit == 0) {
				t.Fatalf("exit mismatch or deadline: %v", err)
			}
			var envelope struct {
				Runs []artifact.ResultEntry `json:"runs"`
			}
			if err := json.Unmarshal(output, &envelope); err != nil {
				t.Fatal(err)
			}
			if len(envelope.Runs) != 1 || envelope.Runs[0].Exit != tc.wantExit || envelope.Runs[0].LocalError != "" || envelope.Runs[0].Truncated {
				t.Fatal("expected a normal, complete shell result")
			}
			captured, err := os.ReadFile(envelope.Runs[0].ArtifactPath)
			if err != nil {
				t.Fatal(err)
			}
			if tc.wantExit != 0 {
				if !strings.Contains(string(captured), "report-b.txt") || strings.Contains(string(captured), `"files"`) {
					t.Fatalf("required failure must identify the missing file, without a success report: %s", captured)
				}
				return
			}
			var report struct {
				Files []struct {
					Name  string `json:"name"`
					Bytes int    `json:"bytes"`
					Label string `json:"label"`
				} `json:"files"`
				Optional string `json:"optional"`
			}
			if err := json.Unmarshal(captured, &report); err != nil {
				t.Fatalf("invalid recipe JSON: %v\n%s", err, captured)
			}
			if len(report.Files) != 2 || report.Optional != tc.wantStatus {
				t.Fatalf("unexpected report: %+v", report)
			}
			if report.Files[0].Name != "report-a.txt" || report.Files[0].Bytes != 5 || report.Files[0].Label != "report-a.txt: required" ||
				report.Files[1].Name != "report-b.txt" || report.Files[1].Bytes != 4 || report.Files[1].Label != "report-b.txt: required" {
				t.Fatalf("collection/interpolation changed: %+v", report.Files)
			}
		})
	}
}
