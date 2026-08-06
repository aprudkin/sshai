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
