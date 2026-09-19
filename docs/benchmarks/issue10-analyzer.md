# Issue 10 analyzer and run guide

**Status:** Separate v3 offline coordination work plus the historical local-only runner reference.
This guide authorizes no model, remote, or network run. Read it with the authoritative
[Issue 10 study protocol](issue10-protocol.md). Offline planning/import/analysis does not implement
live three-series capture or qualify a launch. Do not turn the historical runner into the new study
by merely changing CLI arguments.

## Target design and required changes

The selected design uses separate local/macOS, Linux/SSH, and Windows/SSH series on fixed non-secret
data, with six diagnostic tasks per series. Each task ends in a diagnosis, evidence, and proposed
remedy, not an executed repair. Baseline may filter normally; the sshai arm routes target diagnostic
commands through sshai but may read saved artifacts with ordinary local tools. All session activity
and branch guidance count toward usage.

The ceiling is 120 Codex sessions: 12 technical pilot sessions and 108 measured sessions
(6 tasks × 3 pairs × 2 arms × 3 series), using only the existing ChatGPT subscription quota.
The candidate remains `gpt-5.6-sol` / `high`, subject to availability verification. Retain the proposed
600-second timeout as a recommended default pending protocol freeze. Randomize and near-balance branch order before measurement;
do not replace failed runs. A quota pause does not authorize paid API fallback or model substitution.

Implementation work must address these gaps before a launch can be approved:

| Current implementation | Target requirement |
|---|---|
| Three local fixtures and deterministic pair ordering | Six tasks per series, platform-native cases, frozen randomized/near-balanced order |
| Local-only execution and a network-denying profile | Separately qualified local, Linux/SSH, and Windows/SSH paths with bounded authorized access |
| Strict fact/citation answer schema | Diagnosis, evidence, and recommendation with a frozen output schema and rubric |
| Boolean manual review and both-arms-pass quality gate | Automated fact checks plus user grading of anonymized answers; comparative quality reporting |
| Mandatory proof of no managed MCP, currently always refusing launch | Supported restrictions, observable tool-surface checks, and auditable actual calls; stop if access/calls cannot be qualified |
| Existing fixed command validation | Explicit distinction between routed target diagnostics, permitted setup/artifact reads, and actual violations |
| Existing report decisions and descriptive dispersion | Separate series reports, all quality outcomes, frozen aggregation and uncertainty method |

No code barrier has been removed. Instructions or an inventory alone are not technical enforcement.
A boundary violation must remain visible and cannot be called a clean comparison; an allowed
artifact read is not a violation. Qualify access and actual-call auditability during the pilot.

### Target analysis contract, not current analyzer behavior

- Separate task correctness, evidence/recommendation scores, execution outcome, instrumentation
  validity, and tool-boundary compliance. Preserve every attempted slot and its reasons; do not
  collapse a wrong answer into a parser failure or filter the report to successes.
- For interpretable pairs, show absolute baseline-minus-sshai input differences and relative changes
  with explicit denominators. Show valid usage for failed tasks separately where available; a
  cheaper failed task is not successful savings. Missing usage is unavailable, not zero.
- Report per-task and per-series outcomes, never a pooled local/Linux/Windows savings headline.
  Quality-failure rates use all applicable attempted outcomes, with missing assessments explicit.
- “Without observed degradation” requires no more failed tasks and no worse evidence or recommendation
  scores for sshai in each series under the frozen rubric. Also expose individual task regressions.
  This describes the benchmark, not statistical equivalence. It does not require perfect baseline
  success. Success requires a correct diagnosis and evidence/recommendation scores of 2/2. Average
  each 0–2 scale over three repetitions per task, then equally over six tasks: these are confirmed rules.
- Confirmed: timeout without a final answer is failure with scores 0/0; collector loss or absent
  human grading is unknown quality. Unknown quality for any planned result blocks the series claim
  of no observed degradation. Retain partial scores and task-level regressions.
- Recommended primary percentage: `100 * (sum(baseline) - sum(sshai)) / sum(baseline)` with absolute,
  pair-level, task-level, and denominator reporting. Zero baseline makes the percentage undefined.
  Keep full trustworthy usage from wrong answers, but do not call cheap failures equivalent savings.
- Recommended interval: 10,000 stratified paired bootstrap resamples, seed 1010, percentile 95%
  endpoints. Resample three whole pairs within each fixed task and recompute the ratio of totals.
  Pin the generator/percentile convention later; a zero-denominator resample makes the interval
  unavailable rather than silently dropped. Three repeats provide limited fixed-set variability,
  not broad task-population claims or equivalence. Current dispersion is not this interval method.
- Do not replace incomplete pairs or publish an incomplete series as a completed README measurement.
  Compaction is not automatic exclusion, but cumulative usage must be verified across it. These
  defaults require qualification and freeze before measurement; they are not analyzer behavior.
- Collect cached and output tokens, tool calls, retrievals, artifact reads, retries, elapsed time,
  failures, and observable compactions where reliable. Verify each metric's capture coverage;
  unsupported model-visible byte counts or provider billing remain unavailable.
- Pilot results qualify the procedure only. New README numbers require completed validated
  measurement, reviewed sanitized evidence, conditions, quality, uncertainty, and a report link.

### Paper preparation status

The protocol now preserves confirmed interview answers separately from recommended defaults.
Documentation approval is not protocol freeze or launch permission. Its recommended 18-case matrix
uses healthy post-restart state, read-only dependency, active port override, and missing deployment
template in each series, plus native startup-path failures and platform-specific resource limits
(macOS descriptors, Linux inodes, Windows commit). It also proposes byte budgets, causal keys,
source-fixture citations, and diagnosis/evidence/alternative/recommendation answer fields.
Tasks 1 and 2 are the recommended pilot cases in each series.

The protocol now includes a common prompt and case-text matrix covering M01–M06, L01–L06,
and W01–W06, plus semantic keys specifying required evidence, alternatives, targeted recommendations,
and material errors. Recommended answers use four Markdown sections rather than mandatory JSON;
the evaluator's storage schema remains separate and unspecified. The prompts distinguish fixed
snapshots from live host state and explicitly prohibit repairs. Environment, directory, and time-window
placeholders must be expanded before freeze. Draft common and branch-specific instructions now define
routed diagnostics, ordinary local setup, and saved-output/artifact reads; they still need approved
concrete targets, shells, paths, and practical access/audit qualification.

Calibration tables and full-form paper answers illustrate independent evidence/recommendation
scores, including correct diagnoses with incomplete or irrelevant evidence, unsafe recommendations
despite good evidence, and justified non-intervention. Missing-answer/capture cases remain distinct.
Symbolic source spans are placeholders, not valid line citations or collected results. Binding them
to actual inclusive source lines remains preparation work.

Per-file designs now cover all 18 cases with proposed synthetic UTC windows, identities, source
relationships, native-export requirements, and distractor constraints. A complete healthy-case design
specifies run boundaries, repeated checks, thresholds, and readings. These are descriptions, not
created fixtures, and they do not reproduce incidents on live hosts. The proposed text representation
is UTF-8/LF with one-based physical source lines; exact bytes and hashes remain unset.

The branch drafts allow ordinary local notes and processing of already captured outputs in both arms.
New source-fixture access must use sshai in its arm, including local filesystem reads and reads hidden
inside q; baseline uses ordinary execution and may filter freely. The draft forbids raw-SSH fallback
in the sshai arm, cross-case access, tool installation, fixture transfers, and configuration changes.
Existing approved transport staging is distinguished from arbitrary remote writes. Instructions are
not technical confinement; actual-call auditing and practical restrictions still require qualification.

Automated checks should validate references and objective facts; human grading assesses causal
sufficiency. Recommended edge rules retain manually assessable malformed answers, separate a captured
final answer from a later process hang, and keep unresolved grading disputes unknown. Key corrections
must be recorded and applied to every affected answer in both arms. None is implemented here.

The primary specification is the [protocol](issue10-protocol.md); do not infer fixture existence or
platform qualification from its paper cases. The separate fixture-preparation path below now binds
synthetic bytes and source references. Qualification of those designs and calibration grades, expanded
and frozen prompts/branch instructions, evaluator integration, hosts/access, capture qualification,
runner implementation, and phase-specific approvals remain open. Recommended
90-day private-record retention does not authorize automatic deletion. Historical protocols,
analyzers, and results stay frozen.

## Separate 18-case fixture preparation

The new offline modules are separate from the historical three-task fixtures and runner:

- `scripts/benchmark_issue10_v3_cases.py` defines deterministic synthetic input text, neutral task
  prompts, semantic keys, and original-source line references for M01–M06, L01–L06, and W01–W06.
- `scripts/prepare_issue10_v3_fixtures.py NEW_DIRECTORY` materializes a new bundle, refuses an
  existing destination, validates source references, and rereads written bytes. The parent directory
  must exist. It performs no host inspection, SSH, model request, deployment, or runner integration.
- `python3 scripts/test_issue10_v3_fixtures.py` exercises determinism, source references, invalid
  citations and paths, disk hashes, existing-destination refusal, and calibration metadata offline.
  It also checks deployment inventories against actual tree bytes, authentication chronology,
  healthy thresholds, launchd plist parsing, and resource-accounting consistency.

These are fixture-development commands, not study-slot execution. Use a new ignored local directory
for generated bundles; do not commit generated output or mistake it for an approved release payload.
The reproducible source modules, not a copied bundle, define this draft population. `v3` in these
module names distinguishes the new fixture population; it is not a frozen protocol version.

Bundle layout:

| Path | Role |
|---|---|
| `inputs/<case-id>/` | Only the synthetic source files for that case |
| `prompts/<case-id>.md` | Common task and symptom, with deployment-root substitution still required |
| `evaluator/<case-id>/key.json` | Diagnosis, required facts and optional supporting facts with exact source ranges/quotes, alternatives, remedy, verification, material errors |
| `evaluator/<case-id>/calibration.json` and `answers/` | Full-form authored anchors and candidate scores, explicitly not human-qualified results |
| `evaluator/outcome-calibration.json` | Timeout, collector loss, unassessed/disputed, malformed, and post-answer-hang rules |
| `manifest.json` | Case sizes, source byte/line counts and hashes, plus hashes of prompts and evaluator material |

Never expose the whole bundle to an agent: layout separation is not access isolation. A future
controller must expose only one case's inputs and its rendered prompt, keep evaluator material
inaccessible, and add the frozen branch instructions. The fixture preparer does not implement that
controller or the comparison protocol. Exact manifest hashes describe generated draft bytes, not a
freeze decision. Budget deviations are explicit instead of padded away.

Calibration anchors cover complete answers, incomplete evidence, absent evidence, incomplete
recommendations, and absent recommendations for every case, plus an incorrect historical-failure
answer for each healthy case and an unsafe deletion recommendation for the inode case. Candidate grades describe intended
anchors, not automatic semantic grading; human qualification must check each causal chain and grade.
Source-range/quote validation proves reference integrity, not that a quotation supports a diagnosis.
Native observations remain synthetic normalized exports; neither these tests nor a Linux/macOS build
qualifies Windows formats. No end-to-end capture or new comparative-quality analyzer is implemented.

### Offline draft verification

The first prepared bundle contains 18 cases, 1,195 source files, 3,596,409 source bytes, and 94
full-form calibration answers. These counts concern synthetic inputs and authored examples, not
model sessions. The local generated bundle is under ignored `tmp/issue10-v3-fixtures/`; it is not
committed or an approved deployment. Regenerate only into a new directory, not over this bundle.

| Series | 01 bytes | 02 bytes | 03 bytes | 04 bytes | 05 bytes | 06 bytes |
|---|---:|---:|---:|---:|---:|---:|
| M | 2,480 | 877,670 | 24,013 | 214,494 | 32,242 | 56,530 |
| L | 2,488 | 882,880 | 24,485 | 214,492 | 32,174 | 37,869 |
| W | 2,480 | 880,276 | 24,250 | 214,494 | 43,725 | 29,367 |

M05, M06, L05, L06, and W06 are below their proposed byte budgets. The user subsequently accepted
these smaller coherent cases without padding. The original targets and departures remain visible;
this is not a failed-run exclusion or a silent budget rewrite. Incident cases retain substantial logs.

All 11 offline fixture tests passed, along with the old three-task fixture test script and diff
whitespace checks. Bundle preparation reread and matched written bytes. The new semantic checks
first caught impossible Windows selected-process memory totals and traffic preceding same-connection
authentication; corrected records pass the retained regression tests. Absence citations now cover
the complete relevant inventory; integrity-only metadata was removed from required diagnostic facts.
These checks establish deterministic bytes, citation integrity, and the tested consistency properties,
not comprehensive domain correctness, human grading qualification, OS parity, or launch readiness.

No model experiment, remote command, host deployment, Go runtime change, or runner integration was
performed. The full Go test/vet/build suite was not run for this isolated Python fixture work.

### Corrected draft revision 2

The current generator produces `issue10-synthetic-v3-draft-2`. Its separately prepared bundle is
`tmp/issue10-v3-fixtures-r2/`: 18 cases, 1,195 source files, 3,596,515 source bytes, and 94 calibration
answers. The previous `tmp/issue10-v3-fixtures/` bundle remains unchanged; its table and initial
verification above describe revision 1. Do not use that old bundle as the current generator output.

Only source sizes for M05 (32,118), M06 (56,649), W05 (43,630), and W06 (29,573) changed. The same
five cases remain below proposed budgets. Revision 2 corrects the bounded qualification findings:

- M05 removes the unsupported `service_exit status=78` record without guessing a modern launchd
  replacement status. Its configuration citation now includes all supplied ProgramArguments.
- W05 retains a single service-correlated Event 7000 failure and obtains executable path/arguments
  from separate configuration. A quoted BinaryPathName identifies their normalized source; invented
  start-request and stopped-state Event 7000 rows were removed. Configuration citations include arguments.
- M06 distinguishes process descriptors from system open-file-table entries in both input units and key.
- W06 labels allocated pagefile space explicitly, not current usage. It no longer asserts a maximum
  size or explains the commit-limit difference as accounting overhead. Usable RAM, backing-volume
  capacity, and growth outcome remain unobserved; the commit-limit reading is independent evidence.
- L06 moves file-count context into optional `supporting_facts`. A complete diagnostic answer need
  not cite file counts after establishing the error, target filesystem, available bytes, and zero
  inodes. All supporting citations are still validated. Calibration answers use required facts;
  safety and verification requirements for recommendations remain unchanged.

Source basis: [Apple's published launchd source](https://github.com/apple-oss-distributions/launchd/blob/launchd-442.26.2/src/core.c),
[sysexits definitions](https://github.com/apple-oss-distributions/Libc/blob/main/include/sysexits.h),
[Microsoft Event 7000 example](https://learn.microsoft.com/en-us/troubleshoot/sql/database-engine/startup-shutdown/event-id-7000-fail-start),
and [pagefile field definitions](https://learn.microsoft.com/en-us/windows/win32/cimwin32prov/win32-pagefileusage).
The old Apple source does not establish a modern launchd log schema. These changes remove unsupported
assertions; they do not qualify exact native formatting or all OS versions.

All 13 offline tests pass, including native-attribution/argument-citation and resource-semantic/optional
fact regressions; the old fixture test also passes. Preparation and a separate comparison verified
revision 2 against the deterministic source. Every file in the old bundle was hashed before and after
preparation and remained unchanged; its manifest-listed hashes were checked as well. No human grades,
runner integration, model experiment, SSH, commit, or push occurred. Both revisions remain unfrozen,
and native-format and human-calibration qualification flags remain false.

### Acceptance of six calibration anchors

After revision 2, the user explicitly accepted the proposed grades for examples A–F without an
independent scoring exercise. The [protocol](issue10-protocol.md) records the exact case/variant and
grades. This is acceptance of six proposed anchors, not human qualification of all 94 answers or
assessment of experimental results. Generated candidate scores and false human-qualification flags
remain unchanged. Do not treat this decision as protocol freeze, approval of size departures,
runner implementation authorization, or launch permission. The subsequent user decision separately
accepted the five smaller cases without padding and authorized starting runner implementation;
experimental launches remain outside that authorization.

## New v3 offline coordinator

`scripts/benchmark_issue10_v3.py` is an independent offline coordination path, not a modification of
`scripts/benchmark_issue10.py`. It reuses the historical module's private atomic-file and hashing
helpers, not its strict-success quality gate or command-routing heuristic. It never invokes Codex,
sshai, SSH, or credential/configuration probes during preparation or import. The separate
`collect_slot` development-library API described below can execute an explicitly supplied process;
there is still no experimental launch CLI.

```sh
python3 scripts/benchmark_issue10_v3.py prepare NEW_DIRECTORY --phase pilot --seed 1010
python3 scripts/benchmark_issue10_v3.py prepare ANOTHER_NEW_DIRECTORY --phase measurement --seed 1010
python3 scripts/benchmark_issue10_v3.py import-result ROOT --slot 1 --file OFFLINE_RESULT.json
python3 scripts/benchmark_issue10_v3.py record-review ROOT --slot 1 --file HUMAN_REVIEW.json
python3 scripts/benchmark_issue10_v3.py analyze ROOT
```

`prepare` creates a private new directory, refuses overwrite, hashes source modules and both study
documents, copies the deterministic fixture bundle, and snapshots planned prompts with the actual
common/branch instruction blocks from the protocol. `{fixture_root}` and deployment-specific access
values remain unresolved: these are planned prompts, not executable approval. The manifest pins
exact schedule order, a seed, model/reasoning candidates, 600-second timeout, and false qualification
flags. Measurement has 108 slots; pilot has 12. Pair members remain adjacent. Each measurement task
has a 2:1 arm-first split and each series has nine pairs starting with each arm; pilot balances one
pair each way per series. Preparing multiple offline plans does not authorize multiple experiments.

`import-result` saves a new slot record and refuses overwrites, unscheduled slots, and reused
session IDs. The input is an object with these fields:

```json
{
  "session_id": "synthetic-example-session",
  "execution": "completed",
  "answer_state": "captured",
  "final_answer": "Example only: not an experimental answer",
  "usage": {"input_tokens": 100, "cached_input_tokens": 20, "output_tokens": 10},
  "usage_complete": true,
  "instrumentation": "valid",
  "boundary": "compliant"
}
```

Accepted execution states are `completed`, `timeout`, and `failed`; answer states are `captured`,
`absent`, and `lost`. Only captured answers contain nonempty final text; others have `final_answer:null`.
Usage may be null when incomplete. Instrumentation is `valid`, `invalid`, or `unknown`; boundary is
`compliant`, `violation`, or `unknown`. Counters are nonnegative integers, not booleans, and cached
input cannot exceed total input. These are declared fields of an offline record: the coordinator
cannot certify them without the future qualified capture/audit adapter. It must not infer them from
process success or the presence of a final answer.

`record-review` requires a captured answer and an object such as:

```json
{
  "status": "assessed",
  "diagnosis_correct": true,
  "evidence": 2,
  "recommendation": 2,
  "reviewer": "example-reviewer",
  "reason": "Example only; replace with actual assessment rationale"
}
```

Use `disputed` to retain an unresolved assessment as unknown quality. Later assessment/adjudication
is another append-only numbered revision, linked to the previous revision and the exact slot record;
initial scores are not overwritten. For capture-backed records each review also binds the exact
capture digest, so unchanged projected scores cannot hide changed raw evidence. This command records supplied assessments, not an automatic
free-text diagnosis grader. Do not import the six accepted synthetic anchors as real session grades.

`analyze` verifies the pinned plan, prepared content, record hashes, and review bindings before
computing separate M/L/W summaries through `benchmark_issue10_v3_analysis.py`. Missing scheduled
slots stay visible. Timeout without a final answer is failure with 0/0; lost answers and unassessed
or disputed answers remain unknown. Captured answers can be assessed despite a later process hang.
Quality, usage, instrumentation, and tool-boundary outcomes remain separate. Full usage of wrong
answers stays visible rather than being filtered away. In this offline report, a pair marked
`qualified` means complete declared usage plus declared valid instrumentation/compliant boundary;
it does not mean that the future capture adapter or experiment has actually been qualified.

For complete measurement series, calculate the ratio of input-token totals, equal task/repetition
quality means, failure counts, task regressions, and a within-task paired bootstrap with 10,000
resamples, `random.Random(1010)`, and percentile interpolation at `(n-1)*p`. A zero denominator is
undefined, not zero savings; a zero-denominator bootstrap resample makes the interval unavailable.
Incomplete and pilot populations do not receive completed-measurement headline percentages.
Reports always have `experimental_claim_eligible:false`: passing a descriptive calculation on
operator-supplied data does not prove an experimentally valid comparison.

`run-one ROOT` unconditionally refuses before any process or network activity. There is no enable
flag. Remaining work includes a qualified event/usage capture adapter, bounded target access,
actual-call auditing including saved-output reads and compaction, rendered environment instructions,
binary/model pins, protocol freeze, and phase-specific authorization. Offline shuffled review
presentation is implemented below; live human assessment remains outstanding. The old managed-MCP
barrier also remains unchanged. Review revisions preserve evidence, but live event collection and automated
fact checking of experimental answers are not implemented by this offline coordinator.

Offline development tests:

```sh
python3 scripts/test_benchmark_issue10_v3.py
python3 scripts/test_issue10_v3_analysis.py
python3 scripts/test_issue10_v3_fixtures.py
python3 scripts/test_benchmark_issue10_fixtures.py
```

### Private randomized human-review packets

The offline `benchmark_issue10_v3_review.py` module prepares human-review working data without
launching a process or assigning scores. Use a freshly prepared plan: coordinator source and document
changes invalidate existing source pins; do not rewrite old plans to bypass that check.

Keep both the study root and review output private and outside the repository. `OUTPUT` must be a
new directory separate from `ROOT`, with neither directory nested in the other. Export includes all
currently imported answers whose projected `answer_state` is `captured`, regardless of task quality,
usage, execution outcome, or prior assessment. It is a snapshot, not a continuously updated packet.

```sh
python3 scripts/benchmark_issue10_v3.py export-review-packet ROOT OUTPUT
```

The command returns `packet_id`, `answer_count`, and `excluded_slot_count`. Reviewers use
`OUTPUT/packet.json` for the shuffled answer order and opaque answer/task IDs. The directory contains
exact UTF-8 final-answer text, neutral prepared task prompts, source fixtures, semantic keys, a 0–2
rubric, and a privacy/status notice. It excludes branch instructions and coordinator slot, arm,
session, usage, and prior-grade metadata. Answers are not redacted or rewritten: their wording,
artifact references, and task material can reveal context or the arm. This hides coordinator labels,
not all identifying content, and does not guarantee blinding or sanitize material for publication.

IDs and ordering use `SystemRandom`; the owner mapping, rather than a public seed, preserves the
assignment. Only the coordinator owner retains `ROOT/review-packets/PACKET_ID.json`, which binds the
plan, record envelopes, capture digests where present, answers, and every reviewer file. Its slot
inventory records an export or exclusion reason for every scheduled slot, including unattempted,
reserved-without-result, absent, and lost answers. Collector-retained bytes with unknown finality
remain projected as lost and are not exported as gradable answers. An empty export is permitted and
is not evidence that any answer was assessed.

Files are private (0600) under private directories (0700); existing destinations and symlink paths
are rejected. Individual packet files and the owner map are each limited to 4 MiB. Oversize content
is rejected before publication. Cross-directory publication is not atomic: a `.pending.json` owner
receipt precedes output, `packet.json` is written after reviewer content, and a private
`.complete.json` receipt is last. Success requires completion of the export, not merely the presence
of `packet.json`. On failure, preserve partial files and the pending receipt for inspection; the
resolver refuses an incomplete publication. Do not delete existing evidence or interpret a failed
export as a completed assessment. Use a new output path for a later export after addressing its cause.

Keep the reviewer packet unchanged and save the actual human assessment separately using the
`record-review` schema above. After assessment, the owner resolves the opaque answer ID:

```sh
python3 scripts/benchmark_issue10_v3.py resolve-review-answer ROOT \
  --packet PACKET_ID --answer ANSWER_ID
python3 scripts/benchmark_issue10_v3.py record-review ROOT --slot RESOLVED_SLOT \
  --file HUMAN_REVIEW.json
```

Replace `RESOLVED_SLOT` with the integer printed by the resolver. It verifies the completion receipt,
packet inventory/permissions/hashes, pinned plan, replayed records, and the selected answer's original
record binding before returning the slot. It uses the stored output location; moving or editing the
packet prevents resolution. Do not expose the resolved slot or owner mapping to the grader before
assessment. The two commands are separate: resolution does not record a grade, and `record-review`
retains its existing record/capture-bound append-only revisions, not a new packet-bound review schema.
These are consistency checks, not WORM storage, proof of human authorship, or protection against an
owner rewriting all files and hashes.

Declared offline captures remain declared evidence. Export/resolution does not qualify finality,
usage, access, or experimental claims; `run-one` stays disabled. Automatic fact/citation checking of
experimental answers and actual human grading are still separate unfinished work. Verify this path
with synthetic data only:

```sh
python3 -W error scripts/test_issue10_v3_review.py
```

### Offline coordinator verification

The new coordinator's 9 tests, analysis module's 11 tests, and fixture module's 13 tests pass.
The historical runner and historical fixture offline tests also pass, using synthetic processes
and records rather than a real Codex session. CLI smoke verification prepared a temporary 12-slot
pilot plan, imported one synthetic timeout record, retained the other 11 slots as unattempted,
and produced a report with experimental claims disabled. Expected rejection tests cover launch,
overwrite, tampering, bad fields/types, and record/review binding; review revisions retain originals.

These checks do not qualify live event parsing, network access, model availability, or host behavior.
Full Go test/vet/build checks were not run for this isolated Python coordination work. No experiment
was launched, and no generated test plan or synthetic process receipt is a measured study result.

## Offline v3 event/usage adapter

`scripts/benchmark_issue10_v3_capture.py` analyzes supplied CLI JSONL, persisted rollout JSONL,
and a process receipt without launching any process or probing configuration. It is an offline
parser stage, not a live collector or experimentally qualified capture path.

```sh
python3 scripts/benchmark_issue10_v3_capture.py --events SYNTHETIC_EVENTS.jsonl \
  --rollout SYNTHETIC_ROLLOUT.jsonl --process SYNTHETIC_PROCESS.json \
  --answer SYNTHETIC_FINAL_ANSWER.txt
python3 scripts/test_issue10_v3_capture.py
```

Inputs must be bounded regular files; the CLI rejects symlinks and inputs exceeding 1 MiB.
JSONL records have a 256 KiB line limit and a 20,000-record limit. Duplicate JSON keys,
invalid UTF-8, malformed or unterminated JSONL, incomplete lifecycles, mismatched session IDs,
and inconsistent usage remain explicit findings rather than silently repaired evidence.
The process receipt includes `timed_out` and `exit_code`, with optional `capture_overflow`,
`interrupted`, and `error`/`start_error`. These are supplied observations, not collector receipts
whose origin the adapter independently verifies.

The pure `capture_bytes`/`capture` APIs separate process outcome, final-answer evidence, usage,
compaction observations, and tool inventory. Terminal CLI counters are cross-checked against the
last rollout cumulative total and counted once, never summed across repeated snapshots. Decreasing,
missing, or mismatched totals cannot establish complete usage. Observed compaction leaves usage
continuity unqualified even when final counters match. Structural completeness is not verification
of the selected Codex version's accumulation semantics or provider billing.

Final-answer capture must be distinguished from intermediate CLI messages, especially after timeout.
Retain candidate messages as evidence; do not promote the last intermediate message into an answer.
A separately captured final answer can remain assessable despite a later process failure. Tool
inventory entries retain source and call identity, not automatic shell-substring routing judgments.
CLI and rollout representations are not independent actual calls; inventory counts do not establish
a unique session-wide tool-call total. Saved-output reads, routed diagnostics, auxiliary actions,
and violations still require qualified audit coverage and classification.

`coordinator_record(report)` alone projects available evidence into the offline import schema;
it does not persist evidence. The coordinator's separate `import-capture` path below retains the
full report and original evidence under the private-storage policy. The optional CLI
prints the full report and a projection (null when no matching session identity exists), so its
stdout can contain supplied commands and answer text and must not be published without review.
Instrumentation remains `unknown` or `invalid`, boundary remains `unknown`, and
`experimental_claim_eligible` remains false. No import or parser option enables `run-one`.
Live collection, version-pinned semantics, compaction continuity, and complete actual-call auditing
are still required before launch qualification.

### Paginated TurnItem coverage

The adapter separately decodes `CommandExecution` in paginated `item_started`/`item_completed`
records using the [Codex 0.151.0 TurnItem schema](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/protocol/src/items.rs).
It retains the original ID, argv array, cwd, status, and each raw item observation, without joining
argv into a shell string or inferring routing compliance. TurnItem identities remain separate from
legacy response/event identities and CLI IDs; these observations are not a unique-call total.
Malformed command records and declined commands are not confirmed executions. Existing command
summary counters still describe CLI `command_execution` entries, not a combined stream count.

Known non-call message items do not become calls or final answers. `ContextCompaction` contributes
an observation and keeps usage continuity unqualified. Other variants, including MCP, extension,
file-change and subagent items, are retained as unconfirmed inventory entries with explicit
`unsupported_turn_item` audit gaps. Their detailed decoding remains future work; this bounded
subset must not be described as complete TurnItem or actual-call coverage.

Synthetic verification passed with warnings treated as errors: 25 capture, 17 coordinator,
11 analysis and 13 fixture tests, plus both historical offline runner/fixture scripts.
The paginated regression failed before the decoder was present and passes with it. No live
Codex session was used to establish this result. Binary/source equivalence, provider accounting,
compaction continuity and collector delivery remain unqualified. Full Go checks were not run
for this Python-only stage. Source/document changes require fresh offline plans.

### Call evidence map and qualification boundary

The next offline revision distinguishes reported requests/activity from supported command lifecycle
observations. This map describes inspected source and current decoding, **not** observed live calls.
It is pinned to Codex `0.151.0`, revision `78c290807ce710180111df227df3b7a4fe845452`.

Source owners:

- [Rollout persistence](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/rollout/src/policy.rs#L40-L200):
  selected ResponseItems persist; paginated history persists completed TurnItems, not item starts.
  Exec begin/end, raw-response events, dynamic request/response and approval requests are transient.
  Legacy history retains some alternative end events; this is not a complete event recorder.
- [Exec mapping/filtering and IDs](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L142-L378)
  and [wire schema](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/exec/src/exec_events.rs#L106-L296):
  exec maps selected variants, remaps IDs, drops others and may reconcile unfinished started items.
  Command exit codes are nullable. File-change failures collapse failed and declined states.
- [TurnItem variants and fields](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/protocol/src/items.rs#L40-L458):
  commands, dynamic tools, MCP, file changes, collaboration, images and extensions are distinct
  schemas. MCP status uses camelCase; command/dynamic status uses snake_case.
- [ResponseItem calls](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/protocol/src/models.rs#L1024-L1190),
  [request construction](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/tools/router.rs#L246-L298)
  and [dispatch rejection](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/tools/registry.rs#L519-L587):
  a recorded function/custom request is not proof of handler execution. Dispatch can reject an
  unknown tool, incompatible payload or blocked hook; hosted activity also has different semantics.

| Activity | Source-shaped evidence available | Current adapter / remaining gap |
| --- | --- | --- |
| Command execution | CLI command lifecycle; completed paginated `CommandExecution`; ResponseItem request where emitted | Decodes command fields and retains raw observations. Declined/malformed observations are unconfirmed. A reported lifecycle is not an independent process-spawn receipt or a unique tool-call total. |
| Saved-artifact read, query, retry or routed diagnostic | May be carried inside command argv/input, MCP/dynamic arguments or other tool payloads | Preserves payloads, but has no qualified semantic classification. A shell substring, command name or output size cannot prove which artifact was read or that routing complied. Counts remain unavailable, not zero. |
| MCP | Exec `mcp_tool_call`; paginated `McpToolCall`; legacy MCP end event | Retains raw payloads as unqualified entries. No success/access inference from name, read-only hint or result presence. |
| Dynamic tools | Paginated `DynamicToolCall`; possible function request/output | Exec mapping has no dynamic variant; dynamic request/response events are transient. Retains TurnItem as unsupported, not evidence of absence when missing. |
| File changes | Exec `file_change`; paginated `FileChange`; legacy patch end | CLI changes are now retained instead of skipped. Raw status/changes remain unqualified; exec failed cannot distinguish decline from failed application. |
| Web/image/extension tools | Hosted ResponseItems; paginated `WebSearch`, `ImageView`, `ImageGeneration`, `Extension`; selected exec web search | Preserves recognized call-shaped responses and unsupported TurnItems/CLI items; no full producer/extension decoder or unique invocation mapping. |
| Collaboration/subagents | Paginated collaboration/activity items; only a subset mapped to exec | Retains unsupported activity. Parent records do not establish child call/usage coverage; some collaboration tools/statuses are omitted or mapped lossily by exec. |
| Approval/input requests | Transient events, if independently supplied to the parser | Retained as requests, not confirmed execution. Their absence from persisted rollout is expected and cannot prove absence of requests. |
| Other/unknown variants, compaction, delivery loss | Original bounded raw streams; selected compaction observations | Unsupported inventory is explicit where decoded, but unknown event payloads need not become inventory entries. No zero-gap/full-coverage claim from zero unknown entries, terminal success or matching usage. |

Inventory semantics:

- `actual_call_confirmed` is a retained compatibility field, now restricted to syntactically supported
  non-declined command lifecycle reports. It does **not** attest real execution, session validity,
  successful effects, access compliance or the completeness of either stream. Other call-shaped
  records remain unconfirmed even when they report `status=completed`.
- `reported_call` covers ResponseItem requests/hosted reports; unknown `*_call` types are
  `unknown_response_item`. Legacy requests/lifecycles are `reported_request`/
  `reported_call_lifecycle`. Unsupported CLI items (including file changes) and TurnItems retain
  explicit decoder gaps. These labels describe evidence, not tool-policy classifications.
- All inventory entries retain source-local `raw_observations`. Within one source and ID, grouping
  preserves every observation and distinct `evidence_kinds`; singular `evidence_kind` describes the
  first observation. Any unconfirmed or changed-type observation prevents group confirmation.
  CLI, TurnItem, legacy event, ResponseItem and raw-response-event IDs are separate domains even
  when their strings match. Grouping is not a count of executions.
- `confirmed_call_entry_count` counts supported command-report groups, not unique calls;
  `unknown_item_entry_count` includes **all unconfirmed entries**, including known requests.
  Command completed/failed summaries exclude unconfirmed groups. Captured output bytes still
  describe retained CLI output, not proven model-visible bytes. `unique_session_call_count=null`,
  `coverage=unqualified`, unknown boundary and disabled experimental claims are unconditional.

Finite qualification checklist (a plan, not launch approval):

1. Freeze the exact launcher/native binary hashes, version, history mode, collector/adapter revision,
   rendered prompt, model/reasoning and effective observable tool surface. Recheck pins rather than
   trusting prior receipts. Hash/version agreement alone does not prove binary/source equivalence.
2. Before live qualification, approve specific non-production targets, directories, access/setup/
   cleanup and protocol/phase manifest. Fit qualification into the agreed 12 technical pilot sessions
   and 120-session ceiling; this checklist allocates no extra sessions, paid API fallback or access.
3. In authorized synthetic scenarios, retain an independent expected-action ledger and fixture
   outcomes alongside both streams: normal/failed/declined command, request rejected before handler,
   saved-artifact read, repeated identical command and any permitted non-command tool. Qualify
   disabled tools through observable restrictions rather than invoking unauthorized services.
   Distinguish request, dispatch, process/interaction and side effect; do not count them as synonyms.
4. Match the ledger to source-local evidence, including terminal-interaction inputs, child activity
   if allowed, and all saved-output follow-ups. A tool surface not covered by qualified observation
   must be restricted with verified controls or remain a launch blocker. Do not repair missingness
   by summing streams, accepting suffixes or requiring every task to succeed.
5. Exercise bounded failure/delivery cases (timeout, truncation/overflow, interrupted persistence,
   compaction and nonzero exit) using synthetic collector tests first and an approved live subset.
   Verify answer completion separately with `completion_evidence_bytes`; a match remains unknown
   finality until exact-binary delivery qualification. Preserve every attempted slot and limitation.
6. Record observed versus inferred/missing calls and explicit pass/fail evidence before any change
   to eligibility. If actual-call coverage or authorized-data access cannot be established, stop
   launch qualification; further offline preparation is not a substitute for passing the gate.

Synthetic tests in `test_issue10_v3_call_evidence.py` and coordinator import/replay cover request
non-promotion, file-change retention, declined/malformed commands, nullable exits, identity-domain
separation, mixed observations and rehashed false execution claims. They run no model/SSH session.
This revision requires fresh development plans; it does not migrate or rewrite frozen evidence.

### Single-local-slot qualification specification

**Status: preparation specification, not a frozen manifest, implemented controller, or launch
approval.** This narrows the checklist above to one inspectable pilot target. It neither claims
that the current collector can satisfy every requirement nor permits bypassing `run-one`.

#### Target and budget

Use the existing pilot schedule with seed `1010` as the reference: **slot 7**, pair `M01-r1`,
case `M01`, series `M`, replicate `1`, arm `sshai`. Slot 8 is its baseline counterpart. These
identities come from `schedule('pilot', 1010)`, not from selecting a favorable result. M01 is a
small healthy-state case that limits initial diagnostic complexity; an artifact follow-up is
possible, not guaranteed. This is a specification for that slot, **not permission to execute it
first**: retain the frozen schedule and preceding outcomes. A different launch order requires a
prospective documented protocol/schedule amendment before any affected session, not skipping slots
or changing the seed until a convenient order appears. No such amendment is made here.

The slot consumes one of the existing 12 pilot sessions, never an additional qualification session
or one of the 108 measured sessions. It receives the ordinary rendered M01 task and sshai-arm
instructions, without answer keys, required commands, required tool counts or a forced artifact read.
Use a fresh, non-forked, non-ephemeral, one-user-turn session; no subagents or continuation prompt.
Retain the candidate `gpt-5.6-sol` / `high`, subscription-only policy and proposed 600-second ceiling.
Availability, exact pins and those limits must be qualified/frozen before launch; do not substitute
an available model or use paid API fallback. A failed or uninformative slot is retained, not replaced.

#### Required private launch record

Before any model process, a future controller must bind the following values to the plan/slot and
retain a private preflight record. Missing values mean **blocked**, not a default supplied at launch:

| Field group | Concrete required evidence |
| --- | --- |
| Identity and pins | Plan digest and full scheduled slot; fixture manifest and rendered common/arm/task prompt hashes; actual model/reasoning; launcher and native binary paths/hashes/version; sshai executable hash/revision; collector/adapter revisions; declared paginated history. The reviewed Codex source is `78c290807ce710180111df227df3b7a4fe845452` / `0.151.0`; any different binary version requires a new source contract. |
| Workspace | One fresh local case root with only M01 inputs; separate writable scratch and sshai artifact/state roots; private controller/evidence and evaluator roots unavailable to task tools. Record exact resolved paths privately, never in public examples. Verify starting fixture hashes and reread them afterward. |
| Access | Observable effective tool names/schemas and exact supported configuration; tested restrictions on fixture writes, unrelated data, evaluator/evidence access and tool network. Runtime reads and scratch/artifact writes are explicit allowances. The historical writable-workspace canary does not by itself establish read-only fixture access or live parity. |
| Capture | Exact argv, prompt/environment digests, working directory, limits and source paths; a fresh rollout provenance/selection method rather than a guessed filename or a broad search of user history; absent answer output before spawn. The controller may discover its own isolated session output, but supplies explicit bounded candidates to the collector. |
| Handling | Private evidence destination and access modes, retention/deletion decision, reviewed publication policy, remaining pilot budget, frozen phase authorization and preflight outcome. Existing 90-day retention is only a proposal; no automatic deletion is selected here. |

Use the collector's current bounds: **1,000,000 bytes each for stdout and stderr**; **1 MiB
(1,048,576 bytes) each for prompt, answer and each supplied rollout candidate**; at most 32 candidates.
The adapter also limits each JSONL line to 256 KiB and records to 20,000. The imported envelope limit
remains 32 MiB, so the maximum set of individually valid inputs need not fit one import. Preserve oversize/partial attempts and their reasons; do not
truncate silently, increase limits after seeing an outcome, or recollect the slot. Environment
values and credentials are not public evidence. This specification does not inspect or provision
login material, and task tools must not be able to read it.

#### Minimal intended tool surface

Select **local command execution only**, including any necessary lifecycle/terminal-interaction
operation of the selected executor. Freeze the exact advertised names and schemas from the selected
configuration rather than assuming that CLI `command_execution` is a model-facing tool name.
Agent messages/reasoning and usage events are observations, not additional execution tools.

- New fixture diagnostics use sshai's explicit local Bash path. Ordinary local tools may process
  already captured output, read saved artifacts, and manage permitted scratch notes. They may not
  read source fixtures directly in this arm. Diagnostic success and routing compliance are separate.
- No SSH, web, MCP, dynamic/extension, collaboration, image, dedicated patch/file-change or other
  non-command tool is intended for this target. Disable or restrict these through supported,
  observably verified controls; prompt text or a zero-entry inventory is not that verification.
  Local notes can use the allowed command path; fixture repair remains forbidden.
- An unexpected advertised callable tool blocks this minimal configuration until its exclusion or
  coverage is established. An actual prohibited operation is retained as a violation. An unsupported
  record or uncertain execution is an audit gap, not automatic proof of a violation or harmlessness.
- The launch collector is not an access sandbox. If the selected Codex configuration cannot supply
  this surface or adequate observation, record the concrete missing capability. Do not silently
  expose additional tools, claim the historical managed-MCP barrier solved, or implement every
  optional TurnItem decoder merely to clear this preparation checklist.

#### Independent action ledger and reconciliation

Freeze a small expected-action matrix **outside the agent prompt**. It defines coverage cases,
not a command prescription or an expected correct diagnosis:

| Coverage case | Independent evidence needed | Comparison with captured evidence |
| --- | --- | --- |
| Routed diagnostic | Controller-side observation of the local diagnostic process/interaction and the input resources it actually accesses; initial/final fixture hashes; sshai artifact identity when produced | Retained command request/lifecycle plus source-local IDs, argv/cwd and outcome; distinguish request rejection, process start and completion. |
| Saved-artifact follow-up | Independently observed access to a specific previously produced artifact, with artifact identity/hash and read range or an explicit range-unavailable marker | Retained command/interaction containing the read or query, linked to that artifact, not merely containing the word `cat`, `q` or `sshai`. A query need not read an artifact and does not count automatically. |
| Auxiliary action | Observed scratch/output processing, resource identity and outcome | Classify separately from new fixture diagnostics; do not count every child process as a separate model tool call. |
| Rejection, failure or repetition | Observed disposition and distinct interaction identity, including repeated identical requests where present | Preserve every request and outcome; no confirmation from a call-shaped suffix, exit zero alone or cross-stream ID equality. |

The minimum ledger entry records an independent observer/event identifier, relative sequence,
parent process or interaction reference, action class, resource identity/hash where applicable,
observed disposition, evidence reference and observation limits. Record unreadable/ambiguous resource
or interaction identity as unknown. Paths/arguments can be private; public reports use reviewed
relative identifiers. The observer must be outside task-writable state and independent of the two
Codex streams being compared. A second parse of those streams, an agent-authored note, sshai history
alone or before/after file hashes is **not** a complete independent action ledger. Hashes show
content integrity, not who read a file; process argv alone does not reveal accesses hidden inside a
shell or interpreter. Independence here means a separate observation path, not owner-proof storage.

First exercise this matrix offline using known synthetic processes and inputs. Synthetic ground
truth can qualify decoder/reconciliation behavior, but cannot establish coverage of arbitrary live
actions. The current v3 modules do **not** implement a qualified independent live observer or this
reconciliation report. Selection and bounded verification of that observation path is the first
implementation prerequisite; no OS-wide tracing, elevated access, new dependency or raw process
recording is authorized merely by this document.

For the real slot, reconcile ledger observations with retained source-local inventory. Keep missing,
extra, rejected, ambiguous and unsupported observations explicit; do not add CLI and rollout counts
or promote the compatibility `actual_call_confirmed` field into execution attestation. Ordinary
artifact reads remain permitted and count toward session usage. If the autonomous agent performs no
artifact follow-up, mark that coverage case **unobserved**, not failed task quality, audit success or
permission to add a prompt/replacement session. Qualify it in a later already-planned pilot slot if
observed; otherwise retain the coverage gap. The same rule applies to unobserved failure/retry paths.

#### Qualification result, separate from task quality

Record one result per row: `pass`, `fail`, `unobserved`, or `blocked`, with evidence references and
limitations. These are proposed qualification outcomes, not new fields accepted by the current
result importer. `pass` requires positive evidence; absence of a detected error is insufficient.

| Check | Pass evidence | Failure or remaining limitation |
| --- | --- | --- |
| Preflight/access | All private record fields resolved; effective allowed surface and bounded access verified under the actual invocation | Missing target/configuration/observer evidence blocks launch; observed unauthorized access is a violation. Historical canaries alone are insufficient. |
| Collection and identity | One reserved attempt; fresh session/turn bound to exact retained streams, prompt/slot and delivered bytes; no unresolved rollout competitor or lost/truncated evidence | Partial receipt, ambiguous selection, overflow or lost publication prevents complete-capture qualification; preserve originals and reservation. |
| Usage | Supported pinned accumulation semantics plus agreement of terminal CLI counters and final rollout cumulative totals, counted once; full model-visible guidance/recovery included | Missing, decreasing, default-only or mismatched counters remain unavailable. Matching totals alone cannot prove complete recording or provider billing. Compaction continuity needs separate evidence if observed. |
| Answer delivery | Exact-byte same-turn completion comparison plus independent validation of the selected binary's completed-turn/output-file delivery behavior | `matched` alone is not qualified finality. Missing/partial/unphased/unsupported output remains unknown, not an automatic zero score. A later process failure does not erase independently established completion. |
| Actual-call coverage | Independent ledger reconciles all actions of the permitted surface and records uncertainty; required diagnostic and artifact-read coverage cases observed | Unmatched/unsupported actions leave coverage unqualified; no artifact read means unobserved coverage. Unique-call/follow-up metrics stay unavailable unless their counting unit and coverage are qualified. |
| Quality | Captured qualified answer receives separate fact/citation checks and anonymized human diagnosis/evidence/recommendation assessment | Do not use correct task content as an instrumentation gate or invent human grades. Six accepted calibration anchors are not this slot's assessment. |

A normal slot cannot establish timeout, overflow, compaction or interrupted-delivery behavior merely
because none occurred. Retain the synthetic failure-path evidence separately and any remaining live
qualification limits. Nor can one sshai slot qualify its baseline counterpart, Linux/Windows access,
all tasks, or a token-saving effect. A wrong diagnosis may coexist with technically valid capture;
a correct answer may coexist with invalid instrumentation. Keep both visible.

**Current disposition: blocked for live qualification.** The exact launch record, verified minimal
tool surface/access, fresh-rollout selection, independent live ledger, qualified usage/finality,
review integration and phase approval are not supplied by this specification. Existing
`collect_slot` binds a caller request, not its conformity to this record. Existing collector imports
still project unknown-finality answers to `lost`/null and refuse grading. Keep `run-one` disabled,
`experimental_claim_eligible=false` and all qualification flags unchanged. Only a separately verified
implementation/qualification change may alter those contracts; this document is not a bypass.

### Offline capture-to-slot integration

```sh
python3 scripts/benchmark_issue10_v3.py import-capture ROOT --slot 1 \
  --events SYNTHETIC_EVENTS.jsonl --rollout SYNTHETIC_ROLLOUT.jsonl \
  --process SYNTHETIC_PROCESS.json --answer SYNTHETIC_FINAL_ANSWER.txt
```

This command imports supplied files only; it never records a live session or starts a process.
Alternatively pass `--answer-state absent` or `--answer-state lost` without an answer file.
Omitting both preserves the adapter's existing evidence-inference rules; it is not a claim that
an external collector verified answer absence. Inputs are bounded to 1 MiB each, must be regular
files, and symlink paths/ancestors are rejected. Oversize/unreadable input is rejected before slot
publication, not silently truncated. Malformed bounded bytes are retained with parser findings.

Each slot uses one atomically published private `records/NNN.json` envelope (0600 under 0700
storage), with exact raw bytes encoded as base64, byte counts and SHA-256 hashes, the full parsed
report, explicit answer-state option, full scheduled slot, plan digest, and derived result. The
32 MiB envelope ceiling accommodates base64/report expansion; it does not increase input limits.
One-file publication avoids a record referring to only partially written evidence. Imports never
overwrite a slot and refuse session IDs already used by either import path. Source filenames and
absolute input paths are not copied. The envelope can contain sensitive supplied content: keep it
private outside the repository and never publish it without review. Base64 is not sanitization.

A missing or mismatched stream identity does not discard the attempted slot. The retained report
keeps its null session ID; only the projected record receives a visibly synthetic
`unidentified-capture:<plan-digest>:<slot>` local key, with `identity_origin:local-slot-key`.
This key does not establish Codex identity or uniqueness across unidentified captures.
Instrumentation/boundary are never upgraded by successful storage or replay.

Analysis and review recording verify hashes, exact plan/slot binding, then recompute the report
and projection from retained bytes. Reviews also bind the capture digest. Analysis exposes per-slot
provenance/digests without adding raw bytes or call details to its output. Existing declared-record
imports remain supported and distinguishable. The capture module is now source-pinned in new plans;
source/document changes require a fresh plan, never rewriting a preserved one.

These are application-level append-only writes and consistency checks, not filesystem WORM storage,
collector-origin attestation, or protection against an owner rewriting all evidence and hashes.
Raw retention and replay do not qualify live usage semantics, audit coverage, compaction continuity,
or experimental claims. `run-one` remains unconditionally disabled.

Verification of this integration passed with warnings treated as errors: 17 coordinator tests,
16 capture tests, 11 analysis tests, and 13 fixture tests, plus both historical offline test scripts.
Coverage includes byte-exact retention, malformed/unidentified evidence, mixed import uniqueness,
overwrite refusal, tampered raw/report/projection/slot binding, capture-bound reviews, publication
failure, input limits, envelopes exceeding the old 1 MiB record bound, symlink refusal, and CLI
import of an answer retained despite process timeout. Tests use only synthetic data and temporary
storage; the historical runner tests use synthetic processes. No model/SSH experiment, credentials,
commit or push occurred. Full Go test/vet/build checks were not run for this Python-only stage.

## Bounded collector development library

`scripts/benchmark_issue10_v3_collector.py` exposes `collect_attempt` for an explicitly supplied
command, environment, working directory, timeout, answer path and at most 32 rollout candidate
paths. It has no launch CLI and is not connected to coordinator `run-one`. This API can execute
processes; its existence is not authorization to launch Codex or SSH. Development checks use only
synthetic local Python children:

```sh
python3 -W error scripts/test_issue10_v3_collector.py
python3 -W error scripts/test_issue10_v3_collector_contract.py
```

Each attempt requires a new private directory. `attempt.json` is published before spawn and binds
command/prompt/environment hashes and collection bounds, without storing environment values.
The historical bounded-process helper is reused without changing historical sources: stdout and
stderr are independently capped, deadlines terminate the process group, and timeout, overflow,
interruption and start errors are retained independently of answer delivery. Cleanup/draining can
extend beyond the configured process deadline. Raw streams and process receipts survive later
adapter projection failures; reaching a stream limit does not identify which stream caused an
overflow. A pre-spawn receipt without subsequent files is an incomplete attempt, not an unattempted
slot or permission to retry.

The collector reads only explicitly supplied rollout candidates, never scans a Codex home, and
retains bounded candidate bytes with selection outcomes. Selection requires a single matching CLI
thread identity; it is not proof of fresh-session provenance or full lifecycle/usage validity.
Missing, unreadable, oversized or empty answer files mean delivery loss, not proof that the model
produced no answer. Retained nonempty answer bytes still require adapter validation and final-answer
provenance qualification; partial files cannot be certified merely by their existence. Process
success, answer delivery and rollout selection remain separate outcomes.

Every collector answer receipt includes `finality: "unknown"` and
`finality_reason: "no_qualified_final_answer_evidence"`, including lost delivery.
`state: "captured"` denotes retained bytes only. Neither exit zero, matching CLI message text,
nor a nonempty answer file certifies a complete final answer. Do not map this delivery state
alone into the capture adapter's explicit captured-final-answer input or a gradable final answer.
Unknown finality must remain unknown quality, not an automatic failure score or proven absence.
A future qualified finality check must bind completion evidence to the retained answer bytes;
this collector does not yet provide one. A later process failure must not revoke independently
confirmed finality, and finality must not imply correct content or valid usage.

Storage is private application-level no-overwrite publication, not WORM or crash-atomic multi-file
storage. Receipts contain local paths and errors; raw captures may contain supplied command/output
content. Keep all collector evidence private and unpublished. Disk-write failures may leave only a
partial attempt and must not be called successful capture. The caller still owns authorization,
authentication provisioning, model/binary pins, fresh isolated sources, slot/plan binding and safe
access restrictions. No sandbox or credential isolation is provided by this process collector.

The explicit prompt is delivered on stdin. Bounds are 1 MiB each for prompt, answer and each
rollout candidate; stdout and stderr each have a 1,000,000-byte bound inherited from the historical
process helper. Raw `events.jsonl`, `stderr.txt`, `process.json`, candidate files,
selected `rollout.jsonl`, optional `answer.txt` and `delivery.json` are separate publications.
Oversized source files produce retained error receipts, not retained byte prefixes; original sources
are not deleted. Only stream overflow retains a bounded prefix automatically.

An answer source must not exist before spawn. Pre-existing regular rollout candidates are accepted,
so freshness remains the caller's responsibility. Selection fails closed if any supplied candidate
is unreadable, oversized, malformed, or has missing, invalid or ambiguous session identity. Such
candidates could conceal a duplicate match; `unresolved_candidate_identity` records that uncertainty
unless a CLI identity error or multiple known matches already explains rejection. Invalid metadata
entries are not silently dropped. Readable candidate bytes and per-candidate findings remain retained,
but selected `rollout.jsonl` is empty and delivery is `lost`. A valid identified nonmatch does not
block a unique match. Candidate enumeration and full metadata validation remain unqualified. A delivery `state=captured`
means bytes were copied, not that full audit coverage or final-answer completeness was established.
The helper uses POSIX process-group cleanup, not containment of descendants that start new sessions;
Windows controllers, blocked spawn/filesystem calls and live model delivery are not qualified.

Verification: 16 collector tests and 6 additional contract tests pass with warnings treated as
errors, covering synthetic subprocesses, publication failures, bounds, stale-answer refusal and
pipe-EOF timeout. The existing 66 v3 coordinator/capture/analysis/fixture tests also passed.
Historical source modules remain unchanged; no live model/SSH session was used. The selection
regressions cover unresolved competitors in both candidate orders and a valid nonmatching competitor.
Answer-finality regressions cover retained text after zero exit, nonzero exit and timeout, plus
empty and missing files; both returned and persisted receipts retain explicit unknown finality.

This stage does not enable experimental execution: live coordinator integration, qualified actual-call
coverage, compaction continuity, live delivery semantics and separate launch approval remain open.

### Offline collector-to-slot import

```sh
python3 scripts/benchmark_issue10_v3.py import-collector ROOT --slot 1 --attempt ATTEMPT_DIRECTORY
```

This path imports an existing collector attempt without starting a process. It binds retained
collector evidence to the prepared plan and full scheduled slot **at import time**, not before
spawn. It does not attest that the process ran for that slot, used the planned prompt, accessed
only the intended fixture, or used the selected model. New plans also pin the collector source.

The private, atomic slot envelope retains the collector receipts and bounded evidence, including
answer bytes independently of the projected answer state. Import checks receipt consistency and
retained byte hashes/counts; analysis replays the evidence and verifies the plan/slot binding.
Partial attempts or inconsistent retained files are rejected without rewriting or deleting the
original attempt. A refused import is not permission to rerun an experimental slot. The existing
32 MiB envelope ceiling still applies; retaining many candidate files may exceed it.

Only the current collector's explicit unknown finality is supported. Delivered answer bytes are
**not** supplied as a captured final answer to the adapter: the projection has `answer_state: lost`
and `final_answer: null`. Here `lost` means unavailable qualified final-answer evidence, not that
retained bytes disappeared. Quality remains unknown even after a timeout, exit zero, or matching
CLI message text. Human review of this projected record is refused rather than assigning zero or
promoting a candidate answer. Unsupported finality claims are rejected, not trusted. The source-shaped
completion comparison below adds retained evidence, not an exception to this rule.

This import is distinct from `import-capture`, whose supplied final-answer option remains an
operator declaration. Hashes and replay establish internal consistency, not collector authenticity
or protection against rewriting all data and hashes. Raw receipts can contain private paths and
errors; base64 is not sanitization. Keep the envelope and original attempt outside published data.
`run-one` remains disabled and experimental claims remain false. This unbound import alone does
not establish pre-spawn plan/slot reservation. The development-library path below adds that local
association; qualified finality detection, complete audit coverage, live capture qualification and
protocol freeze remain prerequisites.

Synthetic verification covers successful and timed-out collection, nonzero exits, empty/missing
answer files, start failure, stream overflow, unreadable and malformed candidates, receipt/data/
slot/projection tampering, partial attempts, symlink refusal, duplicate-session and overwrite
refusal, CLI import, source pins, grading refusal, and the envelope-size ceiling. The full v3
suite passed 96 tests with warnings treated as errors (25 coordinator, 25 capture, 16 collector,
6 collector contract, 11 analysis, 13 fixture). Historical offline runner/fixture checks and
Go test/vet/build also passed; Go tests used cache. These are synthetic consistency checks, not
real session qualification or measured token results.

### Source-shaped answer completion comparison

`benchmark_issue10_v3_capture.completion_evidence_bytes(events, rollout, answer)` compares bounded
supplied bytes without launching a process. `import-collector` stores its result as
`collector.report.answer_completion` and recomputes it during replay. It does **not** pass delivered
bytes to the adapter as an explicitly captured final answer. Collector receipts, projected lost/null
answers, unknown quality, and grading refusal remain unchanged even when the comparison matches.

The intentionally narrow recipe targets the reviewed
[Codex 0.151.0 source revision](https://github.com/openai/codex/tree/78c290807ce710180111df227df3b7a4fe845452).
It requires one matching exec thread, declared `paginated` history, one completed turn in each
stream, matching rollout turn/item identities, one completed `AgentMessage` with explicit
`final_answer` phase as the last completed
agent message, and agreement between its text, terminal `last_agent_message`, the last completed
CLI agent message and delivered UTF-8 bytes. The current recipe accepts exactly one `Text` content
block and no asynchronous delivery. Duplicate/failed/ambiguous terminal evidence, later records,
unsupported version/source, partial JSONL, missing phase, mismatches and missing delivery do not
match. This is deliberately not a decoder for every valid Codex answer, review/plan output, or
legacy unphased message. A nonmatch is not proof that an answer was absent or incorrect.

The implementation distinguishes `matched`, `unmatched`, and `unavailable`; all retain
`finality: unknown` and `live_qualified: false`. Reports bind exact stream/answer SHA-256 hashes
and byte counts plus source-local message/terminal record positions and rollout identity. CLI and
rollout message IDs are **not** joined: the CLI remaps them. Text comparison performs no stripping,
newline addition, Unicode normalization, or prefix acceptance. Process exit/timeout is not an input:
a later process failure does not erase an observed match, and exit zero cannot create one. Usage,
call-audit completeness and answer correctness remain independent.

Source basis: the [exec processor](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L476-L548)
retains completed message text, enables final output on a completed turn and clears it on failure
or interruption. Its [turn-item fallback](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L380-L394)
can select a last agent message or plan; the narrower recipe does not infer a final answer from
that fallback alone. The [writer](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/exec/src/event_processor.rs#L31-L48)
writes the supplied string without adding a newline and reports write failures to stderr.
[Turn completion fields](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/protocol/src/protocol.rs#L2099-L2123)
and [message phase/delivery fields](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/protocol/src/items.rs#L120-L158)
provide explicit evidence to compare, not binary provenance or guarantees of lossless capture.
The [core finalizer](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/stream_events_utils.rs#L442-L466)
combines content and removes hidden markup into one visible `Text` block; compare this finalized
text, not raw model response text. The [terminal producer](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/tasks/mod.rs#L796-L837)
carries `last_agent_message` and terminal error separately. A [continuation test](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/tests/suite/pending_input.rs#L1042-L1099)
shows that another model request can follow a final-phase item before turn completion; phase alone
is insufficient. Async messages also [use final phase without ending the turn](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/tools/handlers/send_user_message_async.rs#L76-L99).

This prepares qualification evidence, not a qualified finality detector. Self-reported version and
source fields do not attest a binary or establish fresh-session provenance. Owner-rewritten bytes
and hashes remain outside the consistency guarantee. Exact live producer/delivery semantics, binary
pins and supported failure cases still need qualification before a match may become a gradable
final answer. No option upgrades it; `run-one` and experimental claims remain disabled.
Source/document changes require a fresh development plan, not a rewritten preserved envelope.

Synthetic regressions use `python3 -W error scripts/test_issue10_v3_completion.py` and the coordinator
suite. They cover exact hashes/binding, partial/Unicode/newline delivery, lifecycle and identity
mismatches, explicit phase versus commentary, unsupported content/delivery, duplicate evidence,
replay tampering and grading refusal after a normal exit, a nonzero exit and a post-evidence timeout. These
are source-shaped synthetic records/children, not measured Codex sessions.

### Pre-spawn slot association development API

`benchmark_issue10_v3.collect_slot(root, number, argv, *, prompt, env, cwd,
timeout_seconds, rollout_candidates=(), answer_path=None)` is a library function, not a launch
command. It executes the explicitly supplied process through the bounded collector. Development
verification uses synthetic local Python children only; the API does not authorize model or SSH
execution, and `run-one` continues to refuse unconditionally.

The function validates the plan and supplied collection request, then publishes a new private
`reservations/NNN.json` before invoking the collector at the fixed `attempts/NNN/` path. The
reservation binds the plan digest, full scheduled slot, attempt path, and exact request receipt
(command/environment/prompt hashes, prompt size, cwd, paths, timeout and bounds). Before process
creation the collector publishes its version-2 `attempt.json` with that reservation's hash,
plan digest and full slot. Unassociated library attempts retain the version-1 schema.

The reservation is one-shot. Existing results, attempts or reservations prevent another collection
for that slot. A failure after reservation leaves it intact, including failure before spawn or
partial evidence publication. There is no automatic retry, deletion, replacement, recovery command
or automatic import. Import a completed attempt using the existing `import-collector` command;
retain incomplete originals for diagnosis rather than filling the slot with unrelated evidence.

All three import paths respect reservations. A reserved slot accepts only the associated collector
attempt at its reserved path, with a matching request and reservation hash. The result envelope
retains the reservation as well as original collector files; analysis replays that association and
compares it to the retained reservation. Unreserved version-1 attempts still import with the original
`coordinator-import-after-collection` binding. Associated attempts use
`coordinator-reserved-before-collection`; `pre_spawn_attestation` stays false and receipt authenticity
stays `not-attested`. This records application-level ordering, not independent authenticity.

Short reservation/result publication sections share a fail-fast private `.record-write-lock`
directory to prevent competing import/collection claims and duplicate imported session IDs. The
lock is released before collecting the process. An interrupted writer can leave a lock that blocks
further publication; the API neither removes stale locks automatically nor interprets that condition
as permission to retry a slot. Filesystem publication is not a crash-atomic multi-file transaction
or tamper-proof storage, and owner modifications remain outside the consistency guarantee.

Analysis distinguishes reserved slots without imported results from unattempted slots. Their
quality is unknown with `reserved_without_result`, not a zero score or proven answer absence;
`attempted: false` does not assert that no child ran, only that no result was imported. The separate
`reserved_without_result_slot_count`, `collection_reserved` marker, and `slot_reservations` summary
make this uncertainty explicit. Reservation summaries expose slot numbers, hashes and import status,
not request paths or command/environment values. Raw reservations and attempts remain private.

The supplied request is bound exactly; it is **not** certified as using the rendered planned prompt,
selected model, approved environment, schedule order or qualified access. The current plan remains
an unfrozen draft with unresolved deployment placeholders. This API does not provide fixture or
credential isolation, qualified finality, live audit coverage, model pins, or experimental launch
approval. Unknown-finality answer bytes remain ungradable and experimental claims remain false.
Source/document changes require fresh development plans, not rewriting preserved evidence.

Offline regression command:

```sh
python3 -W error scripts/test_issue10_v3_reservations.py
```

## Historical runner interface

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

Do not invoke `run-one` yet. The unchanged implementation fails closed because the mandatory
`verify_no_managed_mcp` explicitly refuses launch for the retained ChatGPT login: no supported
system/managed/cloud absence proof exists yet. There is no command-line bypass. The named `issue10`
profile through identical `-c` overrides remains mandatory for execution; do not use unrestricted
workspace-write or sandbox-only `-P` execution syntax.

## Current manifest and artifacts

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

## Current event and usage validation

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

## Current quality and integrity gates

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

## Current results contract

For each arm and task publish all retained slots: validity, failure reason, elapsed time, Codex
input/cached/non-cached totals, captured-output bytes, evaluator result, and review status. For
valid pairs, publish signed per-pair baseline-minus-`sshai` differences and a dispersion/uncertainty
summary. Do not pool tasks into a positive headline without task-level evidence.

The report decision is `descriptive reduction`, `measured increase`, `no clear difference`,
`quality regression`, `inconclusive`, or `invalid`. A positive claim is permitted only for a
complete valid **measurement** with `descriptive reduction`; a pilot may report only descriptive
reduction/increase evidence and makes no positive or statistical claim. Dispersion is descriptive,
not a significance test. Never discard failures or select only successful pairs.

## Offline command reference and unchanged launch barrier

The following commands describe the existing local-only draft, not permission to execute them.
For separately authorized offline preparation, they require no model request or network-capable
configuration check:

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
barrier is resolved through separately authorized implementation and qualification. The previously
discussed network-capable configuration check was not performed: a network inventory cannot freeze
cloud configuration for a subsequent ChatGPT-authenticated launch. This guide does not renew that
authorization. The target design uses practical restrictions and call auditing rather than requiring
that absence proof, but the current runner still enforces the old barrier.

Local results stay local. A remote series requires its own manifest, qualification, host and
permission record, approval, and report section. It cannot be used to generalize from the current
Mac. README numeric claims wait for a completed valid series and must link the result and state
scope and caveats.
