// internal/shell/pwsh_test.go
package shell

import (
	"bytes"
	"encoding/base64"
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

func TestPwshInvocationForms(t *testing.T) {
	shell := `C:\Program Files\PowerShell\7\pwsh.exe`
	if got := PwshInvocation("cmd", shell, "-NoProfile -File x.ps1"); strings.HasPrefix(got, "& ") {
		t.Fatalf("cmd form must not lead with '& ': %q", got)
	}
	if got := PwshInvocation("pwsh", shell, "-NoProfile -File x.ps1"); !strings.HasPrefix(got, `& "`) {
		t.Fatalf("pwsh form must lead with '& ': %q", got)
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
