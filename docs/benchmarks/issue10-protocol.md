# Issue 10 local-first protocol

**Status:** Draft, offline preparation only. No model or remote session has run; no
measurement exists. This protocol does not alter the frozen v1.1 or v2.1 artifacts.

## Question and scope

Measure whether autonomous investigation through local `sshai` preserves task quality while
changing the Codex-reported cumulative session input relative to ordinary local filtering. Start
on the current macOS machine without a VM. A later remote series is a separate experiment and
requires a named host, permissions, safety review, and explicit protocol approval; this is not
generic production authorization.

Each fresh session receives one user prompt and completes one turn. It may take arbitrary
autonomous steps, but may not resume or fork a thread, use subagents, or receive a fixed command
script. The paired arms solve the same task:

| Arm | Allowed approach |
|---|---|
| Baseline | Ordinary local shell tools and filtering selected by the model |
| `sshai` | `sshai local --shell bash` plus its normal local artifacts, `q`, `diff`, and `--delta` when useful |

The design measures a tool choice, not forced raw-output dumping. It does not claim that the
captured command output is the exact formatted text visible to the model.

## Tasks and outcome evidence

The fixed local fixtures define three realistic, read-only tasks:

| Task | Required objective result |
|---|---|
| Incident root cause | Pool, error, in-use/max values, endpoint, and log file:line evidence |
| Configuration drift | Host, every changed scalar, before/after values, and file:line evidence |
| Snapshot difference | Changed path, both hashes and sizes, and manifest file:line evidence |

An independent fixture evaluator grades exact facts and generated file:line citations. The strict
output schema has no free-text action. Before interpreting any pilot or measurement, a human must
review task adherence, evidence integrity, and the sandbox receipt. Quality loss, an incomplete
result, a failed integrity review, or invalid data prohibits a positive savings claim.

## Sampling and limits

The proposed pilot is one pair for each task: six sessions total in a predefined order. Pilot data qualifies
the event, rollout, sandbox, account, and quality contracts only; exclude it from measurement.

A draft measurement is six balanced pairs per task: 36 sessions total, with baseline/`sshai` order
alternated within each task and repetition. This count, a 600-second per-session wall timeout, and
any provider budget must receive separate approval before execution. Finite slots and a watchdog
are not dollar or token caps; provider-side limits remain a separate control. Retain every failure
and invalid session. Do not retry, replace, cherry-pick successes, or stop after favorable results.

## Isolation and safety gates

The harness uses the supported named Codex `issue10` permission profile, qualified offline with
exactly the same `-c` TOML overrides passed to `exec` (not `-P`, which is sandbox-only). It sets
`default_permissions="issue10"`, declares the absolute workspace root, starts filesystem access at
`:root="deny"` with `:minimal="read"`, permits the required system and Homebrew runtimes, denies
protected paths, grants only `:workspace_roots={"."="write"}`, and sets `network={enabled=false}`.
The child profile denies the original home, repository, whole study root, auth source, and
evidence while allowing the workspace. One exact-file read exception permits the frozen `sshai`
executable even when installed under a denied parent; it does not allow the executable's directory.
The canary verifies its pinned bytes and `help` output. Workspace-write is rejected because it
permits full-disk reads.

On the current Darwin arm64 Mac, native Codex 0.151.0 passed this no-model qualification: workspace
read/write succeeded; sibling, oracle, evidence, auth, home, and repository sentinels were denied
with `EPERM`; symlink, `..` traversal, nested shell-to-Python, and out-of-workspace writes were
denied; and localhost tool network was denied. The probe used synthetic canaries and did not read
protected contents. A checkout-built `sshai local --shell bash` also read a synthetic fixture and
created its SQLite/artifact store inside the allowed workspace under this profile. A separate
probe verified the exact-file exception for a checkout-built binary outside the workspace under
a denied parent, while all surrounding protected canaries remained denied. These offline
checks do not prove complete confinement, that all secrets are unreadable, or live-Codex parity;
those remain pilot gates.

A paid launch additionally requires an immediate successful canary and an approval file bound to
the exact manifest: phase, model, reasoning effort, slot count, timeout, Codex binary/version,
`sshai` binary, environment, prompts, fixtures, and relevant source hashes. It is currently
**blocked**: parent cloud or managed configuration can add active MCP tools outside the tool
sandbox. `verify_no_managed_mcp` explicitly refuses launch: no supported absence proof exists
for the retained ChatGPT login. An API-key-only alternative was not selected. Authentication
content is never copied into prompts, logs, manifests, or published artifacts.

## Provenance and collection

For every retained slot, save the CLI JSONL, persisted rollout JSONL, final structured answer,
process metadata, sandbox receipt, managed-MCP receipt, and SHA-256 provenance. Bound each
captured stream and artifact parse to 1 MB; a timeout, overflow, interruption, or parse failure
makes the slot immutable failed evidence. Record fixture/prompt/schema bytes, runner and evaluator
hashes, binary paths and hashes, native version information, and the environment shape without
secrets. The runner uses the native binary, not the Node.js wrapper; `sshai` identity is its
bounded `help` output hash because it has no version command. Do not publish raw session logs,
authentication content, or full environment snapshots.

Usage is **Codex-reported**, not exact billing. In Codex `rust-v0.151.0`,
[`event_processor_with_jsonl_output.rs`](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/exec/src/event_processor_with_jsonl_output.rs)
maps `usage_from_last_total` from `ThreadTokenUsage.total`; absent usage notifications produce
zero defaults. The separate
[`exec_events.rs`](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/exec/src/exec_events.rs)
defines the serialized event schema, not its accumulation semantics. `protocol.rs` accumulates
usage in `TokenUsageInfo`. Cached input is a subset of input. The final rollout
`total_token_usage` is a cross-check, not independent provider proof. Whether
`raw_response_completed` offers exact provider records remains unresolved.

Count thread usage once per completed thread: never sum turn records. Report cumulative input,
cached input, non-cached input, captured command-output bytes, and quality evidence. Do not infer
usage from bytes/4; `aggregated_output` is captured output, not a model-visible-output proxy.

## Validity and reporting

Analyze matched pairs by task and publish differences plus dispersion/uncertainty. Report negative,
null, and inconclusive outcomes honestly. Separate local and remote tables and never combine them.
README statistics may be updated only after a completed valid experiment, with a result link,
scope, and caveats; historical results remain unchanged until then.

## Readiness

The current state is draft offline preparation, not ready to launch. A current-Mac offline native
`prepare` against checkout-built `sshai` created a frozen six-slot pilot manifest; it did not run
those slots. Fixture, sandbox, and runner tests plus Go and historical script checks passed. Live Codex parity, provider-usage pilot
compatibility, account availability, and budget are unverified. `gpt-5.6-sol` at high reasoning
effort is the selected pilot candidate, based on the historical benchmark, but does not approve
paid runs or spending. The user chose to retain ChatGPT login. Paid launch remains blocked until
a safe configuration/isolation method is established for that login. A separate network-capable
configuration check without a model request was authorized but not performed: source review and
an independent architecture review found that a network inventory alone cannot resolve the
barrier. No credentials were inspected.

The reviewed implementation sources are
[`permissions_toml.rs`](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/config/src/permissions_toml.rs),
[`exec/src/lib.rs`](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/exec/src/lib.rs)
(permission selection at thread creation and start), and
[`restricted_read_only_platform_defaults.sbpl`](https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/sandboxing/src/restricted_read_only_platform_defaults.sbpl). They support this named-profile configuration; they
do not establish a complete-security proof.
