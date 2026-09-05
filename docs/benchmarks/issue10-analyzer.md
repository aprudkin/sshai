# Issue 10 analyzer and run guide

**Status:** Draft interface and validation contract. It authorizes no paid, model, remote, or
network run. Read this with [Issue 10 local-first protocol](issue10-protocol.md).

## Runner interface

`scripts/benchmark_issue10.py` provides four subcommands:

```text
prepare NEWROOT --codex NATIVE --sshai BINARY --model MODEL \
  --reasoning-effort EFFORT --repetitions N --timeout-seconds N \
  --phase pilot|measurement

run-one ROOT --approval FILE --auth-file PATH --slot N --allow-paid-run

record-review ROOT --slot N --reviewer NAME --adherence-passed \
  --integrity-passed --quality-passed --tool-surface-passed [--note TEXT]

analyze ROOT [--out NEW-REPORT]
```

`prepare` and `analyze` are offline. `prepare` creates a private root, frozen inputs, manifest,
provenance hashes, and an unapproved approval template; it runs only native Codex `--version` and
bounded `sshai help` probes. `analyze` always writes JSON to stdout and writes an optional new
report file with `--out`; it never launches a session. `record-review` binds the required manual
review to a slot's provenance. A `run-one` slot is ordered, one-shot, and immutable after any
attempt or failure.

Do not invoke `run-one` yet. It correctly fails closed because the mandatory
`verify_no_managed_mcp` explicitly refuses launch for the retained ChatGPT login: no supported
system/managed/cloud absence proof exists yet. There is no command-line bypass. The named `issue10`
profile through identical `-c` overrides remains mandatory for execution; do not use unrestricted
workspace-write or sandbox-only `-P` execution syntax.

## Required manifest and artifacts

A manifest freezes phase, local/remote scope, task IDs, pair schedule, model and reasoning effort,
timeout, runner/evaluator/prompt/fixture/schema hashes, native Codex path/hash/version,
`sshai` path/hash, and an empty unique artifact root. `sshai` has no version command.
It records that wall timeout and finite slots are not financial caps.

Each session has an immutable directory with bounded captured stdout/stderr, CLI JSONL, persisted
rollout JSONL, final answer, evaluator result, manual-review record, process result, managed-MCP
preflight receipt, and sandbox receipt. Each captured stream and retained parsed artifact has a
1 MB ceiling; timeout, overflow, interruption, malformed JSONL, or a missing artifact is failed
immutable evidence. The sandbox receipt records native Codex 0.151.0 on Darwin arm64,
profile/override bytes, binary/probe hashes, exact-file executable allowance, qualification
environment hash, and passed no-model cases. Validate evidence hashes
before analysis. Keep raw sessions private; publish only reviewed, sanitized reports. Never capture
auth contents, personal-home snapshots, or SSH material. The expected answer stays outside the
model workspace and is available only to the evaluator.

## Event and usage validation

Accept only one fresh thread with one started and one completed turn and a terminal usage record.
Reject malformed, duplicate, unfinished, truncated, timed-out, cross-thread, or schema-drift
records. Preserve their evidence and label the slot invalid; do not rerun it.

For a completed thread, report these Codex-reported counters:

| Field | Rule |
|---|---|
| Cumulative input | Terminal thread total; count once, never sum turns |
| Cached input | Required non-negative integer, no greater than cumulative input; missing is invalid |
| Non-cached input | Cumulative input minus cached input |
| Captured output | UTF-8 byte count of completed command `aggregated_output` values |

The rollout final `total_token_usage` cross-checks consistency but does not prove provider billing.
`raw_response_completed` availability and its billing meaning remain a pilot qualification item.
Never turn captured bytes into token usage and never describe `aggregated_output` as exact
model-visible formatted output.

## Quality and integrity gates

The evaluator must prove all required objective fields and exact file:line citations for the task.
A separate required human review checks prompt adherence, evidence integrity, and the sandbox
receipt. The schema admits no optional action. A pair is interpretable only when both arms are
complete, valid, and quality-passing with completed manual reviews. Failure in either arm remains
in the report and prevents a positive comparative conclusion for that pair.

The analyzer must also reject changed fixtures/prompt/schema, unbalanced or reordered pairs,
reused homes/threads, missing rollout evidence, failed hash checks, unqualified sandbox receipts,
or any unauthorized remote/network activity. Qualification must show workspace read/write and
denial of synthetic sibling/oracle/evidence/auth/home/repository canaries, symlink and `..`
traversal, nested shell-to-Python access, out-of-workspace writes, and localhost tool network.
Treat this as a compatibility canary, not a security proof. Run standard-library offline tests for
the harness, fixtures, evaluator, parser, pairing, and fail-closed cases before a pilot and before
measurement.

## Results contract

For each arm and task publish all retained slots: validity, failure reason, elapsed time, Codex
input/cached/non-cached totals, captured-output bytes, evaluator result, and review status. For
valid pairs, publish signed per-pair baseline-minus-`sshai` differences and a dispersion/uncertainty
summary. Do not pool tasks into a positive headline without task-level evidence.

The report decision is `descriptive reduction`, `measured increase`, `no clear difference`,
`quality regression`, `inconclusive`, or `invalid`. A positive claim is permitted only for a
complete valid **measurement** with `descriptive reduction`; a pilot may report only descriptive
reduction/increase evidence and makes no positive or statistical claim. Dispersion is descriptive,
not a significance test. Never discard failures or select only successful pairs.

## Safe offline work and launch barrier

You may prepare and inspect a root without a model request or network-capable configuration check:

```shell
python3 scripts/test_benchmark_issue10_fixtures.py
python3 scripts/test_benchmark_issue10_sandbox.py
python3 scripts/test_benchmark_issue10.py
python3 scripts/benchmark_issue10.py prepare NEWROOT --codex NATIVE \
  --sshai BINARY --model gpt-5.6-sol --reasoning-effort high \
  --repetitions 1 --timeout-seconds 600 --phase pilot
python3 scripts/benchmark_issue10.py analyze NEWROOT
```

Replace `NEWROOT`, `NATIVE`, and `BINARY` with safe local paths; the root must be new and outside
the repository/context tree. Do not prepare a paid approval or run a slot until the managed-MCP
barrier is resolved. The separately authorized network-capable configuration check is limited to
that preflight and must not make a model request. It was not performed: a network inventory cannot
freeze cloud configuration for a subsequent ChatGPT-authenticated launch.

Local results stay local. A remote series requires its own manifest, qualification, host and
permission record, approval, and report section. It cannot be used to generalize from the current
Mac. README numeric claims wait for a completed valid series and must link the result and state
scope and caveats.
