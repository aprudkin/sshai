#!/usr/bin/env python3
"""Bounded offline Codex event/rollout capture adapter for Issue 10 v3.

This module does not start Codex, sshai, SSH, or any other process.  It parses
already-captured JSONL and a small process receipt.  Structural validation is
kept separate from the captured answer, process outcome, reported usage, and
call inventory.  Tool-boundary compliance and live usage semantics remain
unqualified, so this adapter never emits ``boundary=compliant``,
``instrumentation=valid``, or an experiment-eligible claim.

Public APIs:

``parse_jsonl(data, source)``
    Parse bounded UTF-8 JSONL bytes without silently accepting duplicate keys.
``capture(cli_records, rollout_records, process, answer_capture=None, ...)``
    Analyze already-decoded records without I/O. A captured final answer must
    be supplied explicitly; CLI agent messages remain non-final candidates.
``capture_bytes(cli_data, rollout_data, process_data, ...)``
    Parse and analyze bounded in-memory bytes.
``coordinator_record(report)``
    Project a capture report into the v3 coordinator's import contract.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
from pathlib import Path
import stat
import sys
from typing import Any

SCHEMA = "sshai-benchmark/issue10-v3-capture-1"
MAX_CAPTURE_BYTES = 1_048_576
MAX_ANSWER_BYTES = 1_048_576
MAX_LINE_BYTES = 262_144
MAX_RECORDS = 20_000
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
ANSWER_STATES = ("captured", "absent", "lost")


class CaptureInputError(ValueError):
    """The caller requested an unsafe/unbounded read or an invalid projection."""


class _DuplicateKey(ValueError):
    pass


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant {value!r}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey(f"duplicate object key {key!r}")
        value[key] = item
    return value


def _issue(source: str, code: str, detail: str, *, record: int | None = None,
           invalid: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source": source,
        "code": code,
        "detail": detail,
        "effect": "invalid" if invalid else "uncertain",
    }
    if record is not None:
        result["record"] = record
    return result


def parse_jsonl(data: bytes, source: str) -> dict[str, Any]:
    """Parse a bounded JSONL capture, retaining all independently valid rows."""
    if not isinstance(data, bytes):
        raise TypeError("JSONL input must be bytes")
    if not isinstance(source, str) or not source:
        raise TypeError("JSONL source must be a nonempty string")
    issues: list[dict[str, Any]] = []
    if len(data) > MAX_CAPTURE_BYTES:
        return {
            "records": [], "issues": [_issue(source, "capture_too_large",
                f"capture exceeds {MAX_CAPTURE_BYTES} bytes")],
            "byte_count": len(data), "record_count": 0, "complete": False,
        }
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {
            "records": [], "issues": [_issue(source, "invalid_utf8", str(exc))],
            "byte_count": len(data), "record_count": 0, "complete": False,
        }
    if data and not data.endswith(b"\n"):
        issues.append(_issue(source, "unterminated_jsonl", "capture does not end with a newline"))
    records: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            issues.append(_issue(source, "blank_jsonl_record", "blank JSONL record", record=line_number))
            continue
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            issues.append(_issue(source, "jsonl_record_too_large",
                f"record exceeds {MAX_LINE_BYTES} bytes", record=line_number))
            continue
        if len(records) >= MAX_RECORDS:
            issues.append(_issue(source, "too_many_records",
                f"capture exceeds {MAX_RECORDS} records", record=line_number))
            break
        try:
            records.append(json.loads(
                line,
                object_pairs_hook=_object_without_duplicate_keys,
                parse_constant=_reject_non_json_constant,
            ))
        except (_DuplicateKey, json.JSONDecodeError, ValueError) as exc:
            issues.append(_issue(source, "malformed_json", str(exc), record=line_number))
    if not records:
        issues.append(_issue(source, "missing_records", "capture contains no valid records"))
    return {
        "records": records, "issues": issues, "byte_count": len(data),
        "record_count": len(records),
        "complete": not any(item["effect"] == "invalid" for item in issues),
    }


def _strict_usage(value: Any, source: str, record: int,
                  issues: list[dict[str, Any]]) -> dict[str, int] | None:
    if not isinstance(value, dict):
        issues.append(_issue(source, "missing_usage", "usage must be an object", record=record))
        return None
    result: dict[str, int] = {}
    for field in USAGE_FIELDS:
        item = value.get(field)
        if type(item) is not int or item < 0:
            issues.append(_issue(source, "invalid_usage",
                f"{field} must be a nonnegative integer", record=record))
            return None
        result[field] = item
    if result["cached_input_tokens"] > result["input_tokens"]:
        issues.append(_issue(source, "invalid_usage",
            "cached_input_tokens exceeds input_tokens", record=record))
        return None
    reported_total = value.get("total_tokens")
    if reported_total is not None and (
        type(reported_total) is not int
        or reported_total != result["input_tokens"] + result["output_tokens"]
    ):
        issues.append(_issue(source, "inconsistent_usage_total",
            "total_tokens does not equal input_tokens plus output_tokens", record=record))
        return None
    return result


def _monotonic(snapshots: list[dict[str, Any]], source: str,
               issues: list[dict[str, Any]]) -> bool:
    valid = True
    for previous, current in zip(snapshots, snapshots[1:]):
        for field in USAGE_FIELDS:
            if current["usage"][field] < previous["usage"][field]:
                issues.append(_issue(source, "usage_decreased",
                    f"cumulative {field} decreased between records "
                    f"{previous['record']} and {current['record']}",
                    record=current["record"]))
                valid = False
    return valid


def _call_identity(item: dict[str, Any], fallback: str) -> str:
    for key in ("call_id", "id", "request_id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return fallback


def _tool_name(item: dict[str, Any], item_type: str) -> str:
    for key in ("name", "tool_name", "tool", "namespace", "server"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return item_type


def _call_detail(item: dict[str, Any]) -> Any:
    for key in ("arguments", "input", "action", "command", "query"):
        if key in item:
            return item[key]
    return None


def _response_call(item: dict[str, Any], source: str, record: int,
                   calls: list[dict[str, Any]]) -> None:
    item_type = item.get("type")
    if not isinstance(item_type, str):
        return
    # These shapes are evidenced by the historical rollout parser.  The suffix
    # fallback retains future/unknown call kinds instead of treating their name
    # as a compliance decision.
    call_types = {
        "function_call", "custom_tool_call", "local_shell_call",
        "web_search_call", "image_generation_call", "mcp_tool_call",
        "tool_search_call", "dynamic_tool_call",
    }
    if item_type not in call_types and not item_type.endswith("_call"):
        return
    calls.append({
        "source": source,
        "record": record,
        "record_type": item_type,
        "call_id": _call_identity(item, f"{source}:{record}"),
        "tool_name": _tool_name(item, item_type),
        "detail": _call_detail(item),
        "status": item.get("status") if isinstance(item.get("status"), str) else None,
        "evidence_kind": "call",
        "actual_call_confirmed": True,
        "classification": "unclassified",
    })


def _parse_cli(records: Any) -> dict[str, Any]:
    source = "cli"
    issues: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    thread_ids: list[str] = []
    turn_starts: list[int] = []
    terminals: list[dict[str, Any]] = []
    item_states: dict[str, dict[str, Any]] = {}
    item_records: list[int] = []
    compactions: list[dict[str, Any]] = []
    if not isinstance(records, list):
        records = []
        issues.append(_issue(source, "records_not_array", "records must be an array"))
    if len(records) > MAX_RECORDS:
        issues.append(_issue(source, "too_many_records", f"more than {MAX_RECORDS} records"))
        records = records[:MAX_RECORDS]
    for number, event in enumerate(records, 1):
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            issues.append(_issue(source, "malformed_record", "event must be an object with type", record=number))
            continue
        kind = event["type"]
        if kind == "compacted":
            compactions.append({"source": "cli", "record": number, "type": kind,
                                "canonical": True})
            continue
        if kind == "thread.started":
            value = event.get("thread_id")
            if not isinstance(value, str) or not value:
                issues.append(_issue(source, "missing_thread_id", "thread.started has no id", record=number))
            else:
                thread_ids.append(value)
                if len(thread_ids) > 1:
                    issues.append(_issue(source, "duplicate_thread_start", "multiple thread.started records", record=number))
            continue
        if kind == "turn.started":
            turn_starts.append(number)
            if len(turn_starts) > 1:
                issues.append(_issue(source, "duplicate_turn_start", "multiple turn.started records", record=number))
            continue
        if kind in ("turn.completed", "turn.failed"):
            terminals.append({"record": number, "type": kind})
            if len(terminals) > 1:
                issues.append(_issue(source, "duplicate_turn_terminal", "multiple terminal turn records", record=number))
            usage = _strict_usage(event.get("usage"), source, number, issues)
            if usage is not None:
                snapshots.append({"record": number, "usage": usage, "terminal": True})
            continue
        if kind in ("error", "turn.aborted"):
            terminals.append({"record": number, "type": kind})
            continue
        if kind not in ("item.started", "item.updated", "item.completed"):
            issues.append(_issue(source, "unknown_event_type", f"unrecognized event type {kind!r}",
                record=number, invalid=False))
            continue
        item_records.append(number)
        item = event.get("item")
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            issues.append(_issue(source, "malformed_item", "item must have a nonempty id", record=number))
            continue
        item_id = item["id"]
        item_type = item.get("type")
        if not isinstance(item_type, str) or not item_type:
            issues.append(_issue(source, "malformed_item", "item must have a type", record=number))
            continue
        previous = item_states.get(item_id)
        if previous is not None and previous["type"] != item_type:
            issues.append(_issue(source, "item_type_changed", f"item {item_id!r} changed type", record=number))
        if kind == "item.started":
            if previous is not None:
                issues.append(_issue(source, "duplicate_item_start", f"item {item_id!r} started more than once", record=number))
            item_states[item_id] = {"type": item_type, "state": "started", "record": number}
        elif kind == "item.updated":
            if previous is None or previous["state"] not in ("started", "updated"):
                issues.append(_issue(source, "orphan_item_update", f"item {item_id!r} update has no active start", record=number))
            item_states[item_id] = {"type": item_type, "state": "updated", "record": number}
        else:
            if previous is not None and previous["state"] == "completed":
                issues.append(_issue(source, "duplicate_item_completion", f"item {item_id!r} completed more than once", record=number))
            item_states[item_id] = {"type": item_type, "state": "completed", "record": number}
            if item_type == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    messages.append({"record": number, "text": text})
                else:
                    issues.append(_issue(source, "malformed_agent_message", "agent message has no text", record=number))
        if item_type == "command_execution":
            command = item.get("command")
            output = item.get("aggregated_output")
            status = item.get("status")
            exit_code = item.get("exit_code")
            if kind == "item.completed":
                if not isinstance(command, str) or not command:
                    issues.append(_issue(source, "malformed_command", "completed command has no command string", record=number))
                if not isinstance(output, str):
                    issues.append(_issue(source, "malformed_command", "completed command has no aggregated_output string", record=number))
                if status not in ("completed", "failed") or type(exit_code) is not int:
                    issues.append(_issue(source, "malformed_command", "completed command lacks terminal status/exit_code", record=number))
            calls.append({
                "source": source, "record": number,
                "record_type": "command_execution", "call_id": item_id,
                "tool_name": "command_execution", "detail": command,
                "status": status if isinstance(status, str) else None,
                "exit_code": exit_code if type(exit_code) is int else None,
                "output_bytes": (len(output.encode("utf-8"))
                                 if kind == "item.completed" and isinstance(output, str) else None),
                "evidence_kind": "call",
                "actual_call_confirmed": True,
                "classification": "unclassified",
            })
        elif item_type not in ("agent_message", "reasoning", "file_change", "todo_list"):
            before = len(calls)
            _response_call(item, source, number, calls)
            if len(calls) == before:
                # Do not silently discard a schema-drift item.  It may or may
                # not represent a tool call, so retain it without promoting it
                # to a confirmed actual call.
                calls.append({
                    "source": source, "record": number,
                    "record_type": item_type, "call_id": item_id,
                    "tool_name": _tool_name(item, item_type),
                    "detail": _call_detail(item),
                    "status": item.get("status") if isinstance(item.get("status"), str) else None,
                    "evidence_kind": "unknown_cli_item",
                    "actual_call_confirmed": False,
                    "classification": "unclassified",
                })
    if not thread_ids:
        issues.append(_issue(source, "missing_thread_start", "no thread.started record"))
    if not turn_starts:
        issues.append(_issue(source, "missing_turn_start", "no turn.started record"))
    if not terminals:
        issues.append(_issue(source, "unfinished_turn", "no terminal turn record"))
    if len(thread_ids) == 1 and len(turn_starts) == 1 and len(terminals) == 1:
        thread_record = next(
            index for index, event in enumerate(records, 1)
            if isinstance(event, dict) and event.get("type") == "thread.started"
        )
        if not (thread_record < turn_starts[0] < terminals[0]["record"]):
            issues.append(_issue(source, "out_of_order_lifecycle",
                "thread start, turn start, and terminal record are out of order"))
        if any(not (turn_starts[0] < item_record < terminals[0]["record"])
               for item_record in item_records):
            issues.append(_issue(source, "item_outside_turn", "item record appears outside the active turn"))
    for item_id, state in item_states.items():
        if state["state"] != "completed":
            issues.append(_issue(source, "unfinished_item", f"item {item_id!r} is unfinished", record=state["record"]))
    _monotonic(snapshots, source, issues)
    return {
        "thread_ids": thread_ids, "turn_starts": turn_starts,
        "terminals": terminals, "usage_snapshots": snapshots,
        "messages": messages, "calls": calls, "compactions": compactions,
        "issues": issues,
    }


def _turn_item(item: Any, number: int, event: str,
               calls: list[dict[str, Any]], issues: list[dict[str, Any]],
               compactions: list[dict[str, Any]]) -> None:
    """Decode a bounded subset of rust-v0.151.0 TurnItem, not ResponseItem.

    Source: protocol/src/items.rs at 78c290807ce710180111df227df3b7a4fe845452.
    Unsupported variants remain explicit audit gaps, not inferred calls.
    """
    if not isinstance(item, dict):
        issues.append(_issue("rollout", "malformed_turn_item",
                             "turn item must be an object", record=number))
        item = {"unparsed_item": item}
    kind = item.get("type")
    identity = item.get("id")
    valid = (isinstance(kind, str) and bool(kind)
             and isinstance(identity, str) and bool(identity))
    if valid and kind == "ContextCompaction":
        compactions.append({"source": "rollout.turn_item", "record": number,
                            "type": kind, "canonical": False})
        return
    # These are not call requests. Do not promote AgentMessage to final output.
    if valid and kind in ("UserMessage", "AgentMessage", "Plan", "Reasoning",
                          "FunctionCallOutput", "HookPrompt"):
        return
    command = item.get("command")
    status = item.get("status")
    if kind == "CommandExecution":
        valid = bool(valid and isinstance(command, list) and command
                     and all(isinstance(arg, str) for arg in command)
                     and isinstance(item.get("cwd"), str) and item["cwd"]
                     and status in ("in_progress", "completed", "failed", "declined")
                     and (item.get("exit_code") is None or type(item["exit_code"]) is int))
        if event == "item_completed" and status == "in_progress":
            valid = False
    else:
        issues.append(_issue("rollout", "unsupported_turn_item",
                             f"unqualified TurnItem variant {kind!r}",
                             record=number, invalid=False))
    if not valid:
        issues.append(_issue("rollout", "malformed_turn_item",
                             "turn item identity/type or command fields are malformed",
                             record=number))
    confirmed = bool(valid and kind == "CommandExecution" and status != "declined")
    calls.append({
        "source": "rollout.turn_item", "record": number,
        "record_type": kind if isinstance(kind, str) else "unknown",
        "call_id": identity if isinstance(identity, str) and identity else f"rollout.turn_item:{number}",
        "tool_name": kind if isinstance(kind, str) else "unknown",
        "detail": command if kind == "CommandExecution" else item,
        "cwd": item.get("cwd"), "status": status if isinstance(status, str) else None,
        "exit_code": item.get("exit_code") if type(item.get("exit_code")) is int else None,
        "evidence_kind": "call" if confirmed else "unqualified_turn_item",
        "actual_call_confirmed": confirmed, "classification": "unclassified",
        "raw_observations": [{"record": number, "event": event, "item": item}],
    })


def _parse_rollout(records: Any) -> dict[str, Any]:
    source = "rollout"
    issues: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    session_ids: list[str] = []
    starts: list[int] = []
    terminals: list[dict[str, Any]] = []
    response_ids: set[str] = set()
    compactions: list[dict[str, Any]] = []
    terminal_seen = False
    if not isinstance(records, list):
        records = []
        issues.append(_issue(source, "records_not_array", "records must be an array"))
    if len(records) > MAX_RECORDS:
        issues.append(_issue(source, "too_many_records", f"more than {MAX_RECORDS} records"))
        records = records[:MAX_RECORDS]

    def response(item: Any, number: int, representation: str) -> None:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            issues.append(_issue(source, "malformed_response_item", "response item lacks a type", record=number))
            return
        item_id = item.get("id")
        if isinstance(item_id, str):
            if item_id in response_ids:
                issues.append(_issue(source, "duplicate_response_item", f"response item {item_id!r} repeated", record=number))
            response_ids.add(item_id)
        kind = item["type"]
        if kind in ("compaction", "compaction_summary", "compaction_trigger", "context_compaction"):
            compactions.append({"source": representation, "record": number,
                                "type": kind, "canonical": False})
        _response_call(item, representation, number, calls)

    for number, record in enumerate(records, 1):
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            issues.append(_issue(source, "malformed_record", "rollout record must have a type", record=number))
            continue
        record_type = record["type"]
        payload = record.get("payload")
        if record_type == "compacted":
            if payload is not None and not isinstance(payload, dict):
                issues.append(_issue(source, "malformed_payload", "compacted payload must be an object or absent", record=number))
            compactions.append({"source": "rollout", "record": number,
                                "type": record_type, "canonical": True})
            continue
        if not isinstance(payload, dict):
            issues.append(_issue(source, "malformed_payload", "rollout payload must be an object", record=number))
            continue
        if record_type == "session_meta":
            value = payload.get("id")
            if not isinstance(value, str) or not value:
                issues.append(_issue(source, "missing_session_id", "session metadata has no id", record=number))
            else:
                session_ids.append(value)
                if len(session_ids) > 1:
                    issues.append(_issue(source, "duplicate_session_meta", "multiple session metadata records", record=number))
        elif record_type == "event_msg":
            subtype = payload.get("type")
            if not isinstance(subtype, str) or not subtype:
                issues.append(_issue(source, "missing_event_subtype", "event_msg has no type", record=number))
                continue
            if subtype in ("task_started", "turn_started"):
                starts.append(number)
                if len(starts) > 1:
                    issues.append(_issue(source, "duplicate_turn_start", "multiple rollout turn starts", record=number))
            elif subtype in ("task_complete", "turn_complete"):
                terminals.append({"record": number, "type": subtype})
                terminal_seen = True
                if len(terminals) > 1:
                    issues.append(_issue(source, "duplicate_turn_terminal", "multiple rollout terminal records", record=number))
                if payload.get("error") is not None:
                    issues.append(_issue(source, "failed_turn", "rollout completion contains an error", record=number))
            elif subtype in ("task_failed", "turn_aborted", "stream_error", "error"):
                terminals.append({"record": number, "type": subtype})
                terminal_seen = True
                if len(terminals) > 1:
                    issues.append(_issue(source, "duplicate_turn_terminal",
                        "multiple rollout terminal records", record=number))
            elif subtype == "token_count":
                info = payload.get("info")
                total = info.get("total_token_usage") if isinstance(info, dict) else None
                usage = _strict_usage(total, source, number, issues)
                if usage is not None:
                    snapshots.append({"record": number, "usage": usage, "terminal": terminal_seen})
                if terminal_seen:
                    issues.append(_issue(source, "usage_after_terminal", "token_count appears after terminal event", record=number))
            elif subtype == "context_compacted":
                compactions.append({"source": "rollout.event_msg", "record": number,
                                    "type": subtype, "canonical": False})
            elif subtype == "raw_response_item":
                response(payload.get("item"), number, "rollout.raw_response_item")
            elif subtype in ("item_started", "item_completed"):
                _turn_item(payload.get("item"), number, subtype, calls, issues, compactions)
            elif subtype in (
                "mcp_tool_call_begin", "mcp_tool_call_end", "web_search_begin", "web_search_end",
                "exec_command_begin", "exec_command_end", "dynamic_tool_call_request",
                "dynamic_tool_call_response", "apply_patch_approval_request", "request_user_input",
            ):
                calls.append({
                    "source": "rollout.event_msg", "record": number,
                    "record_type": subtype,
                    "call_id": _call_identity(payload, f"rollout.event_msg:{number}"),
                    "tool_name": _tool_name(payload, subtype), "detail": _call_detail(payload),
                    "status": "begin" if subtype.endswith(("_begin", "_request")) else "end",
                    "evidence_kind": "call_lifecycle",
                    "actual_call_confirmed": True,
                    "classification": "unclassified",
                })
            # Other event messages are retained by the raw capture but do not
            # acquire invented call or compliance semantics here.
        elif record_type == "response_item":
            response(payload, number, "rollout.response_item")
        elif record_type == "turn_context":
            pass
        else:
            issues.append(_issue(source, "unknown_record_type", f"unrecognized rollout record type {record_type!r}",
                record=number, invalid=False))
    if not session_ids:
        issues.append(_issue(source, "missing_session_meta", "no rollout session metadata"))
    if not starts:
        issues.append(_issue(source, "missing_turn_start", "no rollout turn start"))
    if not terminals:
        issues.append(_issue(source, "unfinished_turn", "no rollout terminal event"))
    if len(starts) == 1 and len(terminals) == 1 and starts[0] >= terminals[0]["record"]:
        issues.append(_issue(source, "out_of_order_lifecycle",
            "rollout turn start does not precede terminal event"))
    if not snapshots:
        issues.append(_issue(source, "missing_usage", "no cumulative token_count snapshot"))
    _monotonic(snapshots, source, issues)
    return {
        "session_ids": session_ids, "starts": starts, "terminals": terminals,
        "usage_snapshots": snapshots, "calls": calls,
        "compactions": compactions, "issues": issues,
    }


def _parse_process(process: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(process, dict):
        return ({"timed_out": False, "exit_code": None, "capture_overflow": False,
                 "interrupted": False, "error": None, "execution": "failed"},
                [_issue("process", "malformed_process", "process receipt must be an object")])
    timed_out = process.get("timed_out")
    exit_code = process.get("exit_code")
    capture_overflow = process.get("capture_overflow", False)
    interrupted = process.get("interrupted", False)
    error = process.get("error")
    if error is None:
        error = process.get("start_error")
    if type(timed_out) is not bool:
        issues.append(_issue("process", "malformed_process", "timed_out must be boolean"))
        timed_out = False
    for field, value in (("capture_overflow", capture_overflow), ("interrupted", interrupted)):
        if type(value) is not bool:
            issues.append(_issue("process", "malformed_process", f"{field} must be boolean"))
            if field == "capture_overflow":
                capture_overflow = False
            else:
                interrupted = False
    if exit_code is not None and type(exit_code) is not int:
        issues.append(_issue("process", "malformed_process", "exit_code must be an integer or null"))
        exit_code = None
    if error is not None and not isinstance(error, str):
        issues.append(_issue("process", "malformed_process", "error/start_error must be a string or null"))
        error = None
    if timed_out:
        execution = "timeout"
    elif issues:
        execution = "failed"
    elif (exit_code == 0 and error is None
          and not capture_overflow and not interrupted):
        execution = "completed"
    else:
        execution = "failed"
    return {"timed_out": timed_out, "exit_code": exit_code,
            "capture_overflow": capture_overflow, "interrupted": interrupted,
            "error": error, "execution": execution}, issues


def _answer(answer_capture: Any, cli_messages: list[dict[str, Any]],
            issues: list[dict[str, Any]]) -> dict[str, Any]:
    if answer_capture is None:
        if cli_messages:
            return {
                "state": "lost", "text": None,
                "source": "cli_agent_messages_are_candidates_not_final_capture",
                "cli_agent_messages": cli_messages,
            }
        return {"state": "absent", "text": None, "source": "no_answer_evidence",
                "cli_agent_messages": cli_messages}
    if not isinstance(answer_capture, dict):
        issues.append(_issue("answer", "malformed_answer_capture", "answer capture must be an object"))
        return {"state": "lost", "text": None, "source": "invalid_explicit_capture",
                "cli_agent_messages": cli_messages}
    state = answer_capture.get("state")
    text = answer_capture.get("text")
    if state not in ANSWER_STATES:
        issues.append(_issue("answer", "malformed_answer_capture", f"state must be one of {ANSWER_STATES!r}"))
        state = "lost"
        text = None
    elif state == "captured":
        if not isinstance(text, str) or not text.strip():
            issues.append(_issue("answer", "malformed_answer_capture", "captured answer needs nonempty text"))
            state, text = "lost", None
    elif text is not None:
        issues.append(_issue("answer", "malformed_answer_capture", "absent/lost answer must have null text"))
        text = None
    if state == "captured" and cli_messages and text != cli_messages[-1]["text"]:
        issues.append(_issue("answer", "answer_mismatch",
            "explicit captured answer differs from the last completed CLI agent message"))
    return {"state": state, "text": text, "source": "explicit",
            "cli_agent_messages": cli_messages}


def _deduplicate_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge repeated lifecycle representations only within the same source family."""
    result: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for call in calls:
        family = call["source"].split(".", 1)[0]
        # TurnItem IDs are not proven to share the legacy response/event ID domain.
        identity_domain = call["source"] if call["source"] == "rollout.turn_item" else family
        key = (identity_domain, call["call_id"])
        if key not in positions:
            positions[key] = len(result)
            result.append({
                **call,
                "observations": 1,
                "sources": [call["source"]],
                "record_types": [call["record_type"]],
            })
        else:
            target = result[positions[key]]
            target["observations"] += 1
            if "raw_observations" in call:
                target.setdefault("raw_observations", []).extend(call["raw_observations"])
                target["actual_call_confirmed"] = (
                    target["actual_call_confirmed"] and call["actual_call_confirmed"]
                )
            if call["source"] not in target["sources"]:
                target["sources"].append(call["source"])
            if call["record_type"] not in target["record_types"]:
                target["record_types"].append(call["record_type"])
            if call.get("status") is not None:
                target["status"] = call["status"]
            if target.get("detail") is None and call.get("detail") is not None:
                target["detail"] = call["detail"]
            if call.get("exit_code") is not None:
                target["exit_code"] = call["exit_code"]
            if call.get("output_bytes") is not None:
                target["output_bytes"] = call["output_bytes"]
    return result


def capture(cli_records: Any, rollout_records: Any, process: Any,
            answer_capture: Any = None, *, source_issues: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Analyze decoded records; only an explicit answer capture can be final."""
    cli = _parse_cli(cli_records)
    rollout = _parse_rollout(rollout_records)
    process_result, process_issues = _parse_process(process)
    issues = [*source_issues, *cli["issues"], *rollout["issues"], *process_issues]
    answer = _answer(answer_capture, cli["messages"], issues)

    cli_candidate = cli["thread_ids"][0] if cli["thread_ids"] else None
    rollout_candidate = rollout["session_ids"][0] if rollout["session_ids"] else None
    cli_id = cli_candidate if len(cli["thread_ids"]) == 1 else None
    rollout_id = rollout_candidate if len(rollout["session_ids"]) == 1 else None
    session_id = cli_id if cli_id is not None and cli_id == rollout_id else None
    if (cli_candidate is not None and rollout_candidate is not None
            and cli_candidate != rollout_candidate):
        issues.append(_issue("capture", "session_id_mismatch", "CLI and rollout session ids differ"))

    cli_final = cli["usage_snapshots"][-1]["usage"] if cli["usage_snapshots"] else None
    rollout_final = rollout["usage_snapshots"][-1]["usage"] if rollout["usage_snapshots"] else None
    usage_match = cli_final is not None and rollout_final is not None and cli_final == rollout_final
    if usage_match:
        usage = cli_final
    elif cli_final is None or rollout_final is None:
        usage = cli_final if cli_final is not None else rollout_final
    else:
        usage = None
        issues.append(_issue("capture", "usage_mismatch", "CLI and rollout cumulative totals differ"))
    usage_invalid_codes = {
        "missing_usage", "invalid_usage", "inconsistent_usage_total", "usage_decreased",
        "usage_after_terminal", "duplicate_turn_terminal", "unfinished_turn",
    }
    usage_has_invalid_issue = any(
        item["effect"] == "invalid" and item["code"] in usage_invalid_codes for item in issues
    )
    capture_stream_invalid = any(
        item["effect"] == "invalid" and item["source"] in ("cli", "rollout", "capture")
        for item in issues
    )
    compaction_observations = [*cli["compactions"], *rollout["compactions"]]
    cli_canonical_compactions = sum(item["canonical"] for item in cli["compactions"])
    rollout_canonical_compactions = sum(item["canonical"] for item in rollout["compactions"])
    if cli_canonical_compactions != rollout_canonical_compactions:
        issues.append(_issue("capture", "compaction_observation_mismatch",
            "CLI and rollout top-level compacted record counts differ"))
    compaction_observed = bool(compaction_observations)
    usage_complete = bool(
        usage_match
        and len(cli["terminals"]) == 1
        and cli["terminals"][0]["type"] == "turn.completed"
        and len(rollout["terminals"]) == 1
        and rollout["terminals"][0]["type"] in ("task_complete", "turn_complete")
        and not usage_has_invalid_issue
        and not capture_stream_invalid
        and not process_result["capture_overflow"]
        and not compaction_observed
    )
    if compaction_observed:
        issues.append(_issue("capture", "compaction_usage_uncertain",
            "cumulative usage continuity across observed compaction is not qualified", invalid=False))

    invalid = any(item.get("effect") == "invalid" for item in issues)
    instrumentation = "invalid" if invalid else "unknown"
    calls = _deduplicate_calls([*cli["calls"], *rollout["calls"]])
    return {
        "schema": SCHEMA,
        "session_id": session_id,
        "execution": process_result,
        "answer": answer,
        "usage": {
            "totals": usage,
            "complete": usage_complete,
            "counting_method": "one final cumulative total; snapshots are never summed",
            "cli_final": cli_final,
            "rollout_final": rollout_final,
            "totals_match": usage_match,
            "cli_snapshot_count": len(cli["usage_snapshots"]),
            "rollout_snapshot_count": len(rollout["usage_snapshots"]),
        },
        "compaction": {
            "observed": compaction_observed,
            "observations": compaction_observations,
            "cli_canonical_count": cli_canonical_compactions,
            "rollout_canonical_count": rollout_canonical_compactions,
            "canonical_counts_match": cli_canonical_compactions == rollout_canonical_compactions,
            "usage_continuity": "unqualified" if compaction_observed else "not_observed",
        },
        "calls": {
            "inventory": calls,
            "inventory_entry_count": len(calls),
            "observation_count": sum(item["observations"] for item in calls),
            "confirmed_call_entry_count": sum(
                item["actual_call_confirmed"] for item in calls
            ),
            "unknown_item_entry_count": sum(
                not item["actual_call_confirmed"] for item in calls
            ),
            "per_primary_source_entry_counts": {
                source: sum(item["source"] == source for item in calls)
                for source in sorted({item["source"] for item in calls})
            },
            "completed_command_calls": sum(
                item["record_type"] == "command_execution"
                and item.get("status") in ("completed", "failed")
                for item in calls
            ),
            "failed_command_calls": sum(
                item["record_type"] == "command_execution"
                and (item.get("status") == "failed"
                     or (item.get("exit_code") is not None and item["exit_code"] != 0))
                for item in calls
            ),
            "captured_command_output_bytes": sum(
                item.get("output_bytes") or 0 for item in calls
                if item["record_type"] == "command_execution"
            ),
            "coverage": "unqualified",
            "compliance_inferred": False,
            "note": (
                "entries are per-source evidence and are not a cross-source unique-call total; "
                "names/details are verbatim and shell substrings are not policy classifications"
            ),
        },
        "instrumentation": {
            "status": instrumentation,
            "structurally_valid": not invalid,
            "semantics_qualified": False,
            "coverage_qualified": False,
        },
        "boundary": {
            "status": "unknown",
            "reason": "call inventory is evidence, not an automatic compliance decision",
        },
        "issues": issues,
        "experimental_claim_eligible": False,
    }


def _decode_process(data: bytes) -> tuple[Any, list[dict[str, Any]]]:
    if len(data) > MAX_CAPTURE_BYTES:
        return None, [_issue("process", "capture_too_large", f"process receipt exceeds {MAX_CAPTURE_BYTES} bytes")]
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_non_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        return None, [_issue("process", "malformed_json", str(exc))]
    return value, []


def capture_bytes(cli_data: bytes, rollout_data: bytes, process_data: bytes,
                  answer_data: bytes | None = None, *, answer_state: str | None = None) -> dict[str, Any]:
    """Boundedly parse in-memory captures and analyze their retained records."""
    cli = parse_jsonl(cli_data, "cli")
    rollout = parse_jsonl(rollout_data, "rollout")
    process, process_issues = _decode_process(process_data)
    issues = [*cli["issues"], *rollout["issues"], *process_issues]
    explicit: dict[str, Any] | None = None
    if answer_state is not None or answer_data is not None:
        state = answer_state or "captured"
        if state not in ANSWER_STATES:
            raise CaptureInputError(f"answer_state must be one of {ANSWER_STATES!r}")
        if answer_data is not None and len(answer_data) > MAX_ANSWER_BYTES:
            issues.append(_issue("answer", "capture_too_large", f"answer exceeds {MAX_ANSWER_BYTES} bytes"))
            explicit = {"state": "lost", "text": None}
        elif state == "captured":
            try:
                text = answer_data.decode("utf-8") if answer_data is not None else None
            except UnicodeDecodeError as exc:
                issues.append(_issue("answer", "invalid_utf8", str(exc)))
                text = None
            explicit = {"state": "captured", "text": text}
        else:
            explicit = {"state": state, "text": None}
            if answer_data not in (None, b""):
                issues.append(_issue("answer", "unexpected_answer_bytes",
                    f"{state} answer state was supplied with answer bytes"))
    return capture(cli["records"], rollout["records"], process, explicit, source_issues=issues)


def coordinator_record(report: dict[str, Any]) -> dict[str, Any]:
    """Project a report to the coordinator import schema without upgrading claims."""
    if not isinstance(report, dict) or report.get("schema") != SCHEMA:
        raise CaptureInputError("not an Issue 10 v3 capture report")
    session_id = report.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise CaptureInputError("capture has no single matching session_id")
    answer = report["answer"]
    instrumentation = report["instrumentation"]["status"]
    if instrumentation not in ("invalid", "unknown"):
        raise CaptureInputError("offline capture cannot claim valid instrumentation")
    if report["boundary"]["status"] != "unknown":
        raise CaptureInputError("offline capture cannot claim boundary compliance")
    return {
        "session_id": session_id,
        "execution": report["execution"]["execution"],
        "answer_state": answer["state"],
        "final_answer": answer["text"],
        "usage": report["usage"]["totals"],
        "usage_complete": report["usage"]["complete"],
        "instrumentation": instrumentation,
        "boundary": "unknown",
    }


def _read_regular_bounded(path: Path, limit: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise CaptureInputError(f"cannot stat {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CaptureInputError(f"capture input is not a regular file: {path}")
    if info.st_size > limit:
        raise CaptureInputError(f"capture input exceeds {limit} bytes: {path}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise CaptureInputError(f"capture input grew beyond {limit} bytes: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True, help="already-captured Codex exec JSONL")
    parser.add_argument("--rollout", type=Path, required=True, help="already-captured persisted rollout JSONL")
    parser.add_argument("--process", type=Path, required=True,
                        help="already-captured process receipt JSON (no process is launched)")
    parser.add_argument("--answer", type=Path, help="optional already-captured final-answer UTF-8 file")
    parser.add_argument("--answer-state", choices=ANSWER_STATES,
                        help="explicit answer state; defaults to captured when --answer is present")
    args = parser.parse_args(argv)
    if args.answer is not None and args.answer_state in ("absent", "lost"):
        parser.error("--answer cannot be combined with absent/lost --answer-state")
    if args.answer is None and args.answer_state == "captured":
        parser.error("captured --answer-state requires --answer")
    try:
        answer = _read_regular_bounded(args.answer, MAX_ANSWER_BYTES) if args.answer else None
        report = capture_bytes(
            _read_regular_bounded(args.events, MAX_CAPTURE_BYTES),
            _read_regular_bounded(args.rollout, MAX_CAPTURE_BYTES),
            _read_regular_bounded(args.process, MAX_CAPTURE_BYTES),
            answer,
            answer_state=args.answer_state,
        )
        try:
            projected = coordinator_record(report)
        except CaptureInputError:
            projected = None
        output = {
            "schema": SCHEMA,
            "capture": report,
            "coordinator_record": projected,
            "experimental_claim_eligible": False,
        }
        print(json.dumps(output, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except CaptureInputError as exc:
        print(f"capture input error: {exc}", file=sys.stderr)
        return 2


__all__ = [
    "ANSWER_STATES", "CaptureInputError", "MAX_ANSWER_BYTES", "MAX_CAPTURE_BYTES",
    "MAX_LINE_BYTES", "MAX_RECORDS", "SCHEMA", "capture", "capture_bytes",
    "coordinator_record", "main", "parse_jsonl",
]


if __name__ == "__main__":
    raise SystemExit(main())
