package artifact

import (
	"encoding/json"
	"path/filepath"
	"time"
)

// Summary is the top-level aggregate of a run invocation's machine-readable
// envelope. Counts are computed by the CLI result-mode layer; RenderResult
// only serializes them. The json tags are the frozen v1 field names.
type Summary struct {
	Hosts           int `json:"hosts"`
	OK              int `json:"ok"`
	Failed          int `json:"failed"`
	TransportErrors int `json:"transport_errors"`
	PolicyDenied    int `json:"policy_denied"`
	WorstExit       int `json:"worst_exit"`
}

// runEntry is one host's result inside the envelope's runs[] array. Existing
// field names are the frozen v1 contract. Empty strings remain present except
// for transport_diagnostic, an additive error-only field omitted when no safe
// diagnostic is available so default envelopes remain byte-compatible.
// ResultEntry is the stable v1 per-run result schema.
type ResultEntry struct {
	ID                         string `json:"id"`
	Host                       string `json:"host"`
	Ctx                        string `json:"ctx"`
	Command                    string `json:"command"`
	Exit                       int    `json:"exit"`
	TransportError             string `json:"transport_error"`
	TransportDiagnostic        string `json:"transport_diagnostic,omitempty"`
	AcceptedHostKeyAlgorithm   string `json:"accepted_host_key_algorithm,omitempty"`
	AcceptedHostKeyFingerprint string `json:"accepted_host_key_fingerprint,omitempty"`
	ArtifactPath               string `json:"artifact_path"`
	Bytes                      int64  `json:"bytes"`
	Lines                      int64  `json:"lines"`
	SHA256                     string `json:"sha256"`
	DurationMs                 int64  `json:"duration_ms"`
	Ts                         string `json:"ts"`
	Truncated                  bool   `json:"truncated"`
	Binary                     bool   `json:"binary"`
	DeltaBase                  string `json:"delta_base"`
}

type envelope struct {
	SchemaVersion string        `json:"schema_version"`
	BatchID       string        `json:"batch_id"`
	Summary       Summary       `json:"summary"`
	Runs          []ResultEntry `json:"runs"`
}

// ResultEntryForMeta maps artifact metadata to the canonical v1 run schema.
func ResultEntryForMeta(root string, m Meta) ResultEntry {
	return ResultEntry{ID: m.ID, Host: m.Host, Ctx: m.Ctx, Command: m.Command, Exit: m.Exit, TransportError: m.TransportErr, TransportDiagnostic: m.TransportDiagnostic, AcceptedHostKeyAlgorithm: m.AcceptedHostKeyAlgorithm, AcceptedHostKeyFingerprint: m.AcceptedHostKeyFingerprint, ArtifactPath: filepath.Join(root, "art", m.ID), Bytes: m.Bytes, Lines: m.Lines, SHA256: m.SHA256, DurationMs: m.DurationMs, Ts: m.Ts.UTC().Format(time.RFC3339Nano), Truncated: m.Truncated, Binary: m.Binary, DeltaBase: m.DeltaBase}
}

// RenderResult builds the v1 machine-readable envelope as a single JSON
// object (no trailing newline). artifact_path is derived from root + each
// Meta.ID (the "<root>/art/<id>" convention). Marshalling cannot fail: every
// field is a JSON-serializable scalar, so the error is swallowed defensively
// rather than returned.
func RenderResult(root string, metas []Meta, summary Summary, batchID string) []byte {
	runs := make([]ResultEntry, 0, len(metas))
	for _, m := range metas {
		runs = append(runs, ResultEntryForMeta(root, m))
	}
	env := envelope{
		SchemaVersion: "v1",
		BatchID:       batchID,
		Summary:       summary,
		Runs:          runs,
	}
	b, err := json.Marshal(env)
	if err != nil {
		// Unreachable: all fields are scalars. Never emit a partial doc.
		return []byte(`{"schema_version":"v1","batch_id":"","summary":{},"runs":[]}`)
	}
	return b
}
