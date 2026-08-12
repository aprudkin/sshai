#!/usr/bin/env python3
"""Focused tests for scripts/benchmark_v1_1.py."""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("benchmark_v1_1.py")
SPEC = importlib.util.spec_from_file_location("benchmark_v1_1", MODULE_PATH)
assert SPEC and SPEC.loader
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


def event_file(path: Path, branch: str, input_tokens: int, output_size: int) -> None:
    events = [{"type": "thread.started", "thread_id": f"thread-{branch}"}]
    for step_id in BENCH.expected_step_ids():
        events.append({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": f"# BENCH_STEP={step_id}\nprintf ok",
                "aggregated_output": "x" * output_size,
                "exit_code": 0,
            },
        })
    events.append({"type": "turn.completed", "usage": {"input_tokens": input_tokens}})
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def main() -> None:
    assert BENCH.percentile_nearest_rank([1, 2, 3, 100], 0.95) == 100
    assert len(BENCH.expected_step_ids()) == 36
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        raw = root / "raw.jsonl"
        sshai = root / "sshai.jsonl"
        manifest = root / "manifest.json"
        targets = [
            {"alias": "linux-a", "os": "linux"},
            {"alias": "linux-b", "os": "linux"},
            {"alias": "windows-a", "os": "windows"},
        ]
        steps = []
        for index, step_id in enumerate(BENCH.expected_step_ids()):
            target = targets[0] if index < 12 else targets[1] if index < 24 else targets[2]
            steps.append({
                "id": step_id,
                "host": target["alias"],
                "os": target["os"],
                "body": "printf ok",
                "expected_exit": 0,
            })
        manifest.write_text(json.dumps({
            "schema": "sshai-benchmark/v1",
            "frozen": True,
            "branch_order": ["raw", "sshai"],
            "targets": targets,
            "steps": steps,
        }), encoding="utf-8")
        event_file(raw, "raw", 100_000, 4000)
        event_file(sshai, "sshai", 10_000, 400)
        report = BENCH.analyze(manifest, raw, sshai, None)
        assert report["input_token_reduction"] == 0.9
        assert report["targets"]["input_reduction_ge_80pct"] is True
        assert report["targets"]["sshai_p95_lt_500"] is True
        assert report["targets"]["all_steps_observed"] is True
        assert report["targets"]["sshai_success_ge_raw"] is True
        assert report["raw"]["tool_response_est_tokens"]["p95"] == 1000
        assert report["sshai"]["tool_response_est_tokens"]["p95"] == 100
    print("benchmark_v1_1 tests: ok")


if __name__ == "__main__":
    main()
