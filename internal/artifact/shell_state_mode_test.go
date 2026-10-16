package artifact

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

func TestShellStateModeMigrationAndComparisons(t *testing.T) {
	root := t.TempDir()
	store, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	legacy, err := store.Save(Meta{Host: "windows01", Ctx: "test", Command: "synthetic", Ts: time.Now()}, "key", []byte("old\n"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.DB.Exec(`ALTER TABLE runs DROP COLUMN shell_state_mode`); err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	old, _, err := store.Get(legacy.ID)
	if err != nil || old.ShellStateMode != "" {
		t.Fatalf("legacy=%+v err=%v", old, err)
	}
	if strings.Contains(string(RenderResult(root, []Meta{old}, Summary{}, "test")), "shell_state_mode") {
		t.Fatal("invented legacy mode evidence")
	}
	if _, ok, err := store.LastByKeyMode("key", ShellStateNone); err != nil || ok {
		t.Fatalf("legacy selected as no-state: %t %v", ok, err)
	}
	last, ok, err := store.LastByKey("key")
	if err != nil || !ok || last.ID != legacy.ID {
		t.Fatalf("legacy default lost: %+v %v", last, err)
	}
	for _, mode := range []string{ShellStateStateful, ShellStateNone} {
		saved, err := store.Save(Meta{Host: "windows01", Ctx: "test", Command: "synthetic", ShellStateMode: mode, Ts: time.Now()}, "key", []byte("new\n"))
		if err != nil {
			t.Fatal(err)
		}
		got, _, err := store.Get(saved.ID)
		if err != nil || got.ShellStateMode != mode {
			t.Fatalf("mode lost: %+v %v", got, err)
		}
		var entry map[string]any
		data, _ := json.Marshal(ResultEntryForMeta(root, got))
		if err := json.Unmarshal(data, &entry); err != nil || entry["shell_state_mode"] != mode {
			t.Fatalf("JSON=%s err=%v", data, err)
		}
		if !strings.Contains(StatusLine(got), "shell-state="+mode) {
			t.Fatal("passport lost mode")
		}
		last, ok, err := store.LastByKeyMode("key", mode)
		if err != nil || !ok || last.ID != saved.ID {
			t.Fatalf("mode lookup=%+v %v", last, err)
		}
	}
}
