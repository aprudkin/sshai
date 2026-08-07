// internal/shell/envdiff_test.go
package shell

import (
	"reflect"
	"testing"
)

func TestEnvRestoreSet(t *testing.T) {
	base := map[string]string{"PATH": "/usr/bin", "HOME": "/root", "SSH_TTY": "/dev/pts/0", "TERM": "xterm"}
	cur := map[string]string{"PATH": "/opt/venv/bin:/usr/bin", "HOME": "/root",
		"VIRTUAL_ENV": "/opt/venv", "SSH_TTY": "/dev/pts/9", "TERM": "vt100", "LC_ALL": "C"}
	got := EnvRestoreSet(base, cur)
	want := map[string]string{"PATH": "/opt/venv/bin:/usr/bin", "VIRTUAL_ENV": "/opt/venv"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v want %v", got, want)
	}
}
