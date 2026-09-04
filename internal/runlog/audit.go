// internal/runlog/audit.go
package runlog

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"time"
)

// redactRe matches a secret-bearing key (password, passwd, pwd, token,
// secret, apikey, api_key) together with its value, case-insensitively,
// in two shapes:
//
//  1. "key<sep>value" where <sep> is '=' or ':' (optionally surrounded by
//     whitespace) — group "eqKey" / "eqVal". This is the CLI/env-var form
//     ("PASSWORD=hunter2", "secret: s3cr3t").
//  2. "-key value" / "--key value" where the separator is whitespace and
//     there is no '=' or ':' at all — groups "flagBoundary" (the required
//     start-of-string-or-whitespace immediately before the dash, echoed
//     back unchanged), "flagDash", "flagPrefix" (an optional run of
//     letters between the dash and the keyword, see below) and "flagKey".
//     This is PowerShell's dominant parameter form ("-Password 'value'").
//
// In both shapes the value, if quoted, is matched wholly (including
// embedded spaces) via the `"[^"]*"` / `'[^']*'` alternatives tried before
// the bare `\S+` fallback — a bare \S+ alone stops at the first space and
// would leave the remainder of a quoted secret unredacted on the line. The
// value group repeats (a trailing `+`) so shell-legal adjacent-quote
// concatenation ('pa'ss'word', one token to a shell) is consumed in full
// rather than stopping after the first quoted span and leaking the rest
// next to the "***".
//
// flagPrefix supports suffix matching: real AD/PowerShell secret
// parameters commonly don't start with the bare keyword, e.g.
// "-NewPassword" rather than "-Password". This is deliberately scoped to
// the flag-style shape only, and only fires once flagBoundary has matched
// a real token boundary (start of string or whitespace) immediately before
// the dash — a dash glued to a preceding identifier, as in the cmdlet name
// "Set-ADAccountPassword", never qualifies, so the cmdlet name itself is
// never mistaken for a "-...Password" flag.
var redactRe = regexp.MustCompile(`(?i)(?:(?P<flagBoundary>^|\s)(?P<flagDash>-{1,2})(?P<flagPrefix>[A-Za-z]*?)(?P<flagKey>password|passwd|pwd|token|secret|apikey|api_key)\s+(?:"[^"]*"|'[^']*'|\S+)+)|(?:(?P<eqKey>password|passwd|pwd|token|secret|apikey|api_key)\s*[=:]\s*(?:"[^"]*"|'[^']*'|\S+)+)`)

// Redact replaces every secret-bearing occurrence matched by redactRe
// (see its doc comment for the two supported shapes) with the key name
// followed by "=***", normalizing '=' / ':' / a bare whitespace separator
// alike, and dropping the value — including a quoted value's internal
// spaces — entirely.
func Redact(s string) string {
	names := redactRe.SubexpNames()
	return redactRe.ReplaceAllStringFunc(s, func(m string) string {
		sub := redactRe.FindStringSubmatch(m)
		vals := make(map[string]string, len(names))
		for i, name := range names {
			if name != "" {
				vals[name] = sub[i]
			}
		}
		if vals["flagKey"] != "" {
			return vals["flagBoundary"] + vals["flagDash"] + vals["flagPrefix"] + vals["flagKey"] + "=***"
		}
		return vals["eqKey"] + "=***"
	})
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
	SetupErr                                                   string `json:",omitempty"`
	LocalError                                                 string `json:",omitempty"`
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

	f, err := os.OpenFile(filepath.Join(root, "audit.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600) // #nosec G304 -- filename is fixed beneath the configured local root.
	if err != nil {
		return err
	}
	if _, err := f.Write(b); err != nil {
		_ = f.Close()
		return err
	}
	return f.Close()
}
