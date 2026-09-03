package artifact

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// TestRenderResultSchemaShape locks the v1 envelope field names, ordering
// invariants, and the empty-string-not-omitted contract. A consumer decodes
// straight into typed structs, so any drift here is a breaking change.
func TestRenderResultSchemaShape(t *testing.T) {
	// A non-UTC zone on purpose: the design mandates ts in UTC, so the
	// fixture must catch a missing .UTC() normalization.
	ts := time.Date(2026, 8, 16, 12, 34, 56, 789000000, time.FixedZone("CEST", 2*3600))
	metas := []Meta{
		{
			ID: "a17", Host: "pg-prod-01", Ctx: "default", Command: "df -h",
			Exit: 0, Bytes: 1024, Lines: 3, SHA256: "abc123",
			DurationMs: 500, Truncated: false, Binary: false, Ts: ts,
			AcceptedHostKeyAlgorithm: "ssh-ed25519", AcceptedHostKeyFingerprint: "SHA256:abc123",
		},
		{
			ID: "a18", Host: "web01", Ctx: "default", Command: "body:1a2b3c4d5e6f7a8b",
			TransportErr: "ssh", TransportDiagnostic: "host key verification failed", DurationMs: 10, Ts: ts,
		},
	}
	summary := Summary{Hosts: 2, OK: 1, TransportErrors: 1, WorstExit: 0}

	var raw map[string]any
	if err := json.Unmarshal(RenderResult("/root", metas, summary, "a12345678901234567890123456789012"), &raw); err != nil {
		t.Fatalf("envelope is not valid JSON: %v", err)
	}
	if raw["schema_version"] != "v1" {
		t.Fatalf("schema_version=%v, want v1", raw["schema_version"])
	}
	if raw["batch_id"] != "a12345678901234567890123456789012" {
		t.Fatalf("batch_id=%v", raw["batch_id"])
	}
	sum, _ := raw["summary"].(map[string]any)
	if sum["hosts"].(float64) != 2 || sum["transport_errors"].(float64) != 1 {
		t.Fatalf("summary=%v", sum)
	}
	runs, _ := raw["runs"].([]any)
	if len(runs) != 2 {
		t.Fatalf("len(runs)=%d, want 2", len(runs))
	}
	r0, _ := runs[0].(map[string]any)
	if r0["artifact_path"] != filepath.Join("/root", "art", "a17") {
		t.Fatalf("artifact_path=%v", r0["artifact_path"])
	}
	// empty-string-not-omitted: transport_error on a success must be "".
	if _, ok := r0["transport_error"]; !ok {
		t.Fatal("transport_error key missing on success entry (must be empty string, not omitted)")
	}
	if r0["transport_error"] != "" {
		t.Fatalf("transport_error=%v, want empty string", r0["transport_error"])
	}
	if r0["accepted_host_key_algorithm"] != "ssh-ed25519" ||
		r0["accepted_host_key_fingerprint"] != "SHA256:abc123" {
		t.Fatalf("accepted host key evidence missing from runs[0]: %v", r0)
	}
	r1, _ := runs[1].(map[string]any)
	if r1["transport_error"] != "ssh" {
		t.Fatalf("runs[1] transport_error=%v", r1["transport_error"])
	}
	if r1["transport_diagnostic"] != "host key verification failed" {
		t.Fatalf("runs[1] transport_diagnostic=%v", r1["transport_diagnostic"])
	}
	if r1["exit"].(float64) != 0 {
		t.Fatalf("runs[1] exit must be 0 when transport_error set, got %v", r1["exit"])
	}
	// ts must be RFC3339Nano, normalized to UTC: 12:34:56.789 +0200
	// normalizes to 10:34:56.789Z.
	if r0["ts"] != "2026-08-16T10:34:56.789Z" {
		t.Fatalf("ts=%v, want RFC3339Nano in UTC", r0["ts"])
	}
}

// TestRenderResultEmptyRunsStillValid covers the all-policy-denied shape:
// runs may be empty, but summary.hosts is still reported and the doc parses.
func TestRenderResultEmptyRunsStillValid(t *testing.T) {
	var raw map[string]any
	if err := json.Unmarshal(RenderResult("/root", nil, Summary{Hosts: 1, PolicyDenied: 1}, "a1"), &raw); err != nil {
		t.Fatalf("empty-runs envelope must still be valid JSON: %v", err)
	}
	runs, _ := raw["runs"].([]any)
	if len(runs) != 0 {
		t.Fatalf("len(runs)=%d, want 0", len(runs))
	}
}

// TestRenderResultNeverOmitsEmptyFields guards the decode-into-struct contract.
func TestRenderResultNeverOmitsEmptyFields(t *testing.T) {
	body := RenderResult("/root", []Meta{{ID: "a1", Host: "h1", Ts: time.Now()}},
		Summary{Hosts: 1, OK: 1}, "a1")
	for _, key := range []string{"sha256", "transport_error", "delta_base", "artifact_path", "ctx", "command"} {
		if !strings.Contains(string(body), `"`+key+`":`) {
			t.Fatalf("field %q missing from envelope (must be present, possibly empty)", key)
		}
	}
	for _, optional := range []string{
		`"transport_diagnostic":`,
		`"accepted_host_key_algorithm":`,
		`"accepted_host_key_fingerprint":`,
	} {
		if strings.Contains(string(body), optional) {
			t.Fatalf("empty optional field %s changed default envelope: %s", optional, body)
		}
	}
}
