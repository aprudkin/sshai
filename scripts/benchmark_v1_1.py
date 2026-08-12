#!/usr/bin/env python3
"""Prepare, run, and analyze the frozen sshai v1.1 Codex benchmark."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


REQUIRED_STEPS = 36
BRANCHES = ("raw", "sshai")
STEP_RE = re.compile(r"BENCH_STEP=([LW][0-9]{2})")


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path} must contain a JSON object")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema") != "sshai-benchmark/v1" or manifest.get("frozen") is not True:
        fail("manifest must be frozen sshai-benchmark/v1")
    targets = manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != 3:
        fail("manifest must contain exactly three targets")
    os_names = sorted(target.get("os") for target in targets if isinstance(target, dict))
    if os_names != ["linux", "linux", "windows"]:
        fail("manifest targets must be exactly two Linux and one Windows host")
    steps = manifest.get("steps")
    if not isinstance(steps, list) or len(steps) != REQUIRED_STEPS:
        fail(f"manifest must contain exactly {REQUIRED_STEPS} steps")
    ids = [step.get("id") for step in steps if isinstance(step, dict)]
    if len(ids) != REQUIRED_STEPS or len(set(ids)) != REQUIRED_STEPS:
        fail("manifest step IDs must be present and unique")
    if manifest.get("branch_order") != list(BRANCHES):
        fail(f"branch_order must be {list(BRANCHES)!r}")
    if "supersedes_manifest" in manifest:
        if manifest.get("timeout_seconds") != 180:
            fail("v2 manifest timeout_seconds must be the frozen value 180")
        if any(step.get("timeout_seconds") != 180 for step in steps):
            fail("every v2 manifest step must freeze timeout_seconds=180")


def shell_call(step: dict[str, Any], branch: str) -> str:
    step_id = step["id"]
    host = step["host"]
    body = step["body"]
    os_name = step["os"]
    delta = bool(step.get("delta"))
    marker = f"BENCH_STEP={step_id}"
    delimiter = f"SSHAI_BENCH_{step_id}"
    timeout_seconds = int(step.get("timeout_seconds", 180))
    if branch == "sshai":
        delta_args = f"--timeout {timeout_seconds} --ctx benchmark-v1.1-{host} "
        if delta:
            delta_args += "--delta "
        return (
            f"# {marker}\n"
            f"sshai run --body-file - {delta_args}{host} <<'{delimiter}'\n"
            f"{body}\n{delimiter}"
        )
    if os_name == "linux":
        remote_command = f"ssh {host} bash -s"
    else:
        remote_command = f"ssh {host} pwsh -NoProfile -NonInteractive -File -"
    command = f"perl -e 'alarm shift; exec @ARGV' {timeout_seconds} {remote_command}"
    return f"# {marker}\n{command} <<'{delimiter}'\n{body}\n{delimiter}"


def render_prompt(manifest: dict[str, Any], branch: str) -> str:
    calls = []
    for step in manifest["steps"]:
        call = shell_call(step, branch)
        calls.append(
            f"### {step['id']} expected_exit={step['expected_exit']}\n"
            f"```bash\n{call}\n```"
        )
    mechanics = (
        "Use only the exact sshai calls below. Do not print artifacts. A local `sshai q` call is "
        "allowed only when a passport cannot establish the required line count."
        if branch == "sshai"
        else
        "Use only direct raw SSH as shown. Do not filter, redirect, or truncate remote stdout. "
        "On syntax or transport failure, retry that step at most twice and retain BENCH_STEP."
    )
    return f"""Run the frozen sshai v1.1 benchmark branch `{branch}`.

This is an explicitly authorized read-only benchmark from aimem#735. It is the documented raw-SSH
exception when branch=`raw`. Do not edit repository files or change remote state. Execute all
{REQUIRED_STEPS} steps sequentially. {mechanics}

Do not add preliminary probes. Do not combine steps. Minimize commentary. After all calls, return
a compact summary listing failed step IDs, retry count, and whether all 36 steps were attempted.

{chr(10).join(calls)}
"""


def run_branch(manifest_path: Path, branch: str, output: Path) -> None:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    if branch not in BRANCHES:
        fail(f"branch must be one of {BRANCHES}")
    if output.exists():
        fail(f"refusing to overwrite existing result: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    prompt = render_prompt(manifest, branch)
    command = [
        "codex", "exec", "--json", "--color", "never",
        "--sandbox", "danger-full-access",
        "--model", manifest["model"],
        "-c", f'model_reasoning_effort="{manifest["reasoning_effort"]}"',
        "-C", manifest["repo"], "-",
    ]
    started = time.monotonic()
    with output.open("x", encoding="utf-8") as stream:
        process = subprocess.run(command, input=prompt, text=True, stdout=stream, stderr=subprocess.PIPE)
    elapsed = time.monotonic() - started
    meta_path = output.with_suffix(output.suffix + ".meta.json")
    meta = {
        "schema": "sshai-benchmark-run/v1",
        "branch": branch,
        "manifest": str(manifest_path.resolve()),
        "result": str(output.resolve()),
        "elapsed_seconds": round(elapsed, 3),
        "process_exit": process.returncode,
        "stderr": process.stderr[-4000:],
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if process.returncode != 0:
        fail(f"codex branch {branch} failed with exit {process.returncode}; see {meta_path}")


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"{path}:{number}: invalid JSON: {exc}")
        if isinstance(event, dict):
            events.append(event)
    return events


def percentile_nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def analyze_branch(path: Path, expected_exits: dict[str, int]) -> dict[str, Any]:
    events = read_events(path)
    completed = [
        event["item"] for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    outputs = [str(item.get("aggregated_output") or "") for item in completed]
    tokens = [(len(value.encode("utf-8")) + 3) // 4 for value in outputs]
    attempts: dict[str, int] = {}
    observed_exits: dict[str, int | None] = {}
    marked_tokens: list[int] = []
    unmarked = 0
    for item in completed:
        match = STEP_RE.search(str(item.get("command") or ""))
        if match:
            step_id = match.group(1)
            attempts[step_id] = attempts.get(step_id, 0) + 1
            exit_code = item.get("exit_code")
            observed_exits[step_id] = exit_code if isinstance(exit_code, int) else None
            output = str(item.get("aggregated_output") or "")
            marked_tokens.append((len(output.encode("utf-8")) + 3) // 4)
        else:
            unmarked += 1
    turns = [event for event in events if event.get("type") == "turn.completed"]
    usage = turns[-1].get("usage", {}) if turns else {}
    compactions = sum(
        1 for event in events
        if "compact" in str(event.get("type") or "").lower()
        or (
            isinstance(event.get("item"), dict)
            and "compact" in str(event["item"].get("type") or "").lower()
        )
    )
    successful_steps = sorted(
        step_id for step_id, expected in expected_exits.items()
        if observed_exits.get(step_id) == expected
    )
    return {
        "path": str(path.resolve()),
        "thread_id": next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None),
        "usage": usage,
        "command_calls": len(completed),
        "marked_steps": len(attempts),
        "missing_steps": sorted(set(expected_step_ids()) - set(attempts)),
        "exit_mismatches": {
            step_id: {"expected": expected, "observed": observed_exits.get(step_id)}
            for step_id, expected in expected_exits.items()
            if observed_exits.get(step_id) != expected
        },
        "successful_steps": len(successful_steps),
        "success_rate": len(successful_steps) / REQUIRED_STEPS,
        "retries": sum(max(0, count - 1) for count in attempts.values()),
        "unmarked_calls": unmarked,
        "tool_response_est_tokens": {
            "p95": percentile_nearest_rank(tokens, 0.95),
            "max": max(tokens, default=0),
            "sum": sum(tokens),
        },
        "marked_tool_response_est_tokens": {
            "p95": percentile_nearest_rank(marked_tokens, 0.95),
            "max": max(marked_tokens, default=0),
            "sum": sum(marked_tokens),
        },
        "explicit_compaction_mentions": compactions,
        "possible_truncation_markers": sum(
            value.lower().count("truncat") + value.lower().count("output clipped")
            for value in outputs
        ),
    }


def expected_step_ids() -> list[str]:
    return [f"L{i:02d}" for i in range(1, 25)] + [f"W{i:02d}" for i in range(1, 13)]


def analyze(manifest_path: Path, raw_path: Path, sshai_path: Path, output: Path | None) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    validate_manifest(manifest)
    expected_exits = {step["id"]: int(step["expected_exit"]) for step in manifest["steps"]}
    raw = analyze_branch(raw_path, expected_exits)
    sshai = analyze_branch(sshai_path, expected_exits)
    raw_input = int(raw["usage"].get("input_tokens") or 0)
    sshai_input = int(sshai["usage"].get("input_tokens") or 0)
    reduction = None if raw_input <= 0 else (raw_input - sshai_input) / raw_input
    raw_cached = int(raw["usage"].get("cached_input_tokens") or 0)
    sshai_cached = int(sshai["usage"].get("cached_input_tokens") or 0)
    cached_reduction = None if raw_cached <= 0 else (raw_cached - sshai_cached) / raw_cached
    raw_noncached = raw_input - raw_cached
    sshai_noncached = sshai_input - sshai_cached
    noncached_reduction = (
        None if raw_noncached <= 0 else (raw_noncached - sshai_noncached) / raw_noncached
    )
    raw_visible = raw["marked_tool_response_est_tokens"]["sum"]
    sshai_visible = sshai["marked_tool_response_est_tokens"]["sum"]
    visible_reduction = None if raw_visible <= 0 else (raw_visible - sshai_visible) / raw_visible
    report = {
        "schema": "sshai-benchmark-analysis/v1",
        "raw": raw,
        "sshai": sshai,
        "input_token_reduction": reduction,
        "cached_input_token_reduction": cached_reduction,
        "noncached_input_token_reduction": noncached_reduction,
        "marked_tool_output_reduction": visible_reduction,
        "targets": {
            "input_reduction_ge_80pct": reduction is not None and reduction >= 0.80,
            "sshai_p95_lt_500": sshai["tool_response_est_tokens"]["p95"] < 500,
            "sshai_zero_compaction_mentions": sshai["explicit_compaction_mentions"] == 0,
            "sshai_success_ge_raw": sshai["success_rate"] >= raw["success_rate"],
            "sshai_quoting_debug_le_1": sshai["retries"] <= 1,
            "all_steps_observed": not raw["missing_steps"] and not sshai["missing_steps"],
        },
    }
    report["decision"] = (
        "confirmed" if all(report["targets"].values()) else "needs-work"
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if output:
        if output.exists():
            fail(f"refusing to overwrite existing analysis: {output}")
        output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--branch", choices=BRANCHES, required=True)
    run_parser.add_argument("--output", type=Path, required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--manifest", type=Path, required=True)
    analyze_parser.add_argument("--raw", type=Path, required=True)
    analyze_parser.add_argument("--sshai", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run_branch(args.manifest, args.branch, args.output)
    else:
        analyze(args.manifest, args.raw, args.sshai, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
