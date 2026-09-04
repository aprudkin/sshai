package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/aprudkin/sshai/internal/artifact"
)

func openOutcomeTestStore(t *testing.T, root string) *artifact.Store {
	t.Helper()
	store, err := artifact.OpenStore(root)
	if err != nil {
		t.Fatalf("OpenStore: %v", err)
	}
	t.Cleanup(func() { store.Close() })
	return store
}

func poisonArtifactDir(t *testing.T, root string) {
	t.Helper()
	artDir := filepath.Join(root, "art")
	if err := os.Remove(artDir); err != nil {
		t.Fatalf("remove artifact dir: %v", err)
	}
	if err := os.WriteFile(artDir, []byte("not a directory"), 0o600); err != nil {
		t.Fatalf("replace artifact dir with file: %v", err)
	}
	t.Cleanup(func() {
		_ = os.Remove(artDir)
		_ = os.MkdirAll(artDir, 0o700)
	})
}

func TestRunHostOutcomeSuccess(t *testing.T) {
	root := t.TempDir()
	seedLinuxFacts(t, root, "web01")
	deps := Deps{Tr: &fakeTr{rc: 0}, Store: openOutcomeTestStore(t, root)}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{Host: "web01", Ctx: "default", Command: "true", Budget: 500}, &stdout, &stderr)

	if outcome.Kind() != runOutcomeSuccess {
		t.Fatalf("kind=%v, want success", outcome.Kind())
	}
	meta, ok := outcome.Meta()
	if !ok || meta.Exit != 0 || meta.TransportErr != "" {
		t.Fatalf("meta=(%+v, %v), want saved exit-0 meta", meta, ok)
	}
	if outcome.ExitCode() != 0 {
		t.Fatalf("exit=%d, want 0", outcome.ExitCode())
	}
}

func TestRunHostOutcomeRemoteNonZero(t *testing.T) {
	root := t.TempDir()
	seedLinuxFacts(t, root, "web01")
	deps := Deps{Tr: &fakeTr{rc: 23}, Store: openOutcomeTestStore(t, root)}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{Host: "web01", Ctx: "default", Command: "exit 23", Budget: 500}, &stdout, &stderr)

	if outcome.Kind() != runOutcomeRemoteNonZero {
		t.Fatalf("kind=%v, want remote-non-zero", outcome.Kind())
	}
	meta, ok := outcome.Meta()
	if !ok || meta.Exit != 23 || meta.TransportErr != "" {
		t.Fatalf("meta=(%+v, %v), want saved exit-23 meta", meta, ok)
	}
	if outcome.ExitCode() != 23 {
		t.Fatalf("exit=%d, want 23", outcome.ExitCode())
	}
}

func TestRunHostOutcomeTransportFailure(t *testing.T) {
	root := t.TempDir()
	deps := Deps{Tr: &probeFailsTr{}, Store: openOutcomeTestStore(t, root)}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{Host: "web01", Ctx: "default", Command: "true", Budget: 500}, &stdout, &stderr)

	if outcome.Kind() != runOutcomeTransportFailure {
		t.Fatalf("kind=%v, want transport-failure", outcome.Kind())
	}
	meta, ok := outcome.Meta()
	if !ok || meta.TransportErr != "ssh" {
		t.Fatalf("meta=(%+v, %v), want saved transport-error meta", meta, ok)
	}
	if outcome.ExitCode() != exitTransport {
		t.Fatalf("exit=%d, want %d", outcome.ExitCode(), exitTransport)
	}
}

func TestRunHostOutcomePolicyDenied(t *testing.T) {
	root := t.TempDir()
	deps := Deps{Tr: &fakeTr{}, Store: openOutcomeTestStore(t, root)}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{
		Host: "web01", Ctx: "default", Command: "rm -rf /tmp/x", Readonly: true, Budget: 500,
	}, &stdout, &stderr)

	if outcome.Kind() != runOutcomePolicyDenied {
		t.Fatalf("kind=%v, want policy-denied", outcome.Kind())
	}
	if _, ok := outcome.Meta(); ok {
		t.Fatal("policy-denied outcome unexpectedly carries artifact meta")
	}
	if outcome.ExitCode() != exitPolicy {
		t.Fatalf("exit=%d, want %d", outcome.ExitCode(), exitPolicy)
	}
}

func TestRunHostOutcomeSaveFailure(t *testing.T) {
	root := t.TempDir()
	seedLinuxFacts(t, root, "web01")
	store := openOutcomeTestStore(t, root)
	poisonArtifactDir(t, root)
	deps := Deps{Tr: &fakeTr{rc: 0}, Store: store}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{Host: "web01", Ctx: "default", Command: "true", Budget: 500}, &stdout, &stderr)

	if outcome.Kind() != runOutcomeInternalFailure {
		t.Fatalf("kind=%v, want internal-failure", outcome.Kind())
	}
	if _, ok := outcome.Meta(); ok {
		t.Fatal("save-failure outcome unexpectedly carries artifact meta")
	}
	if outcome.ExitCode() != exitUsage {
		t.Fatalf("exit=%d, want %d", outcome.ExitCode(), exitUsage)
	}
}

func TestRunHostOutcomePreSaveFailure(t *testing.T) {
	root := t.TempDir()
	seedLinuxFacts(t, root, "web01")
	statePath := filepath.Join(root, "state", "web01", "default.json")
	if err := os.WriteFile(statePath, []byte("{"), 0o600); err != nil {
		t.Fatalf("write corrupt state: %v", err)
	}
	deps := Deps{Tr: &fakeTr{rc: 0}, Store: openOutcomeTestStore(t, root)}

	var stdout, stderr bytes.Buffer
	outcome := runHost(deps, Opts{Host: "web01", Ctx: "default", Command: "true", Budget: 500}, &stdout, &stderr)

	if outcome.Kind() != runOutcomeInternalFailure {
		t.Fatalf("kind=%v, want internal-failure", outcome.Kind())
	}
	if _, ok := outcome.Meta(); ok {
		t.Fatal("pre-save failure unexpectedly carries artifact meta")
	}
	if outcome.ExitCode() != exitUsage {
		t.Fatalf("exit=%d, want %d", outcome.ExitCode(), exitUsage)
	}
	if !strings.Contains(stderr.String(), "run: load state for web01/default:") {
		t.Fatalf("stderr missing pre-save diagnostic: %q", stderr.String())
	}
}

func TestSummarizeRunOutcomesUsesTypedClasses(t *testing.T) {
	outcomes := []RunOutcome{
		newSavedRunOutcome(artifact.Meta{ID: "a1", Host: "ok", Exit: 0}),
		newSavedRunOutcome(artifact.Meta{ID: "a2", Host: "remote", Exit: 23}),
		newSavedRunOutcome(artifact.Meta{ID: "a3", Host: "transport", TransportErr: "ssh"}),
		newPolicyDeniedOutcome(),
	}

	summary, metas := summarizeRunOutcomes(outcomes)

	wantSummary := artifact.Summary{
		Hosts: 4, OK: 1, Failed: 1, TransportErrors: 1, PolicyDenied: 1, WorstExit: 23,
	}
	if summary != wantSummary {
		t.Fatalf("summary=%+v, want %+v", summary, wantSummary)
	}
	if len(metas) != 3 || metas[0].ID != "a1" || metas[1].ID != "a2" || metas[2].ID != "a3" {
		t.Fatalf("metas=%+v, want saved metas in input order", metas)
	}
}
