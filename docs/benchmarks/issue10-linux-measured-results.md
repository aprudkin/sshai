# Issue 10: Linux autonomous-session results

**Report date:** 2026-10-09. **Scope:** remote Linux synthetic snapshots only.
All 36 original measured diagnostic sessions completed on 2026-10-06. Independent
technical review qualified their final answers and cumulative usage. Six independent
same-task model-assessment contexts completed on 2026-10-09, and their finals and
response publications were qualified separately. Windows remains unqualified;
the overall multi-platform study is not complete.

## Result in brief

Across all 18 original pairs, cumulative input was **1,711,462 tokens with sshai
versus 1,641,366 with ordinary execution: 70,096 more, or 4.27% higher**.
These are complete-population **descriptive totals**, not a protocol-qualified
conforming-comparison estimate or evidence of a general causal effect.

The separate model assessor marked **16/18 baseline answers and 14/18 sshai answers
successful** under the frozen diagnosis/evidence/recommendation rule. Both arms had
18/18 model-assessed correct diagnoses; evidence or recommendation scores account
for the unsuccessful answers. The cohort fails the prescribed no-observed-degradation
criterion. These are model judgments, not human validation or a general quality effect.

Only **15/18 pairs** have fully supported recorded conformance. One sshai outcome
changed the supplied artifact root; two baseline outcomes attempted an unsupported
auxiliary invocation. Their original usage and final answers remain in the population.
The full conforming-series estimate and prescribed bootstrap interval are therefore
**unavailable**. No cleaner subset or replacement run is substituted.

The separate [local macOS report](issue10-local-measured-results.md) describes another
population. The studies are not pooled, and their difference is not an operating-system
or transport-effect estimate.

## Conditions and population

The [protocol](issue10-protocol.md), [methodology amendment](issue10-methodology-amendment.md)
and [remote controller contract](issue10-remote-series.md) define the experiment.
The four Linux pilot outcomes are excluded from measurement.

| Condition | Frozen Linux study setting |
| --- | --- |
| Model | `gpt-5.6-sol`, high reasoning, existing subscription |
| Codex | Native CLI 0.151.0; inspected source revision `78c290807ce710180111df227df3b7a4fe845452` |
| sshai | Declared revision `ad1532bab50915787adde31d348057c23272cb0d`; executable hash retained privately |
| Tasks | L01–L06; three paired repetitions per task |
| Population | 36 measured sessions, 18 per arm; four pilot outcomes excluded |
| Schedule | Seed 1010, original fixed Linux projection; balanced arm order |
| Session boundary | Fresh isolated session/configuration/scratch; 600-second diagnostic bound |
| Baseline | Ordinary remote execution tools, including realistic filtering and output limits |
| sshai arm | New fixture diagnostics through sshai; permitted scratch preparation and saved-artifact processing |
| Remote access | Controller-owned OpenSSH broker; per-session synthetic fixture/scratch view with privilege drop |
| Assessment | Six fresh no-tools same-task contexts; six original answers per batch; unchanged model and rubric |

Both arms investigated actual remote snapshots through the same restricted view.
The agent chose commands rather than following a prescribed command list. Guidance,
errors, help requests, recovery, repeated queries, artifact reads and final responses
remain in diagnostic usage. No attempted slot was retried, replaced or reordered.

## Complete-population token totals

Each session contributes its final cumulative counters once. Native totals agree with
CLI terminal counters and retained capture/result totals. Cached input is already
included in input; it is neither added again nor subtracted from the primary metric.
The non-cached row is the explicitly secondary difference `input - cached input`.

| Metric, 18 outcomes per arm | Baseline | sshai | sshai relative to baseline |
| --- | ---: | ---: | ---: |
| Cumulative input tokens | 1,641,366 | 1,711,462 | +4.27% |
| Cached input tokens, included above | 1,251,968 | 1,269,120 | +1.37% |
| Non-cached input tokens | 389,398 | 442,342 | +13.60% |
| Output tokens | 56,023 | 74,410 | +32.82% |

These counters do not measure peak context occupancy or monetary cost. No provider
billing or hidden backend revision was established. No byte-to-token estimate replaces
actual recorded input usage.

### Per-task input totals

Every row retains all three original pairs, including nonconforming and uncertain
outcomes. Rows are descriptive, not selected conforming-subset estimates.

| Task | Baseline input | sshai input | Change |
| --- | ---: | ---: | ---: |
| L01 | 66,006 | 134,949 | +104.45% |
| L02 | 713,613 | 524,388 | -26.52% |
| L03 | 178,629 | 222,213 | +24.40% |
| L04 | 281,613 | 352,211 | +25.07% |
| L05 | 243,852 | 238,116 | -2.35% |
| L06 | 157,653 | 239,585 | +51.97% |

The sshai arm used more input in 13/18 pairs and less in 5/18. Paired
`sshai - baseline` differences range from **-253,154 to +67,307 tokens**;
the median is **+24,098**, and the mean is **+3,894.22**. This is observed dispersion,
not a confidence interval or statistical-significance claim.

## Quality and assessor accounting

All six original same-task assessment contexts ran once. Fresh independent technical
review qualified their synchronous terminal finals, source/packet/access associations,
complete prompt retention and exact native/CLI/delivered answer joins. All six original
response publications passed frozen JSON, answer-ID and citation-coordinate validation;
all 36 rows are assessed, with none missing, invalid or disputed in those publications.
Coordinate validation does not establish quotation accuracy or causal relevance.

Success requires correct diagnosis plus evidence and recommendation scores both equal
to 2. The assessor saw both arms and all three original repetitions for its task,
with opaque answer identifiers and no arm labels, usage, trial order or previous grades.
Answer wording was unchanged and can reveal an arm: blinding is incomplete. Model
grades are not human calibration, proof of equal quality or a non-inferiority result.

| Model-assessed outcome | Baseline | sshai |
| --- | ---: | ---: |
| Correct diagnosis | 18/18 | 18/18 |
| Successful under the complete rubric | 16/18 | 14/18 |
| Unsuccessful under the complete rubric | 2/18 | 4/18 |
| Mean evidence score | 1.89/2 | 1.78/2 |
| Mean recommendation score | 2.00/2 | 1.94/2 |
| Unknown quality | 0/18 | 0/18 |

The prescribed no-observed-degradation criterion fails: the sshai arm has more
rubric-defined failures and lower mean evidence and recommendation scores. Task-level
regression flags occur for L02 and L05; L04 has 1/3 successful answers in each arm.
These are descriptive findings in the retained cohort, not a statistical or general
causal quality claim. A rubric failure is not a nonzero command exit or necessarily
an incorrect diagnosis.

### Original unsuccessful model-assessed outcomes

No answer or grade was repaired, excluded or retried. All six below retain a
model-assessed correct diagnosis; the lower score prevents complete-rubric success.

| Pair | Arm | Evidence / 2 | Recommendation / 2 |
| --- | --- | ---: | ---: |
| L02-r2 | sshai | 0 | 2 |
| L04-r1 | baseline | 1 | 2 |
| L04-r1 | sshai | 1 | 2 |
| L04-r2 | sshai | 1 | 2 |
| L04-r3 | baseline | 1 | 2 |
| L05-r2 | sshai | 2 | 1 |

The six measured assessor contexts consumed **518,677 input / 0 cached input /
37,740 output tokens**, separately from diagnostic totals. The caller ledger records
**80/120 diagnostic slots** and **15/24 assessor contexts** consumed across the study.
These counts are caller-maintained accounting, not provider billing.

## Recorded secondary observations

| Recorded quantity, 18 sessions per arm | Baseline | sshai |
| --- | ---: | ---: |
| Native response-item function requests | 89 | 132 |
| Native function-call output messages | 89 | 132 |
| Decoded UTF-8 tool-response text bytes | 607,463 | 616,150 |
| Sum of collector-timed process durations, seconds | 1,545.29 | 1,981.43 |
| Non-zero Codex process exits / timeouts | 0 / 0 | 0 / 0 |

Request counts use one native `ResponseItem` identity domain, not joined CLI/native
representations or unique OS executions. One request can contain several commands.
Byte counts sum decoded retained tool-response text, including errors and truncation
notices; they are not raw remote process bytes, tokenizer measurements or provider-wire
payloads. Durations cover captured Codex processes, not setup, access checks, assessment
or review. They are not a production latency comparison.

Session-internal recovery and artifact follow-ups remain in usage, but no separate
aggregate retry/follow-up classifier was qualified. Unique OS execution counts,
peak context occupancy and monetary effects remain unavailable. No recorded compaction
or model-rerouting event was observed.

## Retained exceptions and evidence limits

- **Original slot 18, L06-r3, sshai:** two requests changed the supplied `SSHAI_ROOT`.
  A diagnostic returned transport failure 98 with remote completion `not_started`.
  Later requests restored the supplied root. Whole-slot recorded conformance fails;
  no new-source bypass was established. The original outcome is not repaired.
- **Original slots 24 and 29, L05-r2 and L05-r1, baseline:** `ssh -V` was rejected by
  the bounded shim with exit 255 before a source diagnostic reached the broker.
  Subsequent diagnostics used the supported route. These are retained auxiliary
  failures, not evidence of a new-data side path; whole-slot conformance stays unknown.
- Other command/utility nonzero exits and recovery remain in session usage. They do
  not alone invalidate answer finality or demonstrate fixture mutation.
- Named access canaries and fixture-integrity checks are bounded evidence, not exhaustive
  filesystem observation, descendant tracing or OS-sandbox proof. Expected tool schemas
  were not introspected; source/binary pin agreement is not independent execution attestation.
- Original generic completion receipts still say finality is unknown. The independent
  terminal-event qualifications are separate, source-bound evidence; no legacy receipt
  or qualification flag was rewritten.
- Nonidentity process metadata is freshly hash-bound and checked for consistency;
  no unavailable individual historical reviewer hash is invented.
- The new offline packet builder initially accepted contradictory freshly hashed
  producing-role metadata. Independent review caught this before any assessor launch;
  corrected role/slot/prompt/request and observer-replay checks passed substitution tests
  and re-review. The prior preparation and original study outcomes remain unchanged.
- Independent analysis-adapter review also rejected an acceptance path for extra
  structural arm labels in consistently rehashed synthetic assessment packets. Reusing
  the frozen exact packet validator corrected it before grade import. Actual packets
  were canonical; no assessment was retried or regraded.
- Native assessor last-message files initially had mode 0644 beneath mode-0700 private
  directories. Initial metadata was retained, modes were tightened to 0600, and unchanged
  bytes were verified. The initial file modes are not described as 0600.
- The analyzer does not independently validate assessor access receipts. The separate
  fresh technical review qualified the named access evidence; neither implies exhaustive
  OS observation. Complete retained prompts do not prove backend attention or an
  independently established backend context window.

## All 18 original pairs

Input-token differences below are descriptive. Conformance applies to the entire
recorded pair, independently of finality, usage and model grade.

| Pair | Baseline input | sshai input | sshai minus baseline | Recorded conformance |
| --- | ---: | ---: | ---: | --- |
| L01-r1 | 19,664 | 49,239 | +29,575 | qualified, bounded |
| L01-r2 | 26,297 | 41,253 | +14,956 | qualified, bounded |
| L01-r3 | 20,045 | 44,457 | +24,412 | qualified, bounded |
| L02-r1 | 461,595 | 208,441 | -253,154 | qualified, bounded |
| L02-r2 | 120,793 | 166,921 | +46,128 | qualified, bounded |
| L02-r3 | 131,225 | 149,026 | +17,801 | qualified, bounded |
| L03-r1 | 48,188 | 72,599 | +24,411 | qualified, bounded |
| L03-r2 | 83,158 | 75,122 | -8,036 | qualified, bounded |
| L03-r3 | 47,283 | 74,492 | +27,209 | qualified, bounded |
| L04-r1 | 136,043 | 103,202 | -32,841 | qualified, bounded |
| L04-r2 | 69,965 | 106,097 | +36,132 | qualified, bounded |
| L04-r3 | 75,605 | 142,912 | +67,307 | qualified, bounded |
| L05-r1 | 77,242 | 101,027 | +23,785 | unknown |
| L05-r2 | 99,866 | 75,357 | -24,509 | unknown |
| L05-r3 | 66,744 | 61,732 | -5,012 | qualified, bounded |
| L06-r1 | 40,441 | 74,525 | +34,084 | qualified, bounded |
| L06-r2 | 54,907 | 64,213 | +9,306 | qualified, bounded |
| L06-r3 | 62,305 | 100,847 | +38,542 | failed |

## Reproducibility and publication boundary

The original schedule, synthetic task inputs, captured finals, technical qualifications,
blinded packets, owner mappings, usage records and offline analysis remain privately
retained. The public pair table permits independent descriptive arithmetic without
publishing raw rollouts, internal targets, credentials or local paths.

The separately identified Linux adapter reuses frozen pure statistics helpers, not the
historical 108-slot eligibility envelope. It binds producing-slot sources, original
independent reports and the corrected complete input publication. Assessment import
binds exact packet/owner/response/qualification and complete bridge validation provenance,
including owner and validator-source hashes. The complete qualified analysis publication
was replayed into a new private directory with byte-identical output. The public pair and
failed-outcome tables permit independent checks of usage and quality denominators.

Historical v1.1 and v2.1 protocols, manifests, analyzers and results are unchanged.
No built-in usage-statistics feature was implemented.
