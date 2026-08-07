// internal/delta/delta.go
package delta

import (
	"crypto/sha256"
	"encoding/hex"
	"regexp"
	"strings"
)

var wsRe = regexp.MustCompile(`\s+`)

// Key identifies "the same command on the same host in the same context" for
// --delta lookups. Body-file runs pass "body:"+sha256hex(body)[:16] as command.
func Key(host, ctx, command string) string {
	norm := wsRe.ReplaceAllString(strings.TrimSpace(command), " ")
	sum := sha256.Sum256([]byte(host + "\x00" + ctx + "\x00" + norm))
	return hex.EncodeToString(sum[:])[:16]
}
