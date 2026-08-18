# sshai

Remote command execution on Windows and Linux servers for AI agents (Claude Code, Codex CLI),
designed around one constraint: **agent context is more expensive than CPU cycles**.

Raw `ssh host 'powershell -c "…"'` returns everything to the agent — banners, CLIXML wrappers,
blank lines, a hundred-thousand-line `Get-EventLog`. The agent pays a token for every byte and
keeps it until the end of the session. `sshai` owns transport and encodings, returns
budget-bounded output upward, and leaves the full result on disk — by reference, not in context.

The agent gets a compact *passport* instead of raw output:

```
$ sshai run pg-prod-01 -- journalctl -u postgres --since -1h
a17 host=pg-prod-01 exit=0 lines=8412 bytes=612K time=1.8s
file=~/.sshai/art/a17
tail3:
  ...
```

and then queries the artifact locally (`sshai q a17 -- grep -iE 'fatal'`, or its own grep — the
artifact is a plain local file). Repeat runs can return just the diff (`--delta`); a local run-log
survives context compaction.

## Status

v1 implemented: `run` (single host and fan-out, `--delta`, `--ctx` state re-injection), `q`,
`diff`, `log`, `hosts`, `gc`, and `help` all exist and are unit-tested; the Linux path is
integration-verified against a live host, the Windows path against a live host via the manual
parity gate ([evidence](docs/windows-parity.md)). Architecture, CLI surface, and v1 scope:
`docs/superpowers/specs/2026-08-06-sshai-design.md`. Purpose and definition of done:
`docs/superpowers/specs/2026-08-06-sshai-charter.md`. The current server-workflow migration,
fallback boundary, and fresh Windows/Linux acceptance evidence are in
`docs/server-workflow-migration.md`.

## Context economics

The available measurements are a historical baseline for the predecessor `/ps-ssh` workflow,
not a completed before/after benchmark of `sshai`. A 2026-07-29 transcript audit found:

| Measurement | Historical result |
|---|---:|
| Claude `/usage` attribution to `/ps-ssh` | 26% |
| Agent-visible Bash calls per remote invocation | 457 / 113 = **4.04** |
| Longest quoting/`DefaultShell` debug tail | **20** calls |
| Heaviest sampled session | 271 assistant turns, 118 Bash calls, 69 related to `ps-ssh` |
| Same session's total token counters | 40.79M cache-read, 0.24M output |

The 26% and 40.79M figures describe whole sessions and must not be read as token cost caused only
by the helper. They identify the amplification mechanism: **cost grows roughly as agent-visible
round trips × accumulated context**. As an illustrative calculation from the audit, 5k tokens of
output introduced at turn 40 of a 271-turn session can contribute about 1.2M cumulative later
cache-read tokens. This is a model of repeated context reads, not an isolated A/B measurement.

`sshai` reduces that amplification in several layers:

- one `sshai run` contains staging, transport, encoding, execution, and collection behind one
  agent tool call, replacing the historical 4.04-call average and avoiding 10–20-turn quoting
  repair loops;
- tiered output inlines a small result, but a large result returns only metadata plus `tail3`; the
  complete bytes remain in `~/.sshai/art/<id>`;
- the metadata-only passport fixture is unit-asserted below 200 estimated tokens; `run` uses a
  factory 500-token estimate (approximately 2 KB at bytes/4) as its full-body inlining threshold,
  while `q` (per stream) and `diff` trim their output to the same default estimate;
- `sshai q` filters the local artifact, while `diff` and `--delta` return only relevant changes;
  an unchanged delta is represented by the short `no change since ...` line (approximately 20
  tokens by design);
- `log` and artifact IDs keep evidence outside the conversation, so analysis can resume after
  compaction without replaying full remote output;
- fan-out divides the configured inlining threshold across hosts (with a 100-token per-host floor)
  instead of giving every host the full threshold.

Token estimates in the CLI use `ceil(bytes / 4)`, not a model-specific tokenizer.

The controlled v1.1 benchmark completed on 2026-08-13: 36 read-only observations across two Linux
and one Windows host, in fresh raw-SSH and `sshai` Codex sessions. `sshai` reduced agent-visible
marked tool output by **99.86%** (p95 **262,152 → 50** estimated tokens) and actual Codex input
tokens by **44.71%** (**2,896,077 → 1,601,293**). Both branches succeeded on 34/36 observations,
had zero marker retries, and exposed zero compaction events. The p95, compaction, success, and
quoting-debug targets passed; the primary ≥80% input-token target did not, so the decision is
**needs work**, not v1.1 confirmed. See the [full result and boundaries](docs/benchmarks/v1.1-results-2026-08-13.md),
the [design](docs/superpowers/specs/2026-08-06-sshai-design.md), and
[aimem#735](https://github.com/aprudkin/aimem/issues/735).

The follow-up fan-out measurement is frozen in the
[v2.1 protocol](docs/benchmarks/v2.1-protocol.md) and
[analyzer definition](docs/benchmarks/v2.1-analyzer.md). It uses paired no-op controls and does
not promote a measured result until the declared populations, executable provenance, lifecycle
cross-checks, and three complete replicates pass their validity gates. The
[pre-qualification manifest](docs/benchmarks/v2.1-prequalification-manifest.json) freezes the
36/24 call maps, local no-op helper, prompts, runtime/config identity, and executable provenance;
its adjacent `.sha256` file locks the manifest bytes. The v2.1 runner refuses measured branches
while target qualification remains pending, and later branches require immutable rollout and
branch-validation evidence from every earlier branch.

## Usage

```
sshai run [flags] <host...> -- <command>     # execute; N hosts = fan-out
sshai run --body-file f.ps1 <host...>        # body from file or stdin (never argv)
sshai q <id> -- <tool> <args>                # run a local tool over a stored artifact
sshai diff <id1> <id2>                       # diff two artifacts (any hosts)
sshai log [--host H] [--since T] [--grep P]  # search the run-log
sshai hosts                                  # known aliases, detected OS, readonly flag
sshai gc                                     # prune artifacts per retention policy
sshai help [command]                         # this list, or the full reference for one command
```

Add `--result-format=json` to `sshai run` for a versioned machine-readable
result envelope (`schema_version: "v1"`) instead of the human passport — see
`sshai help run` and `docs/agent-usage.md`.

The agent-facing quick-start (what an operator pastes into their agent's instructions):
`docs/agent-usage.md`.

## Installation

Install the current checkout into `~/.local/bin` (or set `SSHAI_INSTALL_DIR` to another existing
PATH directory):

```bash
scripts/install.sh
```

The installer builds a temporary binary, refuses to replace a symlink or non-file, atomically
replaces the exact `sshai` target, and runs `sshai help` as a smoke check.

## Origin

A generalization of the now-archived `ps_ssh.py` helper — one Windows host, one shot. It already
paid for UTF-8 BOM, scp body delivery, pwsh 7 invocation, DefaultShell detection, CLIXML filtering,
head/tail truncation, and a status line as the source of truth. That experience is ported, not
rediscovered; the legacy helper is no longer an active fallback.

What the archived helper lacked and what this project exists for: Linux/bash targets, multiple
hosts per call, persistent session state (cwd/env) between calls, artifact-by-reference output
with local querying, deltas, and a searchable run-log.

## Implementation language

Go. A single static binary with millisecond startup — the agent invokes the tool hundreds of
times per session.

## CI

None yet. The repository is private on GitHub; unit-test CI and a license choice are queued for
the moment it goes public.
