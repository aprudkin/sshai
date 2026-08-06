// internal/runlog/audit_test.go
package runlog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestRedact(t *testing.T) {
	cases := map[string]string{
		"curl -H 'X: y' --token=abc123 x":   "curl -H 'X: y' --token=*** x",
		"Set-ADAccountPassword -pass: Zq1!": "Set-ADAccountPassword -pass: Zq1!", // "pass" alone is not matched
		"PASSWORD=hunter2 ./run":            "PASSWORD=*** ./run",
		"echo secret: s3cr3t":               "echo secret=***",
	}
	for in, want := range cases {
		if got := Redact(in); got != want {
			t.Errorf("Redact(%q) = %q, want %q", in, got, want)
		}
	}
}

// TestRedactQuotedSpansAndFlagStyle covers two redaction gaps found in
// review: (1) a quoted value containing spaces was cut at the first space
// by the original \S+-only value pattern, leaking the remainder of the
// secret onto the line right next to a "***" that looked like full
// redaction; (2) PowerShell's dominant parameter form is a whitespace
// separator with no '=' or ':' at all ("-Password 'value'"), which the
// original pattern never matched. It also probes suffix matching: real
// AD/PowerShell secret parameters commonly don't start with the bare
// keyword (-NewPassword, not -Password). Suffix matching is supported for
// the flag-style form only, gated on the flag's dash beginning a fresh
// whitespace-delimited token — a dash glued to a preceding identifier
// never qualifies, which is what keeps the cmdlet name
// "Set-ADAccountPassword" itself unredacted in both this test and in
// TestRedact's fixed regression case.
func TestRedactQuotedSpansAndFlagStyle(t *testing.T) {
	cases := map[string]string{
		"curl --token='ab cd ef' host":                          "curl --token=*** host",
		"Invoke-Command -Password 'hunter2' -ComputerName dc01": "Invoke-Command -Password=*** -ComputerName dc01",
		"Set-ADAccountPassword -NewPassword 'x'":                "Set-ADAccountPassword -NewPassword=***",
		// Shell-legal adjacent-quote concatenation ('pa'ss'word' is one
		// token to a shell). A single quoted-or-\S+ alternative only
		// consumes the first quoted span ('pa'), leaving "ss'word'" to
		// leak right next to the "***" — the same misleading-adjacency
		// failure the review flagged, reintroduced through a different
		// input. The value group must repeat to consume the whole token.
		"curl --password='pa'ss'word' host": "curl --password=*** host",
	}
	for in, want := range cases {
		if got := Redact(in); got != want {
			t.Errorf("Redact(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestAppendAuditWritesJSONL(t *testing.T) {
	root := t.TempDir()
	e := AuditEntry{Ts: time.Now(), Host: "dc01", Ctx: "default", Subcommand: "run",
		CommandPreview: Preview("Get-Service -Name x"), Verdict: "allowed", Exit: 0}
	if err := AppendAudit(root, e); err != nil {
		t.Fatal(err)
	}
	if err := AppendAudit(root, e); err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(filepath.Join(root, "audit.jsonl"))
	lines := strings.Split(strings.TrimSpace(string(b)), "\n")
	if len(lines) != 2 {
		t.Fatalf("want 2 lines, got %d", len(lines))
	}
	var back AuditEntry
	if err := json.Unmarshal([]byte(lines[0]), &back); err != nil || back.Host != "dc01" {
		t.Fatalf("bad jsonl: %v %+v", err, back)
	}
}
