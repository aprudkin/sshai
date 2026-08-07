// internal/delta/delta_test.go
package delta

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestKeyNormalizesWhitespace(t *testing.T) {
	a := Key("h", "c", "df  -h ")
	b := Key("h", "c", "df -h")
	if a != b {
		t.Fatalf("Key(%q)=%q, want equal to Key(%q)=%q", "df  -h ", a, "df -h", b)
	}
}

func TestKeyDiffersByHost(t *testing.T) {
	if Key("h1", "c", "df -h") == Key("h2", "c", "df -h") {
		t.Fatal("different hosts must produce different keys")
	}
}

func TestKeyDiffersByCtx(t *testing.T) {
	if Key("h", "c1", "df -h") == Key("h", "c2", "df -h") {
		t.Fatal("different ctx must produce different keys")
	}
}

func TestKeyDiffersByCommand(t *testing.T) {
	if Key("h", "c", "df -h") == Key("h", "c", "du -h") {
		t.Fatal("different commands must produce different keys")
	}
}

// TestRenderNoChange covers Render's byte-equal path: identical old/new
// data must produce the ~20-token "no change since <prevID> (<age>)" line,
// per the design doc's exact example ("no change since a12 (3m ago)").
func TestRenderNoChange(t *testing.T) {
	dir := t.TempDir()
	prevPath := filepath.Join(dir, "a1")
	if err := os.WriteFile(prevPath, []byte("same output\n"), 0o600); err != nil {
		t.Fatalf("write previous artifact: %v", err)
	}

	got, err := Render(prevPath, []byte("same output\n"), "a1", time.Now().Add(-3*time.Minute), 500)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(got, "no change since a1") {
		t.Fatalf("Render = %q, want it to contain %q", got, "no change since a1")
	}
}

// TestRenderChangedProducesUnifiedDiff covers Render's diff path: differing
// old/new data must produce a unified diff (context 3) with the classic
// "-old"/"+new" removed/added line markers.
func TestRenderChangedProducesUnifiedDiff(t *testing.T) {
	dir := t.TempDir()
	prevPath := filepath.Join(dir, "a1")
	if err := os.WriteFile(prevPath, []byte("old\n"), 0o600); err != nil {
		t.Fatalf("write previous artifact: %v", err)
	}

	got, err := Render(prevPath, []byte("new\n"), "a1", time.Now(), 500)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if !strings.Contains(got, "-old") || !strings.Contains(got, "+new") {
		t.Fatalf("Render = %q, want it to contain -old and +new", got)
	}
}

// TestRenderChangedBudgetTrims covers the budget-trim path: a diff far
// larger than the token budget must be clipped rather than printed in full.
func TestRenderChangedBudgetTrims(t *testing.T) {
	dir := t.TempDir()
	prevPath := filepath.Join(dir, "a1")
	oldData := []byte(strings.Repeat("old line\n", 500))
	if err := os.WriteFile(prevPath, oldData, 0o600); err != nil {
		t.Fatalf("write previous artifact: %v", err)
	}
	newData := []byte(strings.Repeat("new line\n", 500))

	got, err := Render(prevPath, newData, "a1", time.Now(), 20)
	if err != nil {
		t.Fatalf("Render: %v", err)
	}
	if len(got) >= len(oldData)+len(newData) {
		t.Fatalf("Render output not trimmed: got %d bytes", len(got))
	}
}
