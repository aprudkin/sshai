package cli

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/aprudkin/sshai/internal/artifact"
	"github.com/aprudkin/sshai/internal/delta"
	"github.com/aprudkin/sshai/internal/runlog"
	"github.com/aprudkin/sshai/internal/runner"
)

func finalizationFixture(t *testing.T, subcommand string) (*artifact.Store, runFinalization) {
	t.Helper()
	store, err := artifact.OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	host := "host-a"
	if subcommand == "local" {
		host = "local-bash"
	}
	return store, runFinalization{
		meta: artifact.Meta{
			Host: host, Ctx: "context-a", Command: "echo report",
			ShellStateMode: artifact.ShellStateStateful, DurationMs: 23,
			Ts: time.Now().Add(-time.Minute).UTC().Truncate(time.Second),
		},
		output: []byte("report\n"), budget: 500, subcommand: subcommand,
		commandPreview: "echo report", bodySHA256: sha256Hex("echo report"),
	}
}

func seedFinalization(t *testing.T, store *artifact.Store, meta artifact.Meta, body []byte) artifact.Meta {
	t.Helper()
	saved, err := store.Save(meta, delta.Key(meta.Host, meta.Ctx, meta.Command), body)
	if err != nil {
		t.Fatal(err)
	}
	return saved
}

func checkFinalizationSaved(t *testing.T, store *artifact.Store, outcome RunOutcome, input runFinalization) artifact.Meta {
	t.Helper()
	meta, ok := outcome.Meta()
	if !ok {
		t.Fatalf("unsaved outcome: %+v", outcome)
	}
	stored, path, err := store.Get(meta.ID)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(stored, meta) {
		t.Fatalf("persisted metadata = %+v, outcome = %+v", stored, meta)
	}
	body, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(body, input.output) {
		t.Fatalf("retained output = %q, err = %v; want %q", body, err, input.output)
	}
	want := input.meta
	want.ID, want.Bytes, want.Lines = meta.ID, int64(len(body)), int64(bytes.Count(body, []byte("\n")))
	if len(body) > 0 && body[len(body)-1] != '\n' {
		want.Lines++
	}
	want.SHA256 = sha256Hex(string(body))
	want.Binary = bytes.IndexByte(body[:min(8192, len(body))], 0) >= 0
	want.DeltaBase = meta.DeltaBase // Each scenario asserts predecessor identity separately.
	if !reflect.DeepEqual(meta, want) {
		t.Fatalf("metadata = %+v, want %+v", meta, want)
	}
	return meta
}

func readFinalizationAudit(t *testing.T, store *artifact.Store) runlog.AuditEntry {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(store.Root, "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var entry runlog.AuditEntry
	// Unmarshal also rejects multiple records, so a seed must not append audit.
	if err := json.Unmarshal(data, &entry); err != nil {
		t.Fatal(err)
	}
	return entry
}

func TestFinalizeRunPresentation(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		for _, scenario := range []string{"delta-disabled", "first-delta", "unchanged", "changed", "bounded-preview", "bounded-delta", "binary-predecessor", "binary-first", "empty", "nul-at-8191", "nul-at-8192"} {
			t.Run(subcommand+"/"+scenario, func(t *testing.T) {
				store, input := finalizationFixture(t, subcommand)
				input.delta = scenario != "delta-disabled" && scenario != "bounded-preview" && scenario != "empty" && !strings.HasPrefix(scenario, "nul-")
				havePrev := scenario == "unchanged" || scenario == "changed" || scenario == "bounded-delta" || scenario == "binary-predecessor" || scenario == "delta-disabled"
				var prev artifact.Meta
				if havePrev {
					prev = seedFinalization(t, store, input.meta, input.output)
				}
				switch scenario {
				case "changed":
					input.output = []byte("changed\n")
				case "bounded-preview", "bounded-delta":
					input.budget = 2
					input.output = bytes.Repeat([]byte("retained line\n"), 40)
				case "binary-predecessor", "binary-first":
					input.output = []byte("bin\x00ary\n")
				case "empty":
					input.output = nil
				case "nul-at-8191":
					input.output = append(bytes.Repeat([]byte("x"), 8191), 0)
				case "nul-at-8192":
					input.output = append(bytes.Repeat([]byte("x"), 8192), 0)
				}
				var out, errOut bytes.Buffer
				outcome := finalizeRun(store, input, &out, &errOut)
				meta := checkFinalizationSaved(t, store, outcome, input)
				if outcome.Kind() != runOutcomeSuccess || errOut.Len() != 0 {
					t.Fatalf("outcome=%+v stderr=%q", outcome, errOut.String())
				}
				wantBase := ""
				if input.delta && havePrev {
					wantBase = prev.ID
				}
				if meta.DeltaBase != wantBase {
					t.Fatalf("DeltaBase=%q, want %q", meta.DeltaBase, wantBase)
				}
				path := filepath.Join(store.Root, "art", meta.ID)
				passport := artifact.RenderPassport(meta, path, input.output, input.budget)
				switch scenario {
				case "unchanged":
					prefix := artifact.StatusLine(meta) + "\nfile=" + path + "\nno change since " + prev.ID + " ("
					if !strings.HasPrefix(out.String(), prefix) {
						t.Fatalf("not an unchanged delta: %q", out.String())
					}
				case "changed", "bounded-delta":
					prefix := artifact.StatusLine(meta) + "\nfile=" + path + "\n--- " + prev.ID
					if !strings.HasPrefix(out.String(), prefix) {
						t.Fatalf("not a delta: %q", out.String())
					}
					if scenario == "changed" && !strings.Contains(out.String(), "-report\n+changed\n") {
						t.Fatalf("missing changed lines: %q", out.String())
					}
					if scenario == "bounded-delta" && !strings.Contains(out.String(), "diff clipped at budget") {
						t.Fatalf("unbounded delta: %q", out.String())
					}
				default:
					if input.delta && !havePrev {
						passport += "\ndelta: no previous run for this key"
					}
					if out.String() != passport+"\n" {
						t.Fatalf("passport=%q, want %q", out.String(), passport+"\n")
					}
				}
				if scenario == "nul-at-8191" && !meta.Binary || scenario == "nul-at-8192" && meta.Binary {
					t.Fatalf("changed first-8-KiB binary rule: %+v", meta)
				}
				entry := readFinalizationAudit(t, store)
				if entry.Subcommand != subcommand || entry.Host != meta.Host || entry.Ctx != meta.Ctx || entry.CommandPreview != input.commandPreview || entry.BodySHA256 != input.bodySHA256 || entry.Verdict != "allowed" || entry.Exit != 0 || entry.LocalError != "" || entry.TransportErr != "" || entry.SetupErr != "" || entry.Ts.Before(meta.Ts) {
					t.Fatalf("audit=%+v", entry)
				}
			})
		}
	}
}

func TestFinalizeRunPredecessorSelection(t *testing.T) {
	for _, mode := range []string{artifact.ShellStateStateful, artifact.ShellStateNone} {
		for _, scenario := range []string{"matching", "legacy-only", "other-host", "other-context", "other-command", "body-hash"} {
			t.Run(mode+"/"+scenario, func(t *testing.T) {
				store, input := finalizationFixture(t, "run")
				input.meta.ShellStateMode, input.delta = mode, true
				if scenario == "body-hash" {
					input.bodySHA256 = sha256Hex("synthetic body\n")
					input.meta.Command = "body:" + input.bodySHA256[:16]
					input.commandPreview = input.meta.Command
				}
				previous := input.meta
				if scenario == "legacy-only" {
					previous.ShellStateMode = ""
				}
				base := seedFinalization(t, store, previous, input.output)
				// A newer record must not steal a predecessor across any key or
				// mode dimension, including legacy records for no-state runs.
				other := input.meta
				switch scenario {
				case "other-host":
					other.Host = "host-b"
				case "other-context":
					other.Ctx = "context-b"
				case "other-command", "body-hash":
					other.Command = "echo different"
				default:
					other.ShellStateMode = artifact.ShellStateNone
					if mode == artifact.ShellStateNone {
						other.ShellStateMode = artifact.ShellStateStateful
					}
				}
				seedFinalization(t, store, other, []byte("not the predecessor\n"))
				var out, errOut bytes.Buffer
				outcome := finalizeRun(store, input, &out, &errOut)
				meta := checkFinalizationSaved(t, store, outcome, input)
				wantBase := base.ID
				if scenario == "legacy-only" && mode == artifact.ShellStateNone {
					wantBase = ""
				}
				if meta.DeltaBase != wantBase || errOut.Len() != 0 {
					t.Fatalf("DeltaBase=%q, want %q; stderr=%q", meta.DeltaBase, wantBase, errOut.String())
				}
				if wantBase == "" && !strings.Contains(out.String(), "delta: no previous run") || wantBase != "" && !strings.Contains(out.String(), "no change since "+wantBase) {
					t.Fatalf("wrong predecessor presentation: %q", out.String())
				}
				entry := readFinalizationAudit(t, store)
				if entry.CommandPreview != input.commandPreview || entry.BodySHA256 != input.bodySHA256 {
					t.Fatalf("command evidence changed: %+v", entry)
				}
			})
		}
	}
}

func TestFinalizeRunFailures(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		for _, failure := range []string{"lookup", "missing-body", "binary-missing-body", "save", "audit"} {
			t.Run(subcommand+"/"+failure, func(t *testing.T) {
				store, input := finalizationFixture(t, subcommand)
				input.delta = true
				prev := seedFinalization(t, store, input.meta, input.output)
				input.output = []byte("new retained output\n")
				wantDiagnostic := ""
				switch failure {
				case "lookup":
					// A real predecessor decoding failure leaves Save operational.
					if _, err := store.DB.Exec(`UPDATE runs SET ts='invalid' WHERE art_id=?`, prev.ID); err != nil {
						t.Fatal(err)
					}
					wantDiagnostic = subcommand + ": delta lookup for " + input.meta.Host + ": parse ts for key "
				case "missing-body", "binary-missing-body":
					if err := os.Remove(filepath.Join(store.Root, "art", prev.ID)); err != nil {
						t.Fatal(err)
					}
					wantDiagnostic = subcommand + ": render delta for " + input.meta.Host + ": read previous artifact "
					if failure == "binary-missing-body" {
						input.output = []byte("new\x00binary")
						wantDiagnostic = "" // Binary presentation must not even read the old body.
					}
				case "save":
					if err := os.WriteFile(filepath.Join(store.Root, "art", "a2"), []byte("occupied"), 0o600); err != nil {
						t.Fatal(err)
					}
					wantDiagnostic = subcommand + ": save artifact: write artifact file: refuse existing artifact destination "
				case "audit":
					if err := os.Mkdir(filepath.Join(store.Root, "audit.jsonl"), 0o700); err != nil {
						t.Fatal(err)
					}
					wantDiagnostic = subcommand + ": append audit: "
				}
				var out, errOut bytes.Buffer
				outcome := finalizeRun(store, input, &out, &errOut)
				if wantDiagnostic == "" && errOut.Len() != 0 || wantDiagnostic != "" && (!strings.HasPrefix(errOut.String(), wantDiagnostic) || strings.Count(errOut.String(), "\n") != 1) {
					t.Fatalf("stderr=%q, want one diagnostic starting %q", errOut.String(), wantDiagnostic)
				}
				if failure == "save" {
					if outcome.Kind() != runOutcomeInternalFailure || outcome.ExitCode() != exitUsage || outcome.ArtifactID() != "" || out.Len() != 0 {
						t.Fatalf("save failure outcome=%+v stdout=%q", outcome, out.String())
					}
					if _, err := os.Stat(filepath.Join(store.Root, "audit.jsonl")); !os.IsNotExist(err) {
						t.Fatalf("audit after failed Save: %v", err)
					}
					var count int
					if err := store.DB.QueryRow(`SELECT count(*) FROM runs`).Scan(&count); err != nil || count != 1 {
						t.Fatalf("failed Save left new row: count=%d err=%v", count, err)
					}
					return
				}
				meta := checkFinalizationSaved(t, store, outcome, input)
				wantBase := prev.ID
				if failure == "lookup" {
					wantBase = ""
				}
				if meta.DeltaBase != wantBase || outcome.Kind() != runOutcomeSuccess {
					t.Fatalf("outcome=%+v, want base=%q", outcome, wantBase)
				}
				if failure != "audit" {
					want := artifact.RenderPassport(meta, filepath.Join(store.Root, "art", meta.ID), input.output, input.budget)
					if failure == "lookup" {
						want += "\ndelta: no previous run for this key"
					}
					if out.String() != want+"\n" {
						t.Fatalf("fallback passport=%q, want %q", out.String(), want+"\n")
					}
					readFinalizationAudit(t, store)
				} else if !strings.Contains(out.String(), "-report\n+new retained output\n") {
					t.Fatalf("audit failure lost passport: %q", out.String())
				}
			})
		}
	}
}

// Observe durable evidence at the output seam, not calls to internal helpers.
type finalizationObserver func([]byte) (int, error)

func (f finalizationObserver) Write(p []byte) (int, error) { return f(p) }

func TestFinalizeRunSaveOutputAuditOrdering(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		t.Run(subcommand, func(t *testing.T) {
			store, input := finalizationFixture(t, subcommand)
			input.delta = true
			prev := seedFinalization(t, store, input.meta, input.output)
			var outputAt time.Time
			out := finalizationObserver(func(p []byte) (int, error) {
				outputAt = time.Now()
				meta, path, err := store.Get("a2")
				if err != nil || meta.DeltaBase != prev.ID || !meta.Ts.Equal(input.meta.Ts) {
					t.Fatalf("output before durable Save/DeltaBase: meta=%+v err=%v", meta, err)
				}
				body, err := os.ReadFile(path)
				if err != nil || !bytes.Equal(body, input.output) || !bytes.Contains(p, []byte("no change since "+prev.ID)) {
					t.Fatalf("output not backed by evidence: body=%q output=%q err=%v", body, p, err)
				}
				if _, err := os.Stat(filepath.Join(store.Root, "audit.jsonl")); !os.IsNotExist(err) {
					t.Fatalf("audit must follow output: %v", err)
				}
				return len(p), nil
			})
			var errOut bytes.Buffer
			outcome := finalizeRun(store, input, out, &errOut)
			checkFinalizationSaved(t, store, outcome, input)
			entry := readFinalizationAudit(t, store)
			if outputAt.IsZero() || entry.Ts.Before(outputAt) || errOut.Len() != 0 {
				t.Fatalf("audit timestamp=%v output timestamp=%v stderr=%q", entry.Ts, outputAt, errOut.String())
			}
		})
	}
}

func TestFinalizeRunLocalResults(t *testing.T) {
	for _, failure := range []string{"", "nonzero", "start", "timeout", "output-limit", "capture"} {
		t.Run(fmt.Sprintf("result=%s", failure), func(t *testing.T) {
			store, input := finalizationFixture(t, "local")
			wantKind, wantExit := runOutcomeSuccess, 0
			if failure == "nonzero" {
				input.meta.Exit = 7
				wantKind, wantExit = runOutcomeRemoteNonZero, 7
			} else if failure != "" {
				input.meta.LocalError = failure
				input.meta.Truncated = failure == "output-limit"
				input.output = []byte("partial output")
				wantKind, wantExit = runOutcomeLocalFailure, exitUsage
			}
			var out, errOut bytes.Buffer
			outcome := finalizeRun(store, input, &out, &errOut)
			checkFinalizationSaved(t, store, outcome, input)
			entry := readFinalizationAudit(t, store)
			if outcome.Kind() != wantKind || outcome.ExitCode() != wantExit || entry.LocalError != input.meta.LocalError || entry.Exit != input.meta.Exit || entry.TransportErr != "" || errOut.Len() != 0 {
				t.Fatalf("outcome=%+v audit=%+v stderr=%q", outcome, entry, errOut.String())
			}
		})
	}
}

// Keep this at the callers: the module accepts a prepared advisory but must not
// acquire command text or decide whether a local command deserves one.
func TestFinalizationPipeAdvisoryWiring(t *testing.T) {
	for _, subcommand := range []string{"run", "local"} {
		t.Run(subcommand, func(t *testing.T) {
			t.Setenv("SSHAI_ROOT", t.TempDir())
			var out, errOut bytes.Buffer
			command := "echo report | head -1"
			var rc int
			if subcommand == "run" {
				rc = runWith(&fakeTr{body: "report\n"}, []string{"host-a", "--", command}, &out, &errOut)
			} else {
				rc = localWithRunner(func([]string, []byte, time.Duration, int64) runner.Result {
					return runner.Result{Output: []byte("report\n")}
				}, []string{"--shell", "bash", "--", command}, &out, &errOut)
			}
			if rc != 0 || errOut.Len() != 0 || strings.Contains(out.String(), "note: trailing filter") != (subcommand == "run") {
				t.Fatalf("rc=%d stdout=%q stderr=%q", rc, out.String(), errOut.String())
			}
		})
	}
}
