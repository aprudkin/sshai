// internal/cli/help_test.go
package cli

import (
	"bytes"
	"strings"
	"testing"

	"github.com/aprudkin/sshai/internal/artifact"
)

// TestHelpDefaultListsEverySubcommandUnderTokenBudget is the brief's
// mandated Step 1 case for the bare `sshai help` screen (R5's
// progressive-disclosure surface, design doc): every subcommand name must
// appear, and the whole screen must stay under 400 estimated tokens so an
// agent can afford to read it on every session.
func TestHelpDefaultListsEverySubcommandUnderTokenBudget(t *testing.T) {
	var out, errB bytes.Buffer
	rc := Help(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	s := out.String()
	for _, summaryPrefix := range []string{
		"  run [flags] ",
		"  q [--budget N] ",
		"  diff [--budget N] ",
		"  log [--host H] ",
		"  hosts ",
		"  gc ",
		"  help [command] ",
	} {
		if !strings.Contains(s, summaryPrefix) {
			t.Fatalf("default help missing summary prefix %q: %q", summaryPrefix, s)
		}
	}
	if got := artifact.EstTokens(out.Bytes()); got >= 400 {
		t.Fatalf("default help is %d estimated tokens, want < 400: %q", got, s)
	}
}

// TestHelpRunShowsFullFlagReference is the brief's mandated Step 1 case for
// `sshai help run`: every flag run.go actually defines must be documented,
// verbatim by name, so an agent reading this once has the real interface —
// not a stale or invented one.
func TestHelpRunShowsFullFlagReference(t *testing.T) {
	var out, errB bytes.Buffer
	rc := Help([]string{"run"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	s := out.String()
	for _, flag := range []string{"--body-file", "--powershell-host", "--accept-new-host-key", "--proxy-jump", "--delta", "--budget", "--timeout", "--ctx", "--result-format", "--result-out"} {
		if !strings.Contains(s, flag) {
			t.Fatalf("help run missing flag %q: %q", flag, s)
		}
	}
	for _, want := range []string{"captured output up to the configured stream cap", "factory default 500", "factory default 60"} {
		if !strings.Contains(s, want) {
			t.Fatalf("help run missing accuracy qualifier %q: %q", want, s)
		}
	}
}

func TestHelpQExplainsFinalArtifactPathArgument(t *testing.T) {
	var out, errB bytes.Buffer
	if rc := Help([]string{"q"}, &out, &errB); rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	for _, want := range []string{
		"artifact is not sent on stdin",
		"<tool> <args...> <artifact-path>",
		"sys.argv[-1]",
	} {
		if !strings.Contains(out.String(), want) {
			t.Fatalf("help q missing %q: %q", want, out.String())
		}
	}
}

func TestHelpFlaggedSubcommandsDocumentEveryFlag(t *testing.T) {
	for topic, flags := range map[string][]string{
		"q":    {"--budget"},
		"diff": {"--budget"},
		"log":  {"--host", "--since", "--grep", "--limit"},
	} {
		var out, errB bytes.Buffer
		if rc := Help([]string{topic}, &out, &errB); rc != 0 {
			t.Fatalf("help %s: rc=%d stderr=%s", topic, rc, errB.String())
		}
		for _, flag := range flags {
			if !strings.Contains(out.String(), flag) {
				t.Errorf("help %s missing flag %q: %q", topic, flag, out.String())
			}
		}
	}
}

// TestHelpUnknownCommandExitsUsage covers the one behavior the brief leaves
// implicit but any CLI needs: `sshai help bogus` must not silently print
// nothing or exit 0 — it is a usage error, same as any other unrecognized
// subcommand name in this codebase (see cmd/sshai/main.go's own "unknown
// command" path).
func TestHelpUnknownCommandExitsUsage(t *testing.T) {
	var out, errB bytes.Buffer
	rc := Help([]string{"bogus"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stdout=%q stderr=%q", rc, exitUsage, out.String(), errB.String())
	}
	if errB.Len() == 0 {
		t.Fatal("expected an error message on stderr")
	}
}

// TestHelpNeverTouchesStoreOrConfig proves Help works with no ~/.sshai at
// all: a fresh agent's very first invocation might be `sshai help`, before
// any config.toml or store exists, and unlike every other subcommand it
// must not require SSHAI_ROOT to point at a writable, initialized root. A
// bogus, non-existent root is passed deliberately — if Help ever grows a
// config.Load() or artifact.OpenStore() call, this would start failing.
func TestHelpNeverTouchesStoreOrConfig(t *testing.T) {
	t.Setenv("SSHAI_ROOT", "/nonexistent/does-not-exist-and-must-not-be-created")

	var out, errB bytes.Buffer
	rc := Help(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if out.Len() == 0 {
		t.Fatal("expected default help output")
	}
}

// TestHelpAllSubcommandsHaveDetail asserts every name the default screen
// lists also resolves via `sshai help <name>` — no topic advertised on the
// summary screen that then 404s.
func TestHelpAllSubcommandsHaveDetail(t *testing.T) {
	for _, name := range []string{"run", "q", "diff", "log", "hosts", "gc", "help"} {
		var out, errB bytes.Buffer
		rc := Help([]string{name}, &out, &errB)
		if rc != 0 {
			t.Fatalf("help %s: rc=%d stderr=%s", name, rc, errB.String())
		}
		if out.Len() == 0 {
			t.Fatalf("help %s: empty output", name)
		}
	}
}
