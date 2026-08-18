#!/usr/bin/env python3
"""Population, lifecycle, and control math for sshai benchmark protocol v2.1."""

from __future__ import annotations

import math
import re
from typing import Any


MARKER_RE = re.compile(
    r"^# BENCH_V21_CALL=(raw-control|raw|fanout-control|fanout):"
    r"([A-Za-z0-9][A-Za-z0-9._-]*) BENCH_V21_OBS="
    r"([LW][0-9]{2}(?:,[LW][0-9]{2})*)$",
    re.MULTILINE,
)


class AnalysisInvalid(ValueError):
    """The measured input violates the frozen analyzer contract."""


def percentile_nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def token_summary(values: list[int]) -> dict[str, int]:
    return {
        "p95": percentile_nearest_rank(values, 0.95),
        "max": max(values, default=0),
        "sum": sum(values),
    }


def estimated_tokens(output: str) -> int:
    return (len(output.encode("utf-8")) + 3) // 4


def analyze_command_population(
    events: list[dict[str, Any]],
    branch: str,
    declared_calls: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Analyze only completed command items and fail closed on marker drift."""
    seen: set[str] = set()
    observations: list[str] = []
    all_tokens: list[int] = []
    marked_tokens: list[int] = []
    unmarked_calls = 0

    for event in events:
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "command_execution"
        ):
            continue
        command = str(item.get("command") or "")
        output = str(item.get("aggregated_output") or "")
        output_tokens = estimated_tokens(output)
        all_tokens.append(output_tokens)
        matches = list(MARKER_RE.finditer(command))
        if not matches:
            if "BENCH_V21_" in command:
                raise AnalysisInvalid("malformed v2.1 command marker")
            unmarked_calls += 1
            continue
        if len(matches) != 1:
            raise AnalysisInvalid("command contains multiple v2.1 markers")
        marker_branch, call_id, observation_text = matches[0].groups()
        if marker_branch != branch:
            raise AnalysisInvalid(
                f"marker branch {marker_branch!r} does not match analyzed branch {branch!r}"
            )
        if call_id not in declared_calls:
            raise AnalysisInvalid(f"unexpected call ID {call_id!r}")
        if call_id in seen:
            raise AnalysisInvalid(f"duplicate call ID {call_id!r}")
        declared_observations = declared_calls[call_id]
        observed = tuple(observation_text.split(","))
        if observed != declared_observations:
            raise AnalysisInvalid(
                f"call {call_id!r} observations {observed!r} do not match "
                f"manifest {declared_observations!r}"
            )
        seen.add(call_id)
        observations.extend(observed)
        marked_tokens.append(output_tokens)

    missing = sorted(set(declared_calls) - seen)
    if missing:
        raise AnalysisInvalid(f"missing declared call IDs: {', '.join(missing)}")
    if len(observations) != len(set(observations)):
        raise AnalysisInvalid("duplicate declared observation assignment")
    return {
        "all_command_calls": len(all_tokens),
        "marked_calls": len(marked_tokens),
        "unmarked_calls": unmarked_calls,
        "observations": observations,
        "all_tool_response_est_tokens": token_summary(all_tokens),
        "marked_tool_response_est_tokens": token_summary(marked_tokens),
    }


def lifecycle_cross_check(
    codex_exec_events: list[dict[str, Any]],
    persisted_rollout_events: list[dict[str, Any]],
) -> dict[str, int | bool]:
    codex_exec = sum(1 for event in codex_exec_events if event.get("type") == "compacted")
    persisted = sum(
        1 for event in persisted_rollout_events if event.get("type") == "compacted"
    )
    return {
        "codex_exec": codex_exec,
        "persisted_rollout": persisted,
        "matched": codex_exec == persisted,
    }


def control_adjusted(
    raw: int,
    raw_control: int,
    fanout: int,
    fanout_control: int,
) -> dict[str, int | float | str | None]:
    raw_margin = raw - raw_control
    fanout_margin = fanout - fanout_control
    if raw_margin <= 0:
        status = "inconclusive"
        reduction = None
    elif fanout_margin <= 0:
        status = "indistinguishable-from-control-floor"
        reduction = None
    else:
        status = "defined"
        reduction = (raw_margin - fanout_margin) / raw_margin
    return {
        "raw_margin": raw_margin,
        "fanout_margin": fanout_margin,
        "reduction": reduction,
        "status": status,
    }
