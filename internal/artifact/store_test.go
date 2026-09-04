// internal/artifact/store_test.go
package artifact

import (
	"os"
	"path/filepath"
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

func TestOpenStoreRefusesSymlinkArtifactDirectory(t *testing.T) {
	root := t.TempDir()
	outside := t.TempDir()
	if err := os.Symlink(outside, filepath.Join(root, "art")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if st, err := OpenStore(root); err == nil {
		_ = st.Close()
		t.Fatal("OpenStore accepted a symlink artifact directory")
	}
	entries, err := os.ReadDir(outside)
	if err != nil || len(entries) != 0 {
		t.Fatalf("artifact directory symlink was followed: entries=%v err=%v", entries, err)
	}
}

func TestSaveRefusesSymlinkArtifactDestination(t *testing.T) {
	root := t.TempDir()
	st, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()

	target := filepath.Join(root, "outside")
	if err := os.WriteFile(target, []byte("unchanged"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(target, filepath.Join(root, "art", "a1")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	if _, err := st.Save(Meta{Host: "h1", Ctx: "default", Ts: time.Now()}, "k1", []byte("secret")); err == nil {
		t.Fatal("Save succeeded through a symlink artifact destination")
	}
	got, err := os.ReadFile(target)
	if err != nil || string(got) != "unchanged" {
		t.Fatalf("symlink target changed: %q, err=%v", got, err)
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

func TestOpenStoreMigratesAndPersistsOptionalTransportEvidence(t *testing.T) {
	root := t.TempDir()
	legacy, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, column := range []string{
		"transport_diagnostic",
		"setup_error",
		"setup_diagnostic",
		"local_error",
		"accepted_host_key_algorithm",
		"accepted_host_key_fingerprint",
	} {
		if _, err := legacy.DB.Exec(`ALTER TABLE runs DROP COLUMN ` + column); err != nil {
			legacy.Close()
			t.Fatalf("drop legacy column %s: %v", column, err)
		}
	}
	if err := legacy.Close(); err != nil {
		t.Fatal(err)
	}

	st, err := OpenStore(root)
	if err != nil {
		t.Fatalf("migrate store: %v", err)
	}
	defer st.Close()
	diagnostic := "host key verification failed"
	algorithm := "ssh-ed25519"
	fingerprint := "SHA256:abc123"
	saved, err := st.Save(Meta{
		Host: "h1", Ctx: "default", Command: "true", TransportErr: "ssh", LocalError: "start",
		TransportDiagnostic: diagnostic, AcceptedHostKeyAlgorithm: algorithm,
		AcceptedHostKeyFingerprint: fingerprint, Ts: time.Now(),
	}, "k1", []byte("transport diagnostic: "+diagnostic+"\n"))
	if err != nil {
		t.Fatalf("save migrated evidence: %v", err)
	}
	got, _, err := st.Get(saved.ID)
	if err != nil || got.TransportDiagnostic != diagnostic || got.LocalError != "start" ||
		got.AcceptedHostKeyAlgorithm != algorithm || got.AcceptedHostKeyFingerprint != fingerprint {
		t.Fatalf("Get evidence=%+v err=%v", got, err)
	}
	setup, err := st.Save(Meta{Host: "h1", Ctx: "default", Command: "true", SetupErr: "windows-shell", SetupDiagnostic: "Windows shell setup failed", Ts: time.Now()}, "k1", []byte("setup diagnostic: Windows shell setup failed\n"))
	if err != nil {
		t.Fatalf("save setup failure: %v", err)
	}
	setupBack, _, err := st.Get(setup.ID)
	if err != nil || setupBack.SetupErr != "windows-shell" || setupBack.SetupDiagnostic != "Windows shell setup failed" {
		t.Fatalf("Get setup evidence=%+v err=%v", setupBack, err)
	}
	last, ok, err := st.LastByKey("k1")
	if err != nil || !ok || last.TransportDiagnostic != diagnostic || last.LocalError != "start" ||
		last.AcceptedHostKeyAlgorithm != algorithm || last.AcceptedHostKeyFingerprint != fingerprint {
		t.Fatalf("LastByKey=%+v ok=%v err=%v", last, ok, err)
	}
}
