package artifact

import (
	"fmt"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"
)

type Meta struct {
	ID, Host, Ctx, Command     string
	Exit                       int
	TransportErr               string
	TransportDiagnostic        string
	SetupErr                   string
	SetupDiagnostic            string
	LocalError                 string
	FailurePhase               string
	RemoteCompletion           string
	AcceptedHostKeyAlgorithm   string
	AcceptedHostKeyFingerprint string
	Bytes, Lines               int64
	SHA256                     string
	DurationMs                 int64
	Truncated, Binary          bool
	DeltaBase                  string
	Ts                         time.Time
}

// failureEvidence accepts only paired, control-flow evidence on remote errors.
// Empty legacy metadata is never upgraded by inference from an error or exit.
func failureEvidence(m Meta) (phase, completion string) {
	if m.LocalError != "" || (m.SetupErr == "" && m.TransportErr == "") {
		return "", ""
	}
	if m.SetupErr != "" && (m.FailurePhase != "probe" || m.RemoteCompletion != "not_started") {
		return "", ""
	}
	switch m.FailurePhase {
	case "probe", "stage":
		if m.RemoteCompletion == "not_started" {
			return m.FailurePhase, m.RemoteCompletion
		}
	case "exec":
		if m.RemoteCompletion == "not_started" || m.RemoteCompletion == "unknown" {
			return m.FailurePhase, m.RemoteCompletion
		}
	}
	return "", ""
}

func EstTokens(b []byte) int { return (len(b) + 3) / 4 }

func HumanBytes(n int64) string {
	switch {
	case n >= 1<<20:
		return fmt.Sprintf("%.1fM", float64(n)/(1<<20))
	case n >= 1<<10:
		return fmt.Sprintf("%dK", n/(1<<10))
	default:
		return fmt.Sprintf("%dB", n)
	}
}

func HumanDuration(ms int64) string {
	if ms >= 1000 {
		return fmt.Sprintf("%.1fs", float64(ms)/1000)
	}
	return fmt.Sprintf("%dms", ms)
}

func StatusLine(m Meta) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s host=%s", m.ID, m.Host)
	if m.LocalError != "" {
		fmt.Fprintf(&b, " local-error=%s", m.LocalError)
	} else if m.SetupErr != "" {
		fmt.Fprintf(&b, " setup-error=%s", m.SetupErr)
	} else if m.TransportErr != "" {
		fmt.Fprintf(&b, " transport-error=%s", m.TransportErr)
	} else {
		fmt.Fprintf(&b, " exit=%d", m.Exit)
	}
	if phase, completion := failureEvidence(m); phase != "" {
		fmt.Fprintf(&b, " failure-phase=%s remote-completion=%s", phase, completion)
	}
	if m.AcceptedHostKeyAlgorithm != "" && m.AcceptedHostKeyFingerprint != "" {
		fmt.Fprintf(&b, " accepted-host-key-algorithm=%s accepted-host-key-fingerprint=%s",
			m.AcceptedHostKeyAlgorithm, m.AcceptedHostKeyFingerprint)
	}
	fmt.Fprintf(&b, " lines=%d bytes=%s time=%s", m.Lines, HumanBytes(m.Bytes), HumanDuration(m.DurationMs))
	if m.Truncated {
		b.WriteString(" truncated=1")
	}
	if m.Binary {
		b.WriteString(" binary=1")
	}
	if m.DeltaBase != "" {
		fmt.Fprintf(&b, " delta=%s", m.DeltaBase)
	}
	return b.String()
}

var pipeRe = regexp.MustCompile(`\|\s*(tail|head|grep)\b[^|]*$`)

func PipeAdvisory(command string) string {
	if pipeRe.MatchString(command) {
		return "note: trailing filter discarded data the artifact would have kept; prefer `sshai q <id>`"
	}
	return ""
}

// RenderPassport limits copied body bytes to 4*max(budgetTokens, 0). If the
// body exceeds that estimate, it shows a suffix of the final three lines,
// advancing a clipped start to a UTF-8 rune boundary. Tail framing and the
// omission notice add at most 80 bytes; the unchanged status/path metadata
// and any caller-added newline are outside that limit. It never mutates body
// or the capture's Truncated flag.
func RenderPassport(m Meta, artPath string, body []byte, budgetTokens int) string {
	var b strings.Builder
	b.WriteString(StatusLine(m))
	b.WriteString("\nfile=" + artPath)
	if m.Binary || len(body) == 0 {
		return b.String()
	}
	if EstTokens(body) <= budgetTokens {
		b.WriteString("\n" + strings.TrimRight(string(body), "\n"))
		return b.String()
	}
	lines := strings.Split(strings.TrimRight(string(body), "\n"), "\n")
	n := 3
	if len(lines) < n {
		n = len(lines)
	}
	tail := strings.Join(lines[len(lines)-n:], "\n")
	if budgetTokens <= 0 {
		tail = ""
	} else if EstTokens([]byte(tail)) > budgetTokens {
		// Multiply only after comparing to the tail size, so even an
		// arbitrarily large requested budget cannot overflow this bound.
		start := len(tail) - budgetTokens*4
		for start < len(tail) && !utf8.RuneStart(tail[start]) {
			start++
		}
		tail = tail[start:]
	}
	b.WriteString("\ntail3:")
	if tail != "" {
		for _, ln := range strings.Split(tail, "\n") {
			b.WriteString("\n  " + ln)
		}
	}
	b.WriteString("\npreview omitted; use sshai q <id> -- <tool> <args> (id above)")
	return b.String()
}
