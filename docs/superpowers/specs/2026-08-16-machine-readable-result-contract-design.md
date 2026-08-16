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
| Companion file flag | `--result-out <path>` writes the same envelope bytes to disk (mode `0600`, `O_APPEND|O_CREATE`) after stdout, only meaningful with `--result-format=json` | None — a small, mechanical addition that helps processes that prefer file handoffs |
| Field naming | `snake_case` keys derived verbatim from `artifact.Meta` plus top-level `schema_version`, `batch_id`, `summary` | Human-friendly aliases (`error_reason`, `duration`) — duplicates the source of truth and invites drift |
| Body excerpt in envelope | None. `tail3` / delta body / pipe advisory / fan-out aggregate line all suppressed on stdout in JSON mode | Inline last-N lines — violates "Do not expose ... broad raw output" |
| Field for run id | `runs[].id` is the existing artifact autoincrement id (`Meta.ID`); a separate `batch_id` at the top level correlates fan-out | A new UUID per host — collides with the persisted artifact id the agent is already using as the index into `sshai q`/`diff`/`log` |
| Body-file boundary | Inherited from `metaCommand`: `--body-file` / `--body-file -` bodies become `"body:<sha256hex>[:16]"` in `runs[].command` automatically | A second-pass redactor — the boundary already lives next to the data it protects |
| Default behavior on `Save` failure in JSON mode | Skip the envelope, fall through to the existing human passport path, exit `exitUsage` | Emit a partial envelope (`runs.length < summary.hosts - summary.policy_denied`) — violates the run-count-vs-hosts invariant |
| Default behavior on `--result-out` without `--result-format=json` | `exitUsage` + stderr note: `--result-out requires --result-format=json` | Honor the file and write the human passport there — surprising, undocumented |
| Mixed mode (human + JSON on one stream) | Forbidden. JSON mode writes exactly one JSON object to stdout, nothing else | Interleaved — violates "machine-readable output is not mixed" |

## Architecture

A single new helper, `artifact.RenderResult`, takes a slice of `Meta` (one per host, already in argv order) plus a `Summary` and returns the JSON bytes for the v1 envelope. The three current stdout write sites in `internal/cli/run.go` route through it:

| Site | Path | Today | After |
|---|---|---|---|
| `runHost:747` | success path, single host | `fmt.Fprintln(stdout, RenderPassport(...))` | If `opts.RenderFormat == json`: append to a per-host envelope buffer; else unchanged |
| `handleTransportError:786` | single-host transport failure | `fmt.Fprintln(stdout, RenderPassport(...))` | Same gate — transport errors are still a valid run with `transport_error != ""`, included in the envelope |
| `runFanout:365-406` | per-host passports + aggregate line | write each `bytes.Buffer`, then `fmt.Fprintf(stdout, "hosts=%d ok=%d ...")` | Each goroutine still fills its per-host `bytes.Buffer` (no shared mutable state, today's pattern); on `wg.Wait`, aggregate counters are recomputed from `Meta` (same algorithm as today) and `RenderResult` is called once for the whole fan-out |

`runArgs` parses `--result-format` and `--result-out` once and threads them into `Opts`. `runFanout` already owns the per-host-buffer pattern that makes a single end-of-invocation write trivial — the JSON envelope is just a different renderer consuming the same buffer slice.

The classification loop in `runFanout:387-403` (which scans each host's first line for `transport-error=`, the literal `policy-denied` line, or a numeric exit) is preserved verbatim for human mode. For JSON mode the same counters are rebuilt from `Meta` directly:

- `transport_errors` = count of hosts where `Meta.TransportErr != ""`
- `failed` = count of hosts with `TransportErr == ""` and `Meta.Exit != 0`
- `ok` = rest
- `worst_exit` = `max(Meta.Exit)` over hosts where `TransportErr == ""` (0 if none)
- `policy_denied` = count of hosts that were denied before running

A policy-denied host is **not** in `runs[]` — that path returns `exitPolicy` from `runHost:513-523` before `Store.Save` is ever called, so no `Meta` and no artifact exists for it. It is surfaced only via the `summary.policy_denied` counter (and the process exit `exitPolicy`), so the adapter still learns which host was denied without the envelope fabricating a result that never happened. This preserves the invariant that every `runs[]` entry has a real saved artifact.

`batch_id` is a single `crypto/rand`-generated UUID-ish identifier (`a[0-9a-z]{32}` is fine; the field's only contract is "stable, unique per `sshai run` invocation") so a consumer receiving fan-out output can correlate the N `runs[]` entries. It is not persisted.

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

### Field reference

| Field | Source | Type | Notes |
|---|---|---|---|
| `schema_version` | constant | string `"v1"` | Required. Future breaking changes move to a new `result-format` value; non-breaking additions stay on `v1`. |
| `batch_id` | generated per invocation | string | Same shape as the existing artifact ids; not persisted. |
| `summary.hosts` | `len(hosts)` | int | The number of hosts requested in argv. Always equals `len(runs) + summary.policy_denied`. |
| `summary.policy_denied` | computed | int | Hosts denied by the readonly policy before any run; such hosts never call `Store.Save` and are absent from `runs[]`. |
| `summary.ok` | computed | int | `runs[]` entries where `transport_error == ""` and `exit == 0`. |
| `summary.failed` | computed | int | `runs[]` entries where `transport_error == ""` and `exit != 0`. |
| `summary.transport_errors` | computed | int | `runs[]` entries where `transport_error != ""`. |
| `summary.worst_exit` | computed | int | `max(exit)` over hosts where `transport_error == ""`; `0` if all transport-errored. |
| `runs[].id` | `Meta.ID` | string | The existing artifact autoincrement id; the consumer's index into `sshai q`/`diff`/`log`. |
| `runs[].host` | `Meta.Host` | string | As resolved in argv. |
| `runs[].ctx` | `Meta.Ctx` | string | The named state context used for this run. |
| `runs[].command` | `Meta.Command` | string | Already hash-only for `--body-file` / `--body-file -` runs (`"body:<sha256hex>[:16]"`); human-readable for inline `-- words`. Never the raw body. |
| `runs[].exit` | `Meta.Exit` | int | `0` when `transport_error != ""` (the agent's contract: an unset exit is disambiguated by the presence of `transport_error`). |
| `runs[].transport_error` | `Meta.TransportErr` | string | Empty when absent. Possible values today: `""`, `"timeout"`, `"ssh"`, `"scp"`. A policy denial is not a transport error and never reaches `runs[]` (see `summary.policy_denied`). |
| `runs[].artifact_path` | `filepath.Join(root, "art", Meta.ID)` | string | Always present for every `runs[]` entry — including transport-error runs, whose empty artifact is saved by `handleTransportError` via `Store.Save` (run.go:778). A policy-denied host has no artifact and therefore no `runs[]` entry. |
| `runs[].bytes` | `Meta.Bytes` | int64 | `0` for transport-error runs. |
| `runs[].lines` | `Meta.Lines` | int64 | `0` for transport-error runs. |
| `runs[].sha256` | `Meta.SHA256` | string | Empty string (""), not omitted, for transport-error runs. |
| `runs[].duration_ms` | `Meta.DurationMs` | int64 | Transport errors can record a partial duration (probe time). |
| `runs[].ts` | `Meta.Ts` | string (RFC3339Nano) | UTC. |
| `runs[].truncated` | `Meta.Truncated` | bool | |
| `runs[].binary` | `Meta.Binary` | bool | |
| `runs[].delta_base` | `Meta.DeltaBase` | string | Empty when not `--delta` or no previous run; otherwise the previous artifact id. |

`sha256`, `transport_error`, `delta_base` are written as the empty string `""` when their zero value would otherwise have been dropped by encoding/json — never omitted — so consumers can `decode` straight into typed structs with no `omitempty` decisions to make. (`policy_denied` is a count in `summary`, not a per-run field.)

`json.Marshal` indents are off; the consumer decides formatting.

## Flag behavior

### `--result-format=<human|json>`

- Default `human`. The existing per-host human passport and the fan-out aggregate line are unchanged.
- `json`: stdout renders exactly one envelope, single-host or fan-out alike. `tail3`, `--delta` body diffs, pipe advisories, and the aggregate line are all suppressed on stdout; the envelope is the only stdout write in this mode.
- Stderr is unchanged in either mode (diagnostics only).
- Unknown value (`--result-format=xml`): `exitUsage` + stderr note from the flag parser.

### `--result-out <path>`

- Optional; only meaningful with `--result-format=json`.
- When set in JSON mode: after the envelope has been written to stdout, the same bytes are written to `<path>` with `O_APPEND|O_CREATE|O_WRONLY`, mode `0600`. If `<path>` exists and is a regular file it is appended to (the envelope is then invalid JSON — enforce a fresh path per run by convention); if it is a directory or symlink-to-directory, refuse with `exitUsage`.
- When set in human mode: `exitUsage` + `run: --result-out requires --result-format=json`.
- No `--result-out` in JSON mode: identical to today's behavior except for the envelope itself.

### Unchanged flags

`--body-file`, `--delta`, `--budget`, `--timeout`, `--ctx` — exact same parsing, semantics, and dispatch. `--body-file -` (stdin) keeps the body entirely off argv and out of the envelope (the JSON `command` value is the `body:<sha256hex>[:16]` form, never the body text).

## Error handling

| Failure | Mode | Outcome |
|---|---|---|
| Flag parse (`--result-format=foo`) | both | `exitUsage` from `flag.ContinueOnError`; stderr gets the standard usage line; no envelope |
| `--result-out` without `--result-format=json` | human | `exitUsage` + stderr `run: --result-out requires --result-format=json` |
| `config.Load` fail (missing `~/.sshai/`, bad TOML) | both | `exitUsage` + stderr `run: load config: ...`; no envelope |
| Zero hosts / no `--` separator / bad `--ctx` | both | `exitUsage` + stderr note; no envelope |
| Policy denial per host | JSON | Host is **not** in `runs[]` (no artifact is saved on that path). `summary.policy_denied++`; `summary.hosts` still includes the host, so `summary.hosts == len(runs) + summary.policy_denied`. Process exits `exitPolicy` (97). |
| `Store.Save` failure | both | Single-host: human passport path (existing behavior); fan-out: drop the failing host from `runs[]` and continue (`summary.hosts` matches surviving length; an unenclosed host is preferable to a malformed envelope). |
| `*transport.TransportError` (ssh, scp, timeout) | both | Routing through `handleTransportError` records the run with empty body; envelope entry has `transport_error: <reason>`, `exit: 0`. |
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
8. **`TestRunResultFormatJSONBodyFileBoundary`** — pipe a 200-byte script into `sshai run --body-file - <host>` with `result-format=json`. Assert: `runs[0].command` equals `"body:<sha256hex>[:16]"`; the envelope, treated as a string, contains zero matches of three distinctive substrings taken from the script body; `audit.jsonl` likewise contains zero matches (`metaCommand` and `auditCommandPreview` already encode hash-only — assert the JSON inherits this).
9. **`TestRunResultFormatJSONResultOutToFile`** — `--result-out <file>` with `result-format=json`. Assert: `os.Stat(file).Mode().Perm() == 0o600`; reading the file yields bytes identical to stdout; closing and re-running produces a second envelope appended (call out the convention that callers use a fresh path per invocation to keep the file a single-envelope artifact too — and add a test that `--result-out` on a directory path returns `exitUsage`).
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

- **Field-name drift.** `Meta` and the schema must agree. Mitigation: the schema's per-host keys are generated from `Meta` via a single helper (`metaForJSON(m Meta) map[string]any`) so any rename fails the helper's table-driven test in `internal/artifact/result_test.go`. Field names then stay frozen.
- **Goroutine safety.** Per-host `Meta` records are read-only after `wg.Wait`; identical to today's human fan-out. No new shared mutable state.
- **`--result-out` collisions.** Symlink-to-directory, existing directory, permission mismatch. Mitigation: pre-stat with `os.Lstat`; refuse with `exitUsage` on any non-regular-file path; create with `O_APPEND|O_CREATE|O_WRONLY` and `0o600`; never follow a symlink (`O_NOFOLLOW` if available; falling back to `Lstat` re-check after open).
- **Marshal performance.** `Meta` is O(100 bytes); envelope is O(hosts). Worst-case fan-out (say 200 hosts) is still O(KB); unmarshalling is well under a millisecond per call. Not a concern.
- **Out-of-order `runs[]`.** The pre-decision is `runs[]` is argv order. The fan-out classification loop is preserved; failure to preserve order is caught by an explicit positional assertion in `TestRunResultFormatJSONFanOutMixed`.
- **`--delta` interaction.** The `runs[].delta_base` field is the only envelope surface for delta; the body diff is human-passport territory and is suppressed in JSON. Adapters that want the diff call `sshai diff <delta_base> <runs[i].id>`. Documented in the example.
- **Runlog, audit.jsonl.** Untouched. `Meta.Command` already encodes `body:<sha256hex>[:16]` for body-file runs; the envelope inherits this. `auditCommandPreview` already does the same for the audit log.

## Acceptance

- `go build ./...` clean.
- `go test ./...` green.
- `go test -run ResultFormat -v ./...` green (all 11 tests listed above).
- `sshai help run` shows the new flags.
- `sshai run --result-format=json --body-file - pg-prod-01 <<<'Get-Date'` produces one parseable JSON object; `jq -e '.runs[0].command | startswith("body:")'` returns true; the body text does not appear anywhere on stdout.
- An adapter reading the envelope byte-for-byte via `json.Decoder` into a typed struct succeeds without any text-side fallback.
- Default human output, today, equals default human output after the change (Test #10).
