#!/usr/bin/env python3
"""Standalone assertions for benchmark_issue10_fixtures; run with Python 3."""

from __future__ import annotations

import copy
import json

from benchmark_issue10_fixtures import TASK_IDS, build_task, grade


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    expect(TASK_IDS == ("incident", "config-drift", "snapshot-diff"), "stable task IDs")
    for task_id in TASK_IDS:
        one = build_task(task_id)
        two = build_task(task_id)
        expect(set(one) == {"files", "prompt", "answer_schema", "expected_answer"}, f"{task_id}: API keys")
        expect(json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True), f"{task_id}: nondeterministic build")
        expect(all("\n" not in path and not path.startswith("/") for path in one["files"]), f"{task_id}: relative paths")
        expect(sum(len(text.splitlines()) for text in one["files"].values()) >= 500, f"{task_id}: realistic volume")
        schema_text = json.dumps(one["answer_schema"], sort_keys=True)
        expect(
            set(one["answer_schema"]["required"]) == set(one["answer_schema"]["properties"]),
            f"{task_id}: strict schema requires every property",
        )
        for key, value in one["expected_answer"].items():
            if isinstance(value, str):
                expect(value not in schema_text, f"{task_id}: schema leaks {key}")
        answer = copy.deepcopy(one["expected_answer"])
        result = grade(task_id, answer)
        expect(
            result == {"passed": True, "errors": []},
            f"{task_id}: correct answer rejected: {result}",
        )
        reordered = copy.deepcopy(answer)
        reordered["evidence"].reverse()
        if "changes" in reordered:
            reordered["changes"].reverse()
        expect(grade(task_id, reordered)["passed"], f"{task_id}: reordered facts rejected")
        additional_evidence = copy.deepcopy(answer)
        additional_evidence["evidence"].append({"file": next(iter(one["files"])), "line": 1})
        expect(
            grade(task_id, additional_evidence)["passed"],
            f"{task_id}: additional valid evidence rejected",
        )

        incorrect = copy.deepcopy(answer)
        string_field = next(key for key in incorrect if key != "evidence")
        incorrect[string_field] = "wrong"
        expect(not grade(task_id, incorrect)["passed"], f"{task_id}: incorrect value accepted")

        missing = copy.deepcopy(answer)
        del missing[string_field]
        expect(any("missing field" in error for error in grade(task_id, missing)["errors"]), f"{task_id}: missing field")

        extra = copy.deepcopy(answer)
        extra["surplus"] = "no"
        expect(any("unexpected field" in error for error in grade(task_id, extra)["errors"]), f"{task_id}: extra field")

        bad_types = copy.deepcopy(answer)
        bad_types[string_field] = 7
        bad_types["evidence"] = "not-a-list"
        typed_errors = grade(task_id, bad_types)["errors"]
        expect(any("must be a string" in error for error in typed_errors), f"{task_id}: string type")
        expect("evidence must be an array" in typed_errors, f"{task_id}: evidence type")

        fabricated = copy.deepcopy(answer)
        fabricated["evidence"][0]["line"] = 999999
        fabricated_errors = grade(task_id, fabricated)["errors"]
        expect(any("nonexistent file or line" in error for error in fabricated_errors), f"{task_id}: fabricated evidence rejected")
        fabricated = copy.deepcopy(answer)
        fabricated["evidence"][0]["line"] = 1
        expect(not grade(task_id, fabricated)["passed"], f"{task_id}: unrelated existing line accepted")

    config_answer = build_task("config-drift")["expected_answer"]
    for malformed_value in (None, [], {"nested": "value"}, 7):
        malformed = copy.deepcopy(config_answer)
        malformed["changes"][0]["before"] = malformed_value
        expect(not grade("config-drift", malformed)["passed"], "malformed change accepted")
    mixed = copy.deepcopy(config_answer)
    mixed["changes"].append("not-an-object")
    expect(not grade("config-drift", mixed)["passed"], "mixed malformed changes accepted")
    duplicate = copy.deepcopy(config_answer)
    duplicate["changes"].append(copy.deepcopy(duplicate["changes"][0]))
    expect(not grade("config-drift", duplicate)["passed"], "duplicate change accepted")
    duplicate_evidence = copy.deepcopy(config_answer)
    duplicate_evidence["evidence"].append(copy.deepcopy(duplicate_evidence["evidence"][0]))
    expect(not grade("config-drift", duplicate_evidence)["passed"], "duplicate evidence accepted")

    expect(grade("incident", []) == {"passed": False, "errors": ["answer must be an object"]}, "top-level type")
    try:
        build_task("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown task must fail")


if __name__ == "__main__":
    main()
    print("benchmark_issue10_fixtures tests passed")
