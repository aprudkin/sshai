// internal/runlog/audit.go
package runlog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

// redactRe matches a secret-bearing key followed by '=' or ':' and its
// value, case-insensitively. The value is everything up to the next
// whitespace run.
var redactRe = regexp.MustCompile(`(?i)(password|passwd|pwd|token|secret|apikey|api_key)\s*[=:]\s*\S+`)

// Redact replaces every "key<sep>value" occurrence of a secret-bearing key
// (password, passwd, pwd, token, secret, apikey, api_key) with
// "key=***", normalizing the separator to '=' regardless of whether the
// source used '=' or ':'.
func Redact(s string) string {
	return redactRe.ReplaceAllString(s, "$1=***")
}

// Preview redacts command, then clips it to at most 80 runes for inclusion
// in an AuditEntry.CommandPreview.
func Preview(command string) string {
	r := []rune(Redact(command))
	if len(r) > 80 {
		r = r[:80]
	}
	return string(r)
}

// AuditEntry is one line of the append-only audit.jsonl log. Verdict is one
// of "allowed" or "denied-readonly".
type AuditEntry struct {
	Ts                                                         time.Time
	Host, Ctx, Subcommand, CommandPreview, BodySHA256, Verdict string
	Exit                                                       int
	TransportErr                                               string
}

// AppendAudit appends e as one JSON line to <root>/audit.jsonl, creating
// the file if needed. The file is opened O_APPEND|O_CREATE with mode
// 0o600 so writes from concurrent processes interleave safely at the line
// level and the log stays readable only by its owner.
func AppendAudit(root string, e AuditEntry) error {
	b, err := json.Marshal(e)
	if err != nil {
		return err
	}
	b = append(b, '\n')

	f, err := os.OpenFile(filepath.Join(root, "audit.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(b); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}
