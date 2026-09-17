#!/usr/bin/env python3
"""Offline standard-library tests for the Issue 10 v3 descriptive analyzer."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("benchmark_issue10_v3_analysis.py")
SPEC = importlib.util.spec_from_file_location("benchmark_issue10_v3_analysis", MODULE_PATH)
assert SPEC and SPEC.loader
ANALYSIS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYSIS)


def manifest(phase: str = "measurement") -> dict[str, object]:
    tasks = range(1, 3) if phase == "pilot" else range(1, 7)
    replicates = range(1, 2) if phase == "pilot" else range(1, 4)
    slots = []
    number = 1
    for series in "MLW":
        for task in tasks:
            for replicate in replicates:
                pair_id = f"{series}{task:02d}-r{replicate}"
                for arm in ("baseline", "sshai"):
                    slots.append({
                        "slot": number,
                        "case_id": f"{series}{task:02d}",
                        "series": series,
                        "task": task,
                        "replicate": replicate,
                        "arm": arm,
                        "pair_id": pair_id,
                    })
                    number += 1
    return {
        "phase": phase,
        "slots": slots,
        "analysis": {"bootstrap_seed": 1010, "bootstrap_resamples": 10_000},
    }


def record(
    slot: int,
    *,
    input_tokens: int = 100,
    cached: int = 20,
    output: int = 10,
    execution: str = "completed",
    answer_state: str = "captured",
    final_answer: str | None = "answer",
    usage_complete: bool = True,
    instrumentation: str = "valid",
    boundary: str = "compliant",
    review: dict[str, object] | None = None,
) -> dict[str, object]:
    if review is None and answer_state == "captured":
        review = {
            "diagnosis_correct": True,
            "evidence": 2,
            "recommendation": 2,
            "status": "assessed",
        }
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
    }
    return {
        "slot": slot,
        "execution": execution,
        "final_answer": final_answer,
        "answer_state": answer_state,
        "usage": usage,
        "usage_complete": usage_complete,
        "instrumentation": instrumentation,
        "boundary": boundary,
        "review": review,
    }


def complete_records(plan: dict[str, object], baseline: int = 100, sshai: int = 80) -> list[dict[str, object]]:
    return [
        record(
            slot["slot"],
            input_tokens=baseline if slot["arm"] == "baseline" else sshai,
            cached=10,
        )
        for slot in plan["slots"]
    ]


def invalid(plan: dict[str, object], records: list[dict[str, object]], text: str) -> None:
    with unittest.TestCase().assertRaisesRegex(ANALYSIS.AnalysisInvalid, text):
        ANALYSIS.analyze(plan, records)


class AnalysisTests(unittest.TestCase):
    def test_complete_measurement_has_equal_case_totals_and_weighted_summary(self) -> None:
        plan = manifest()
        records = complete_records(plan)
        report = ANALYSIS.analyze(plan, records)
        self.assertFalse(report["experimental_claim_eligible"])
        self.assertEqual(report["planned_slot_count"], 108)
        for series in "MLW":
            result = report["series"][series]
            self.assertEqual(result["quality"]["arms"]["baseline"]["planned"], 18)
            self.assertEqual(result["quality"]["arms"]["sshai"]["successes"], 18)
            self.assertEqual(result["quality"]["arms"]["baseline"]["evidence_mean"], 2.0)
            self.assertTrue(result["quality"]["no_observed_degradation"]["observed"])
            summary = result["usage"]["series_summary"]
            self.assertEqual(summary["planned_pairs"], 18)
            self.assertTrue(summary["complete_qualified"])
            self.assertEqual(summary["measurement_reduction_percent"], 20.0)
            self.assertTrue(result["usage"]["bootstrap_input_reduction_95_percent"]["available"])

        # The primary ratio is a ratio of sums, not a mean of pair percentages.
        first_pair = [slot for slot in plan["slots"] if slot["pair_id"] == "M01-r1"]
        second_pair = [slot for slot in plan["slots"] if slot["pair_id"] == "M01-r2"]
        by_slot = {item["slot"]: item for item in records}
        by_slot[first_pair[0]["slot"]]["usage"]["input_tokens"] = 100
        by_slot[first_pair[1]["slot"]]["usage"]["input_tokens"] = 50
        by_slot[second_pair[0]["slot"]]["usage"]["input_tokens"] = 900
        by_slot[second_pair[1]["slot"]]["usage"]["input_tokens"] = 810
        report = ANALYSIS.analyze(plan, records)
        metric = report["series"]["M"]["usage"]["series_summary"]["measurement_metrics"]["input_tokens"]
        self.assertEqual(metric["baseline"], 2600)
        self.assertEqual(metric["sshai"], 2140)
        self.assertAlmostEqual(metric["reduction_percent"], 100 * 460 / 2600)

    def test_unknown_loss_omission_and_timeout_without_answer_are_distinct(self) -> None:
        plan = manifest("pilot")
        records = complete_records(plan)
        records = [item for item in records if item["slot"] != 1]
        lost = next(item for item in records if item["slot"] == 2)
        lost.update({"answer_state": "lost", "final_answer": None, "review": None})
        timed_out = next(item for item in records if item["slot"] == 3)
        timed_out.update({
            "execution": "timeout", "answer_state": "absent",
            "final_answer": None, "review": None,
        })
        report = ANALYSIS.analyze(plan, records)
        by_slot = {item["slot"]: item for item in report["slots"]}
        self.assertEqual(by_slot[1]["quality"]["reason"], "unattempted")
        self.assertEqual(by_slot[2]["quality"]["reason"], "final_answer_lost")
        self.assertEqual(by_slot[3]["quality"], {
            "status": "failure", "reason": "timeout_without_final_answer",
            "diagnosis_correct": False, "evidence": 0,
            "recommendation": 0, "success": False,
        })
        quality = report["series"]["M"]["quality"]
        self.assertEqual(quality["arms"]["baseline"]["failures"], 1)
        self.assertEqual(quality["arms"]["baseline"]["unknown"], 1)
        self.assertEqual(quality["arms"]["sshai"]["unknown"], 1)
        self.assertFalse(quality["no_observed_degradation"]["applicable"])

    def test_partial_scores_are_independent_and_task_regressions_are_visible(self) -> None:
        plan = manifest()
        records = complete_records(plan)
        target = next(
            item for item in records
            if item["slot"] == next(
                slot["slot"] for slot in plan["slots"]
                if slot["series"] == "M" and slot["task"] == 1
                and slot["replicate"] == 1 and slot["arm"] == "sshai"
            )
        )
        target["review"] = {
            "diagnosis_correct": True, "evidence": 0,
            "recommendation": 1, "status": "assessed",
        }
        report = ANALYSIS.analyze(plan, records)
        row = next(item for item in report["slots"] if item["slot"] == target["slot"])
        self.assertEqual(row["quality"]["evidence"], 0)
        self.assertEqual(row["quality"]["recommendation"], 1)
        self.assertEqual(row["quality"]["status"], "failure")
        quality = report["series"]["M"]["quality"]
        self.assertIn(1, quality["task_regressions"])
        self.assertFalse(quality["no_observed_degradation"]["observed"])

    def test_captured_answer_after_timeout_is_reviewed_independently(self) -> None:
        plan = manifest("pilot")
        records = complete_records(plan)
        records[0]["execution"] = "timeout"
        report = ANALYSIS.analyze(plan, records)
        row = report["slots"][0]
        self.assertEqual(row["execution"], "timeout")
        self.assertEqual(row["quality"]["status"], "success")

    def test_failed_wrong_answer_usage_is_retained(self) -> None:
        plan = manifest()
        records = complete_records(plan)
        records[0]["execution"] = "failed"
        records[0]["review"] = {
            "diagnosis_correct": False, "evidence": 1,
            "recommendation": 0, "status": "assessed",
        }
        report = ANALYSIS.analyze(plan, records)
        pair = report["series"]["M"]["usage"]["pairs"][0]
        self.assertTrue(pair["qualified"])
        self.assertEqual(pair["metrics"]["input_tokens"]["baseline"], 100)
        self.assertEqual(report["series"]["M"]["quality"]["arms"]["baseline"]["failures"], 1)

    def test_partial_usage_is_visible_but_not_replaced_or_claimed(self) -> None:
        plan = manifest()
        records = complete_records(plan)
        records[0]["usage_complete"] = False
        records[1]["instrumentation"] = "invalid"
        report = ANALYSIS.analyze(plan, records)
        pair = report["series"]["M"]["usage"]["pairs"][0]
        self.assertEqual(pair["arms"]["baseline"]["usage"]["input_tokens"], 100)
        self.assertFalse(pair["declared_complete"])
        self.assertFalse(pair["qualified"])
        summary = report["series"]["M"]["usage"]["series_summary"]
        self.assertEqual(summary["declared_complete_pairs"], 17)
        self.assertIsNone(summary["complete_declared_metrics"])
        self.assertIsNone(summary["measurement_metrics"])

    def test_accepts_coordinator_metadata_and_rejects_reused_session_ids(self) -> None:
        plan = manifest("pilot")
        plan.update({"schema": "coordinator-schema", "launch_enabled": False})
        plan["analysis"]["percentile"] = "linear interpolation at (n-1)*p"
        records = complete_records(plan)
        for index, item in enumerate(records, 1):
            item["session_id"] = f"session-{index}"
            item["review"].update({"reviewer": "operator", "reason": "rubric assessment"})
        report = ANALYSIS.analyze(plan, records)
        self.assertEqual(report["slots"][0]["session_id"], "session-1")
        pilot_summary = report["series"]["M"]["usage"]["series_summary"]
        self.assertIsNone(pilot_summary["measurement_reduction_percent"])
        self.assertTrue(pilot_summary["complete_qualified"])
        self.assertTrue(all(
            metric["reduction_percent"] is None
            for metric in pilot_summary["complete_qualified_metrics"].values()
        ))
        records[1]["session_id"] = "session-1"
        invalid(plan, records, "duplicate session_id")

    def test_rejects_bool_negative_duplicate_unexpected_and_contradictory_values(self) -> None:
        plan = manifest("pilot")
        records = complete_records(plan)
        bad = copy.deepcopy(records)
        bad[0]["usage"]["input_tokens"] = True
        invalid(plan, bad, "booleans")
        bad = copy.deepcopy(records)
        bad[0]["usage"]["output_tokens"] = -1
        invalid(plan, bad, "at least 0")
        invalid(plan, records + [copy.deepcopy(records[0])], "duplicate record")
        bad = copy.deepcopy(records)
        bad[0]["slot"] = 999
        invalid(plan, bad, "unexpected slot")
        bad = copy.deepcopy(records)
        bad[0].update({
            "execution": "timeout", "answer_state": "absent", "final_answer": None,
            "review": {"diagnosis_correct": True, "evidence": 2,
                       "recommendation": 2, "status": "assessed"},
        })
        invalid(plan, bad, "contradicts")
        bad = copy.deepcopy(records)
        bad[0]['final_answer'] = '   '
        invalid(plan, bad, 'nonempty')

    def test_bootstrap_is_deterministic(self) -> None:
        plan = manifest()
        records = complete_records(plan)
        # Vary complete whole pairs so the interval has nonzero width.
        for slot, item in zip(plan["slots"], records, strict=True):
            item["usage"]["input_tokens"] += slot["replicate"] * (
                17 if slot["arm"] == "baseline" else 7
            )
        first = ANALYSIS.analyze(plan, records)
        second = ANALYSIS.analyze(plan, copy.deepcopy(records))
        for series in "MLW":
            interval = first["series"][series]["usage"]["bootstrap_input_reduction_95_percent"]
            self.assertEqual(
                interval,
                second["series"][series]["usage"]["bootstrap_input_reduction_95_percent"],
            )
            self.assertLess(interval["lower"], interval["upper"])

    def test_bootstrap_rejects_all_zero_and_any_zero_denominator_resample(self) -> None:
        plan = manifest()
        records = complete_records(plan, baseline=0, sshai=0)
        for item in records:
            item["usage"]["cached_input_tokens"] = 0
        report = ANALYSIS.analyze(plan, records)
        interval = report["series"]["M"]["usage"]["bootstrap_input_reduction_95_percent"]
        self.assertEqual(interval["reason"], "zero_baseline_total")
        self.assertIsNone(
            report["series"]["M"]["usage"]["series_summary"]
            ["measurement_reduction_percent"]
        )

        # Each task has one positive-baseline pair and two zero pairs.  The
        # frozen random stream produces at least one all-zero resample; it may
        # not be silently discarded.
        for slot, item in zip(plan["slots"], records, strict=True):
            if slot["arm"] == "baseline" and slot["replicate"] == 1:
                item["usage"]["input_tokens"] = 100
        report = ANALYSIS.analyze(plan, records)
        interval = report["series"]["M"]["usage"]["bootstrap_input_reduction_95_percent"]
        self.assertEqual(interval["reason"], "zero_baseline_in_a_resample")
        self.assertFalse(interval["available"])

    def test_rejects_malformed_schedules(self) -> None:
        plan = manifest()
        bad = copy.deepcopy(plan)
        bad["slots"][0]["case_id"] = "M02"
        invalid(bad, [], "case_id")
        bad = copy.deepcopy(plan)
        bad["slots"][1]["pair_id"] = "unpaired"
        invalid(bad, [], "exactly two arms")
        bad = copy.deepcopy(plan)
        bad["slots"][0]["task"] = 2
        bad["slots"][0]["case_id"] = "M02"
        invalid(bad, [], "malformed measurement schedule")
        bad = copy.deepcopy(plan)
        bad["slots"][1]["slot"] = bad["slots"][0]["slot"]
        invalid(bad, [], "duplicate planned slot")


if __name__ == "__main__":
    unittest.main()
