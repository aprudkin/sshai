package artifact

import (
	"encoding/json"
	"strings"
	"testing"
	"time"
)

// Fixture expectations use literals, independent of the evidence sanitizer.
func evidenceMeta(phase, completion, transportError, setupError, localError string) Meta {
	return Meta{
		Host: "host01", Ctx: "default", Command: "synthetic body", Ts: time.Now(),
		FailurePhase: phase, RemoteCompletion: completion,
		TransportErr: transportError, SetupErr: setupError, LocalError: localError,
	}
}

func checkEvidenceRendering(t *testing.T, meta Meta, phase, completion string) {
	t.Helper()
	data, err := json.Marshal(ResultEntryForMeta("/synthetic", meta))
	if err != nil {
		t.Fatal(err)
	}
	var entry map[string]any
	if err := json.Unmarshal(data, &entry); err != nil {
		t.Fatal(err)
	}
	status := StatusLine(meta)
	for _, tc := range []struct{ jsonKey, humanKey, want string }{
		{"failure_phase", "failure-phase", phase},
		{"remote_completion", "remote-completion", completion},
	} {
		got, exists := entry[tc.jsonKey]
		if tc.want == "" {
			if exists || strings.Contains(status, tc.humanKey+"=") {
				t.Errorf("invalid/absent %s evidence exposed: JSON=%s status=%s", tc.jsonKey, data, status)
			}
		} else if got != tc.want || !strings.Contains(status, tc.humanKey+"="+tc.want) {
			t.Errorf("%s=%v want %q, status=%s", tc.jsonKey, got, tc.want, status)
		}
	}
}

// Catches raw/unrecognized metadata being emitted or persisted as trustworthy
// phase evidence, and adding remote claims to command outcomes/local failures.
func TestFailureEvidenceAllowlist(t *testing.T) {
	for _, tc := range []struct {
		name, phase, completion, transportError, setupError, localError, wantPhase, wantCompletion string
	}{
		{"probe", "probe", "not_started", "ssh", "", "", "probe", "not_started"},
		{"stage", "stage", "not_started", "scp", "", "", "stage", "not_started"},
		{"exec-dispatched", "exec", "unknown", "timeout", "", "", "exec", "unknown"},
		{"exec-not-dispatched", "exec", "not_started", "timeout", "", "", "exec", "not_started"},
		{"setup", "probe", "not_started", "", "windows-shell", "", "probe", "not_started"},
		{"raw-phase", "private host diagnostic\n", "unknown", "ssh", "", "", "", ""},
		{"raw-completion", "exec", "private diagnostic", "ssh", "", "", "", ""},
		{"partial-evidence", "exec", "", "ssh", "", "", "", ""},
		{"probe-unknown", "probe", "unknown", "ssh", "", "", "", ""},
		{"stage-unknown", "stage", "unknown", "scp", "", "", "", ""},
		{"setup-exec", "exec", "unknown", "", "windows-shell", "", "", ""},
		{"normal-command", "exec", "unknown", "", "", "", "", ""},
		{"local-failure", "exec", "unknown", "ssh", "", "timeout", "", ""},
		{"legacy-transport", "", "", "ssh", "", "", "", ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			meta := evidenceMeta(tc.phase, tc.completion, tc.transportError, tc.setupError, tc.localError)
			checkEvidenceRendering(t, meta, tc.wantPhase, tc.wantCompletion)
			store, err := OpenStore(t.TempDir())
			if err != nil {
				t.Fatal(err)
			}
			defer store.Close()
			saved, err := store.Save(meta, "key", []byte("sanitized body\n"))
			if err != nil {
				t.Fatal(err)
			}
			got, _, err := store.Get(saved.ID)
			if err != nil {
				t.Fatal(err)
			}
			checkEvidenceRendering(t, got, tc.wantPhase, tc.wantCompletion)
			var phase, completion string
			if err := store.DB.QueryRow(`SELECT failure_phase, remote_completion FROM runs WHERE art_id=?`, saved.ID).Scan(&phase, &completion); err != nil {
				t.Fatal(err)
			}
			if phase != tc.wantPhase || completion != tc.wantCompletion {
				t.Errorf("persisted=(%q,%q) want (%q,%q)", phase, completion, tc.wantPhase, tc.wantCompletion)
			}
		})
	}
}

// Migration leaves old errors uncertain (no inferred phase/completion), while
// new evidence survives reopening and both metadata lookup paths.
func TestFailureEvidenceLegacyMigration(t *testing.T) {
	root := t.TempDir()
	legacy, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	old, err := legacy.Save(Meta{Host: "host01", Ctx: "default", Command: "true", TransportErr: "ssh", Ts: time.Now()}, "old", nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, column := range []string{"failure_phase", "remote_completion"} {
		exists, err := runColumnExists(legacy.DB, column)
		if err != nil {
			t.Fatal(err)
		}
		if exists {
			if _, err := legacy.DB.Exec(`ALTER TABLE runs DROP COLUMN ` + column); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := legacy.Close(); err != nil {
		t.Fatal(err)
	}
	store, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	oldBack, _, err := store.Get(old.ID)
	if err != nil {
		t.Fatal(err)
	}
	checkEvidenceRendering(t, oldBack, "", "")
	meta := evidenceMeta("exec", "unknown", "timeout", "", "")
	saved, err := store.Save(meta, "new", nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	reopened, err := OpenStore(root)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	got, _, err := reopened.Get(saved.ID)
	if err != nil {
		t.Fatal(err)
	}
	checkEvidenceRendering(t, got, "exec", "unknown")
	last, ok, err := reopened.LastByKey("new")
	if err != nil || !ok {
		t.Fatalf("LastByKey: ok=%v err=%v", ok, err)
	}
	checkEvidenceRendering(t, last, "exec", "unknown")
}
