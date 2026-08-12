# sshai v1 design — context-frugal remote execution for AI agents

**Date:** 2026-08-06
**Issue:** [aimem#636](https://github.com/aprudkin/aimem/issues/636)
**Status:** design approved in brainstorm; implementation plan pending
**Relation to charter:** amends `2026-08-06-sshai-charter.md` (amended in the same commit); where they
conflict, this document governs.

## Problem

An operator drives a fleet of remote machines (Linux/bash, Windows/PowerShell) from a local AI
coding agent (Claude Code, Codex CLI). Typical tasks are 10–60 dependent steps: incident diagnosis,
config migration, rollouts, log analysis. Six failure modes drain the agent's scarcest resource —
context:

1. **Schema tax** — MCP tool schemas ride along in every request (~40k tokens for a 37-tool server).
2. **Output tax** — full command output lands in history; the model needed 3 lines of an 8 KB dump.
3. **Redundancy tax** — step N's output stays in history forever, even after step N+1 makes it stale.
4. **State amnesia** — one-shot `ssh_exec` loses cwd/env/venv between calls; the only "memory" is
   dragging state through context.
5. **Cross-shell quoting hell** — bash vs PowerShell vs cmd escaping burns turns on quoting bugs.
6. **Context loss on compaction** — compaction loses *intent* (what was tried, what failed, why),
   forcing rediscovery.

Existing tools cover transport (dozens of SSH MCP servers) or output offloading
(transport-agnostic context compressors), never both. See "Prior art" below — verified 2026-08-06,
the intersection still does not exist.

## Decisions (brainstorm outcomes, 2026-08-06)

| Question | Decision | Rejected alternatives |
|---|---|---|
| Governing document | This brief; charter amended | Charter-as-written; prompt taken literally |
| Form | CLI invoked via the agent's shell tool | MCP server (pays schema tax — problem 1 by construction); CLI+MCP hybrid now |
| Language | Go | Python (continuity with `ps_ssh.py`, but slow start at hundreds of invocations/session) |
| Artifact location | Local, on the agent's machine (`~/.sshai/`) | Remote-side (litters prod, host must stay reachable, no cross-host diff); threshold hybrid |
| Session persistence | State re-injection over ssh ControlMaster | Live broker-held shells (REPL-driving fragility); ControlMaster only (fails R4) |
| Windows transport | OpenSSH only in v1; transport is an interface | WinRM in v1 (second auth/encoding stack for hosts the fleet doesn't have) |
| v1 extras | R3 deltas + R6 run-log/search (near-free over local store) | Full S2 policy engine (v2; v1 ships a readonly allowlist); benchmark inside v1 (it is milestone v1.1) |
| Repo language | English everywhere (code, comments, docs, help) — the tool is intended to be open-sourced | Russian comments (previous repo rule) |

## Requirements disposition

| Req | Meaning | Disposition |
|---|---|---|
| R1 | Artifact-by-reference: passport instead of output | **v1.** Passport target <200 tokens, asserted in tests |
| R2 | Query-over-artifact | **v1.** Artifacts are local files: the agent's own Grep/Read work directly; `sshai q` adds budgeted passthrough |
| R3 | Delta-only repeat runs | **v1.** `run --delta`, keyed by (host, ctx, command) |
| R4 | Persistent session + shell abstraction | **v1** via state re-injection (cwd, env, venv). sudo context and live REPLs → v2 `session open` |
| R5 | Progressive disclosure | **v1** for free: one line in the agent's instructions + `sshai help` |
| R6 | Run-log outside context | **v1.** SQLite index + `sshai log` search |
| R7 | Claude Code + Codex compatibility | **v1** for free: both invoke shell commands; no per-harness config at all |
| S1 | No credentials in context | **v1.** Host aliases resolve via `ssh_config`; sshai stores no credentials, ever |
| S2 | Per-host policy modes | **v1 minimal:** `readonly` flag + curated read-only allowlist, fail-closed. Full allow/deny regex engine → v2 |
| S3 | Audit log JSONL with redaction | **v1.** Append-only `audit.jsonl` |
| S4 | Alias expansion before policy check | v2 (with the full S2 engine); noted so the v2 design cannot miss it |
| M1–M5 | Measurable success criteria | Milestone v1.1 (benchmark needs a working tool first) |

## Architecture

One static Go binary, `sshai`. **No daemon of its own.** The two things a broker seemed necessary
for are already solved by existing mechanisms:

- *Connection persistence* — ssh ControlMaster/ControlPersist. sshai passes
  `-o ControlMaster=auto -o ControlPath=~/.sshai/cm/%C -o ControlPersist=15m` on every call.
  The ssh client manages the background master process itself; reconnection is transparent; the
  user's `ssh_config` needs no edits.
- *State persistence* (cwd/env, artifacts, run-log) — files and SQLite under `~/.sshai/`, safe for
  concurrent agent sessions (WAL + write-once artifact files via temp+rename).

The transport is an interface (`Exec`, `Put`, `Get`). The v1 implementation shells out to the
system OpenSSH client — the only thing that honors the full `ssh_config` semantics (ProxyJump,
Match, Include, agent, keys). Reimplementing that on `golang.org/x/crypto/ssh` would mean
duplicating SSH configuration, which the charter forbids. WinRM, if ever needed, becomes a second
implementation of the same interface.

### Code layout

```
cmd/sshai/           # entrypoint, subcommand dispatch
internal/cli/        # flags, help text (R5)
internal/transport/  # Transport interface + openssh implementation (exec ssh/scp)
internal/shell/      # bash + pwsh adapters: wrappers, encodings, CLIXML filter
internal/session/    # state capture/re-injection, host-facts cache
internal/artifact/   # store, passport rendering, budget trimmer
internal/delta/      # command keying + local diff
internal/runlog/     # SQLite run-log + audit.jsonl
internal/policy/     # v1: readonly allowlist (fail-closed)
```

## CLI surface (complete for v1)

```
sshai run [flags] <host...> -- <command>     # execute; N hosts = fan-out
sshai run --body-file f.ps1 <host...>        # body from file or stdin (never argv)
      --delta          # R3: print diff vs previous run of same (host, ctx, command)
      --budget N       # output budget in tokens (~bytes/4); default 500
      --timeout N      # seconds; default 60
      --ctx NAME       # named state context; default "default"; SSHAI_CTX env respected
sshai q <id> -- <tool> <args>                # R2: run a local tool (grep/jq/awk/...); artifact path is appended as the tool's final argument
sshai diff <id1> <id2>                       # diff two artifacts (any hosts)
sshai log [--host H] [--since T] [--grep P]  # R6: search the run-log
sshai hosts                                  # known aliases (from ssh_config), detected OS, readonly flag
sshai gc                                     # prune artifacts per retention policy
sshai help [command]                         # full reference on demand (R5)
```

## Passport

The only thing that enters agent context. First line is the status line — the source of truth:

```
a17 host=pg-prod-01 exit=0 lines=8412 bytes=612K time=1.8s
file=~/.sshai/art/a17
tail3:
  ...last 3 lines...
```

- `exit=N` — the body ran and returned N. `transport-error=timeout|auth|dns|channel` — delivery
  failed; the body may not have run at all. Exactly one of the two is present.
- Optional flags appended to the status line: `truncated=1` (stream cap hit), `binary=1`
  (NUL detected; tail suppressed), `delta=a12` (delta mode, diff base).
- **Tiered inlining:** when total output fits the budget (default 500 tokens ≈ 2 KB), the passport
  includes the full body instead of `tail3` — no second call for `echo ok`-sized results. The
  artifact is stored regardless (deltas and audit need it).
- **Self-pipe advisory:** if the command itself ends in `| tail`, `| head`, or `| grep`, the
  passport appends a one-line note that the filter discarded data the artifact would have kept.
  Advisory only, never a block.

sshai's own process exit code mirrors the remote command's exit code, so the agent's harness
surfaces failure naturally. Reserved codes, disambiguated by the status line: 96 usage error,
97 policy denied, 98 transport error. Fan-out returns the worst outcome.

### Fan-out

`sshai run web01 web02 web03 -- df -h` produces one passport per host plus one aggregate line
(`3 hosts: 3 ok` / `2 ok, 1 transport-error`). The `--budget` applies to the total printed output;
per-host artifacts are always complete.

## Sessions and transport

**First contact** with a host probes OS and shell (uname vs DefaultShell/pwsh detection carried
over from `/ps-ssh`), cached in the local DB; overridable per host in config.

**Windows path** carries the paid-for lessons from `ps_ssh.py` (314 lines, ported with its control
cases): UTF-8 BOM on body files, body delivery via scp staging (command-line length limits,
quoting), pwsh 7 with fallback, CLIXML filtering of stderr.

**Re-injection mechanics.** Every run is wrapped: prologue (`cd` to saved cwd + restore changed env
vars) → user body → epilogue (dump cwd/env after a sentinel marker; runs on failure too —
trap/finally). The artifact contains only the body's stdout/stderr; the state block is stripped
before storage. Env is restored as a diff against a baseline captured at first contact — never
wholesale (that would drag `SSH_*`, `TERM`, etc.). Documented non-persisted state: shell functions,
aliases, background jobs, sudo timestamps, open REPLs (psql). Those are v2 (`session open`).

**Contexts.** State is keyed by (host, ctx). Parallel agent sessions working on one host in
different directories separate themselves with `--ctx` or `SSHAI_CTX`; the default stays simple.

**Timeouts.** Default 60 s, `--timeout` to change. On timeout the exec channel is killed and the
passport reports `transport-error=timeout`. Because every run is a fresh exec channel, a hung
command never poisons the session.

## Storage (`~/.sshai/`)

```
art/<id>             # raw artifact bytes, write-once (temp+rename)
db.sqlite            # run-log + artifact index + host facts (WAL mode)
audit.jsonl          # append-only audit (S3)
state/<host>/<ctx>.json
cm/                  # ControlMaster sockets
config.toml          # budgets, retention, timeouts, per-host: os override, readonly
```

Artifact IDs are short and monotonic (`a1`, `a2`, …) via a SQLite sequence. The run-log row stores:
ts, host, ctx, command (or body hash only), exit/transport-error, bytes, lines, sha256,
duration, truncated/binary flags, delta base.

**Retention:** artifacts pruned by age and total size (default 7 days / 1 GB), auto-gc on run plus
explicit `sshai gc`. Run-log rows outlive artifact files: after pruning, queries against a pruned
id answer "artifact pruned", metadata intact.

## Deltas (R3)

Key = (host, ctx, command text with leading/trailing whitespace trimmed and internal runs of
whitespace collapsed; body-file runs key on the body's sha256). `run --delta` executes
normally, stores the full new artifact, then prints either `no change since a12 (3m ago)` (~20
tokens) or a budgeted unified diff against the previous artifact with the same key. History is
never lost to delta mode.

## Security

- **S1:** sshai holds no credentials and takes none as input. Aliases, keys, jumps live in
  `ssh_config`/ssh-agent. Only aliases and passports enter agent context.
- **Bodies never in argv** (process table): `--body-file` or stdin, enforced (no `--body` string
  flag exists).
- **S3:** `audit.jsonl`, append-only: ts, host, ctx, subcommand, body sha256, policy verdict, exit.
  Inline commands retain a short secret-redacted preview; `--body-file`/stdin bodies are hash-only
  because heuristic redaction cannot safely classify arbitrary script text.
- **readonly flag (mini-S2):** a host with `readonly = true` in `config.toml` accepts only
  commands matching a curated global allowlist of read-only patterns (shipped default: `cat`, `ls`,
  `grep`, `df`, `ps`, `journalctl`, `systemctl status`, `Get-*`, …), fail-closed otherwise. The
  full per-host allow/deny regex engine, including S4 alias expansion before the check, is v2.

## Errors and edge cases

- Encodings: pwsh — force UTF-8 (BOM, `$OutputEncoding`), CLIXML filter on stderr; Linux — UTF-8
  as-is. Artifacts store raw bytes.
- Binary output: NUL detection → `binary=1`, tail suppressed in the passport.
- Stream cap (default 64 MB) → `truncated=1`.
- ControlMaster death: `auto` re-establishes on the next call; host-facts cache is not invalidated.
- Body-file staging failure, DNS, auth → `transport-error=...` with the specific reason.

## Testing

- **Unit:** CLIXML filter, passport rendering, budget trimmer, prologue/epilogue generation,
  env-diff logic, delta keying, redaction filter, readonly allowlist. Control fixtures ported from
  `/ps-ssh` (emoji, Cyrillic, BOM) — parity is proven on the already-paid-for cases.
- **Passport size** (<200 tokens for the metadata-only form) is a unit assertion, not an intention.
- **Integration** (build-tagged, not run in CI): bash path against localhost sshd; pwsh path
  against a real Windows fleet host. Before declaring `/ps-ssh` superseded, its existing scenarios
  run through sshai.

## Milestones

- **v1** — everything above. Exit criterion: `/ps-ssh` declared deprecated, its skill rewritten to
  call sshai.
- **v1.1** — benchmark M1–M5: incident scenario, 2 Linux + 1 Windows hosts, 30+ steps, vs baseline
  (raw ssh via the agent's Bash tool). Targets: ≥80 % input-token reduction, p95 tool response
  <500 tokens at outputs up to 10 MB, 0 compactions, success rate ≥ baseline, ~0 quoting-debug turns.
- **v2 candidates** (not commitments): `session open` (live REPL, sudo context), full S2+S4 policy
  engine, WinRM transport, MCP facade, FTS5 index over artifacts, GitHub Actions CI + LICENSE
  choice before the repo goes public.

## Prior art and reusable components (verified 2026-08-06)

Landscape check before writing this spec; searches over GitHub/web via Exa.

**Closest neighbors — none occupies the niche:**

- `naoto256/ssh-mcp` (Rust) — policy-gated SSH MCP for Claude Code *and* Codex; `exec` returns
  metadata-only by default with an op-pipeline (head/tail/grep) and a `trace` ring buffer (depth 5,
  10 MiB). Closest in spirit to R1/R2. Gaps: MCP form (schema tax), resident daemon, trace is
  ephemeral (no durable artifacts → no deltas, no run-log), no Windows/PowerShell handling. Its
  self-pipe advisory (agent piping through `tail` defeats the trace path → server notes it) is
  adopted into the passport design.
- `muchiny/mcp-ssh-bridge` — MCP + CLI dual mode, server-side jq/yq filtering *before* truncation,
  `output_id` pagination, Unix-socket daemon. Gaps: no artifact diffing, no Windows encodings,
  protocol-adapter-centric (docker/k8s/helm).
- `hunchom/claude-code-ssh` — 13 verb-tools (~5k tokens always loaded), pooled connections, sudo
  via stdin. Head/tail truncation only.
- `xiongjiwei/mcp-ssh` — uses the system `ssh` binary and honors `~/.ssh/config`; independent
  validation of our transport decision.
- Generic output-offload layers (`Toolaria`, `ctx-saver` (Go), `bpcontext`, `context-mode`,
  `sift-gateway`, `Few-Word`) — confirm the spill-to-disk + handle + query pattern works, and that
  all of them are transport-agnostic: none knows SSH sessions, fleets, or PowerShell encodings.
  `Few-Word`'s tiered offloading (tiny outputs stay inline) is adopted as passport tiered inlining.

**Reusable Go components:**

- `modernc.org/sqlite` — CGO-free SQLite, keeps the binary static.
- `kevinburke/ssh_config` — parse `ssh_config` for `sshai hosts` listing (execution still goes
  through the ssh binary).
- `sergi/go-diff` or `pmezard/go-difflib` — local unified diffs for R3.
- CLIXML: v1 ports the proven `/ps-ssh` filter. If structured decoding is ever needed:
  `go-psrpcore` (pure-Go PSRP incl. CLIXML serialization — heavyweight) or
  `nocturnaluncl/gowindows/parsing.DecodeCliXmlErr` (small, single-purpose) as references.
- `masterzen/winrm` — the v2 WinRM transport, if that milestone ever lands.

## Charter amendments (applied in the same commit)

1. **Form:** "no daemon, no state between calls" → the tool still runs no daemon of its own, but
   on-disk state (artifacts, run-log, session state) and the ssh-managed ControlMaster background
   process are in scope by design.
2. **Language:** resolved — Go. The follow-ups owed on that decision (CLAUDE.md commands section,
   layout, `/sshai` in `.gitignore`) are applied.
3. **Repo language:** English everywhere; the tool is intended to be open-sourced.
