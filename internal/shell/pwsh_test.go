// internal/shell/pwsh_test.go
package shell

import (
	"bytes"
	"encoding/base64"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestPwshScriptStartsWithBOMAndPreamble(t *testing.T) {
	s := PwshScript("Get-Date", State{}, nil, "__SSHAI_x__")
	if !bytes.HasPrefix(s, []byte{0xEF, 0xBB, 0xBF}) {
		t.Fatal("missing UTF-8 BOM")
	}
	txt := string(s)
	for _, want := range []string{
		"[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
		"Get-Date", "finally", "'__SSHAI_x__'", "exit $__sshai_rc",
	} {
		if !strings.Contains(txt, want) {
			t.Errorf("script missing %q", want)
		}
	}
}

func TestPwshScriptFollowCombinesAllBodyStreamsOnlyInFollowMode(t *testing.T) {
	body := "Write-Error 'err'"
	if !bytes.Equal(PwshScript(body, State{}, nil, "S"), PwshScriptFollow(body, State{}, nil, "S", "")) {
		t.Fatal("non-follow script bytes changed")
	}
	follow := string(PwshScriptFollow(body, State{}, nil, "S", "MARK"))
	if !strings.Contains(follow, "Write-Output 'MARK'\n  & {\n"+body+"\n  } *>&1") {
		t.Fatalf("follow script lacks all-stream redirect:\n%s", follow)
	}
}

func TestPwshScriptGuardsLastExitCodeUnderStrictMode(t *testing.T) {
	body := "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n[pscustomobject]@{ ok = $true } | ConvertTo-Json -Compress"
	txt := string(PwshScript(body, State{}, nil, "__SSHAI_x__"))

	if !strings.Contains(txt, body) {
		t.Fatal("script changed the caller body")
	}
	if strings.Contains(txt, "if ($LASTEXITCODE -ne $null)") {
		t.Fatal("script reads an undefined $LASTEXITCODE before checking it")
	}
	if want := "if (Test-Path Variable:LASTEXITCODE) { $__sshai_rc = $LASTEXITCODE }"; !strings.Contains(txt, want) {
		t.Fatalf("script missing guarded native exit propagation %q", want)
	}
}

func TestPwshScriptStrictModeExitBehavior(t *testing.T) {
	powerShell, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh is not installed")
	}

	run := func(t *testing.T, body string) ([]byte, error) {
		t.Helper()
		path := filepath.Join(t.TempDir(), "script.ps1")
		if err := os.WriteFile(path, PwshScript(body, State{}, nil, "__SSHAI_x__"), 0o600); err != nil {
			t.Fatal(err)
		}
		return exec.Command(powerShell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path).CombinedOutput()
	}

	t.Run("no native process", func(t *testing.T) {
		body := "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n[pscustomobject]@{ ok = $true } | ConvertTo-Json -Compress"
		out, err := run(t, body)
		if err != nil {
			t.Fatalf("script failed: %v\n%s", err, out)
		}
		if !strings.Contains(string(out), `{"ok":true}`) {
			t.Fatalf("body output missing or changed: %q", out)
		}
	})

	t.Run("native nonzero", func(t *testing.T) {
		body := "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n/bin/sh -c 'exit 5'"
		if runtime.GOOS == "windows" {
			body = "Set-StrictMode -Version Latest\n$ErrorActionPreference = 'Stop'\n& \"$env:WINDIR\\System32\\cmd.exe\" /c exit 5"
		}
		out, err := run(t, body)
		var exitErr *exec.ExitError
		if !errors.As(err, &exitErr) || exitErr.ExitCode() != 5 {
			t.Fatalf("exit = %v, want 5; output=%q", err, out)
		}
	})
}

func TestPwshScriptFollowExitStreamsAndState(t *testing.T) {
	powerShell, err := exec.LookPath("pwsh")
	if err != nil {
		t.Skip("pwsh is not installed")
	}

	dir := t.TempDir()
	body := "Set-StrictMode -Version Latest\nSet-Location -LiteralPath '" + pwq(dir) + "'\n$env:SSHAI_FOLLOW_TEST = 'kept'\n/bin/sh -c 'printf follow-stderr >&2; exit 5'"
	if runtime.GOOS == "windows" {
		body = "Set-StrictMode -Version Latest\nSet-Location -LiteralPath '" + pwq(dir) + "'\n$env:SSHAI_FOLLOW_TEST = 'kept'\n& \"$env:WINDIR\\System32\\cmd.exe\" /c \"echo follow-stderr 1>&2 & exit 5\""
	}
	const sentinel = "__SSHAI_follow_state__"
	const marker = "__SSHAI_follow_started__"
	path := filepath.Join(t.TempDir(), "follow.ps1")
	if err := os.WriteFile(path, PwshScriptFollow(body, State{}, nil, sentinel, marker), 0o600); err != nil {
		t.Fatal(err)
	}
	var combined bytes.Buffer
	cmd := exec.Command(powerShell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path)
	// ExecStream assigns one writer to both pipes so os/exec retains their
	// order; native PowerShell stderr is therefore part of remote output.
	cmd.Stdout, cmd.Stderr = &combined, &combined
	err = cmd.Run()
	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) || exitErr.ExitCode() != 5 {
		t.Fatalf("exit=%v, want 5; output=%q", err, combined.Bytes())
	}
	out, st, ok := PwshParse(combined.Bytes(), sentinel)
	if !ok || !strings.Contains(string(out), marker) || !strings.Contains(string(out), "follow-stderr") {
		t.Fatalf("out=%q ok=%v", out, ok)
	}
	if filepath.Clean(st.Cwd) != filepath.Clean(dir) || st.Env["SSHAI_FOLLOW_TEST"] != "kept" {
		t.Fatalf("state=%+v, want cwd=%q and env continuity", st, dir)
	}
}

func TestPwshInvocationForms(t *testing.T) {
	shell := `C:\Program Files\PowerShell\7\pwsh.exe`
	if got, want := PwshInvocation("cmd", shell, "-NoProfile -File x.ps1"), `"C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -File x.ps1`; got != want {
		t.Fatalf("cmd form = %q, want %q", got, want)
	}
	if got, want := PwshInvocation("pwsh", shell, "-NoProfile -File x.ps1"), `& "C:\Program Files\PowerShell\7\pwsh.exe" -NoProfile -File x.ps1; exit $LASTEXITCODE`; got != want {
		t.Fatalf("pwsh form = %q, want %q", got, want)
	}
}

func TestLooksLikePwshDefault(t *testing.T) {
	if !LooksLikePwshDefault([]byte("ParserError: ...")) ||
		LooksLikePwshDefault([]byte("The system cannot find the path")) {
		t.Fatal("signature detection wrong")
	}
}

func TestPwshParseFiltersCLIXMLThenSplits(t *testing.T) {
	env := base64.StdEncoding.EncodeToString([]byte("COMPUTERNAME=DC01\nX=1"))
	raw := []byte("#< CLIXML\nService running_x000D_\n\n__SSHAI_x__\nC:\\Users\\svc\n" + env + "\n")
	out, st, ok := PwshParse(raw, "__SSHAI_x__")
	if !ok || !strings.Contains(string(out), "Service running") || strings.Contains(string(out), "CLIXML") {
		t.Fatalf("out=%q ok=%v", out, ok)
	}
	if st.Cwd != `C:\Users\svc` || st.Env["COMPUTERNAME"] != "DC01" {
		t.Fatalf("state: %+v", st)
	}
}

// TestPwshParseTolerantOfCRLFWireOutput guards against a real-world
// failure mode the brief's fixture doesn't exercise: pwsh over OpenSSH
// writes CRLF line endings, but FilterCLIXML only splits on "\n", so a
// naive implementation leaves a trailing "\r" on the sentinel, cwd, and
// env-dump lines — breaking the sentinel's exact-match scan on every
// real invocation (not just a cosmetic artifact in the command's own
// output). Added beyond the brief's committed Step 1 tests because this
// is a genuine correctness gap, not a design equivalence to double-check.
func TestPwshParseTolerantOfCRLFWireOutput(t *testing.T) {
	env := base64.StdEncoding.EncodeToString([]byte("COMPUTERNAME=DC01\nX=1"))
	raw := []byte("Service running\r\n\r\n__SSHAI_x__\r\nC:\\Users\\svc\r\n" + env + "\r\n")
	out, st, ok := PwshParse(raw, "__SSHAI_x__")
	if !ok {
		t.Fatalf("sentinel not found in CRLF-terminated output: out=%q", out)
	}
	if !strings.Contains(string(out), "Service running") {
		t.Errorf("real output missing: out=%q", out)
	}
	if st.Cwd != `C:\Users\svc` {
		t.Errorf("cwd carries trailing CR: st.Cwd=%q", st.Cwd)
	}
	if st.Env["COMPUTERNAME"] != "DC01" || st.Env["X"] != "1" {
		t.Errorf("env dump not decoded (trailing CR broke base64?): %+v", st.Env)
	}
}

func TestBodySlugStableAcrossCRLF(t *testing.T) {
	if BodySlug([]byte("Get-Date\r\n")) != BodySlug([]byte("Get-Date\n")) {
		t.Fatal("slug must normalize CRLF")
	}
	if len(BodySlug([]byte("x"))) != 8 {
		t.Fatal("slug is sha1[:8]")
	}
}
