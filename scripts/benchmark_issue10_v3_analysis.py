#!/usr/bin/env python3
"""Pure offline descriptive analysis for the Issue 10 v3 study design.

The controller is responsible for collecting records.  This module performs no
I/O and exposes only ``analyze(manifest, records)`` plus ``AnalysisInvalid``.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any


SERIES = ("M", "L", "W")
ARMS = ("baseline", "sshai")
EXECUTIONS = ("completed", "timeout", "failed")
ANSWER_STATES = ("captured", "absent", "lost")
INSTRUMENTATION = ("valid", "invalid", "unknown")
BOUNDARIES = ("compliant", "violation", "unknown")
REVIEW_STATUSES = ("assessed", "disputed")
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


class AnalysisInvalid(ValueError):
    """The input does not satisfy the v3 analysis contract."""


def _object(value: Any, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisInvalid(f"{label} must be an object")
    missing = fields - set(value)
    extra = set(value) - fields
    if missing or extra:
        raise AnalysisInvalid(
            f"{label} fields differ from the contract "
            f"(missing={sorted(missing)!r}, extra={sorted(extra)!r})"
        )
    return value


def _integer(value: Any, label: str, *, minimum: int | None = None) -> int:
    if type(value) is not int:
        raise AnalysisInvalid(f"{label} must be an integer (booleans are not integers)")
    if minimum is not None and value < minimum:
        raise AnalysisInvalid(f"{label} must be at least {minimum}")
    return value


def _enum(value: Any, label: str, allowed: tuple[str, ...]) -> str:
    if type(value) is not str or value not in allowed:
        raise AnalysisInvalid(f"{label} must be one of {allowed!r}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise AnalysisInvalid(f"{label} must be a boolean")
    return value


def _validate_manifest(manifest: Any) -> tuple[str, list[dict[str, Any]], int, int]:
    if not isinstance(manifest, dict):
        raise AnalysisInvalid("manifest must be an object")
    required_manifest = {"phase", "slots", "analysis"}
    if not required_manifest <= set(manifest):
        raise AnalysisInvalid(
            f"manifest is missing fields {sorted(required_manifest - set(manifest))!r}"
        )
    document = manifest
    phase = _enum(document["phase"], "manifest.phase", ("pilot", "measurement"))
    slots = document["slots"]
    if not isinstance(slots, list):
        raise AnalysisInvalid("manifest.slots must be an array")

    analysis = document["analysis"]
    if not isinstance(analysis, dict):
        raise AnalysisInvalid("manifest.analysis must be an object")
    required_analysis = {"bootstrap_seed", "bootstrap_resamples"}
    if not required_analysis <= set(analysis):
        raise AnalysisInvalid(
            "manifest.analysis is missing bootstrap_seed or bootstrap_resamples"
        )
    seed = _integer(analysis["bootstrap_seed"], "bootstrap_seed", minimum=0)
    resamples = _integer(
        analysis["bootstrap_resamples"], "bootstrap_resamples", minimum=1
    )
    if seed != 1010 or resamples != 10_000:
        raise AnalysisInvalid("analysis must freeze bootstrap_seed=1010 and bootstrap_resamples=10000")

    expected_tasks = range(1, 3) if phase == "pilot" else range(1, 7)
    expected_replicates = range(1, 2) if phase == "pilot" else range(1, 4)
    expected = Counter(
        (series, task, replicate, arm)
        for series in SERIES
        for task in expected_tasks
        for replicate in expected_replicates
        for arm in ARMS
    )
    expected_count = 12 if phase == "pilot" else 108
    if len(slots) != expected_count:
        raise AnalysisInvalid(f"{phase} manifest must contain exactly {expected_count} slots")

    seen_slots: set[int] = set()
    combinations: Counter[tuple[str, int, int, str]] = Counter()
    pair_members: dict[str, list[tuple[str, int, int, str]]] = {}
    validated: list[dict[str, Any]] = []
    slot_fields = {"slot", "case_id", "series", "task", "replicate", "arm", "pair_id"}
    for index, raw in enumerate(slots):
        slot = _object(raw, f"manifest.slots[{index}]", slot_fields)
        number = _integer(slot["slot"], f"manifest.slots[{index}].slot", minimum=1)
        if number in seen_slots:
            raise AnalysisInvalid(f"duplicate planned slot {number}")
        seen_slots.add(number)
        series = _enum(slot["series"], f"slot {number} series", SERIES)
        task = _integer(slot["task"], f"slot {number} task", minimum=1)
        replicate = _integer(slot["replicate"], f"slot {number} replicate", minimum=1)
        arm = _enum(slot["arm"], f"slot {number} arm", ARMS)
        case_id = slot["case_id"]
        if type(case_id) is not str or case_id != f"{series}{task:02d}":
            raise AnalysisInvalid(f"slot {number} case_id must be {series}{task:02d}")
        pair_id = slot["pair_id"]
        if type(pair_id) is not str or not pair_id:
            raise AnalysisInvalid(f"slot {number} pair_id must be a non-empty string")
        key = (series, task, replicate, arm)
        combinations[key] += 1
        pair_members.setdefault(pair_id, []).append(key)
        validated.append(dict(slot))

    if combinations != expected:
        missing = sorted((expected - combinations).elements())
        unexpected = sorted((combinations - expected).elements())
        raise AnalysisInvalid(
            f"malformed {phase} schedule (missing={missing!r}, unexpected={unexpected!r})"
        )
    for pair_id, members in pair_members.items():
        if len(members) != 2:
            raise AnalysisInvalid(f"pair_id {pair_id!r} must identify exactly two arms")
        coordinates = {(series, task, replicate) for series, task, replicate, _ in members}
        arms = {arm for _, _, _, arm in members}
        if len(coordinates) != 1 or arms != set(ARMS):
            raise AnalysisInvalid(f"pair_id {pair_id!r} does not bind one baseline/sshai pair")
    if len(pair_members) * 2 != expected_count:
        raise AnalysisInvalid("pair_id values must be unique across planned pairs")
    return phase, validated, seed, resamples


def _validate_usage(value: Any, label: str) -> dict[str, int] | None:
    if value is None:
        return None
    usage = _object(value, label, set(USAGE_FIELDS))
    validated = {
        name: _integer(usage[name], f"{label}.{name}", minimum=0)
        for name in USAGE_FIELDS
    }
    if validated["cached_input_tokens"] > validated["input_tokens"]:
        raise AnalysisInvalid(f"{label}.cached_input_tokens exceeds input_tokens")
    return validated


def _validate_review(value: Any, label: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AnalysisInvalid(f"{label} must be an object")
    required = {"diagnosis_correct", "evidence", "recommendation", "status"}
    allowed = required | {"reviewer", "reason"}
    if not required <= set(value) or not set(value) <= allowed:
        raise AnalysisInvalid(f"{label} fields differ from the review contract")
    review = value
    diagnosis = _boolean(review["diagnosis_correct"], f"{label}.diagnosis_correct")
    evidence = _integer(review["evidence"], f"{label}.evidence", minimum=0)
    recommendation = _integer(
        review["recommendation"], f"{label}.recommendation", minimum=0
    )
    if evidence > 2 or recommendation > 2:
        raise AnalysisInvalid(f"{label} scores must be in range 0..2")
    status = _enum(review["status"], f"{label}.status", REVIEW_STATUSES)
    validated = {
        "diagnosis_correct": diagnosis,
        "evidence": evidence,
        "recommendation": recommendation,
        "status": status,
    }
    for field in ("reviewer", "reason"):
        if field in review:
            if type(review[field]) is not str or not review[field].strip():
                raise AnalysisInvalid(f"{label}.{field} must be a non-empty string")
            validated[field] = review[field]
    return validated


def _quality(record: dict[str, Any], label: str) -> dict[str, Any]:
    state = record["answer_state"]
    answer = record["final_answer"]
    review = record["review"]
    if state == "captured":
        if type(answer) is not str or not answer.strip():
            raise AnalysisInvalid(f"{label}: captured answer requires a nonempty string final_answer")
    elif answer is not None:
        raise AnalysisInvalid(f"{label}: {state} answer requires final_answer=null")

    if state in {"absent", "lost"} and review is not None:
        consistent_timeout = (
            state == "absent"
            and record["execution"] == "timeout"
            and review["status"] == "assessed"
            and review["diagnosis_correct"] is False
            and review["evidence"] == 0
            and review["recommendation"] == 0
        )
        if not consistent_timeout:
            raise AnalysisInvalid(f"{label}: review contradicts unavailable final answer")

    if record["execution"] == "timeout" and state == "absent":
        return {
            "status": "failure",
            "reason": "timeout_without_final_answer",
            "diagnosis_correct": False,
            "evidence": 0,
            "recommendation": 0,
            "success": False,
        }
    if state == "lost":
        return {
            "status": "unknown",
            "reason": "final_answer_lost",
            "diagnosis_correct": None,
            "evidence": None,
            "recommendation": None,
            "success": None,
        }
    if state == "absent":
        return {
            "status": "unknown",
            "reason": "no_final_answer_without_timeout_rule",
            "diagnosis_correct": None,
            "evidence": None,
            "recommendation": None,
            "success": None,
        }
    if review is None:
        return {
            "status": "unknown",
            "reason": "review_absent",
            "diagnosis_correct": None,
            "evidence": None,
            "recommendation": None,
            "success": None,
        }
    if review["status"] == "disputed":
        return {
            "status": "unknown",
            "reason": "review_disputed",
            "diagnosis_correct": None,
            "evidence": None,
            "recommendation": None,
            "success": None,
        }
    success = (
        review["diagnosis_correct"]
        and review["evidence"] == 2
        and review["recommendation"] == 2
    )
    return {
        "status": "success" if success else "failure",
        "reason": "assessed",
        "diagnosis_correct": review["diagnosis_correct"],
        "evidence": review["evidence"],
        "recommendation": review["recommendation"],
        "success": success,
    }


def _validate_records(
    records: Any, planned_by_slot: dict[int, dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if not isinstance(records, list):
        raise AnalysisInvalid("records must be an array")
    required_fields = {
        "slot", "execution", "final_answer", "answer_state", "usage",
        "usage_complete", "instrumentation", "boundary", "review",
    }
    allowed_fields = required_fields | {"session_id"}
    result: dict[int, dict[str, Any]] = {}
    seen_sessions: set[str] = set()
    for index, raw in enumerate(records):
        if not isinstance(raw, dict):
            raise AnalysisInvalid(f"records[{index}] must be an object")
        if not required_fields <= set(raw) or not set(raw) <= allowed_fields:
            raise AnalysisInvalid(f"records[{index}] fields differ from the contract")
        record = raw
        number = _integer(record["slot"], f"records[{index}].slot", minimum=1)
        if number not in planned_by_slot:
            raise AnalysisInvalid(f"record has unexpected slot {number}")
        if number in result:
            raise AnalysisInvalid(f"duplicate record for slot {number}")
        session_id = record.get("session_id")
        if session_id is not None:
            if type(session_id) is not str or not session_id.strip():
                raise AnalysisInvalid(f"record {number} session_id must be a non-empty string")
            if session_id in seen_sessions:
                raise AnalysisInvalid(f"duplicate session_id {session_id!r}")
            seen_sessions.add(session_id)
        execution = _enum(record["execution"], f"record {number} execution", EXECUTIONS)
        answer_state = _enum(
            record["answer_state"], f"record {number} answer_state", ANSWER_STATES
        )
        if record["final_answer"] is not None and type(record["final_answer"]) is not str:
            raise AnalysisInvalid(f"record {number} final_answer must be a string or null")
        usage = _validate_usage(record["usage"], f"record {number} usage")
        usage_complete = _boolean(record["usage_complete"], f"record {number} usage_complete")
        if usage_complete and usage is None:
            raise AnalysisInvalid(f"record {number} complete usage cannot be null")
        instrumentation = _enum(
            record["instrumentation"],
            f"record {number} instrumentation",
            INSTRUMENTATION,
        )
        boundary = _enum(record["boundary"], f"record {number} boundary", BOUNDARIES)
        review = _validate_review(record["review"], f"record {number} review")
        validated = {
            "slot": number,
            "session_id": session_id,
            "execution": execution,
            "final_answer": record["final_answer"],
            "answer_state": answer_state,
            "usage": usage,
            "usage_complete": usage_complete,
            "instrumentation": instrumentation,
            "boundary": boundary,
            "review": review,
        }
        validated["quality"] = _quality(validated, f"record {number}")
        result[number] = validated
    return result


def _arm_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    known = [row for row in rows if row["quality"]["status"] != "unknown"]
    failures = sum(row["quality"]["status"] == "failure" for row in known)
    successes = sum(row["quality"]["status"] == "success" for row in known)
    complete = len(known) == len(rows)
    return {
        "planned": len(rows),
        "known": len(known),
        "successes": successes,
        "failures": failures,
        "unknown": len(rows) - len(known),
        "evidence_mean": (
            sum(row["quality"]["evidence"] for row in known) / len(rows)
            if complete else None
        ),
        "recommendation_mean": (
            sum(row["quality"]["recommendation"] for row in known) / len(rows)
            if complete else None
        ),
    }


def _metric(baseline: int, sshai: int) -> dict[str, Any]:
    return {
        "baseline": baseline,
        "sshai": sshai,
        "baseline_minus_sshai": baseline - sshai,
        "reduction_percent": (
            100.0 * (baseline - sshai) / baseline if baseline != 0 else None
        ),
    }


def _usage_metrics(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pairs:
        return None
    metrics: dict[str, Any] = {}
    for field in USAGE_FIELDS:
        baseline = sum(pair["arms"]["baseline"]["usage"][field] for pair in pairs)
        sshai = sum(pair["arms"]["sshai"]["usage"][field] for pair in pairs)
        metrics[field] = _metric(baseline, sshai)
    baseline_non_cached = sum(
        pair["arms"]["baseline"]["usage"]["input_tokens"]
        - pair["arms"]["baseline"]["usage"]["cached_input_tokens"]
        for pair in pairs
    )
    sshai_non_cached = sum(
        pair["arms"]["sshai"]["usage"]["input_tokens"]
        - pair["arms"]["sshai"]["usage"]["cached_input_tokens"]
        for pair in pairs
    )
    metrics["non_cached_input_tokens"] = _metric(baseline_non_cached, sshai_non_cached)
    return metrics


def _percentile(sorted_values: list[float], proportion: float) -> float:
    position = (len(sorted_values) - 1) * proportion
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] + fraction * (
        sorted_values[upper] - sorted_values[lower]
    )


def _bootstrap(
    pairs_by_task: dict[int, list[dict[str, Any]]], seed: int, resamples: int
) -> dict[str, Any]:
    rng = random.Random(seed)
    reductions: list[float] = []
    for _ in range(resamples):
        baseline = 0
        sshai = 0
        for task in range(1, 7):
            task_pairs = pairs_by_task[task]
            for _draw in range(3):
                pair = task_pairs[rng.randrange(3)]
                baseline += pair["arms"]["baseline"]["usage"]["input_tokens"]
                sshai += pair["arms"]["sshai"]["usage"]["input_tokens"]
        if baseline == 0:
            return {
                "available": False,
                "lower": None,
                "upper": None,
                "reason": "zero_baseline_in_a_resample",
                "seed": seed,
                "resamples": resamples,
                "percentile_method": "linear interpolation at (n-1)*p",
            }
        reductions.append(100.0 * (baseline - sshai) / baseline)
    reductions.sort()
    return {
        "available": True,
        "lower": _percentile(reductions, 0.025),
        "upper": _percentile(reductions, 0.975),
        "reason": None,
        "seed": seed,
        "resamples": resamples,
        "percentile_method": "linear interpolation at (n-1)*p",
    }


def _usage_pair(pair_rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    arms: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    for arm in ARMS:
        row = pair_rows[arm]
        arms[arm] = {
            "slot": row["slot"],
            "attempted": row["attempted"],
            "execution": row["execution"],
            "usage": row["usage"],
            "usage_complete": row["usage_complete"],
            "instrumentation": row["instrumentation"],
            "boundary": row["boundary"],
        }
        if not row["usage_complete"] or row["usage"] is None:
            reasons.append(f"{arm}:usage_incomplete")
        if row["instrumentation"] != "valid":
            reasons.append(f"{arm}:instrumentation_{row['instrumentation']}")
        if row["boundary"] != "compliant":
            reasons.append(f"{arm}:boundary_{row['boundary']}")
    declared_complete = all(
        arms[arm]["usage_complete"] and arms[arm]["usage"] is not None for arm in ARMS
    )
    qualified = declared_complete and not reasons
    shell = {"arms": arms, "declared_complete": declared_complete, "qualified": qualified,
             "qualification_reasons": reasons, "metrics": None}
    if declared_complete:
        shell["metrics"] = _usage_metrics([shell])
    return shell


def analyze(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and descriptively analyze a v3 manifest and its collected records."""
    phase, planned, seed, resamples = _validate_manifest(manifest)
    planned_by_slot = {slot["slot"]: slot for slot in planned}
    actual = _validate_records(records, planned_by_slot)

    rows: list[dict[str, Any]] = []
    for slot in sorted(planned, key=lambda item: item["slot"]):
        record = actual.get(slot["slot"])
        if record is None:
            row = {
                **slot,
                "attempted": False,
                "session_id": None,
                "execution": None,
                "answer_state": None,
                "review": None,
                "quality": {
                    "status": "unknown", "reason": "unattempted",
                    "diagnosis_correct": None, "evidence": None,
                    "recommendation": None, "success": None,
                },
                "usage": None,
                "usage_complete": False,
                "instrumentation": "unknown",
                "boundary": "unknown",
            }
        else:
            row = {
                **slot,
                "attempted": True,
                "session_id": record["session_id"],
                "execution": record["execution"],
                "answer_state": record["answer_state"],
                "review": record["review"],
                "quality": record["quality"],
                "usage": record["usage"],
                "usage_complete": record["usage_complete"],
                "instrumentation": record["instrumentation"],
                "boundary": record["boundary"],
            }
        rows.append(row)

    series_results: dict[str, Any] = {}
    for series in SERIES:
        series_rows = [row for row in rows if row["series"] == series]
        tasks: list[dict[str, Any]] = []
        quality_arms: dict[str, dict[str, Any]] = {}
        for arm in ARMS:
            arm_rows = [row for row in series_rows if row["arm"] == arm]
            quality_arms[arm] = _arm_quality(arm_rows)

        task_numbers = (1, 2) if phase == "pilot" else tuple(range(1, 7))
        for task in task_numbers:
            arm_summaries = {
                arm: _arm_quality([
                    row for row in series_rows
                    if row["task"] == task and row["arm"] == arm
                ])
                for arm in ARMS
            }
            available = all(arm_summaries[arm]["unknown"] == 0 for arm in ARMS)
            reasons: list[str] = []
            if available:
                if arm_summaries["sshai"]["failures"] > arm_summaries["baseline"]["failures"]:
                    reasons.append("more_failures")
                if arm_summaries["sshai"]["evidence_mean"] < arm_summaries["baseline"]["evidence_mean"]:
                    reasons.append("lower_evidence_mean")
                if arm_summaries["sshai"]["recommendation_mean"] < arm_summaries["baseline"]["recommendation_mean"]:
                    reasons.append("lower_recommendation_mean")
            tasks.append({
                "task": task,
                "arms": arm_summaries,
                "regression": {
                    "available": available,
                    "observed": bool(reasons) if available else None,
                    "reasons": reasons,
                },
            })

        quality_complete = all(summary["unknown"] == 0 for summary in quality_arms.values())
        criterion_reasons: list[str] = []
        if phase == "measurement" and quality_complete:
            if quality_arms["sshai"]["failures"] > quality_arms["baseline"]["failures"]:
                criterion_reasons.append("more_failures")
            if quality_arms["sshai"]["evidence_mean"] < quality_arms["baseline"]["evidence_mean"]:
                criterion_reasons.append("lower_equal_task_repeat_evidence_mean")
            if quality_arms["sshai"]["recommendation_mean"] < quality_arms["baseline"]["recommendation_mean"]:
                criterion_reasons.append("lower_equal_task_repeat_recommendation_mean")
        criterion_available = phase == "measurement" and quality_complete
        no_degradation = {
            "applicable": phase == "measurement",
            "available": criterion_available,
            "observed": (not criterion_reasons) if criterion_available else None,
            "reasons": (
                criterion_reasons
                if criterion_available
                else ["pilot_not_applicable"] if phase == "pilot" else ["unknown_quality_outcomes"]
            ),
        }

        grouped: dict[tuple[int, int, str], dict[str, dict[str, Any]]] = {}
        for row in series_rows:
            grouped.setdefault((row["task"], row["replicate"], row["pair_id"]), {})[
                row["arm"]
            ] = row
        usage_pairs: list[dict[str, Any]] = []
        for (task, replicate, pair_id), pair_rows in sorted(grouped.items()):
            pair = {"pair_id": pair_id, "task": task, "replicate": replicate}
            pair.update(_usage_pair(pair_rows))
            usage_pairs.append(pair)

        usage_tasks: list[dict[str, Any]] = []
        for task in task_numbers:
            task_pairs = [pair for pair in usage_pairs if pair["task"] == task]
            declared = [pair for pair in task_pairs if pair["declared_complete"]]
            qualified = [pair for pair in task_pairs if pair["qualified"]]
            usage_tasks.append({
                "task": task,
                "planned_pairs": len(task_pairs),
                "declared_complete_pairs": len(declared),
                "qualified_pairs": len(qualified),
                "complete_declared": len(declared) == len(task_pairs),
                "complete_qualified": len(qualified) == len(task_pairs),
                "observed_complete_pair_subset_metrics": _usage_metrics(declared),
                "complete_declared_metrics": (
                    _usage_metrics(declared) if len(declared) == len(task_pairs) else None
                ),
                "complete_qualified_metrics": (
                    _usage_metrics(qualified) if len(qualified) == len(task_pairs) else None
                ),
            })

        declared_pairs = [pair for pair in usage_pairs if pair["declared_complete"]]
        qualified_pairs = [pair for pair in usage_pairs if pair["qualified"]]
        all_declared = len(declared_pairs) == len(usage_pairs)
        all_qualified = len(qualified_pairs) == len(usage_pairs)
        complete_declared_metrics = _usage_metrics(declared_pairs) if all_declared else None
        complete_qualified_metrics = _usage_metrics(qualified_pairs) if all_qualified else None
        observed_subset_metrics = _usage_metrics(declared_pairs)
        if phase == "pilot":
            # Pilot series totals qualify the procedure only.  Keep absolute
            # values visible without emitting a measured-series percentage.
            for metrics in (observed_subset_metrics, complete_declared_metrics,
                            complete_qualified_metrics):
                if metrics is not None:
                    for value in metrics.values():
                        value["reduction_percent"] = None
        measurement_metrics = (
            complete_qualified_metrics
            if phase == "measurement" and all_qualified
            else None
        )

        if phase != "measurement":
            bootstrap = {
                "available": False, "lower": None, "upper": None,
                "reason": "pilot_not_applicable", "seed": seed,
                "resamples": resamples,
                "percentile_method": "linear interpolation at (n-1)*p",
            }
        elif not all_qualified:
            bootstrap = {
                "available": False, "lower": None, "upper": None,
                "reason": "series_lacks_all_complete_qualified_pairs", "seed": seed,
                "resamples": resamples,
                "percentile_method": "linear interpolation at (n-1)*p",
            }
        elif complete_qualified_metrics["input_tokens"]["baseline"] == 0:
            bootstrap = {
                "available": False, "lower": None, "upper": None,
                "reason": "zero_baseline_total", "seed": seed,
                "resamples": resamples,
                "percentile_method": "linear interpolation at (n-1)*p",
            }
        else:
            pairs_by_task = {
                task: [pair for pair in qualified_pairs if pair["task"] == task]
                for task in range(1, 7)
            }
            bootstrap = _bootstrap(pairs_by_task, seed, resamples)

        status_counts = {
            "instrumentation": {
                arm: dict(Counter(
                    row["instrumentation"] for row in series_rows if row["arm"] == arm
                ))
                for arm in ARMS
            },
            "boundary": {
                arm: dict(Counter(
                    row["boundary"] for row in series_rows if row["arm"] == arm
                ))
                for arm in ARMS
            },
        }
        series_results[series] = {
            "case_ids": [f"{series}{task:02d}" for task in task_numbers],
            "quality": {
                "arms": quality_arms,
                "tasks": tasks,
                "task_regressions": [
                    item["task"] for item in tasks
                    if item["regression"]["observed"] is True
                ],
                "no_observed_degradation": no_degradation,
            },
            "usage": {
                "pairs": usage_pairs,
                "tasks": usage_tasks,
                "series_summary": {
                    "planned_pairs": len(usage_pairs),
                    "declared_complete_pairs": len(declared_pairs),
                    "qualified_pairs": len(qualified_pairs),
                    "complete_declared": all_declared,
                    "complete_qualified": all_qualified,
                    "observed_complete_pair_subset_metrics": observed_subset_metrics,
                    "complete_declared_metrics": complete_declared_metrics,
                    "complete_qualified_metrics": complete_qualified_metrics,
                    "measurement_metrics": measurement_metrics,
                    "measurement_reduction_percent": (
                        measurement_metrics["input_tokens"]["reduction_percent"]
                        if measurement_metrics is not None else None
                    ),
                },
                "bootstrap_input_reduction_95_percent": bootstrap,
            },
            **status_counts,
        }

    return {
        "schema": "sshai-issue10-v3-analysis/1",
        "phase": phase,
        "experimental_claim_eligible": False,
        "experimental_claim_reason": (
            "offline descriptive analysis only; v3 capture and launch remain unqualified"
        ),
        "planned_slot_count": len(planned),
        "recorded_slot_count": len(actual),
        "unattempted_slot_count": len(planned) - len(actual),
        "slots": rows,
        "series": series_results,
    }


__all__ = ["AnalysisInvalid", "analyze"]
