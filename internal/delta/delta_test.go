// internal/delta/delta_test.go
package delta

import "testing"

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
