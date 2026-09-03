// internal/cli/misc_test.go
package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/session"
)

func setTestUserHome(t *testing.T, home string) {
	t.Helper()
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	if runtime.GOOS == "windows" {
		volume := filepath.VolumeName(home)
		if volume != "" {
			t.Setenv("HOMEDRIVE", volume)
			t.Setenv("HOMEPATH", strings.TrimPrefix(home, volume))
		}
	}
}

// ---- log ----

func TestLogPrintsNewestFirstWithClippedCommand(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	long := "journalctl -u postgres --since today --no-pager --output=short-iso -n 500 --follow please"
	if _, err := st.Save(artifact.Meta{Host: "pg-prod-01", Ctx: "default", Command: long, Exit: 0, DurationMs: 1800, Ts: time.Date(2026, 8, 6, 21, 4, 11, 0, time.UTC)}, "k1", []byte("x")); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Save(artifact.Meta{Host: "web01", Ctx: "default", Command: "uptime", Exit: 0, DurationMs: 100, Ts: time.Date(2026, 8, 6, 21, 5, 0, 0, time.UTC)}, "k2", []byte("x")); err != nil {
		t.Fatal(err)
	}
	st.Close()

	var out, errB bytes.Buffer
	rc := Log(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	lines := strings.Split(strings.TrimRight(out.String(), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 lines, got %d: %q", len(lines), out.String())
	}
	if !strings.HasPrefix(lines[0], "a2  2026-08-06T21:05:00Z  web01  exit=0  100ms  uptime") {
		t.Fatalf("newest-first: line0 = %q", lines[0])
	}
	if !strings.Contains(lines[1], "a1  2026-08-06T21:04:11Z  pg-prod-01  exit=0  1.8s  ") {
		t.Fatalf("line1 = %q", lines[1])
	}
	r := []rune(long)
	if strings.Contains(lines[1], string(r[60:])) {
		t.Fatalf("command must be clipped at 60 runes, full command leaked: %q", lines[1])
	}
	if !strings.Contains(lines[1], "…") {
		t.Fatalf("clipped command must carry the ellipsis marker: %q", lines[1])
	}
}

func TestLogHostAndGrepAndLimitFilters(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	st.Save(artifact.Meta{Host: "h1", Ctx: "default", Command: "df -h", Ts: time.Now()}, "k1", []byte("x"))
	st.Save(artifact.Meta{Host: "h2", Ctx: "default", Command: "uptime", Ts: time.Now()}, "k2", []byte("x"))
	st.Close()

	var out, errB bytes.Buffer
	rc := Log([]string{"--host", "h1"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "h1") || strings.Contains(out.String(), "h2") {
		t.Fatalf("--host filter failed: %q", out.String())
	}

	out.Reset()
	rc = Log([]string{"--grep", "uptime"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "uptime") || strings.Contains(out.String(), "df -h") {
		t.Fatalf("--grep filter failed: %q", out.String())
	}

	out.Reset()
	rc = Log([]string{"--limit", "1"}, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if len(strings.Split(strings.TrimRight(out.String(), "\n"), "\n")) != 1 {
		t.Fatalf("--limit 1 did not limit output: %q", out.String())
	}
}

func TestLogSinceRejectsGarbage(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	var out, errB bytes.Buffer
	rc := Log([]string{"--since", "not-a-time"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitUsage, errB.String())
	}
	if errB.Len() == 0 {
		t.Fatal("expected an error message on stderr")
	}
}

func TestParseSinceDurationsAndDate(t *testing.T) {
	now := time.Date(2026, 8, 7, 12, 0, 0, 0, time.UTC)

	got, err := parseSince("2h", now)
	if err != nil || !got.Equal(now.Add(-2*time.Hour)) {
		t.Fatalf("parseSince(2h) = %v, err=%v", got, err)
	}
	got, err = parseSince("30m", now)
	if err != nil || !got.Equal(now.Add(-30*time.Minute)) {
		t.Fatalf("parseSince(30m) = %v, err=%v", got, err)
	}
	got, err = parseSince("7d", now)
	if err != nil || !got.Equal(now.Add(-7*24*time.Hour)) {
		t.Fatalf("parseSince(7d) = %v, err=%v", got, err)
	}
	got, err = parseSince("2026-08-01", now)
	want := time.Date(2026, 8, 1, 0, 0, 0, 0, time.UTC)
	if err != nil || !got.Equal(want) {
		t.Fatalf("parseSince(date) = %v, err=%v, want %v", got, err, want)
	}
	if _, err := parseSince("not-a-time", now); err == nil {
		t.Fatal("parseSince(garbage) must error")
	}
}

// ---- hosts ----

func TestHostsParsesSSHConfigSkipsWildcardsAndMerges(t *testing.T) {
	home := t.TempDir()
	setTestUserHome(t, home)
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	sshDir := filepath.Join(home, ".ssh")
	if err := os.MkdirAll(sshDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfgText := "Host web01\nHost *.internal\nHost db01 db02\n"
	if err := os.WriteFile(filepath.Join(sshDir, "config"), []byte(cfgText), 0o600); err != nil {
		t.Fatal(err)
	}
	toml := "[hosts.cfgonly]\nos = \"linux\"\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}

	var out, errB bytes.Buffer
	rc := Hosts(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	s := out.String()
	for _, h := range []string{"web01", "db01", "db02", "cfgonly"} {
		if !strings.Contains(s, h) {
			t.Fatalf("missing host %q in output: %q", h, s)
		}
	}
	if strings.Contains(s, "*") {
		t.Fatalf("wildcard pattern leaked into output: %q", s)
	}
}

func TestHostsOSFallbackOrder(t *testing.T) {
	home := t.TempDir()
	setTestUserHome(t, home)
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	sshDir := filepath.Join(home, ".ssh")
	if err := os.MkdirAll(sshDir, 0o700); err != nil {
		t.Fatal(err)
	}
	cfgText := "Host web01\nHost dbhost\nHost mystery\n"
	if err := os.WriteFile(filepath.Join(sshDir, "config"), []byte(cfgText), 0o600); err != nil {
		t.Fatal(err)
	}
	toml := "[hosts.web01]\nos = \"linux-from-config\"\n\n[hosts.dbhost]\nos = \"windows-from-config\"\nreadonly = true\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := session.SaveFacts(root, "web01", session.Facts{OS: "linux-from-facts"}); err != nil {
		t.Fatal(err)
	}

	var out, errB bytes.Buffer
	rc := Hosts(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	s := out.String()
	if !strings.Contains(s, "web01  os=linux-from-facts") {
		t.Fatalf("facts.json OS must win over config: %q", s)
	}
	if !strings.Contains(s, "dbhost  os=windows-from-config  readonly=true") {
		t.Fatalf("config OS/readonly must be used when no facts exist: %q", s)
	}
	if !strings.Contains(s, "mystery  os=-  readonly=false") {
		t.Fatalf("host with neither facts nor config OS must show '-': %q", s)
	}
}

func TestHostsNoSSHConfigFileIsNotAnError(t *testing.T) {
	home := t.TempDir()
	setTestUserHome(t, home)
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	toml := "[hosts.onlyhost]\nos = \"linux\"\n"
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte(toml), 0o600); err != nil {
		t.Fatal(err)
	}

	var out, errB bytes.Buffer
	rc := Hosts(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "onlyhost") {
		t.Fatalf("config-only host missing: %q", out.String())
	}
}

// ---- gc ----

func TestGcStorePrunesByCutoffKeepsRowMarksPruned(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	m, err := st.Save(artifact.Meta{Host: "web01", Ctx: "default", Command: "df -h", Ts: time.Now()}, "k1", []byte("data"))
	if err != nil {
		t.Fatal(err)
	}

	// A cutoff strictly after the row's ts prunes it regardless of the
	// configured RetentionDays value — the brief's "RetentionDays=0
	// equivalent" case, expressed directly as a cutoff.
	cutoff := time.Now().Add(time.Hour)
	pruned, freed, err := gcStore(st, cutoff, 0, nil)
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 1 {
		t.Fatalf("pruned=%d, want 1", pruned)
	}
	if freed != int64(len("data")) {
		t.Fatalf("freed=%d, want %d", freed, len("data"))
	}

	if _, statErr := os.Stat(filepath.Join(root, "art", m.ID)); !os.IsNotExist(statErr) {
		t.Fatalf("artifact file must be gone, stat err=%v", statErr)
	}

	_, _, getErr := st.Get(m.ID)
	if getErr == nil || !strings.Contains(getErr.Error(), "artifact pruned") {
		t.Fatalf("Get after prune: want ErrPruned, got %v", getErr)
	}

	var count int
	if err := st.DB.QueryRow(`SELECT COUNT(*) FROM runs WHERE art_id=?`, m.ID).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("row count=%d, want 1 (row retained for audit history)", count)
	}
}

func TestGcStorePrunesOldestFirstUntilUnderSizeCap(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	now := time.Now()
	m1, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c1", Ts: now.Add(-3 * time.Hour)}, "k1", bytes.Repeat([]byte("a"), 100))
	if err != nil {
		t.Fatal(err)
	}
	m2, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c2", Ts: now.Add(-2 * time.Hour)}, "k2", bytes.Repeat([]byte("b"), 100))
	if err != nil {
		t.Fatal(err)
	}
	m3, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c3", Ts: now.Add(-1 * time.Hour)}, "k3", bytes.Repeat([]byte("c"), 100))
	if err != nil {
		t.Fatal(err)
	}

	// No age cutoff (zero time.Time disables age-based pruning); cap at
	// 150 bytes forces oldest-first pruning until the remaining total is
	// <= cap: removing m1 alone leaves 200 (still over), so m2 must go
	// too, leaving only m3's 100 bytes. nil protect: nothing exempted.
	pruned, freed, err := gcStore(st, time.Time{}, 150, nil)
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 2 {
		t.Fatalf("pruned=%d, want 2", pruned)
	}
	if freed != 200 {
		t.Fatalf("freed=%d, want 200", freed)
	}
	for _, id := range []string{m1.ID, m2.ID} {
		if _, _, err := st.Get(id); err == nil || !strings.Contains(err.Error(), "artifact pruned") {
			t.Fatalf("expected %s pruned, got err=%v", id, err)
		}
	}
	if _, path, err := st.Get(m3.ID); err != nil || path == "" {
		t.Fatalf("m3 (newest) must survive: path=%q err=%v", path, err)
	}
}

// TestGcStoreNilProtectPrunesEvenNewestRowUnderSizeCap is fix round 1's
// replacement for the removed "floor" heuristic (advisor review found it
// wrongly applied to standalone `sshai gc` too, defeating that command's
// own literal "oldest first until under" contract — a cap=1 test used to
// leave one row alive no matter how small the cap, 100x over cap). With
// no automatic exemption, a nil protect set (what Gc, the standalone
// command, always passes) prunes strictly oldest-first with NO
// exceptions: even the single newest row goes once nothing protects it.
func TestGcStoreNilProtectPrunesEvenNewestRowUnderSizeCap(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	now := time.Now()
	m1, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c1", Ts: now.Add(-2 * time.Hour)}, "k1", bytes.Repeat([]byte("a"), 100))
	if err != nil {
		t.Fatal(err)
	}
	m2, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c2", Ts: now.Add(-1 * time.Hour)}, "k2", bytes.Repeat([]byte("b"), 100))
	if err != nil {
		t.Fatal(err)
	}

	// cap=1, protect=nil: far smaller than even m2 (the newest row)
	// alone, and nothing exempts it — both rows must go.
	pruned, freed, err := gcStore(st, time.Time{}, 1, nil)
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 2 {
		t.Fatalf("pruned=%d, want 2 (nil protect exempts nothing, including the newest row)", pruned)
	}
	if freed != 200 {
		t.Fatalf("freed=%d, want 200", freed)
	}
	for _, id := range []string{m1.ID, m2.ID} {
		if _, _, err := st.Get(id); err == nil || !strings.Contains(err.Error(), "artifact pruned") {
			t.Fatalf("expected %s pruned under a nil protect set, got err=%v", id, err)
		}
	}
}

// TestGcStoreProtectExemptsFromBothAgeAndSizePasses covers the controller
// ruling's chosen scope for protect: a protected id is exempt from BOTH
// pruning passes, not just the size-based one — otherwise a
// RetentionDays=0 misconfiguration could still prune an artifact
// maybeGC's caller (run.go) meant to protect, via the age path instead of
// the size path. cutoff is set to prune the row by age, and maxBytes=1
// would also prune it by size; protecting its id must survive both.
func TestGcStoreProtectExemptsFromBothAgeAndSizePasses(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	m, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c", Ts: time.Now()}, "k", []byte("data"))
	if err != nil {
		t.Fatal(err)
	}

	cutoff := time.Now().Add(time.Hour) // would prune m by age if unprotected
	pruned, freed, err := gcStore(st, cutoff, 1, map[string]bool{m.ID: true})
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 0 {
		t.Fatalf("pruned=%d, want 0 (protected row must survive both the age cutoff and the size cap)", pruned)
	}
	if freed != 0 {
		t.Fatalf("freed=%d, want 0", freed)
	}
	if _, path, err := st.Get(m.ID); err != nil || path == "" {
		t.Fatalf("protected row must survive: path=%q err=%v", path, err)
	}
}

// TestGcStoreNilProtectStillPrunesByAge is the un-protected control for
// the test above: with protect=nil, the identical cutoff prunes the row
// exactly as before this fix round — protect is opt-in, not a change to
// gcStore's default (nil) behavior.
func TestGcStoreNilProtectStillPrunesByAge(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	m, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "c", Ts: time.Now()}, "k", []byte("data"))
	if err != nil {
		t.Fatal(err)
	}

	cutoff := time.Now().Add(time.Hour)
	pruned, _, err := gcStore(st, cutoff, 0, nil)
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 1 {
		t.Fatalf("pruned=%d, want 1 (nil protect must not exempt anything)", pruned)
	}
	if _, _, err := st.Get(m.ID); err == nil || !strings.Contains(err.Error(), "artifact pruned") {
		t.Fatalf("expected %s pruned by age under a nil protect set, got err=%v", m.ID, err)
	}
}

func TestGcStoreRemovesOldOrphanedTmpButKeepsFreshOnes(t *testing.T) {
	root := t.TempDir()
	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	artDir := filepath.Join(root, "art")
	oldTmp := filepath.Join(artDir, "a99.tmp")
	freshTmp := filepath.Join(artDir, "a100.tmp")
	if err := os.WriteFile(oldTmp, []byte("orphaned"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(freshTmp, []byte("in-flight"), 0o600); err != nil {
		t.Fatal(err)
	}
	old := time.Now().Add(-1 * time.Hour)
	if err := os.Chtimes(oldTmp, old, old); err != nil {
		t.Fatal(err)
	}
	// freshTmp keeps its just-written mtime, well within tmpOrphanAge.

	pruned, freed, err := gcStore(st, time.Time{}, 0, nil)
	if err != nil {
		t.Fatalf("gcStore: %v", err)
	}
	if pruned != 0 {
		t.Fatalf("pruned=%d, want 0 (no runs rows involved)", pruned)
	}
	if freed != int64(len("orphaned")) {
		t.Fatalf("freed=%d, want %d (only the orphaned tmp's bytes)", freed, len("orphaned"))
	}
	if _, err := os.Stat(oldTmp); !os.IsNotExist(err) {
		t.Fatalf("old tmp must be removed, stat err=%v", err)
	}
	if _, err := os.Stat(freshTmp); err != nil {
		t.Fatalf("fresh (in-flight) tmp must survive: %v", err)
	}
}

func TestGcCommandPrintsSummaryLine(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)
	if err := os.WriteFile(filepath.Join(root, "config.toml"), []byte("retention_days = 0\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	st, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "df", Ts: time.Now().Add(-time.Hour)}, "k", []byte("data")); err != nil {
		t.Fatal(err)
	}
	st.Close()

	var out, errB bytes.Buffer
	rc := Gc(nil, &out, &errB)
	if rc != 0 {
		t.Fatalf("rc=%d stderr=%s", rc, errB.String())
	}
	if !strings.Contains(out.String(), "pruned 1 artifacts, freed") {
		t.Fatalf("stdout = %q", out.String())
	}
}

func TestGcCommandRejectsExtraArgs(t *testing.T) {
	root := t.TempDir()
	t.Setenv("SSHAI_ROOT", root)

	var out, errB bytes.Buffer
	rc := Gc([]string{"extra"}, &out, &errB)
	if rc != exitUsage {
		t.Fatalf("rc=%d, want %d; stderr=%s", rc, exitUsage, errB.String())
	}
}
