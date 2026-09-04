// internal/delta/delta.go
package delta

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/pmezard/go-difflib/difflib"
)

var wsRe = regexp.MustCompile(`\s+`)

// Key identifies "the same command on the same host in the same context" for
// --delta lookups. Body-file runs pass "body:"+sha256hex(body)[:16] as command.
func Key(host, ctx, command string) string {
	norm := wsRe.ReplaceAllString(strings.TrimSpace(command), " ")
	sum := sha256.Sum256([]byte(host + "\x00" + ctx + "\x00" + norm))
	return hex.EncodeToString(sum[:])[:16]
}

// deltaClipSuffix is appended when Render's diff is trimmed to fit
// budgetTokens. Unlike cli's q/diff clipSuffix, the raw bytes are not lost —
// the full new artifact is always stored (run.go's own invariant), so the
// message points at `sshai diff` instead of claiming the data is gone.
const deltaClipSuffix = "\n… [diff clipped at budget — full artifacts kept, rerun with --budget N or `sshai diff <old> <new>`]"

// Render implements --delta's body: byte-equal old/new data produces the
// ~20-token "no change since <prevID> (<age>)" line (the design doc's own
// example: "no change since a12 (3m ago)"); otherwise a unified diff
// (context 3, matching cli.Diff's own shape) against the previous
// artifact's bytes at prevPath, budget-trimmed the same way cli.Q/Diff trim
// their own output (tokens ≈ bytes/4, never cutting a UTF-8 rune in half).
func Render(prevPath string, newData []byte, prevID string, prevTs time.Time, budgetTokens int) (string, error) {
	prevData, err := os.ReadFile(prevPath) // #nosec G304 -- prevPath is resolved from the local artifact database.
	if err != nil {
		return "", fmt.Errorf("read previous artifact %s: %w", prevPath, err)
	}

	if bytes.Equal(prevData, newData) {
		return fmt.Sprintf("no change since %s (%s)", prevID, humanizeAge(time.Since(prevTs))), nil
	}

	diffText, err := difflib.GetUnifiedDiffString(difflib.UnifiedDiff{
		A:        difflib.SplitLines(string(prevData)),
		B:        difflib.SplitLines(string(newData)),
		FromFile: prevID,
		ToFile:   "new",
		Context:  3,
	})
	if err != nil {
		return "", fmt.Errorf("compute diff against %s: %w", prevID, err)
	}
	return trimToBudget(diffText, budgetTokens), nil
}

// humanizeAge renders d as a short "<N><unit> ago" string, e.g. "3m ago" or
// "2h ago" — matching the design doc's `no change since a12 (3m ago)`
// example. artifact.HumanDuration (used for a run's own duration) only
// covers the ms/s range and has no "ago" framing, so a delta age — which
// can span minutes, hours or days between runs of the same command — needs
// this small local humanizer instead.
func humanizeAge(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	switch {
	case d < time.Minute:
		return fmt.Sprintf("%ds ago", int(d.Seconds()))
	case d < time.Hour:
		return fmt.Sprintf("%dm ago", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh ago", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd ago", int(d.Hours()/24))
	}
}

// trimToBudget truncates s to approximately budgetTokens tokens (using
// artifact.EstTokens's own ~4-bytes-per-token estimate, inverted) and
// appends deltaClipSuffix when truncation happened. This package can't
// reuse cli.trimToBudget (package cli is unexported there, and importing it
// from here would invert the existing cli -> delta dependency into a
// cycle), so this is a small local copy of the same bytes/4 math, per the
// task brief's explicit allowance.
func trimToBudget(s string, budgetTokens int) string {
	if budgetTokens <= 0 || artifact.EstTokens([]byte(s)) <= budgetTokens {
		return s
	}
	maxBytes := budgetTokens * 4
	if maxBytes > len(s) {
		maxBytes = len(s)
	}
	if maxBytes < 0 {
		maxBytes = 0
	}
	// Diff output can contain arbitrary UTF-8 (the command's own remote
	// output), so a raw byte cut can land mid-rune. Back off to the start
	// of the rune straddling the cut, never forward, so the result never
	// exceeds the requested budget by even one byte.
	for maxBytes > 0 && maxBytes < len(s) && !utf8.RuneStart(s[maxBytes]) {
		maxBytes--
	}
	return s[:maxBytes] + deltaClipSuffix
}
