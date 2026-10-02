package artifact

import (
	"bytes"
	"strings"
	"testing"
	"unicode/utf8"
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

func TestStatusLineReportsExplicitlyAcceptedHostKey(t *testing.T) {
	m := meta()
	m.AcceptedHostKeyAlgorithm = "ssh-ed25519"
	m.AcceptedHostKeyFingerprint = "SHA256:abc123"
	got := StatusLine(m)
	for _, want := range []string{
		"accepted-host-key-algorithm=ssh-ed25519",
		"accepted-host-key-fingerprint=SHA256:abc123",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("status line missing %q: %q", want, got)
		}
	}
}

func TestStatusLineLocalErrorTakesPrecedence(t *testing.T) {
	m := meta()
	m.LocalError = "output-limit"
	m.TransportErr = "timeout"
	m.Exit = 23
	got := StatusLine(m)
	if !strings.Contains(got, "local-error=output-limit") || strings.Contains(got, "transport-error=") || strings.Contains(got, "exit=") {
		t.Fatalf("bad line: %q", got)
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

func TestStatusLineSetupErrorForm(t *testing.T) {
	m := meta()
	m.SetupErr = "windows-shell"
	m.TransportErr = "ssh"
	m.Exit = 23
	got := StatusLine(m)
	if !strings.Contains(got, "setup-error=windows-shell") || strings.Contains(got, "transport-error=") || strings.Contains(got, "exit=") {
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

// An unbounded tail, a per-line rather than per-preview limit, or a byte cut
// through a rune must fail these checks. The 80-byte allowance covers only
// preview framing/notice, never output-dependent metadata or body bytes.
func TestPassportPreviewBudget(t *testing.T) {
	for _, tc := range []struct {
		name   string
		body   string
		budget int
	}{
		{"one long line", strings.Repeat("x", 200000), 300},
		{"three long lines", strings.Repeat(strings.Repeat("y", 3000)+"\n", 3), 500},
		{"compressed JSON", `{"data":"` + strings.Repeat("x", 10000) + `"}`, 100},
		{"multiline", strings.Repeat("ordinary line\n", 500), 100},
		{"clipped multiline tail", "old\n1111\n2222\n3333", 3},
		{"multibyte", strings.Repeat("я日🙂", 1000), 101},
		{"tiny budget", strings.Repeat("日", 50), 1},
		{"zero budget", "not shown", 0},
		{"negative budget", "not shown", -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := meta()
			body := []byte(tc.body)
			original := bytes.Clone(body)
			p := RenderPassport(m, "/tmp/a17", body, tc.budget)
			prefix := StatusLine(m) + "\nfile=/tmp/a17"
			if !strings.HasPrefix(p, prefix) {
				t.Fatal("artifact identity/metadata changed")
			}
			preview := strings.TrimPrefix(p, prefix)
			if len(preview) > max(0, tc.budget)*4+80 {
				t.Errorf("preview/framing = %d bytes, exceeds %d-byte body budget + 80", len(preview), max(0, tc.budget)*4)
			}
			framed, _, _ := strings.Cut(strings.TrimPrefix(preview, "\ntail3:"), "\npreview omitted")
			payload := strings.ReplaceAll(strings.TrimPrefix(framed, "\n  "), "\n  ", "\n")
			if len(payload) > max(0, tc.budget)*4 || !strings.HasSuffix(strings.TrimRight(tc.body, "\n"), payload) {
				t.Errorf("copied payload is not a suffix within the byte budget: %d bytes", len(payload))
			}
			if !utf8.ValidString(p) {
				t.Error("preview split a UTF-8 rune")
			}
			if !strings.Contains(preview, "preview omitted") || !strings.Contains(preview, "sshai q <id> -- <tool> <args>") {
				t.Error("omission must be explicit with query guidance")
			}
			if strings.Contains(p, "truncated=1") || !bytes.Equal(body, original) {
				t.Error("preview omission changed capture semantics or input bytes")
			}
		})
	}
}

func TestPassportClipsTailNotHeadAtRuneBoundary(t *testing.T) {
	for _, tc := range []struct {
		body string
		want string
	}{
		{"old\nabcdefgh", "efgh"},
		{"old\nабв", "бв"},
		{"old\n日本語", "語"},
		{"old\n🙂🙂", "🙂"},
		{"old\n🙂abc", "abc"},
	} {
		p := RenderPassport(meta(), "/tmp/a17", []byte(tc.body), 1)
		_, tail, ok := strings.Cut(p, "\ntail3:\n  ")
		if !ok {
			t.Fatalf("missing tail for %q", tc.body)
		}
		got, _, ok := strings.Cut(tail, "\npreview omitted")
		if !ok || got != tc.want {
			t.Errorf("tail for %q = %q; want %q", tc.body, got, tc.want)
		}
	}
}

func TestPassportShortBodyAndCaptureFlags(t *testing.T) {
	for _, body := range []string{"", "ok\n", "abcd", "abc\n", "🙂"} {
		m := meta()
		p := RenderPassport(m, "/tmp/a17", []byte(body), 1)
		want := StatusLine(m) + "\nfile=/tmp/a17"
		if body != "" {
			want += "\n" + strings.TrimRight(body, "\n")
		}
		if p != want {
			t.Errorf("short body %q: got %q want %q", body, p, want)
		}
	}
	m := meta()
	m.Truncated = true
	p := RenderPassport(m, "/tmp/a17", []byte(strings.Repeat("x", 100)), 1)
	if !strings.Contains(p, "truncated=1") || !strings.Contains(p, "preview omitted") {
		t.Fatal("capture truncation and preview omission must remain distinct")
	}
	m.Binary = true
	p = RenderPassport(m, "/tmp/a17", []byte{0, 1, 2, 3, 4}, 1)
	if p != StatusLine(m)+"\nfile=/tmp/a17" {
		t.Fatal("binary output must remain metadata-only")
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
