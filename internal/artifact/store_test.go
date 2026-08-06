// internal/artifact/store_test.go
package artifact

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestSaveAssignsMonotonicIDsAndWritesFile(t *testing.T) {
	st, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	m1, err := st.Save(Meta{Host: "h1", Ctx: "default", Command: "df -h", Ts: time.Now()}, "k1", []byte("out1\n"))
	if err != nil {
		t.Fatal(err)
	}
	m2, _ := st.Save(Meta{Host: "h1", Ctx: "default", Command: "df -h", Ts: time.Now()}, "k1", []byte("out2\n"))
	if m1.ID != "a1" || m2.ID != "a2" {
		t.Fatalf("ids: %s %s", m1.ID, m2.ID)
	}
	_, path, err := st.Get("a1")
	if err != nil {
		t.Fatal(err)
	}
	b, _ := os.ReadFile(path)
	if string(b) != "out1\n" {
		t.Fatalf("artifact content: %q", b)
	}
}

func TestLastByKey(t *testing.T) {
	st, _ := OpenStore(t.TempDir())
	defer st.Close()
	st.Save(Meta{Host: "h1", Ctx: "default", Command: "df", Ts: time.Now()}, "kA", []byte("old"))
	st.Save(Meta{Host: "h1", Ctx: "default", Command: "df", Ts: time.Now()}, "kA", []byte("new"))
	m, ok, err := st.LastByKey("kA")
	if err != nil || !ok || m.ID != "a2" {
		t.Fatalf("want a2, got %+v ok=%v err=%v", m, ok, err)
	}
	if _, ok, _ := st.LastByKey("missing"); ok {
		t.Fatal("missing key must return ok=false")
	}
}

func TestGetUnknownAndPruned(t *testing.T) {
	st, _ := OpenStore(t.TempDir())
	defer st.Close()
	if _, _, err := st.Get("a99"); err == nil {
		t.Fatal("unknown id must error")
	}
	m, _ := st.Save(Meta{Host: "h", Ctx: "default", Ts: time.Now()}, "k", []byte("x"))
	if _, err := st.DB.Exec(`UPDATE runs SET pruned=1 WHERE art_id=?`, m.ID); err != nil {
		t.Fatal(err)
	}
	got, path, err := st.Get(m.ID)
	if err == nil || !strings.Contains(err.Error(), "artifact pruned") {
		t.Fatalf("want pruned error, got %v", err)
	}
	if path != "" {
		t.Fatalf("want empty path on pruned artifact, got %q", path)
	}
	if got.ID != m.ID || got.Host != "h" {
		t.Fatalf("pruned run's metadata must still be returned (audit history), got %+v", got)
	}
}
