# sshai run — machine-readable result contract

**Date:** 2026-08-16
**Issue:** [aimem#767](https://github.com/aprudkin/aimem/issues/767)
**Status:** design approved in brainstorm; implementation plan pending
**Relation to existing design:** additive feature on `2026-08-06-sshai-design.md`. Does not amend any prior decision. Where this document is silent, the v1 design governs.

## Problem

`sshai run`'s only output today is a human passport:

```
a17 host=pg-prod-01 exit=0 lines=8412 bytes=612K time=1.8s
file=~/.sshai/art/a17
tail3:
  ...
```

Adapters that need the run's metadata (run id, host, exit, artifact path, counts, duration) currently have to parse that text — `a90 host=...`, the second `file=...` line, the `k/M/B` byte suffix, the `ms`/`s` duration suffix — and recover correctly when the remote command's own output (which `sshai` does not control) happens to contain those tokens. The Windows App apply workflow (`aimem#764`) is one such consumer; the exact-DC collector has to prove the sshai passport (`host`, remote exit, artifact identity) and then read the stored JSON result. A stable, versioned, machine-readable contract removes the parser and the false-match risk in one stroke.

## Goals

1. `sshai run` exposes an explicit machine-readable mode with a versioned schema.
2. One bounded JSON document carries run id, resolved host identity, remote exit code, artifact path/identifier, line count, byte count, and duration per host — plus an aggregate summary at the top level for fan-out.
3. Machine-readable output is not mixed with human tail/preview text.
4. Default human-readable output remains byte-equivalent on every existing path.
5. `--body-file -` continues to keep the command body out of argv and out of the envelope; logs safe hash-only metadata.
6. Tests cover: success, non-zero remote exit, transport failure, malformed/unavailable artifact, paths containing spaces, body-file boundary, fan-out mixed outcomes.
7. Documentation gives consumers one stable parsing contract and a safe example using stdin body mode.

## Non-goals

- Do not change anything for subcommands other than `run`.
- Do not expose command bodies, secrets, authorization material, or raw captured output in the envelope.
- Do not couple the contract specifically to Windows App or `aimem#764`.
- Do not introduce a daemon, streaming, or per-host multi-document mode.

## Decisions (brainstorm outcomes, 2026-08-16)

| Question | Decision | Rejected alternatives |
|---|---|---|
| Flag shape | `--result-format=human\|json` (enum, default `human`) | `--json` boolean (locks a second format behind another flag); per-flag `human-passport/json-passport/...` (overfits the schema's name to its transport) |
| Fan-out envelope | One document per `sshai run` invocation: top-level `runs: [...]` in argv order + `summary` counters | NDJSON (one envelope per host, line-by-line) — conflicts with the acceptance criterion's "one bounded document" |
| Schema versioning | Literal `"schema_version": "v1"`; `vN` literal only — new flag value `json-v2` may be added later in `result-format` for breaking changes | SemVer `"1.0.0"` (over-precise; the field exists so adapters know when to switch parsers, not to negotiate additively) |
| Companion file flag | `--result-out <path>` atomically replaces one regular destination with the same envelope bytes plus newline (mode `0600`), refusing symlinks and other non-regular paths | None — a small, mechanical addition that helps processes that prefer file handoffs |
| Field naming | `snake_case` keys derived verbatim from `artifact.Meta` plus top-level `schema_version`, `batch_id`, `summary` | Human-friendly aliases (`error_reason`, `duration`) — duplicates the source of truth and invites drift |
| Body excerpt in envelope | None. `tail3` / delta body / pipe advisory / fan-out aggregate line all suppressed on stdout in JSON mode | Inline last-N lines — violates "Do not expose ... broad raw output" |
| Field for run id | `runs[].id` is the existing artifact autoincrement id (`Meta.ID`); a separate `batch_id` at the top level correlates fan-out | A new UUID per host — collides with the persisted artifact id the agent is already using as the index into `sshai q`/`diff`/`log` |
| Body-file boundary | Inherited from `deltaKeyCommand`: `--body-file` / `--body-file -` bodies become `"body:<sha256hex>[:16]"` in `runs[].command` automatically | A second-pass redactor — the boundary already lives next to the data it protects |
| Default behavior on `Save` failure in JSON mode | Skip the envelope, fall through to the existing human passport path, exit `exitUsage` | Emit a partial envelope (`runs.length < summary.hosts - summary.policy_denied`) — violates the run-count-vs-hosts invariant |
| Default behavior on `--result-out` without `--result-format=json` | `exitUsage` + stderr note: `--result-out requires --result-format=json` | Honor the file and write the human passport there — surprising, undocumented |
| Mixed mode (human + JSON on one stream) | Forbidden. JSON mode writes exactly one JSON object to stdout, nothing else | Interleaved — violates "machine-readable output is not mixed" |

## Architecture

`artifact.RenderResult` takes saved `Meta` values in argv order plus a `Summary` and returns the JSON bytes for the v1 envelope. `runArgs` parses `--result-format` and `--result-out` once as invocation-level options; per-host `Opts` contains only execution state.

Both a single host and fan-out enter `runInvocation`. Each worker writes human stdout/stderr into its own `hostRunResult` buffers and returns a typed `RunOutcome`. After every worker completes, one `writeRunResults` controller owns deterministic stderr flushing, human fallback, aggregation, JSON rendering, exit precedence, and optional side-file publication. Human mode renders an aggregate line only when the invocation has more than one host.

`RunOutcome` distinguishes success, remote non-zero, transport failure, Windows setup failure, policy denial, and unsaved internal failure without interpreting `nil Meta` plus a numeric return code. JSON summaries are computed from these typed outcomes:

- `transport_errors` = count of hosts where `Meta.TransportErr != ""`
- `setup_errors` = count of hosts where `Meta.SetupErr != ""`
- `failed` = remote non-zero exits plus setup failures
- `ok` = rest
- `worst_exit` = `max(Meta.Exit)` over actual command exits (0 if none)
- `policy_denied` = count of hosts that were denied before running

A policy-denied host is **not** in `runs[]` because no `Meta` or artifact exists for it. It is surfaced only via `summary.policy_denied` and process exit `exitPolicy`. An unsaved internal failure suppresses the envelope and uses the existing human fallback with `exitUsage`, preserving the invariant that every `runs[]` entry has a real saved artifact.

`batch_id` is a single `crypto/rand`-generated `a` plus 32 lowercase hexadecimal characters; it correlates the `runs[]` entries from one invocation and is not persisted.

## Schema (v1)

The envelope is one JSON object on stdout, terminated by `\n`:

```json
{
  "schema_version": "v1",
  "batch_id": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6",
  "summary": {
    "hosts": 1,
    "ok": 1,
    "failed": 0,
    "transport_errors": 0,
    "policy_denied": 0,
    "worst_exit": 0
  },
  "runs": [
    {
      "id": "a17",
      "host": "pg-prod-01",
      "ctx": "default",
      "command": "journalctl -u postgres --since -1h",
      "exit": 0,
      "transport_error": "",
      "artifact_path": "/Users/op/.sshai/art/a17",
      "bytes": 627456,
      "lines": 8412,
      "sha256": "9f1c2e3a4b5d6e7f8091a2b3c4d5e6f7081928374a5b6c7d8e9f0a1b2c3d4e5f6",
      "duration_ms": 1843,
      "ts": "2026-08-16T12:34:56.789012345Z",
      "truncated": false,
      "binary": false,
      "delta_base": ""
    }
  ]
}
```

Transport-error entries may additionally carry
`"transport_diagnostic": "host key verification failed"`. Windows shell setup failures additionally
carry `"setup_error": "windows-shell"` and the fixed `"setup_diagnostic": "Windows shell setup failed"`.
These are optional, non-breaking v1 additions.

### Field reference

| Field | Source | Type | Notes |
|---|---|---|---|
| `schema_version` | constant | string `"v1"` | Required. Future breaking changes move to a new `result-format` value; non-breaking additions stay on `v1`. |
| `batch_id` | generated per invocation | string | Same shape as the existing artifact ids; not persisted. |
| `summary.hosts` | `len(hosts)` | int | The number of hosts requested in argv. Always equals `len(runs) + summary.policy_denied`. |
| `summary.policy_denied` | computed | int | Hosts denied by the readonly policy before any run; such hosts never call `Store.Save` and are absent from `runs[]`. |
| `summary.ok` | computed | int | Successful `runs[]` entries with no transport, setup, or local error and `exit == 0`. |
| `summary.failed` | computed | int | Remote non-zero exits, setup failures, and local failures. |
| `summary.transport_errors` | computed | int | `runs[]` entries where `transport_error != ""`. |
| `summary.setup_errors` | computed | int | `runs[]` entries where `setup_error != ""`; setup failures are also included in `summary.failed`. |
| `summary.worst_exit` | computed | int | `max(exit)` over actual command exits; setup and transport failures do not change it. |
| `runs[].id` | `Meta.ID` | string | The existing artifact autoincrement id; the consumer's index into `sshai q`/`diff`/`log`. |
| `runs[].host` | `Meta.Host` | string | As resolved in argv. |
| `runs[].ctx` | `Meta.Ctx` | string | The named state context used for this run. |
| `runs[].command` | `Meta.Command` | string | Already hash-only for `--body-file` / `--body-file -` runs (`"body:<sha256hex>[:16]"`); human-readable for inline `-- words`. Never the raw body. |
| `runs[].exit` | `Meta.Exit` | int | `0` when `transport_error != ""` or `setup_error != ""`; those fields distinguish an unset exit from an honest command exit. |
| `runs[].transport_error` | `Meta.TransportErr` | string | Empty when absent. Possible values today: `""`, `"timeout"`, `"ssh"`, `"scp"`. A policy denial is not a transport error and never reaches `runs[]` (see `summary.policy_denied`). |
| `runs[].transport_diagnostic` | `Meta.TransportDiagnostic` | string, optional | Canonical allowlisted cause derived from SSH/scp output. Omitted when absent; raw transport output is never exposed. |
| `runs[].setup_error` | `Meta.SetupErr` | string, optional | `"windows-shell"` when Windows PowerShell setup is exhausted; omitted otherwise. |
| `runs[].setup_diagnostic` | `Meta.SetupDiagnostic` | string, optional | Fixed canonical setup diagnostic; probe output is never exposed. |
| `runs[].artifact_path` | `filepath.Join(root, "art", Meta.ID)` | string | Always present for every `runs[]` entry. A diagnosed transport error or setup error stores only its canonical diagnostic; an unclassified transport error keeps an empty artifact. A policy-denied host has no artifact and therefore no `runs[]` entry. |
| `runs[].bytes` | `Meta.Bytes` | int64 | Stored output or canonical diagnostic body size; `0` when the artifact is empty. |
| `runs[].lines` | `Meta.Lines` | int64 | Stored output or canonical diagnostic line count; `0` when the artifact is empty. |
| `runs[].sha256` | `Meta.SHA256` | string | SHA-256 of the saved artifact, including an empty transport-error artifact when no diagnostic matched. |
| `runs[].duration_ms` | `Meta.DurationMs` | int64 | Transport errors can record a partial duration (probe time). |
| `runs[].ts` | `Meta.Ts` | string (RFC3339Nano) | UTC. |
| `runs[].truncated` | `Meta.Truncated` | bool | |
| `runs[].binary` | `Meta.Binary` | bool | |
| `runs[].delta_base` | `Meta.DeltaBase` | string | Empty when not `--delta` or no previous run; otherwise the previous artifact id. |

`sha256`, `transport_error`, and `delta_base` are written as `""` rather than omitted. The additive
`transport_diagnostic`, `setup_error`, and `setup_diagnostic` are intentionally omitted when empty so
default v1 envelopes remain byte-compatible; consumers must treat them as optional. `summary.setup_errors`
is also omitted when zero. Consumers must inspect error fields before interpreting `exit`; an `exit` of `0`
with `setup_error` present does not mean that the user command succeeded.

`json.Marshal` indents are off; the consumer decides formatting.

## Flag behavior

### `--result-format=<human|json>`

- Default `human`. The existing per-host human passport and the fan-out aggregate line are unchanged.
- `json`: stdout renders exactly one envelope, single-host or fan-out alike. `tail3`, `--delta` body diffs, pipe advisories, and the aggregate line are all suppressed on stdout; the envelope is the only stdout write in this mode.
- Stderr is unchanged in either mode (diagnostics only).
- Unknown value (`--result-format=xml`): `exitUsage` + stderr note from the flag parser.

### `--result-out <path>`

- Optional; only meaningful with `--result-format=json`.
- When set in JSON mode: after the envelope has been written to stdout, the same bytes plus a trailing newline are written to a same-directory temporary file with mode `0600`, synced, closed, and atomically renamed to `<path>`. Existing regular files are replaced; symlinks and other non-regular paths are refused.
- When set in human mode: `exitUsage` + `run: --result-out requires --result-format=json`.
- No `--result-out` in JSON mode: identical to today's behavior except for the envelope itself.

### Flags independent of result format

`--body-file`, `--delta`, `--budget`, `--timeout`, `--ctx`, and `--powershell-host` retain the same
semantics in human and JSON modes. `--body-file -` keeps the body entirely off argv and out of the
envelope (the JSON `command` value is the `body:<sha256hex>[:16]` form, never the body text).

## Error handling

| Failure | Mode | Outcome |
|---|---|---|
| Flag parse (`--result-format=foo`) | both | `exitUsage` from `flag.ContinueOnError`; stderr gets the standard usage line; no envelope |
| `--result-out` without `--result-format=json` | human | `exitUsage` + stderr `run: --result-out requires --result-format=json` |
| `config.Load` fail (missing `~/.sshai/`, bad TOML) | both | `exitUsage` + stderr `run: load config: ...`; no envelope |
| Zero hosts / no `--` separator / bad `--ctx` | both | `exitUsage` + stderr note; no envelope |
| Policy denial per host | JSON | Host is **not** in `runs[]` (no artifact is saved on that path). `summary.policy_denied++`; `summary.hosts` still includes the host, so `summary.hosts == len(runs) + summary.policy_denied`. Process exits `exitPolicy` (97). |
| `Store.Save` failure | both | Single-host: human passport path (existing behavior); fan-out: drop the failing host from `runs[]` and continue (`summary.hosts` matches surviving length; an unenclosed host is preferable to a malformed envelope). |
| `*transport.TransportError` (ssh, scp, timeout) | both | Routing through `handleTransportError` records the run with `transport_error: <reason>` and `exit: 0`. If raw output matches the fixed safe allowlist, `transport_diagnostic` is present and the canonical phrase is the artifact body; otherwise the field is omitted and the artifact remains empty. |
| `*session.RemoteSetupError` | both | Records `setup_error: "windows-shell"`, the fixed `setup_diagnostic`, and a fixed diagnostic artifact; returns process exit `99`. It executes no user body, caches no facts, and retains no probe output. |
| `delta.Render` fail post-`Save` (previous artifact pruned underfoot) | both | Stderr `run: render delta for %s: ...`; envelope carries the saved meta as if `--delta` had no previous run (mirrors today's human-path fallback). |
| Runlog `AppendAudit` fail post-`Save` | both | Stderr note; never fatal in either mode. |
| Flag-set `-help` on `run` | both | Today's help text now lists the new flags. |

## Testing

All tests live in `internal/cli/run_test.go` against the existing `fakeTr` (no network; `t.TempDir()`-rooted `SSHAI_ROOT`). Total addition: ~11 tests, all unit-grade, runtime budget <200 ms cumulative.

1. **`TestRunResultFormatJSONSuccess`** — `fakeTr` returns `ExitCode:0`, a small body. Assert: stdout parses as one JSON object; `schema_version == "v1"`; `len(runs) == 1`; `runs[0].id` matches an artifact file under `<root>/art/`; `runs[0].sha256` equals `sha256sum` of the on-disk bytes; `runs[0].bytes > 0`; `runs[0].lines > 0`; `summary.ok == 1`; `summary.hosts == 1`; the literal substring `"tail3:"` is absent from stdout.
2. **`TestRunResultFormatJSONNonZeroExit`** — `fakeTr` returns `ExitCode:2`. Assert: `runs[0].exit == 2`; `summary.failed == 1`; `summary.ok == 0`; `summary.worst_exit == 2`.
3. **`TestRunResultFormatJSONTransportError`** — `fakeTr.Exec` returns a `*transport.TransportError{R:"ssh"}`. Assert: `runs[0].transport_error == "ssh"`; `runs[0].exit == 0`; `runs[0].bytes == 0`; `runs[0].artifact_path` is still a string under `<root>/art/<id>` (the empty artifact was saved by `handleTransportError`); `summary.transport_errors == 1`; `summary.failed == 0`.
4. **`TestRunResultFormatJSONSaveFailure`** — substitute `Store` with one whose `Save` returns an error; single host. Assert: stdout contains no JSON envelope (`json.Unmarshal` of `strings.TrimSpace(stdout)` errors); stderr contains today's `run: save artifact: ...` line; exit is `exitUsage` (96).
5. **`TestRunResultFormatJSONFanOutMixed`** — three hosts in argv order: one ok, one `ExitCode:1`, one `*transport.TransportError`. Assert: `len(runs) == 3` in argv order; `summary.hosts == 3`; `summary.ok == 1`; `summary.failed == 1`; `summary.transport_errors == 1`; `summary.policy_denied == 0`; `summary.worst_exit == 1`; each `runs[i].host` matches the corresponding argv host.
6. **`TestRunResultFormatJSONPolicyDenied`** — one host whose command fails the readonly allowlist (`policy.CheckReadonly` returns an error). Assert: `len(runs) == 0`; `summary.hosts == 1`; `summary.policy_denied == 1`; `summary.ok == 0`; the envelope round-trips; the process exits `exitPolicy` (97); the envelope contains no fabricated `artifact_path`.
7. **`TestRunResultFormatJSONPathWithSpaces`** — set `SSHAI_ROOT` to a `t.TempDir()` containing a space in the path (`/tmp/with space/.sshai`). Run a successful single host. Assert: the envelope round-trips through `json.Unmarshal`; `runs[0].artifact_path` contains the space; `runs[0].artifact_path` resolves via `os.ReadFile` to a non-empty result.
8. **`TestRunResultFormatJSONBodyFileBoundary`** — pipe a 200-byte script into `sshai run --body-file - <host>` with `result-format=json`. Assert: `runs[0].command` equals `"body:<sha256hex>[:16]"`; the envelope, treated as a string, contains zero matches of three distinctive substrings taken from the script body; `audit.jsonl` likewise contains zero matches (artifact command metadata and `auditCommandPreview` already encode hash-only — assert the JSON inherits this).
9. **`TestRunResultFormatJSONResultOutToFile`** — `--result-out <file>` with `result-format=json`. Assert: `os.Stat(file).Mode().Perm() == 0o600`; reading the file yields bytes identical to stdout; reusing the path atomically replaces the previous document rather than appending; injected write/close failures leave the previous document unchanged and publish no temporary file; directory and symlink targets return `exitUsage`.
10. **`TestRunResultFormatHumanModeByteEquivalent`** — freeze today's per-host passport and aggregate line against the existing `fakeTr` fixtures (commands `TestRunSingleHost*`, `TestRunFanout*`); rerun with `--result-format=human`; assert byte-equality. The whole point of the change is "default `human` is unchanged"; this is that guarantee.
11. **`TestHelpRunDocumentsResultFormat`** — `internal/cli/help_test.go` flag-presence assertions grow by two: `--result-format` and `--result-out`. The detail block `helpDetail["run"]` is updated in lockstep.

A regression test for fan-out's `runs[]` ordering vs `summary.hosts` consistency: under any host mix, `len(runs) == summary.hosts - summary.policy_denied` and `summary.ok + summary.failed + summary.transport_errors == len(runs)`. Failure implies the envelope's own invariant is broken.

`go test -run ResultFormat -v ./...` must show every above test green; `go test ./...` must continue green (no race detector regressions — fan-out goes through the same `wg.Wait` boundary).

## Documentation updates

| File | Change |
|---|---|
| `docs/superpowers/specs/2026-08-16-machine-readable-result-contract-design.md` | This document. Committed before any code lands. |
| `internal/cli/help.go::helpDetail["run"]` | Add `--result-format` and `--result-out` to the `Flags:` block, plus one short paragraph in the prose describing the JSON mode's contract. |
| `internal/cli/help.go::helpSummary["run"]` | No change — the one-liner stays as-is (the flag is for `help run`, not the bare screen). |
| `docs/agent-usage.md` | New short section `## Machine-readable mode` with one example: `sshai run --result-format=json --body-file - pg-prod-01 <<<'Get-Date'` followed by `jq '.runs[0].exit'`. |
| `README.md` | Append one paragraph to the `## Usage` block noting `--result-format=json` and pointing at `sshai help run`. |
| `docs/superpowers/specs/2026-08-06-sshai-design.md` | New subsection `### Result formats` cross-linking this design; existing sections unchanged. |
| `docs/server-workflow-migration.md`, `docs/windows-parity.md`, `docs/benchmarks/*` | No change. |

English everywhere, matching the repo rule.

## Migration, compatibility, rollout

- **CLI migration.** Default `human` output is byte-equivalent on every existing path. No operator action needed; existing agents continue to see the same passport. Opting into JSON mode is a flag addition on the call site.
- **Adapter migration.** The Windows App apply workflow in `aimem#764` switches from "parse the human passport" to "--result-format=json". That is a one-line swap in the adapter; an adapter can ship it without coordinating with the sshai release. This design does not authorize that change — `aimem#764` is the consumer's own milestone.
- **Help surface.** R5's progressive-disclosure contract holds: one line in the agent's instructions, then `sshai help run` for the full flag reference. The help tests (`help_test.go`) catch flag-presence drift.
- **Backwards compatibility.** JSON mode is gated behind an explicit flag; no consumer sees it accidentally. The `schema_version` field is the future break point — a breaking change adds a new `result-format` value (`json-v2` etc.) while `v1` keeps working unchanged. Non-breaking additions stay on `v1`.
- **No secrets in argv.** The body-file boundary (`body:<sha256hex>[:16]`) is unchanged; `--body-file -` from stdin keeps the body off argv and out of the envelope and the audit log.

## Risks & edge cases

- **Field-name drift.** `Meta` and the schema must agree. Mitigation: `artifact.RenderResult` maps each `Meta` into one typed `runEntry` whose explicit JSON tags are locked by `internal/artifact/result_test.go`.
- **Goroutine safety.** Per-host `Meta` records are read-only after `wg.Wait`; identical to today's human fan-out. No new shared mutable state.
- **`--result-out` collisions.** Symlink, existing directory, and other non-regular targets are refused with `exitUsage`; existing regular files are atomically replaced. The complete document is written to a same-directory `0600` temporary file, synced, closed, then renamed, so write/close failures cannot publish partial JSON.
- **Marshal performance.** `Meta` is O(100 bytes); envelope is O(hosts). Worst-case fan-out (say 200 hosts) is still O(KB); unmarshalling is well under a millisecond per call. Not a concern.
- **Out-of-order `runs[]`.** `runInvocation` stores every `hostRunResult` by argv index; failure to preserve order is caught by explicit positional assertions in `TestRunResultFormatJSONFanOutMixed` and `TestRenderResultEnvelopePreservesOutcomeOrder`.
- **`--delta` interaction.** The `runs[].delta_base` field is the only envelope surface for delta; the body diff is human-passport territory and is suppressed in JSON. Adapters that want the diff call `sshai diff <delta_base> <runs[i].id>`. Documented in the example.
- **Runlog, audit.jsonl.** Setup failures remain searchable and appear as `setup-error=windows-shell`; audit records may carry `SetupErr` but retain verdict `allowed`. `Meta.Command` already encodes `body:<sha256hex>[:16]` for body-file runs; the envelope inherits this. `auditCommandPreview` already does the same for the audit log.

## Acceptance

- `go build ./...` clean.
- `go test ./...` green.
- `go test -run ResultFormat -v ./...` green (all 11 tests listed above).
- `sshai help run` shows the new flags.
- `sshai run --result-format=json --body-file - pg-prod-01 <<<'Get-Date'` produces one parseable JSON object; `jq -e '.runs[0].command | startswith("body:")'` returns true; the body text does not appear anywhere on stdout.
- An adapter reading the envelope byte-for-byte via `json.Decoder` into a typed struct succeeds without any text-side fallback.
- Default human output, today, equals default human output after the change (Test #10).
