# Issue 10 prospective tool-audit and model-assessment amendment

**Issue:** [sshai#10](https://github.com/aprudkin/sshai/issues/10)

**Status:** Methodology selected by the user before any runs under this amendment.
This is not a completed pilot, a frozen execution manifest, or a result report.
The original [protocol](issue10-protocol.md) and [analyzer guide](issue10-analyzer.md)
remain applicable except for the replacements explicitly listed here. Frozen v1.1 and
v2.1 studies and retained earlier preparation records are unchanged.

## Purpose and replaced requirements

The user selected the recommended practical audit, independent model assessment, and
local-pilot-first approach. This replaces two preparation requirements that prevented
execution; it does not establish that those earlier requirements passed.

| Earlier requirement | Prospective replacement |
| --- | --- |
| Independent observation of descendant processes and the resources they actually read | Verified restrictions on accessible data plus retained tool requests/lifecycles, with unsupported activity and semantic uncertainty explicit |
| User grading of every final answer | A separate model-assessment context with branch-hidden presentation, frozen rubric and retained assessment rationale |
| One interleaved three-series pilot schedule | Separately frozen per-series schedules; complete the local pilot before starting qualified remote series |

The task remains autonomous diagnosis of synthetic snapshots, not execution of a
prescribed command sequence or repairs. The baseline may filter output normally.
The sshai arm routes new diagnostics through sshai and may read saved artifacts
using ordinary tools. All model-visible steps count toward diagnostic usage.

## Tool-call audit: unit, evidence, and limits

The audit unit is a recorded tool request or lifecycle in the pinned Codex capture
format. It is not an independently observed OS process, syscall, or filesystem read.
Do not merge unrelated CLI and rollout identifiers or count both representations as
two calls. Preserve each source and its identity domain. Report unique-call counts
only for a tested, unambiguous counting rule; otherwise leave them unavailable.

Before a phase launches:

- Freeze the exact executable, model, reasoning effort, configuration, tools and
  instructions. Use a fresh isolated configuration and supported controls to exclude
  unrelated tools. Document any advertised surface that cannot be inspected directly.
- Qualify local access restrictions with synthetic positive and negative canaries
  under the same permission configuration used by the actual invocation. Keep keys,
  other cases, retained sessions, credentials and unrelated user data outside task
  access. Tool network is unnecessary for the local series and must be denied.
- Keep the controller's capture and evaluator data outside task-writable storage.
  Expose only the current case, task instructions, permitted scratch and artifact state.
- Retain bounded raw CLI/rollout output, process outcomes, exact final-answer bytes,
  fixture hashes and configuration/attempt receipts privately. Stop on capture overflow,
  unsafe access or an unsupported execution path; do not replace an attempted slot.

After a slot, inspect retained tool records and classify observed routed diagnostics,
allowed auxiliary/saved-output activity, explicit violations and unresolved activity.
A command containing `sshai`, `cat` or `q` is not by itself a semantic routing proof.
Shell/interpreter behavior that cannot be established remains unknown. A permitted
artifact read is not a violation; absent artifact follow-up is not failed task quality.

An explicit violation remains a violation. Unsupported or ambiguous observations
remain unqualified, not automatically compliant or automatically malicious. Preserve
these outcomes alongside quality and usage. No result may be described as completely
observed, confined, or routing-verified merely because the recorded calls look normal.
A conforming-comparison claim requires positive evidence within the stated audit unit;
missing coverage must appear in the report and cannot be repaired by selecting a more
favorable replacement run.

This amendment removes the need for a complete independent descendant-resource ledger.
It does not turn an exec-server proxy into one, remove access restrictions, authorize
OS-wide tracing, or allow unrelated managed tools to execute unchecked. Historical
`run-one` barriers and v3 offline qualification flags are not retroactively upgraded.
A separately identified controller must enforce the prospective phase conditions.

## Independent model assessment

Use the existing private randomized review-packet design where its retained-answer
contract applies. Give the assessor neutral task evidence, the semantic key, the
rubric and exact final answers with opaque identifiers. Do not provide arm labels,
usage totals, trial order or previous grades. Answer wording can still reveal an arm;
report this as incomplete blinding rather than rewriting answers to conceal it.

The assessor must be a separate context from each diagnostic session. Freeze its model,
reasoning settings, instructions and batching before scoring the phase. Keep the same
assessor configuration and rubric for both arms. Assess diagnosis correctness and the
independent 0–2 evidence/recommendation scales. Preserve the original success rule:
correct diagnosis and both scores equal to 2. Retain rationale and supporting source
references, including unsupported assertions and unsafe recommendations.

These are **model assessments**, not human grades, independent human calibration,
objective truth, or proof of equal quality. Retain missing, malformed or disputed
assessments as unknown quality. Do not silently replace inconvenient scores or use
accepted synthetic calibration anchors as grades of collected answers. Source-line
existence/quotation checks are distinct from semantic correctness; a valid citation
alone does not establish its causal relevance.

Keep diagnostic and assessor usage/cost accounting separate. The primary token outcome
still covers complete diagnostic sessions, not the scorer. Never subtract branch
guidance, errors, recovery or allowed evidence retrieval from those session totals.
Any assessor usage that cannot be measured must be explicitly unavailable.

## Phase order and resource ceilings

The diagnostic ceiling remains 120 sessions across all three series: 12 pilot sessions
and 108 measured sessions. No diagnostic sessions have been established as completed
under this amendment merely by preparing code or synthetic capture tests.

1. Freeze and run four local pilot sessions: M01 and M02, one fresh baseline/sshai pair
   each, with one pair starting in each arm. Select a deterministic randomized order
   before observing outcomes. These replace the local allocation within the original
   12-slot pilot; they do not add four sessions or reorder an already-started population.
2. Review local access, capture, usage, final-answer delivery, tool records and assessment
   outcomes. Pilot sessions are never confirmatory measurements or README savings figures.
   Necessary changes require a new prospective version, with original attempts retained.
3. Freeze later per-series phases only after their prerequisites pass. Remote series
   still require exact targets, directories, shells, fixture setup and bounded access.
   A local pass does not qualify Linux or Windows. Do not combine their results.
4. Retain the original measured allocation: six tasks, three paired repetitions per task
   per series. Keep all failures, incomplete pairs and invalidation reasons. No outcome-based
   stopping, replacement, sample-size adjustment or favorable-subset headline is permitted.

Use a 600-second wall timeout per diagnostic session. The ceiling bounds attempts and
wall time, not provider token expenditure. Use only the existing subscription; pause
on quota exhaustion or unavailable model, with no paid API fallback, new purchase,
automatic model substitution or quota increase.

Model grading is a separate necessary allocation introduced by this amendment: at most
24 assessment contexts (two task batches per pilot series and six task batches per
measurement series). Each batch contains both arms for the same task and all its planned
repetitions; it does not include other batches' scores. Record actual assessor model and
usage where available. This is separate from the unchanged 120 diagnostic-session ceiling,
not an unreported increase in the measured population. Do not automatically retry failed
assessor contexts or buy additional capacity.

## Remote scope and privacy

The user supplied a Windows target and a local operations project from which to select
Linux targets. Keep exact internal aliases, addresses and paths in private launch records,
not this public protocol or issue comments. Prefer a suitable application/test host over a
database host. Use newly created dedicated directories containing only synthetic fixtures;
never inspect production application data, backups, credentials or service logs as fixtures.
Do not restart services, change firewalls, install system packages or modify SSH host-key
policy. Production-role hosts do not authorize production-data access.

Preserve the original private-retention and reviewed-publication rules. Retain raw records
for 90 days after report or termination without automatic deletion. Public reports contain
only reviewed synthetic materials, anonymized aggregates and explicit limitations. A
private review packet is not automatically safe to publish.

## Local-pilot controller

[`scripts/benchmark_issue10_local_pilot.py`](../../scripts/benchmark_issue10_local_pilot.py)
implements a separate local preparation and capture path. It does not enable the
historical v3 `run-one` command or implement model assessment or measured analysis.
The [reusable assessor instructions](issue10-assessment-instructions.md) define the
separate assessment prompt and response contract; they are not an assessor runner.

- `prepare` creates a new private manifest and freezes the inputs, rendered prompts,
  binary/configuration pins, four-slot schedule, and assessment instructions/rubric.
  It requires explicit configuration and does not launch a model.
- `preflight` retains no-model access canaries for M01 and M02 without reserving a
  diagnostic slot. It is one-shot; a failed preparation remains retained.
- `run-slot` requires a successful preflight, manifest-matching approval and
  `--allow-model-run`. It reserves the next slot before fresh path-specific access
  qualification. Reserved slots cannot be retried, overwritten or resumed. Before
  reserving a later slot, it requires every prior slot to have a result that allows
  continuation. Blockers include failed rollout discovery, rollout or answer delivery
  not marked `captured`, all capture-parser issues with `effect=invalid` (including
  malformed records and missing identities), capture overflow, fixture access/integrity
  failure, and unsupported or unallowed audit records. The audit checks each retained
  observation's actual signature, not only the first signature of a grouped entry.
  Tool records need an explicit nonempty identity; `function_call`, `custom_tool_call`
  and `local_shell_call` specifically require `call_id`, not a response `id` or a
  parser-generated record position. Compact blocker codes appear in the result;
  detailed delivery/parser/audit reasons remain in the private evidence receipts.
- `summary` reports `unattempted` only when the slot directory is absent. A reserved
  directory without `result.json` is `reserved-incomplete`, with launch
  `attempted-or-unknown`, execution `unknown`, retry disabled and continuation blocked.
  This includes interruption before `reservation.json` is written or after process
  evidence appears; missing results do not prove that no model ran. Summary does not
  repair slots, grade answers or estimate savings.

Expected tool names and unavailable schema hashes remain explicitly labelled rather
than presented as observed schemas. Intentional unknown finality, semantic routing and
unqualified instrumentation semantics alone do not block continuation. Nor does
`usage.complete=false` alone: capture-invalid reasons remain distinct from uncertainty
such as unqualified usage continuity across compaction. An accepted record signature
is not proof of compliant shell behavior.
Synthetic tests exercise controller behavior, not installed-binary readiness or live
capture qualification. Use the command's `--help` for its argument inventory.

### Native runtime files and no-model regression

Codex 0.151.0 can leave `CODEX_HOME/tmp/arg0/codex-arg0*/` after a no-model sandbox
probe. The controller accepts only the pinned Darwin helper layout: physical directories,
an empty regular `.lock`, and `apply_patch`, `applypatch` and `codex-execve-wrapper`
symlinks pointing exactly to the selected native binary. It records bounded layout metadata
before collection and during rollout discovery. Other entries or symlink targets remain
errors; helper aliases are neither rollout candidates nor evidence of tool execution.
This matches the [pinned arg0 implementation](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/arg0/src/lib.rs#L327-L445),
not a general exception for temporary files. No runtime evidence is deleted to pass the guard.

The default unit suite uses synthetic processes. On macOS, an additional opt-in regression
runs the installed pinned Codex access helper followed by synthetic collection. Supply an
existing built sshai executable; the test uses temporary private files under `/Users/Shared`
and briefly creates synthetic deny-test canaries in the invoking user's home directory.
It removes its temporary files afterward, uses empty synthetic authentication data, and
launches no model, SSH command or retained study slot:

```sh
SSHAI_TEST_CODEX_RUNTIME_SSHAI=/absolute/path/to/sshai \
  python3 -W error -m unittest discover -s scripts -p test_issue10_local_pilot.py
```

Passing this check establishes that boundary's compatibility, not live model availability,
usage/finality qualification or completion of a pilot. A previously consumed slot remains
immutable and non-retryable after a controller fix; changed source pins require prospective
preparation, not editing the old manifest or replacing the failed outcome.

## Readiness and reporting

A prospective manifest must bind this amendment, concrete source/binary/configuration
pins, exact inputs/prompts, fixed phase order, limits and assessment settings before launch.
Implementation checks and independent artifact review precede use of new launch code.
Technical readiness must be demonstrated rather than replaced with an approval boolean.
Do not certify live usage/finality from synthetic tests alone; the pilot must establish
what the actual invocation emitted and preserve any disagreement or missing evidence.

Only a completed, valid measured series supports its own token-effect report. Report
quality, audit uncertainty, failures, denominators and the original stratified paired
uncertainty analysis alongside effects. A valid pilot can justify proceeding; it cannot
justify a savings claim. Inconclusive and negative findings remain acceptable results.
