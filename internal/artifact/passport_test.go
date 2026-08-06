package artifact

import (
	"strings"
	"testing"
)

func meta() Meta {
	return Meta{ID: "a17", Host: "pg-prod-01", Ctx: "default", Exit: 0,
		Bytes: 612340, Lines: 8412, DurationMs: 1800}
}

func TestStatusLineExitForm(t *testing.T) {
	got := StatusLine(meta())
	want := "a17 host=pg-prod-01 exit=0 lines=8412 bytes=597K time=1.8s"
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestStatusLineTransportFormAndFlags(t *testing.T) {
	m := meta()
	m.TransportErr = "timeout"
	m.Truncated = true
	got := StatusLine(m)
	if !strings.Contains(got, "transport-error=timeout") || strings.Contains(got, "exit=") ||
		!strings.Contains(got, "truncated=1") {
		t.Fatalf("bad line: %q", got)
	}
}

func TestPassportTiering(t *testing.T) {
	small := []byte("ok\n")
	p := RenderPassport(meta(), "/tmp/a17", small, 500)
	if !strings.Contains(p, "ok") || strings.Contains(p, "tail3:") {
		t.Fatalf("small body must inline fully: %q", p)
	}
	big := []byte(strings.Repeat("line of text here\n", 500))
	p = RenderPassport(meta(), "/tmp/a17", big, 500)
	if !strings.Contains(p, "tail3:") || strings.Count(p, "line of text here") != 3 {
		t.Fatalf("big body must show tail3: %q", p)
	}
}

func TestPassportMetadataOnlyUnder200Tokens(t *testing.T) {
	m := meta()
	m.Binary = true // suppresses body entirely
	p := RenderPassport(m, "/home/user/.sshai/art/a17", nil, 500)
	if EstTokens([]byte(p)) >= 200 {
		t.Fatalf("metadata-only passport too big: %d tokens", EstTokens([]byte(p)))
	}
}

func TestPipeAdvisory(t *testing.T) {
	if PipeAdvisory("journalctl -u x | grep err") == "" {
		t.Fatal("want advisory for trailing grep")
	}
	if PipeAdvisory("grep err /var/log/syslog") != "" {
		t.Fatal("grep as the command itself is fine")
	}
}
