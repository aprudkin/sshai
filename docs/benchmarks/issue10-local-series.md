# Issue 10 local measured-series controller

**Scope:** Prospective observer-capacity recovery and intercepted-patch continuation
following the first five original diagnostic executions. This reference describes
launch gates, not phase completion. Code or synthetic tests alone establish no phase
approval, pilot outcome, assessment, live model availability or savings claim.
The separate [completed local-series report](issue10-local-measured-results.md)
records all 36 diagnostic outcomes and six model-assessment batches, including
retained nonconformance and unavailable conforming-series uncertainty. It does not
rewrite this preparation reference or authorize a remote phase.

The [protocol](issue10-protocol.md) and [current amendment](issue10-methodology-amendment.md)
remain authoritative. The coordinator must review the local pilot's access, capture,
usage, answers, tool audit and independent model assessments before preparing and
approving a real measured phase. Four consumed local pilot outcomes, including the
two retained failures, remain excluded from measurement; none is replaced.

The [reviewed pilot checkpoint](issue10-local-pilot-results.md), also source-pinned by
preparation, permits preparation only. Its positive technical evidence path covers
both M01 arms and a separate model assessment, not four successful pilot answers.
M02 live large-case capture/assessment remains an explicit coverage gap. Advancement
is not based on favorable grades or token effects; measurement launch still needs
reviewed code, fresh all-six-case access readiness and a separate manifest-bound
approval record under the existing task authorization.

## Fixed population and schedule

`scripts/benchmark_issue10_local_series.py` identifies a separate local measured
population: M01–M06 × three repetitions × baseline/sshai = **36 sessions**. It takes
the M-series projection of the existing v3 measurement schedule with seed 1010,
preserves its relative pair order and original protocol slot identifiers, and assigns
local slot numbers 1–36. Paired arms remain adjacent; nine pairs start in each arm.
The seed, cases, repetition count and sample size are not configurable launch options.

The original ceiling remains **120 diagnostic sessions**: four consumed local pilot
slots, 36 allocated local measurement slots, eight remote pilot slots and 72 remote
measurement slots. This is allocation accounting, not a new global budget service,
provider token cap, quota increase or claim that remote phases are authorized. Local
assessment has six same-task batches within the separate 24-context study ceiling.
No paid API fallback, model substitution, automatic replacement or effect-based
stopping is supported. This controller does not grade answers or estimate effects.

## Preparation and launch gates

- `prepare` takes explicit fixtures, native Codex and built sshai executables,
  configuration, model catalog, tool controls, dedicated authentication-file path,
  and assessment instructions/rubric. It freezes source/protocol/documentation pins,
  exact input/prompt/catalog/control/assessment bytes, canonical configuration,
  binaries, schedule and limits in a new private root. It reads no credential contents
  and runs only bounded version/help probes, not a model.
- `preflight` is one-shot no-model access qualification of all six selected cases,
  under the same access-profile builder and protected-root policy as actual slots.
  It consumes no diagnostic slot. Failed readiness is retained and cannot be repaired
  by overwriting it.
- `run-slot` requires a matching successful preflight, a separate private manifest-
  bound approval and `--allow-model-run`. Approval must contain the actual task
  authorization and a descriptive provenance record for the coordinator's passed
  local-pilot review. These are caller records, not automatically verified human
  approval, model grades or backend qualification. The generated approval template
  is false/pending; preparation never manufactures an approval.
- Each slot is reserved exactly once before fresh path-specific access canaries.
  Only the next original slot may execute. Every prior slot needs a retained result
  whose technical continuation gate permits proceeding. Capture overflow/loss,
  unsafe fixture changes, invalid capture evidence and unsupported/unallowed tool
  records stop later reservations. Non-tool CLI error items also block qualification,
  including observable model-rerouting warnings: a requested model context alone does
  not exclude substitution. Rollout model/effort contexts must be present and match
  the frozen model settings. Raw process outcomes, answers and usage remain retained;
  a warning does not manufacture a process failure or a grade. Quality or favorable
  token effects do not control execution order. Failed or interrupted slots cannot
  retry, overwrite or resume.

Execution composes the reviewed current pilot helpers rather than enabling historical
`v3 run-one`. `_fixture_inventory(..., cases=...)` accepts only bounded unique known
local case identifiers; its pilot default remains M01/M02. `collect_reserved_slot`
verifies the retained manifest, supplied approval and existing one-shot reservation
before provisioning, then reuses the same bounded capture/audit/failure finalization.
The series controller owns its distinct schemas, phase gates, readiness and ordering.
Historical source code is never imported for predecessor validation. The two
predecessor operations below are narrowly pinned; neither is generic resume or retry.

Actual commands keep gpt-5.6-sol/high, the pinned Codex controls and 600-second timeout,
ignore global user configuration/rules and deny tool network. Of study evidence, only
the current case and permitted per-slot scratch/sshai artifact root are exposed; the
profile also permits minimal/runtime and Homebrew reads and writable scratch.
Model/provider residual cache and unobserved OS activity remain evidence limits. A passed access canary is not proof
of exhaustive confinement or semantic routing.

## Prospective observer capacity

New manifests explicitly freeze 8 MiB **per** stdout stream, stderr stream and native
rollout candidate, and 4 MiB per JSONL record. The actual incremental subprocess
reader, rollout identity/copy collector, adapter, completion comparison and model
notice guard receive the same explicit capacity. No runtime constant patching is
used. Legacy defaults remain 1,000,000 bytes per process stream, 1 MiB per capture
and 256 KiB per JSONL record; historical validation uses those unchanged defaults.
Prompt, delivered-answer, model/assessor configuration and assessment-packet bounds
are unchanged, as are record/candidate/discovery counts and the 600-second deadline.

Native `CommandExecution` records can duplicate raw stdout in `stdout` and
`aggregated_output` while also carrying shorter `formatted_output`. CLI aggregated
output can likewise exceed the old line cap without OS stream overflow. A native
model-facing output-truncation notice is retained session behavior, not an observer
stdout-overflow flag. New bounds are finite: oversize streams, records or candidates
still retain failed outcomes and block subsequent original reservations.

## One pinned size-only capture recovery

`prepare-capture-recovery ROOT --predecessor ORIGINAL --source-snapshot SNAPSHOT
--reason TEXT --authorization-note TEXT` prepares a new private sibling; it launches
no model. This exception accepts only the original `4d283d77f4289d91dff1462a45629a32b8aeb1e1`
source pins and exactly its consumed original slots 1–3. It delegates only still
unreserved original slots 4–36, with the same schedule, case/prompt semantics, model,
assessment rubric, resource allocation and four consumed diagnostic-pilot outcomes.
It never retries any of slots 1–3 or resets the 36-outcome denominator.

Validation hashes the source snapshot without importing it and checks all-six-case
original readiness, reservation/request/prompt/config associations, exact typed
producer inventories, unchanged original results, healthy complete nonoverflow
process streams, one genuine CODEX_HOME candidate per slot, matching identities,
fixed model/effort contexts, cumulative CLI/native usage and final answer bytes.
Slots 1–2 must remain clean original collections. Slot 3 must be precisely the old
CLI line-cap/native capture-cap defect with an intact unique original native
candidate and an empty original collector rollout copy. Reparse at prospective
bounds must have no issues, compaction, error/rerouting notice, unsupported/unallowed
record or other delivery/access/process failure. Eligibility uses capture consistency,
not particular token-counter values, quality or favorable outcomes.

The original trees and unknown/incomplete/capture-blocked flags stay unchanged. The
only predecessor write is an atomic exclusive `capture-recovery-owner.json` claim,
bound to the new root and inherited evidence. The new root retains a supplementary
native copy, capture report, completion comparison and tool audit with bound hashes.
Slot 3 acquisition is explicitly **late**: the oversized original file had no
collector-time retained byte hash. A new hash must never be presented as an earlier
collection hash or proof that the original collection was clean. External finality,
routing qualification and subsequent model correctness grades remain separate;
recovery does not automatically upgrade them.

Before any slot 4–36 launch, the new root needs fresh all-six-case no-model preflight,
its own manifest-bound actual task approval and `--allow-model-run`, then fresh
per-slot canaries. Any other failure is refused rather than skipped; later failures
cannot invoke this exception again. Combined summaries retain all 36 outcomes,
including all three immutable original outcomes and slot 3's explicit supplementary
acquisition limitation. A failed/interrupted preparation can leave its exclusive
claim or partial new root retained; there is no automatic ownership rollback or
replacement continuation.

## Intercepted scratch patches and the remaining-slot continuation

Codex 0.151.0 can intercept a literal `apply_patch` command submitted through
`exec_command` before normal process execution. The existing call identity then
appears in a native `FileChange` lifecycle and a separately identified CLI
`file_change` lifecycle. Hiding the dedicated patch tool does not remove this path;
see the [pinned handler](https://github.com/openai/codex/blob/78c290807ce710180111df227df3b7a4fe845452/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs#L355-L388).
These representations must not be counted as distinct patches or OS executions.

The prospective `intercepted_patch_profile` qualifies bounded recorded lifecycles
at the local collector/audit boundary. Version 1 handles a single literal update
that fills an empty scratch helper. Version 2 additionally handles the evidenced
literal `Add File` form; it does not reinterpret a version-1 manifest or report.
Both require a quoted heredoc with only added lines, a relative non-hidden target
outside scratch `sshai-root` and `tmp`, no symlink components, and a retained file
matching the body. The recorded `exec_command` request, native call identity and
completed update diff or add content, and CLI start/completion kind/target/status
must agree. Repeated ambiguous targets, nonempty-file updates, delete/move operations
and other syntax remain unsupported. This is not blanket permission for file changes,
dedicated patch tools or fixture changes.
Semantic routing still requires a separate inspection of actual commands and body
execution. Preparing a scratch script is different from reading fixtures; a saved
artifact read remains permitted. Historical adapter and audit defaults are unchanged.

`prepare-patch-continuation ROOT --predecessor PREDECESSOR --source-snapshot SNAPSHOT
--qualification RECEIPT --reason TEXT --authorization-note TEXT` accepts only two
explicit predecessor paths:

- The pinned `7b201d1` size-recovery root containing only original slot 4, with
  unchanged size-recovery ancestry for slots 1–3. Binding schema 1 and profile 1
  inherit slots 1–4 and delegate only unreserved original slots **5–36**. Slot 4
  retains the evidenced empty-helper update and its original audit-blocked result.
- The pinned `8c7cdc8` patch-continuation root containing only original slot 5, with
  that same earlier ancestry. Binding schema 2 and profile 2 inherit slots 1–5 and
  delegate only unreserved original slots **6–36**. Slot 5 retains the evidenced
  helper add and its original version-1 audit-blocked result. All four producing
  roots remain distinct; no original result or profile is upgraded in place.

These are not general unsupported-tool waivers. Each separately accepted qualification
is source-bound provenance, not an approval boolean or a substitute for validating
the actual retained records.

The new manifest binds the historical sources, complete consumed prefix, original
schedule and unchanged model/configuration, inputs, prompts, rubric and ceilings.
The `patch_continuation.ancestor_binding_sha256` hashes the direct predecessor's
`capture_recovery` in schema 1 or `patch_continuation` in schema 2, rather than
copying a top-level owner binding to the new root. Each load revalidates the fixed
predecessor chain and its original owners. A sole exclusive
`patch-continuation-owner.json` binds ownership of remaining slots. Interrupted
preparation may leave a consumed claim or partial root; no automatic replacement
or ownership rollback is provided.
No consumed slot may be retried, replaced, renumbered or treated as unattempted.
Original audit, finality and eligibility flags remain unchanged; combined reporting
retains all 36 outcomes and the distinct supplementary-evidence limitations.

Preparation launches no model. Fresh all-six-case readiness, a new manifest-bound
actual approval and `--allow-model-run` precede the first delegated slot (5 or 6);
fresh per-slot canaries and ordinary prior-result gates apply afterward. A new
unsupported event or other capture/access failure still stops later reservations.
These paths do not permit arbitrary continuation chains, other consumed prefixes
or reuse of any predecessor allocation.

## Retained inventory and reporting

Every summary includes all 36 planned slots. Absent directories are `unattempted`;
a reserved directory missing its result is `reserved-incomplete`, with launch
`attempted-or-unknown`, unknown execution, retry disabled and continuation blocked.
Existing results remain unchanged and are summarized using the current bounded local
result contract. No partial series is relabelled as a completed measurement.

Answer finality, semantic routing, quality, assessment and usage qualification remain
explicitly unknown where evidence is missing. Accepted record signatures are not
compliance proof. Diagnostic session usage includes guidance, errors and permitted
follow-up retrieval; no bytes-to-token estimate or savings/grade claim is emitted.
Private raw artifacts and identifiers are not included in sanitized summaries. Retain
raw evidence under the amendment's private retention and reviewed-publication policy.

## Checks and coordinator handoff

```sh
python3 -W error -m unittest discover -s scripts -p 'test_issue10_local_series.py'
python3 -W error -m unittest discover -s scripts -p 'test_issue10_observer_capacity.py'
python3 -W error -m unittest discover -s scripts -p 'test_issue10_local_series_recovery.py'
python3 -W error -m unittest discover -s scripts -p 'test_issue10_intercepted_patch.py'
python3 -W error -m unittest discover -s scripts -p 'test_issue10_patch_continuation.py'
python3 -W error -m unittest discover -s scripts -p 'test_issue10_local_pilot*.py'
python3 scripts/benchmark_issue10_local_series.py --help
```

Synthetic tests exercise inventory, seeded pairing, allocation, gates, per-slot access,
limits, source/input tampering, retained capture failures and one-shot behavior. They
use synthetic executables/collector subprocesses, not an installed model, real
credentials, SSH or private study roots. They do not establish live measured-phase
readiness. Independent review and actual pilot qualification precede coordinator-led
real phase preparation, preflight, approval and any model launch.
