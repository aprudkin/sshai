package artifact

import (
	"fmt"
	"regexp"
	"strings"
	"time"
)

type Meta struct {
	ID, Host, Ctx, Command string
	Exit                   int
	TransportErr           string
	Bytes, Lines           int64
	SHA256                 string
	DurationMs             int64
	Truncated, Binary      bool
	DeltaBase              string
	Ts                     time.Time
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
	if m.TransportErr != "" {
		fmt.Fprintf(&b, " transport-error=%s", m.TransportErr)
	} else {
		fmt.Fprintf(&b, " exit=%d", m.Exit)
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
	b.WriteString("\ntail3:")
	for _, ln := range lines[len(lines)-n:] {
		b.WriteString("\n  " + ln)
	}
	return b.String()
}
