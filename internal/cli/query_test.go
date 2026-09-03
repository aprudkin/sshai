// internal/cli/query_test.go
package cli

import (
	"bytes"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/aprudkin/sshai/internal/artifact"
)

// newQueryTestStore opens a store rooted at a fresh t.TempDir() (via
// SSHAI_ROOT, matching config.Load()'s own precedence) and saves two
// artifacts that differ by exactly one line, returning their ids.
func newQueryTestStore(t *testing.T) (root, id1, id2 string) {
	t.Helper()
	root = t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	m1, err := st.Save(artifact.Meta{Host: "h1", Ctx: "default", Command: "cat f1", Ts: time.Now()}, "k1", []byte("alpha\nbeta\n"))
	if err != nil {
		t.Fatal(err)
	}
	m2, err := st.Save(artifact.Meta{Host: "h1", Ctx: "default", Command: "cat f2", Ts: time.Now()}, "k2", []byte("alpha\ngamma\n"))
	if err != nil {
		t.Fatal(err)
	}
	return root, m1.ID, m2.ID
}

func TestQRunsLocalToolOverArtifact(t *testing.T) {
	_, id1, _ := newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Q([]string{id1, "--", "grep", "beta"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "beta") {
		t.Fatalf("stdout missing %q: %q", "beta", out.String())
	}
	if strings.Contains(out.String(), "gamma") {
		t.Fatalf("stdout must not contain %q (wrong artifact): %q", "gamma", out.String())
	}
}

func TestQUnknownIDExits96(t *testing.T) {
	newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Q([]string{"a999", "--", "grep", "beta"}, &out, &errB)
	if rc != 96 {
		t.Fatalf("rc=%d, want 96; stderr=%s", rc, errB.String())
	}
	if errB.String() == "" {
		t.Fatal("expected an error message on stderr for an unknown id")
	}
}

func TestDiffShowsAddedAndRemovedLines(t *testing.T) {
	_, id1, id2 := newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Diff([]string{id1, id2}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "-beta") {
		t.Fatalf("diff missing removed line %q: %q", "-beta", out.String())
	}
	if !strings.Contains(out.String(), "+gamma") {
		t.Fatalf("diff missing added line %q: %q", "+gamma", out.String())
	}
}

func TestDiffOfArtifactWithItselfPrintsNoDifference(t *testing.T) {
	_, id1, _ := newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Diff([]string{id1, id1}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "no difference") {
		t.Fatalf("stdout = %q, want it to contain %q", out.String(), "no difference")
	}
}

// TestQBudgetTrimAppendsClipMessage covers the trim path directly: a
// --budget too small for the tool's own output must clip it and append the
// brief's exact clip message, not silently truncate.
func TestQBudgetTrimAppendsClipMessage(t *testing.T) {
	_, id1, _ := newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Q([]string{"--budget", "1", id1, "--", "cat"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "output clipped at budget") {
		t.Fatalf("stdout not clipped despite --budget 1: %q", out.String())
	}
	if strings.Contains(out.String(), "beta") {
		t.Fatalf("clipped output should not still contain the tail of the artifact: %q", out.String())
	}
}

// markPruned flips the given artifact's row to pruned=1 directly in SQLite
// (mirroring internal/artifact's own TestGetUnknownAndPruned, since there is
// no exported "prune" API yet — gc/retention is a later task), simulating
// artifact.ErrPruned's "metadata intact, bytes gone" state for Q/Diff.
func markPruned(t *testing.T, root, id string) {
	t.Helper()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	if _, err := st.DB.Exec(`UPDATE runs SET pruned=1 WHERE art_id=?`, id); err != nil {
		t.Fatal(err)
	}
}

// TestQPrunedIDExits96WithDistinctMessage covers the pruned branch of
// resolveArtifact: same exit code as an unknown id (96, per the brief) but
// a message that names the id as pruned rather than unknown, so a caller
// can tell the two apart from stderr alone.
func TestQPrunedIDExits96WithDistinctMessage(t *testing.T) {
	root, id1, _ := newQueryTestStore(t)
	markPruned(t, root, id1)

	var out, errB bytes.Buffer
	rc := Q([]string{id1, "--", "grep", "beta"}, &out, &errB)
	if rc != 96 {
		t.Fatalf("rc=%d, want 96; stderr=%s", rc, errB.String())
	}
	if !strings.Contains(errB.String(), "pruned") {
		t.Fatalf("stderr must name %s as pruned, got %q", id1, errB.String())
	}
	if strings.Contains(errB.String(), "unknown") {
		t.Fatalf("pruned id must not be reported as unknown: %q", errB.String())
	}
}

// TestDiffPrunedIDExits96WithDistinctMessage is Diff's counterpart to
// TestQPrunedIDExits96WithDistinctMessage.
func TestDiffPrunedIDExits96WithDistinctMessage(t *testing.T) {
	root, id1, id2 := newQueryTestStore(t)
	markPruned(t, root, id2)

	var out, errB bytes.Buffer
	rc := Diff([]string{id1, id2}, &out, &errB)
	if rc != 96 {
		t.Fatalf("rc=%d, want 96; stderr=%s", rc, errB.String())
	}
	if !strings.Contains(errB.String(), "pruned") {
		t.Fatalf("stderr must name %s as pruned, got %q", id2, errB.String())
	}
}

// TestQMissingToolBinaryExits96 covers the exec.LookPath failure branch:
// a tool name that resolves to nothing on PATH must fail with the reserved
// usage exit code, not a transport or generic error code.
func TestQMissingToolBinaryExits96(t *testing.T) {
	_, id1, _ := newQueryTestStore(t)

	var out, errB bytes.Buffer
	rc := Q([]string{id1, "--", "definitely-not-a-real-tool-xyz"}, &out, &errB)
	if rc != 96 {
		t.Fatalf("rc=%d, want 96; stderr=%s", rc, errB.String())
	}
	if errB.String() == "" {
		t.Fatal("expected an error message on stderr for a missing tool binary")
	}
}

// TestQBudgetTrimIsValidUTF8AtMultiByteBoundary guards trimToBudget's
// rune-boundary backoff: a raw byte cut at budgetTokens*4 can land inside a
// multi-byte rune (this tool's remote output is arbitrary UTF-8 — Cyrillic
// hostnames, pwsh CLIXML, etc.), which would hand the agent an invalid,
// mojibake-producing string. The artifact is 50 repeats of "привет мир "
// (Cyrillic is 2-byte UTF-8; the trailing ASCII space shifts alignment
// every repeat), and --budget 4 is not an arbitrary small number: it was
// picked by computing the byte layout by hand and confirming maxBytes=16
// lands on a continuation byte (the second byte of "и") rather than a rune
// start — i.e. the naive cut this test guards against is a real, verified
// failure mode here, not a hoped-for one.
func TestQBudgetTrimIsValidUTF8AtMultiByteBoundary(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	cyrillic := strings.Repeat("привет мир ", 50)
	m, err := st.Save(artifact.Meta{Host: "h1", Ctx: "default", Command: "cat", Ts: time.Now()}, "k1", []byte(cyrillic))
	if err != nil {
		t.Fatal(err)
	}
	st.Close()

	var out, errB bytes.Buffer
	rc := Q([]string{"--budget", "4", m.ID, "--", "cat"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "output clipped at budget") {
		t.Fatalf("expected a clip with --budget 4 against %d bytes of Cyrillic: %q", len(cyrillic), out.String())
	}
	if !utf8.ValidString(out.String()) {
		t.Fatalf("clipped output is not valid UTF-8: %q", out.String())
	}
}

// signalKillTr is not a transport fake — Q never touches the network — it
// just names what this test targets: cmd.Run()'s error path when the local
// tool dies from a signal rather than exiting normally, where ExitCode()
// returns a negative number per its own documented contract.
//
// TestQSignalKilledToolExits96NotNegative covers that branch: passing a
// negative "exit code" straight to os.Exit would wrap to a nonsensical
// status (e.g. -1 -> 255, indistinguishable from a real exit 255), so Q
// must recognize it and fall back to the reserved usage code instead.
func TestQSignalKilledToolExits96NotNegative(t *testing.T) {
	_, id1, _ := newQueryTestStore(t)

	var out, errB bytes.Buffer
	// "sh -c 'kill -9 $$' <path>": the appended path becomes $0 inside the
	// script, which the script itself never uses, and the process kills
	// itself with SIGKILL before producing any output.
	rc := Q([]string{id1, "--", "sh", "-c", "kill -9 $$"}, &out, &errB)
	if rc != 96 {
		t.Fatalf("rc=%d, want 96 (usage) for a signal-killed tool; stderr=%s", rc, errB.String())
	}
	if errB.String() == "" {
		t.Fatal("expected an error message on stderr for a signal-killed tool")
	}
}
