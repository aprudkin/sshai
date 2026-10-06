# Issue 10: local autonomous-session results

**Report date:** 2026-10-06. **Scope:** local macOS synthetic snapshots only.
The 36-session local allocation and six model-assessment batches are complete.
Linux and Windows/SSH phases were not launched; the overall multi-platform study
is not complete.

## Result in brief

**No cumulative-input saving was observed in this local cohort.** Across all
18 original pairs, sshai used **2,514,333 input tokens**, versus **1,330,784** for
ordinary command execution: **1,183,549 more, or 88.9% higher**.
These are complete-population **descriptive totals**, not a protocol-qualified
conforming-comparison estimate or evidence of a general causal effect.

The separate model assessor marked all 18 answers in each arm successful, with
mean evidence and recommendation scores of 2/2 in both arms. Thus no degradation
was observed on these model-assessed scales. This is not human validation, proof
of equal quality, or a non-inferiority result.

One sshai session attempted a write in the read-only fixture directory. The request
was denied and fixture bytes remained unchanged. The outcome, usage and model grade
are retained, but the session is not labelled conforming. Only **17/18 pairs** have
fully supported amended conformance; the prescribed conforming-series estimate
and bootstrap interval are therefore **unavailable**. No cleaner subset replaces
the original population.

## Conditions and population

The [protocol](issue10-protocol.md), [methodology amendment](issue10-methodology-amendment.md)
and [local controller contract](issue10-local-series.md) define the experiment.
The [pilot](issue10-local-pilot-results.md) is a separate population.

| Condition | Frozen local study setting |
| --- | --- |
| Model | `gpt-5.6-sol`, high reasoning, existing subscription |
| Codex | Native CLI 0.151.0; inspected source revision `78c290807ce710180111df227df3b7a4fe845452` |
| sshai | Declared revision `ad1532bab50915787adde31d348057c23272cb0d`; executable hash retained privately |
| Tasks | M01–M06; three paired repetitions per task |
| Population | 36 measured sessions, 18 per arm; four pilot outcomes excluded |
| Schedule | Seed 1010, fixed local projection; paired arms adjacent, nine pairs start each arm |
| Session boundary | Fresh isolated session/configuration/scratch; 600-second diagnostic bound |
| Baseline | Ordinary execution tools, including realistic filtering and output limits |
| sshai arm | New fixture diagnostics through local sshai; permitted scratch preparation and saved-artifact processing |
| Assessment | Six fresh same-task contexts, six original answers per batch, unchanged model and rubric |

The agent chose its commands rather than following a prescribed command list.
Branch guidance, mistakes, help requests, recovery, repeated queries, saved-output
reads and the final response all remain in diagnostic usage. Prompts, fixtures,
model/account settings, schedule, rubric and allocation were not revised to obtain
a favorable result. There were no replacement sessions or assessor retries.

## Complete-population token totals

Each session contributes its final cumulative counters once. Cached input is already
included in input; it is neither added again nor subtracted from the primary metric.
The non-cached row is the explicitly secondary difference `input - cached input`.

| Metric, 18 outcomes per arm | Baseline | sshai | sshai relative to baseline |
| --- | ---: | ---: | ---: |
| Cumulative input tokens | 1,330,784 | 2,514,333 | +88.9% |
| Cached input tokens, included above | 968,064 | 2,061,568 | +113.0% |
| Non-cached input tokens | 362,720 | 452,765 | +24.8% |
| Output tokens | 49,892 | 77,880 | +56.1% |

These counters do not measure peak context occupancy or monetary cost. No provider
billing or hidden backend revision was established, and no byte-to-token estimate
is substituted for actual input usage.

### Per-task input totals

Each row retains all three original pairs, including the nonconforming M01 outcome.
All task rows are descriptive, not selected conforming-subset estimates.

| Task | Scenario | Baseline input | sshai input | Change |
| --- | --- | ---: | ---: | ---: |
| M01 | Healthy after restart | 97,096 | 160,248 | +65.0% |
| M02 | Read-only dependency | 334,480 | 624,755 | +86.8% |
| M03 | Active port override | 255,830 | 342,742 | +34.0% |
| M04 | Missing required template | 327,175 | 932,976 | +185.2% |
| M05 | Wrong startup path | 136,818 | 201,536 | +47.3% |
| M06 | Per-process descriptor limit | 179,385 | 252,076 | +40.5% |

The sshai arm used more input in 16/18 individual pairs and less in 2/18. Paired
`sshai - baseline` input differences range from **-19,815 to +291,032 tokens**;
the median is **+30,752.5**, and the mean is **+65,752.7**. This reports observed
dispersion, not a confidence interval or statistical-significance claim.

## Quality and assessor accounting

Success retains the original rule: correct diagnosis and evidence/recommendation
scores both equal to 2. The six independent model contexts assessed all 36 original
answers. All six response publications passed the existing schema and source-line
coordinate validation, with no missing, disputed or unassessable rows.

| Model-assessed outcome | Baseline | sshai |
| --- | ---: | ---: |
| Successful answers | 18/18 | 18/18 |
| Mean evidence score | 2.0/2 | 2.0/2 |
| Mean recommendation score | 2.0/2 | 2.0/2 |
| Unknown quality | 0/18 | 0/18 |

No task-level score regression was observed. The judge used the frozen semantic keys
and rubric, not arm labels, usage totals, trial order or prior grades. Original answer
wording was not edited and can reveal an arm: blinding is incomplete. Every score
reached the scale ceiling; grade sensitivity and human calibration remain unestablished.
Valid citation coordinates do not prove quotation accuracy or causal relevance.

The six measured assessor contexts consumed **521,528 input / 0 cached input /
26,780 output tokens**, separately from diagnostic totals. Including the earlier
pilot assessor, **7/24 allocated assessor contexts** were consumed. Diagnostic
allocation consumption is **40/120 slots**: four pilot outcomes and 36 measured
outcomes. Allocation is caller-maintained accounting, not provider billing.

## Recorded secondary observations

| Recorded quantity, 18 sessions per arm | Baseline | sshai |
| --- | ---: | ---: |
| Native response-item tool requests | 82 | 176 |
| Native function-call output messages | 82 | 176 |
| Decoded UTF-8 tool-response text bytes | 663,870 | 615,311 |
| Sum of collector-timed process durations, seconds | 1,163.1 | 1,822.9 |
| Median collector-timed process duration, seconds | 58.3 | 92.3 |
| Non-zero Codex process exits / timeouts | 0 / 0 | 0 / 0 |

Request counts use one native `ResponseItem` identity domain, not joined CLI/native
representations or unique OS executions. One request can contain multiple commands.
Byte counts sum retained string `function_call_output.output` values, including
errors and truncation notices; they are not raw process-stream bytes, tokenizer
measurements or independently observed provider-wire payloads. Durations cover the
captured Codex process, excluding preparation, access preflight, engineering review
and inter-session waits. They are not a production latency comparison.

Recorded tool-response text was slightly smaller with sshai while cumulative input
was higher. This illustrates why output bytes alone do not establish session input
savings; it does not isolate the causal contribution of guidance, extra interactions
or caching. Session-internal recovery and artifact follow-ups remain in the evidence
and usage, but no separate aggregate retry/follow-up classifier was qualified.
Unique OS execution counts, peak context occupancy and monetary effects remain
unavailable. No recorded compaction or model-rerouting event was observed.

## Retained exceptions and evidence limits

- **Original slot 3:** the old observer exceeded its record/native-file limits.
  Its original empty native copy and incomplete/blocked flags remain. A separately
  retained native source supports finality and cumulative usage, with explicitly
  **late acquisition** and no collector-time retained native byte hash.
- **Original slots 4 and 5:** pinned Codex intercepted `exec_command` scratch-helper
  patches as native `FileChange` / CLI `file_change`. Update and Add were qualified
  separately. Their original audit-blocked outcomes remain; reviewed prospective
  controllers delegated only still-unreserved slots through exclusive ownership,
  fresh access preflight and manifest-bound task approval.
- **Slot 28, M01:** the fixture-directory write request was denied, with no observed
  fixture mutation. Its request/output lacks a command lifecycle. Subsequent scratch
  preparation and diagnostic reads used sshai, but the denied boundary attempt remains
  explicit nonconformance, not a clean result or a fabricated wrong-answer grade.
- **Slot 22:** one baseline request has a recorded exit-1 output but no native/CLI
  command lifecycle. Runtime-library denial and shell failure remain visible.
  Argument-level routing and final-answer evidence do not attest that missing lifecycle.
- Tool usage errors, denied runtime-library loads, expected non-zero diagnostic
  statuses and model-facing output truncation were retained. They were not removed
  from token totals or treated as replacement opportunities.
- Before measured assessment, prospective assessor manifest 2 fixed native capacity
  at 8 MiB/file and 4 MiB/record because the producer records each complete input twice.
  All six actual packets stayed below 1 MiB; M02 was 924,847 bytes and M04 contained
  369 physical fixture files. The actual M02 native capture was 1,960,623 bytes and
  was retained, not truncated. Response eligibility remained 64 KiB.

The audit unit is bounded recorded requests/lifecycles plus verified named access
restrictions, not exhaustive OS/resource observation. Source inspection and executable
hashes do not attest binary-to-source build correspondence. Backend identity/revision,
provider cache behavior and billing remain outside verified coverage.

## Uncertainty, reproducibility and conclusion

All **18/18 pairs** have qualified cumulative usage, but only **17/18** meet the
amended conformance condition. Accordingly the analysis leaves the conforming-series
metrics and prescribed 95% stratified paired-bootstrap interval unavailable. It does
not silently drop the nonconforming pair, create a substitute interval, replace the
session, or set experimental claim eligibility true. The prescribed procedure remains
10,000 paired resamples within the six fixed tasks, seed 1010 and linear percentiles;
even when applicable, it does not establish task-population generalization or equivalence.

A reviewed private offline adapter reuses the frozen analyzer's pure local metric,
quality and bootstrap functions, not its obsolete 108-slot M/L/W qualification envelope.
It binds the four producing roots, exact answers, qualifications, six packet owners,
actual assessor responses and replayed bridge-validation publications. The original
controller flags and historical v1.1/v2.1 artifacts are not rewritten. Private raw
records, identifiers, answers, owner maps, receipts and analysis outputs remain retained
without public upload; the sanitized pair table below permits independent checking
of the reported primary arithmetic.

The supported conclusion is narrow: **this local autonomous-session cohort did not
show input-token savings, while the model judge reported no task/evidence-score
degradation**. It does not establish a conforming comparative effect, equal-quality
guarantee, financial saving, remote-platform result or general result for other tasks,
models, agents or sshai versions. No README savings claim follows from this study.

## Sanitized original-pair input inventory

Numbers are actual cumulative input tokens, including cached input. Slot identifiers
are original allocation positions; every pair remains present.

| Task | Repetition | Baseline / sshai slots | Baseline input | sshai input |
| --- | ---: | --- | ---: | ---: |
| M01 | 1 | 16 / 15 | 29,433 | 67,880 |
| M01 | 2 | 27 / 28 | 19,076 | 50,461 |
| M01 | 3 | 33 / 34 | 48,587 | 41,907 |
| M02 | 1 | 12 / 11 | 99,177 | 222,037 |
| M02 | 2 | 3 / 4 | 100,024 | 243,266 |
| M02 | 3 | 30 / 29 | 135,279 | 159,452 |
| M03 | 1 | 20 / 19 | 46,081 | 151,156 |
| M03 | 2 | 17 / 18 | 150,537 | 130,722 |
| M03 | 3 | 35 / 36 | 59,212 | 60,864 |
| M04 | 1 | 7 / 8 | 101,505 | 392,537 |
| M04 | 2 | 22 / 21 | 174,171 | 367,658 |
| M04 | 3 | 31 / 32 | 51,499 | 172,781 |
| M05 | 1 | 2 / 1 | 29,522 | 44,141 |
| M05 | 2 | 6 / 5 | 57,232 | 70,673 |
| M05 | 3 | 25 / 26 | 50,064 | 86,722 |
| M06 | 1 | 14 / 13 | 51,820 | 81,313 |
| M06 | 2 | 9 / 10 | 52,532 | 82,652 |
| M06 | 3 | 24 / 23 | 75,033 | 88,111 |
