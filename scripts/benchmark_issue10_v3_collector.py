#!/usr/bin/env python3
"""Bounded subprocess evidence collector for the Issue 10 v3 offline path.

This is a library, not a launch CLI.  It has no Codex-, sshai-, SSH-, auth-, or
coordinator-specific discovery.  Callers must supply the exact command,
environment, working directory, final-answer path, and a bounded list of
persisted-rollout candidates.

An immutable private attempt receipt is published before process creation.  Raw
process evidence and delivery outcomes are then published as new files.  This
keeps timeout, overflow, start-failure, missing-answer, and rollout-selection
evidence available even when a later adapter/coordinator import refuses it.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import stat
from typing import Any, Mapping, Sequence

import benchmark_issue10 as legacy
import benchmark_issue10_v3_capture as capture_adapter

ATTEMPT_SCHEMA = "sshai-benchmark/issue10-v3-collector-attempt-1"
PROCESS_SCHEMA = "sshai-benchmark/issue10-v3-collector-process-1"
DELIVERY_SCHEMA = "sshai-benchmark/issue10-v3-collector-delivery-1"
MAX_PROMPT_BYTES = 1_048_576
MAX_ROLLOUT_CANDIDATES = 32
MAX_STREAM_BYTES = legacy.MAX_CAPTURE


class CollectorInputError(ValueError):
    """The requested collection has unsafe paths or invalid bounds."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _private_new_directory(path: Path) -> Path:
    try:
        physical = legacy._physical(Path(path), must_exist=False)
        legacy._new_dir(physical)
    except (OSError, ValueError) as exc:
        raise CollectorInputError(str(exc)) from exc
    if stat.S_IMODE(physical.stat().st_mode) != 0o700:
        raise CollectorInputError(f"collector directory is not private: {physical}")
    return physical


def _existing_directory(path: Path, label: str) -> Path:
    try:
        physical = legacy._physical(Path(path))
    except (OSError, ValueError) as exc:
        raise CollectorInputError(f"unsafe {label}: {exc}") from exc
    if not physical.is_dir():
        raise CollectorInputError(f"{label} is not a directory: {physical}")
    return physical


def _future_source(path: Path, label: str) -> Path:
    try:
        return legacy._physical(Path(path), must_exist=False)
    except (OSError, ValueError) as exc:
        raise CollectorInputError(f"unsafe {label}: {exc}") from exc


def _validate_request(
    attempt_dir: Path,
    argv: Sequence[str],
    prompt: bytes,
    env: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float,
    rollout_candidates: Sequence[Path],
    answer_path: Path | None,
) -> tuple[Path, list[str], dict[str, str], Path, float, list[Path], Path | None]:
    if (not isinstance(argv, Sequence) or isinstance(argv, (str, bytes))
            or not argv or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)):
        raise CollectorInputError("argv must be a nonempty sequence of nonempty strings")
    if not isinstance(prompt, bytes) or len(prompt) > MAX_PROMPT_BYTES:
        raise CollectorInputError(f"prompt must be bytes bounded to {MAX_PROMPT_BYTES}")
    if not isinstance(env, Mapping):
        raise CollectorInputError("env must be an explicit string mapping")
    environment = dict(env)
    if any(not isinstance(key, str) or not isinstance(value, str) or "\x00" in key + value
           for key, value in environment.items()):
        raise CollectorInputError("env keys and values must be strings without NUL")
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise CollectorInputError("timeout_seconds must be a finite positive number")
    if (not isinstance(rollout_candidates, Sequence)
            or isinstance(rollout_candidates, (str, bytes))
            or len(rollout_candidates) > MAX_ROLLOUT_CANDIDATES):
        raise CollectorInputError(
            f"rollout_candidates must be a sequence of at most {MAX_ROLLOUT_CANDIDATES} paths"
        )

    try:
        output = legacy._physical(Path(attempt_dir), must_exist=False)
    except (OSError, ValueError) as exc:
        raise CollectorInputError(f"unsafe collector output: {exc}") from exc
    work = _existing_directory(Path(cwd), "working directory")
    candidates = [
        _future_source(Path(path), f"rollout candidate {index}")
        for index, path in enumerate(rollout_candidates, 1)
    ]
    answer = _future_source(Path(answer_path), "answer source") if answer_path is not None else None
    if answer is not None and answer.exists():
        raise CollectorInputError("answer source must not exist before process spawn")
    sources = [*candidates, *([answer] if answer is not None else [])]
    if len(set(sources)) != len(sources):
        raise CollectorInputError("rollout and answer source paths must be distinct")
    for source in sources:
        if source == output or output in source.parents:
            raise CollectorInputError("source paths must be outside the collector output directory")
    return (output, list(argv), environment, work, float(timeout_seconds), candidates, answer)


def _read_explicit_source(path: Path, limit: int) -> bytes:
    """Read exactly one supplied regular path; never search a directory."""
    try:
        physical = legacy._physical(path)
        return legacy._read_bounded(physical, limit)
    except (OSError, ValueError) as exc:
        raise CollectorInputError(str(exc)) from exc


def _cli_identity(events: bytes) -> tuple[str | None, str | None]:
    parsed = capture_adapter.parse_jsonl(events, "collector_cli_identity")
    if not parsed["complete"]:
        return None, "malformed_cli_jsonl"
    starts = [
        record
        for record in parsed["records"]
        if isinstance(record, dict) and record.get("type") == "thread.started"
    ]
    if not starts:
        return None, "missing_cli_thread_identity"
    identities = [record.get("thread_id") for record in starts]
    if any(not isinstance(item, str) or not item for item in identities):
        return None, "invalid_cli_thread_identity"
    if len(identities) != 1:
        return None, "ambiguous_cli_thread_identity"
    return identities[0], None


def _rollout_identity(data: bytes) -> tuple[str | None, str, list[str]]:
    parsed = capture_adapter.parse_jsonl(data, "collector_rollout_candidate")
    issue_codes = [item["code"] for item in parsed["issues"]]
    metadata = [
        record for record in parsed["records"]
        if isinstance(record, dict) and record.get("type") == "session_meta"
    ]
    if not parsed["complete"]:
        return None, "malformed", issue_codes
    identities = []
    for record in metadata:
        payload = record.get("payload")
        identity = payload.get("id") if isinstance(payload, dict) else None
        if not isinstance(identity, str) or not identity:
            return None, "invalid_identity", issue_codes
        identities.append(identity)
    if len(identities) == 0:
        return None, "missing_identity", issue_codes
    if len(identities) != 1:
        return None, "ambiguous_identity", issue_codes
    return identities[0], "usable", issue_codes


def _collect_rollout(
    attempt: Path, events: bytes, candidates: list[Path], process_started: bool,
) -> dict[str, Any]:
    cli_id, cli_problem = _cli_identity(events)
    observations: list[dict[str, Any]] = []
    matching: list[tuple[int, bytes]] = []
    if process_started:
        for index, source in enumerate(candidates, 1):
            observation: dict[str, Any] = {"index": index, "source": str(source)}
            try:
                data = _read_explicit_source(source, capture_adapter.MAX_CAPTURE_BYTES)
            except CollectorInputError as exc:
                observation.update({"status": "input_error", "error": str(exc)})
            else:
                retained = attempt / "rollout-candidates" / f"{index:03}.jsonl"
                legacy._write_new(retained, data)
                identity, status, issue_codes = _rollout_identity(data)
                observation.update({
                    "status": status,
                    "thread_id": identity,
                    "bytes": len(data),
                    "sha256": _sha(data),
                    "retained": str(retained.relative_to(attempt)),
                    "parser_issue_codes": issue_codes,
                })
                if status == "usable":
                    if cli_id is not None and identity == cli_id:
                        observation["identity_match"] = True
                        matching.append((index, data))
                    else:
                        observation["identity_match"] = False
                        if cli_id is not None:
                            observation["status"] = "mismatched_identity"
            observations.append(observation)
    elif candidates:
        observations = [
            {"index": index, "source": str(source), "status": "not_read_process_not_started"}
            for index, source in enumerate(candidates, 1)
        ]

    # An unreadable or invalid candidate could conceal another match. Only
    # positively identified nonmatches can be excluded from uniqueness checks.
    unresolved = any(
        item["status"] not in {"usable", "mismatched_identity"}
        for item in observations
    )
    selected: tuple[int, bytes] | None = (
        matching[0] if len(matching) == 1 and not unresolved else None
    )
    rollout = selected[1] if selected is not None else b""
    legacy._write_new(attempt / "rollout.jsonl", rollout)
    if not process_started:
        reason = "process_not_started"
    elif cli_problem is not None:
        reason = cli_problem
    elif len(matching) > 1:
        reason = "ambiguous_matching_candidates"
    elif unresolved:
        reason = "unresolved_candidate_identity"
    elif selected is None:
        reason = "no_candidate_matched_cli_thread_identity"
    else:
        reason = "matched_cli_thread_identity"
    return {
        "state": "captured" if selected is not None else "lost",
        "reason": reason,
        "cli_thread_id": cli_id,
        "selected_candidate": selected[0] if selected is not None else None,
        "bytes": len(rollout),
        "sha256": _sha(rollout),
        "candidates": observations,
    }


def _collect_answer(attempt: Path, source: Path | None, process_started: bool) -> dict[str, Any]:
    if not process_started:
        return {"state": "lost", "reason": "process_not_started", "source": str(source) if source else None}
    if source is None:
        return {"state": "lost", "reason": "no_explicit_answer_path", "source": None}
    try:
        data = _read_explicit_source(source, capture_adapter.MAX_ANSWER_BYTES)
    except CollectorInputError as exc:
        return {"state": "lost", "reason": "answer_input_error", "source": str(source), "error": str(exc)}
    if not data:
        return {"state": "lost", "reason": "empty_answer_file", "source": str(source),
                "bytes": 0, "sha256": _sha(data)}
    legacy._write_new(attempt / "answer.txt", data)
    return {"state": "captured", "reason": "nonempty_explicit_answer_file", "source": str(source),
            "bytes": len(data), "sha256": _sha(data), "retained": "answer.txt"}


def _execution(process: dict[str, Any]) -> str:
    if process["timed_out"]:
        return "timeout"
    if (process["exit_code"] == 0 and not process["capture_overflow"]
            and not process["interrupted"] and process["start_error"] is None):
        return "completed"
    return "failed"


def collect_attempt(
    attempt_dir: Path,
    argv: Sequence[str],
    *,
    prompt: bytes,
    env: Mapping[str, str],
    cwd: Path,
    timeout_seconds: float,
    rollout_candidates: Sequence[Path] = (),
    answer_path: Path | None = None,
) -> dict[str, Any]:
    """Run one explicitly supplied process and retain bounded private evidence.

    ``attempt_dir`` must be new.  Missing, empty, unsafe, or oversized final
    answer files are ``lost`` delivery evidence, never proof that no answer was
    produced.  Rollout candidates are only the paths supplied by the caller;
    no home or session directory is searched.
    """
    (requested_output, command, environment, work, timeout, candidates,
     answer) = _validate_request(
        Path(attempt_dir), argv, prompt, env, Path(cwd), timeout_seconds,
        rollout_candidates, answer_path,
    )
    attempt = _private_new_directory(requested_output)
    attempt_receipt = {
        "schema": ATTEMPT_SCHEMA,
        "argv_sha256": _sha(legacy._canon(command)),
        "prompt_bytes": len(prompt),
        "prompt_sha256": _sha(prompt),
        "environment_sha256": _sha(legacy._canon(environment)),
        "cwd": str(work),
        "timeout_seconds": timeout,
        "limits": {
            "stdout_bytes": MAX_STREAM_BYTES,
            "stderr_bytes": MAX_STREAM_BYTES,
            "prompt_bytes": MAX_PROMPT_BYTES,
            "rollout_candidate_bytes_each": capture_adapter.MAX_CAPTURE_BYTES,
            "answer_bytes": capture_adapter.MAX_ANSWER_BYTES,
            "rollout_candidate_count": MAX_ROLLOUT_CANDIDATES,
        },
        "rollout_candidates": [str(path) for path in candidates],
        "answer_path": str(answer) if answer is not None else None,
    }
    # This file is intentionally complete before process creation and is never
    # reopened or replaced by this module.
    legacy._write_new(attempt / "attempt.json", legacy._canon(attempt_receipt))

    captured = legacy._bounded_process(command, prompt, environment, work, timeout)
    events = captured.pop("stdout")
    stderr = captured.pop("stderr")
    process_receipt = {
        "schema": PROCESS_SCHEMA,
        **captured,
        "execution": _execution(captured),
        "stdout_bytes": len(events),
        "stderr_bytes": len(stderr),
        # The reused historical helper records exact aggregate overflow and
        # independently caps each stream. Reaching a cap identifies possible
        # stream truncation without falsely claiming which read crossed first.
        "stdout_limit_reached": len(events) == MAX_STREAM_BYTES,
        "stderr_limit_reached": len(stderr) == MAX_STREAM_BYTES,
    }
    legacy._write_new(attempt / "events.jsonl", events)
    legacy._write_new(attempt / "stderr.txt", stderr)
    legacy._write_new(attempt / "process.json", legacy._canon(process_receipt))

    process_started = process_receipt["pid"] is not None
    delivery = {
        "schema": DELIVERY_SCHEMA,
        "process_execution": process_receipt["execution"],
        "rollout": _collect_rollout(attempt, events, candidates, process_started),
        "answer": {
            **_collect_answer(attempt, answer, process_started),
            # Delivery is not finality evidence, even after a zero exit.
            # Keep this explicit for lost bytes as well: loss proves no absence.
            "finality": "unknown",
            "finality_reason": "no_qualified_final_answer_evidence",
        },
    }
    legacy._write_new(attempt / "delivery.json", legacy._canon(delivery))
    return {
        "attempt_dir": attempt,
        "attempt": attempt_receipt,
        "process": process_receipt,
        "delivery": delivery,
    }


__all__ = [
    "ATTEMPT_SCHEMA", "CollectorInputError", "DELIVERY_SCHEMA",
    "MAX_PROMPT_BYTES", "MAX_ROLLOUT_CANDIDATES", "MAX_STREAM_BYTES",
    "PROCESS_SCHEMA", "collect_attempt",
]
