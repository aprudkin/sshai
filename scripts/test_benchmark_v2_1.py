#!/usr/bin/env python3
"""Negative-control tests for the frozen v2.1 analyzer definition."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("benchmark_v2_1.py")
assert MODULE_PATH.exists(), "benchmark_v2_1.py must implement the frozen analyzer definition"
SPEC = importlib.util.spec_from_file_location("benchmark_v2_1", MODULE_PATH)
assert SPEC and SPEC.loader
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


def completed_command(command: str, output: str, exit_code: int = 0) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": command,
            "aggregated_output": output,
            "exit_code": exit_code,
        },
    }


def assert_invalid(events: list[dict[str, object]], expected: str) -> None:
    try:
        BENCH.analyze_command_population(
            events,
            "fanout",
            {"linux-01": ("L01", "L13")},
        )
    except BENCH.AnalysisInvalid as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected AnalysisInvalid containing {expected!r}")


def main() -> None:
    marker = "# BENCH_V21_CALL=fanout:linux-01 BENCH_V21_OBS=L01,L13\nprintf ok"
    events = [
        completed_command(marker, "x" * 400),
        completed_command("printf setup", "BENCH_V21_CALL=fanout:fake " + "s" * 40_000),
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "No compact or truncated event occurred."},
        },
    ]
    population = BENCH.analyze_command_population(
        events,
        "fanout",
        {"linux-01": ("L01", "L13")},
    )
    assert population["marked_calls"] == 1
    assert population["unmarked_calls"] == 1
    assert population["observations"] == ["L01", "L13"]
    assert population["marked_tool_response_est_tokens"]["p95"] == 100
    assert population["all_tool_response_est_tokens"]["p95"] > 10_000

    assert_invalid([completed_command(marker, "ok"), completed_command(marker, "ok")], "duplicate")
    assert_invalid([], "missing")
    assert_invalid(
        [completed_command("# BENCH_V21_CALL=fanout:linux-01\nprintf ok", "ok")],
        "malformed",
    )
    assert_invalid(
        [completed_command(
            "# BENCH_V21_CALL=fanout:linux-01 BENCH_V21_OBS=L01,W01\nprintf ok",
            "ok",
        )],
        "observations",
    )
    assert_invalid(
        [completed_command(
            "# BENCH_V21_CALL=fanout:unexpected BENCH_V21_OBS=L01,L13\nprintf ok",
            "ok",
        )],
        "unexpected",
    )

    exec_events = [
        {"type": "message.compaction_hint", "message": "compact prose"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "compacted"}},
        {"type": "compacted"},
    ]
    rollout_events = [
        {"type": "response_item", "payload": {"type": "message", "content": "compacted"}},
        {"type": "compacted"},
    ]
    assert BENCH.lifecycle_cross_check(exec_events, rollout_events) == {
        "codex_exec": 1,
        "persisted_rollout": 1,
        "matched": True,
    }
    assert BENCH.lifecycle_cross_check([], rollout_events)["matched"] is False

    adjusted = BENCH.control_adjusted(1000, 200, 400, 200)
    assert adjusted == {
        "raw_margin": 800,
        "fanout_margin": 200,
        "reduction": 0.75,
        "status": "defined",
    }
    assert BENCH.control_adjusted(1000, 200, 100, 200) == {
        "raw_margin": 800,
        "fanout_margin": -100,
        "reduction": None,
        "status": "indistinguishable-from-control-floor",
    }
    assert BENCH.control_adjusted(100, 200, 300, 100)["status"] == "inconclusive"
    print("benchmark_v2_1 tests: ok")


if __name__ == "__main__":
    main()
