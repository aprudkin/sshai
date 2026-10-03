# Issue 10 local measured-series controller

**Status:** Offline implementation/preparation path, not an approved or completed
measurement phase. No pilot outcome, assessment, live model availability or savings
claim is established by this controller's code or synthetic tests.

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
Historical source code is never imported for predecessor validation; this controller
has no predecessor recovery operation.

Actual commands keep gpt-5.6-sol/high, the pinned Codex controls and 600-second timeout,
ignore global user configuration/rules and deny tool network. Of study evidence, only
the current case and permitted per-slot scratch/sshai artifact root are exposed; the
profile also permits minimal/runtime and Homebrew reads and writable scratch.
Model/provider residual cache and unobserved OS activity remain evidence limits. A passed access canary is not proof
of exhaustive confinement or semantic routing.

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
python3 -W error -m unittest discover -s scripts -p 'test_issue10_local_pilot*.py'
python3 scripts/benchmark_issue10_local_series.py --help
```

Synthetic tests exercise inventory, seeded pairing, allocation, gates, per-slot access,
limits, source/input tampering, retained capture failures and one-shot behavior. They
use synthetic executables/collector subprocesses, not an installed model, real
credentials, SSH or private study roots. They do not establish live measured-phase
readiness. Independent review and actual pilot qualification precede coordinator-led
real phase preparation, preflight, approval and any model launch.
