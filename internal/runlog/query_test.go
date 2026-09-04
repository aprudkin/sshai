// internal/runlog/query_test.go
package runlog

import (
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
)

// seedSearchStore opens a fresh store and saves 3 runs across two hosts
// with distinct commands, then backdates one row directly via UPDATE
// (per the task brief's Step 1), giving --since a real row to exclude.
// Returns the store and the three ids in insertion order (a1, a2, a3).
func seedSearchStore(t *testing.T) (st *artifact.Store, id1, id2, id3 string) {
	t.Helper()
	st, err := artifact.OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })

	base := time.Date(2026, 8, 6, 12, 0, 0, 0, time.UTC)

	m1, err := st.Save(artifact.Meta{Host: "web01", Ctx: "default", Command: "journalctl -u postgres -f", Ts: base}, "k1", []byte("out1"))
	if err != nil {
		t.Fatal(err)
	}
	m2, err := st.Save(artifact.Meta{Host: "db01", Ctx: "default", Command: "df -h", LocalError: "output-limit", Ts: base.Add(time.Minute)}, "k2", []byte("out2"))
	if err != nil {
		t.Fatal(err)
	}
	m3, err := st.Save(artifact.Meta{Host: "web01", Ctx: "default", Command: "uptime", Ts: base.Add(2 * time.Minute)}, "k3", []byte("out3"))
	if err != nil {
		t.Fatal(err)
	}

	old := base.Add(-30 * 24 * time.Hour).UTC().Format(time.RFC3339)
	if _, err := st.DB.Exec(`UPDATE runs SET ts=? WHERE art_id=?`, old, m3.ID); err != nil {
		t.Fatal(err)
	}

	return st, m1.ID, m2.ID, m3.ID
}

func TestSearchFiltersByHost(t *testing.T) {
	st, _, id2, _ := seedSearchStore(t)

	got, err := Search(st.DB, "db01", "", time.Time{}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].ID != id2 || got[0].LocalError != "output-limit" {
		t.Fatalf("Search(host=db01) = %+v, want exactly [%s] with local error", got, id2)
	}
}

func TestSearchGrepMatchesCommand(t *testing.T) {
	st, id1, _, _ := seedSearchStore(t)

	got, err := Search(st.DB, "", "postgres", time.Time{}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].ID != id1 {
		t.Fatalf("Search(grep=postgres) = %+v, want exactly [%s]", got, id1)
	}
}

func TestSearchSinceExcludesOldRow(t *testing.T) {
	st, id1, id2, id3 := seedSearchStore(t)

	since := time.Date(2026, 8, 6, 0, 0, 0, 0, time.UTC)
	got, err := Search(st.DB, "", "", since, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("Search(since=2026-08-06) = %d rows, want 2 (old row a3 excluded): %+v", len(got), got)
	}
	for _, m := range got {
		if m.ID == id3 {
			t.Fatalf("old row %s must be excluded by --since, got %+v", id3, got)
		}
	}
	// Newest-first: id2 (base+1m) before id1 (base).
	if got[0].ID != id2 || got[1].ID != id1 {
		t.Fatalf("Search results not newest-first: got %+v, want [%s, %s]", got, id2, id1)
	}
}

func TestSearchLimitDefaultsTo20(t *testing.T) {
	st, err := artifact.OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	for i := 0; i < 25; i++ {
		if _, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "cmd", Ts: time.Now()}, "k", []byte("x")); err != nil {
			t.Fatal(err)
		}
	}

	got, err := Search(st.DB, "", "", time.Time{}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 20 {
		t.Fatalf("Search with limit<=0 = %d rows, want default 20", len(got))
	}
}

func TestSearchGrepEscapesLikeWildcards(t *testing.T) {
	st, err := artifact.OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	if _, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "echo 100% done", Ts: time.Now()}, "k1", []byte("x")); err != nil {
		t.Fatal(err)
	}
	if _, err := st.Save(artifact.Meta{Host: "h", Ctx: "default", Command: "echo up done", Ts: time.Now()}, "k2", []byte("x")); err != nil {
		t.Fatal(err)
	}

	got, err := Search(st.DB, "", "100%", time.Time{}, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || !strings.Contains(got[0].Command, "100%") {
		t.Fatalf("Search(grep=%q) = %+v, want exactly the literal-%% row", "100%", got)
	}
}
