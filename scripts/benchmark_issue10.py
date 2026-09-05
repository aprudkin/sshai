#!/usr/bin/env python3
"""Gated, local-only Issue 10 Codex benchmark runner.

``prepare`` and ``analyze`` are offline.  ``run-one`` is the only command which
can start Codex, and requires both a manifest-bound approval and
``--allow-paid-run``.  A failed or interrupted slot is evidence and is never
reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import statistics
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from benchmark_issue10_fixtures import TASK_IDS, build_task, grade

SCHEMA = "sshai-benchmark/issue10-v1"
REPORT_SCHEMA = "sshai-benchmark/issue10-report-v1"
CODEX_VERSION = "codex-cli 0.151.0"
CODEX_EXEC_EVENTS_SOURCE = "https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/exec/src/exec_events.rs"
MAX_CAPTURE = 1_000_000
ENV_PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_HARNESS = Path(__file__).resolve(strict=True)
_SOURCE_DIR = _HARNESS.parent
_REPO_DIR = _SOURCE_DIR.parent
_POPEN: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen
_QUALIFIER: Callable[..., dict[str, Any]] | None = None
_MCP_PREFLIGHT: Callable[..., dict[str, Any]] | None = None


class AnalysisInvalid(ValueError):
    """Retained evidence is not a valid one-turn benchmark sample."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canon(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _read_bounded(path: Path, limit: int = MAX_CAPTURE) -> bytes:
    _regular(path)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"bounded file exceeds {limit} bytes: {path.name}")
    return data


def _read_json(path: Path) -> Any:
    return json.loads(_read_bounded(path).decode("utf-8"))


def _physical(path: Path, *, must_exist: bool = True) -> Path:
    """Return an absolute physical path while rejecting symlink input/ancestors."""
    path = path.expanduser().absolute()
    check = path if path.exists() or path.is_symlink() else path.parent
    for candidate in (check, *check.parents):
        if candidate.is_symlink():
            raise ValueError(f"symlink path is not allowed: {path}")
    if must_exist and not path.exists():
        raise ValueError(f"path does not exist: {path}")
    return path.resolve(strict=must_exist)


def _regular(path: Path, *, mode_0600: bool = False) -> None:
    path = Path(path)
    if path.is_symlink() or not path.exists() or not path.is_file():
        raise ValueError(f"unsafe regular file required: {path}")
    if mode_0600 and stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError(f"file must have mode 0600: {path}")


def _make_private_tree(path: Path) -> None:
    """Create missing directories as 0700 without traversing symlinks."""
    path = path.absolute()
    missing = []
    cursor = path
    while not cursor.exists() and not cursor.is_symlink():
        missing.append(cursor)
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise ValueError(f"unsafe directory ancestor: {cursor}")
    for ancestor in (cursor, *cursor.parents):
        if ancestor.is_symlink():
            raise ValueError(f"symlink directory ancestor: {ancestor}")
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        directory.chmod(0o700)


def _new_dir(path: Path) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing existing path: {path}")
    _make_private_tree(path)


def _mkdir_private(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing existing directory: {path}")
    _make_private_tree(path)


def _write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Publish a complete new file atomically, never exposing partial bytes."""
    if path.exists() or path.is_symlink():
        raise ValueError(f"refusing overwrite: {path}")
    _make_private_tree(path.parent)
    _atomic_new(path, data, mode)


def _atomic_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError(f"unsafe atomic publication parent: {path.parent}")
    _physical(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".new-")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.close(fd)
        fd = -1
        os.link(temporary, path)
    except FileExistsError:
        raise ValueError(f"refusing overwrite: {path}") from None
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _file_digest(path: Path) -> str:
    _regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_digest(manifest: dict[str, Any]) -> str:
    copy = dict(manifest)
    copy.pop("digest", None)
    return _sha(_canon(copy))


def _parse_jsonl(data: bytes, label: str) -> list[Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalysisInvalid(f"{label} is not UTF-8") from exc
    records = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AnalysisInvalid(f"malformed {label} record at line {number}") from exc
    return records


def _native_codex(path: Path) -> None:
    """Require a native Mach-O executable rather than a script/JS wrapper."""
    _regular(path)
    if not os.access(path, os.X_OK):
        raise ValueError("Codex must be executable")
    with path.open("rb") as stream:
        magic = stream.read(4)
    mach_o = {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    }
    if magic not in mach_o:
        raise ValueError("Codex must be a native Mach-O executable, not a wrapper")


def _offline_probe(path: Path, arguments: list[str], expected: str | None = None) -> tuple[str, str]:
    result = _bounded_process(
        [str(path), *arguments], b"",
        {"PATH": ENV_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
        path.parent, 15,
    )
    stdout = result.pop("stdout")
    stderr = result.pop("stderr")
    if result["exit_code"] != 0 or result["timed_out"] or result["capture_overflow"] or result["interrupted"] or result["start_error"] or len(stdout) > 4096 or len(stderr) > 4096:
        raise ValueError(f"version probe failed: {path}")
    value = stdout.decode("utf-8", "strict").strip()
    if not value or (expected is not None and value != expected):
        raise ValueError(f"unexpected probe output for {path}: {value!r}")
    return value, _sha(stdout)


def _branch_prompt(task_prompt: bytes, arm: str, sshai: str) -> bytes:
    if arm == "baseline":
        guidance = "Baseline branch: use ordinary local Bash tools and filtering in this workspace. Do not invoke sshai."
    else:
        guidance = (
            f"sshai branch: execute commands only through {sshai} local --shell bash. "
            "Use sshai q, diff, and --delta when useful; do not force raw output dumps. "
            "SSHAI_ROOT is a private writable state directory."
        )
    safety = "Do not use child agents, MCP, web search, apps/plugins, or remote execution."
    return task_prompt + b"\n" + guidance.encode() + b"\n" + safety.encode() + b"\nReturn only JSON matching the supplied schema.\n"


def _task_freeze(task_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    task = build_task(task_id)
    files = {name: body.encode("utf-8") for name, body in task["files"].items()}
    record = {
        "prompt_sha256": _sha(task["prompt"].encode()),
        "schema_sha256": _sha(_canon(task["answer_schema"])),
        "files": {name: _sha(data) for name, data in sorted(files.items())},
        "fixture_sha256": _sha(_canon({name: _sha(data) for name, data in sorted(files.items())})),
    }
    return record, files


def _study_ancestor_scan(root: Path) -> list[str]:
    """Reject project/user context which a parent Codex could load before sandboxing."""
    if root.is_relative_to(_REPO_DIR) or _REPO_DIR.is_relative_to(root):
        raise ValueError("study root must be physically outside the repository tree")
    forbidden_files = ("AGENTS.md", "AGENTS.override.md", "CLAUDE.md")
    forbidden_directories = (".codex", ".agents")
    checked = []
    for ancestor in (root.parent, *root.parent.parents):
        checked.append(str(ancestor))
        found = [name for name in forbidden_files if (ancestor / name).exists() or (ancestor / name).is_symlink()]
        found += [name for name in forbidden_directories if (ancestor / name).exists() or (ancestor / name).is_symlink()]
        if found:
            raise ValueError(f"study root ancestor contains agent context/config: {ancestor} ({', '.join(sorted(found))})")
    return checked


def prepare(args: argparse.Namespace) -> int:
    root = _physical(Path(args.root), must_exist=False)
    ancestor_scan = _study_ancestor_scan(root)
    codex = _physical(Path(args.codex))
    sshai = _physical(Path(args.sshai))
    _native_codex(codex)
    _regular(sshai)
    if not os.access(sshai, os.X_OK):
        raise ValueError("sshai must be executable")
    if args.repetitions < 1 or args.timeout_seconds < 1:
        raise ValueError("repetitions and timeout must be positive")
    if not args.model.strip() or not args.reasoning_effort.strip():
        raise ValueError("model and reasoning effort must be non-empty")
    _new_dir(root)
    try:
        codex_version, codex_version_sha256 = _offline_probe(codex, ["--version"], CODEX_VERSION)
        _, sshai_help_sha256 = _offline_probe(sshai, ["help"])
        slots: list[dict[str, Any]] = []
        for replicate in range(args.repetitions):
            order = ("baseline", "sshai") if replicate % 2 == 0 else ("sshai", "baseline")
            for task_id in TASK_IDS:
                task_prompt = build_task(task_id)["prompt"].encode()
                slots.extend({
                    "task": task_id, "replicate": replicate, "arm": arm,
                    "prepared_prompt_sha256": _sha(_branch_prompt(task_prompt, arm, str(sshai))),
                } for arm in order)
        fixture_records: dict[str, Any] = {}
        inputs = root / "inputs"
        _mkdir_private(inputs)
        for task_id in TASK_IDS:
            task = build_task(task_id)
            frozen, files = _task_freeze(task_id)
            fixture_records[task_id] = frozen
            target = inputs / task_id
            _mkdir_private(target)
            _write_new(target / "prompt.txt", task["prompt"].encode())
            _write_new(target / "answer-schema.json", _canon(task["answer_schema"]))
            fixture_dir = target / "fixture"
            _mkdir_private(fixture_dir)
            for relative, body in files.items():
                destination = fixture_dir / relative
                _make_private_tree(destination.parent)
                _write_new(destination, body)
        sources = {
            "harness": _file_digest(_HARNESS),
            "fixtures": _file_digest(_SOURCE_DIR / "benchmark_issue10_fixtures.py"),
            "sandbox": _file_digest(_SOURCE_DIR / "benchmark_issue10_sandbox.py"),
            "codex": _file_digest(codex),
            "sshai": _file_digest(sshai),
        }
        protocols = {}
        for name in ("issue10-protocol.md", "issue10-analyzer.md"):
            path = _REPO_DIR / "docs" / "benchmarks" / name
            _regular(path)
            protocols[name] = _file_digest(path)
        manifest = {
            "schema": SCHEMA, "scope": "local-macos", "phase": args.phase,
            "model": args.model, "reasoning_effort": args.reasoning_effort,
            "repetitions": args.repetitions, "timeout_seconds": args.timeout_seconds,
            "codex": str(codex), "codex_version": codex_version,
            "codex_version_sha256": codex_version_sha256,
            "sshai": str(sshai), "sshai_help_sha256": sshai_help_sha256,
            "environment": {"keys": ["CODEX_HOME", "HOME", "LANG", "LC_ALL", "LOGNAME", "PATH", "SHELL", "SSHAI_ROOT", "TMPDIR", "USER", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"], "PATH": ENV_PATH},
            "task_ids": list(TASK_IDS), "slots": slots, "tasks": fixture_records,
            "sources": sources, "protocols": protocols,
            "codex_configuration": {
                "source": "https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/config/src/config_toml.rs#L545-L590",
                "exec_events_source": CODEX_EXEC_EVENTS_SOURCE,
                "feature_source": "https://github.com/openai/codex/blob/rust-v0.151.0/codex-rs/features/src/lib.rs#L560-L640",
                "multi_agent": "agents.enabled=false",
                "defensive_feature_disables": ["apps", "multi_agent", "multi_agent_v2", "plugins"],
                "managed_mcp_limitation": "No global MCP disable exists; paid launch is blocked unless an offline system/managed/cloud preflight proves absence, observed remote items are rejected, and pilot review inspects the effective tool surface.",
            },
            "ancestor_context_policy": {
                "checked": ancestor_scan,
                "rejected_names": [".agents", ".codex", "AGENTS.md", "AGENTS.override.md", "CLAUDE.md"],
                "result": "none-present",
            },
            "usage_contract": "Codex-reported cumulative totals; pilot verification required before measurement; no hard dollar or token cap is promised.",
        }
        manifest["digest"] = _manifest_digest(manifest)
        _write_new(root / "manifest.json", _canon(manifest))
        approval = {
            "approved": False, "approved_at_utc": "",
            "manifest_digest": manifest["digest"],
            "phase": args.phase, "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "session_count": len(slots),
            "per_session_timeout_seconds": args.timeout_seconds,
            "budget_acknowledged": False,
            "operator_budget_note": "",
            "no_hard_cap_acknowledged": False,
            "pilot_usage_verified": False,
            "pilot_report_reference": "",
        }
        _write_new(root / "approval-template.json", _canon(approval))
        _mkdir_private(root / "runs")
    except Exception:
        # Keep a failed prepare private for inspection; never reuse it.
        raise
    print(json.dumps({"approved": False, "manifest_digest": manifest["digest"], "root": str(root)}, sort_keys=True))
    return 0


def _strict_int(value: Any, *, positive: bool, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise AnalysisInvalid(f"{field} must be a {qualifier} integer")
    return value


def _usage(info: Any) -> dict[str, int]:
    if not isinstance(info, dict):
        raise AnalysisInvalid("missing terminal usage")
    input_tokens = _strict_int(info.get("input_tokens"), positive=True, field="input_tokens")
    output_tokens = _strict_int(info.get("output_tokens"), positive=False, field="output_tokens")
    cached = _strict_int(info.get("cached_input_tokens"), positive=False, field="cached_input_tokens")
    if cached > input_tokens:
        raise AnalysisInvalid("cached_input_tokens exceeds input_tokens")
    reasoning_value = info.get("reasoning_output_tokens", info.get("reasoning_tokens"))
    cache_write_value = info.get("cache_write_input_tokens")
    usage = {
        "input_tokens": input_tokens, "cached_input_tokens": cached,
        "non_cached_input_tokens": input_tokens - cached,
        "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens,
    }
    if reasoning_value is not None:
        usage["reasoning_output_tokens"] = _strict_int(reasoning_value, positive=False, field="reasoning_output_tokens")
    if cache_write_value is not None:
        usage["cache_write_input_tokens"] = _strict_int(cache_write_value, positive=False, field="cache_write_input_tokens")
    reported_total = info.get("total_tokens")
    if reported_total is not None and _strict_int(reported_total, positive=True, field="total_tokens") != usage["total_tokens"]:
        raise AnalysisInvalid("reported total_tokens is inconsistent")
    return usage


def analyze_events(events: list[Any]) -> dict[str, Any]:
    """Validate rust-v0.151.0 ``exec_events.rs`` one-turn JSONL."""
    if not isinstance(events, list):
        raise AnalysisInvalid("events must be an array")
    stage = 0
    thread_id: str | None = None
    terminal_usage: dict[str, int] | None = None
    item_states: dict[str, tuple[str, str]] = {}
    commands = output_bytes = failed_commands = failed_file_changes = 0
    command_lines: list[str] = []
    agent_messages: list[str] = []
    allowed_events = {"thread.started", "turn.started", "item.started", "item.updated", "item.completed", "turn.completed"}
    allowed_items = {"reasoning", "command_execution", "file_change", "agent_message", "todo_list"}
    for event in events:
        if not isinstance(event, dict):
            raise AnalysisInvalid("malformed event")
        event_type = event.get("type")
        if event_type not in allowed_events:
            raise AnalysisInvalid(f"unknown or failed event type: {event_type!r}")
        if event_type == "thread.started":
            if stage != 0 or thread_id is not None:
                raise AnalysisInvalid("duplicate or out-of-order thread start")
            thread_id = event.get("thread_id")
            if not isinstance(thread_id, str) or not thread_id:
                raise AnalysisInvalid("thread start has no id")
            stage = 1
            continue
        if event_type == "turn.started":
            if stage != 1:
                raise AnalysisInvalid("duplicate or out-of-order turn start")
            stage = 2
            continue
        if event_type == "turn.completed":
            if stage != 2:
                raise AnalysisInvalid("duplicate or out-of-order turn completion")
            terminal_usage = _usage(event.get("usage"))
            stage = 3
            continue
        if stage != 2:
            raise AnalysisInvalid("item outside active turn")
        item = event.get("item")
        if not isinstance(item, dict):
            raise AnalysisInvalid("malformed item")
        item_id, item_type = item.get("id"), item.get("type")
        if not isinstance(item_id, str) or not item_id:
            raise AnalysisInvalid("item has no id")
        if item_type not in allowed_items:
            raise AnalysisInvalid(f"unknown or prohibited item type: {item_type!r}")
        prior = item_states.get(item_id)
        if prior is not None and prior[0] != item_type:
            raise AnalysisInvalid("item type changed across lifecycle")
        if event_type == "item.started":
            if prior is not None:
                raise AnalysisInvalid("duplicate item start")
            item_states[item_id] = (item_type, "started")
            continue
        if event_type == "item.updated":
            if prior is None or prior[1] not in {"started", "updated"}:
                raise AnalysisInvalid("item update has no active start")
            item_states[item_id] = (item_type, "updated")
            continue
        if prior is not None and prior[1] == "completed":
            raise AnalysisInvalid("duplicate item completion")
        item_states[item_id] = (item_type, "completed")
        if item_type == "command_execution":
            command, output, status, exit_code = item.get("command"), item.get("aggregated_output"), item.get("status"), item.get("exit_code")
            if not isinstance(command, str) or not command or not isinstance(output, str):
                raise AnalysisInvalid("completed command fields are missing")
            if status not in {"completed", "failed"} or isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise AnalysisInvalid("command is declined, nonterminal, or lacks exit code")
            commands += 1
            failed_commands += int(status == "failed" or exit_code != 0)
            command_lines.append(command)
            output_bytes += len(output.encode("utf-8"))
        elif item_type == "file_change":
            status = item.get("status")
            if status not in {"completed", "failed"}:
                raise AnalysisInvalid("file change is nonterminal")
            failed_file_changes += int(status == "failed")
        elif item_type == "agent_message":
            text = item.get("text")
            if not isinstance(text, str):
                raise AnalysisInvalid("agent message text is missing")
            agent_messages.append(text)
        elif item_type == "todo_list" and not isinstance(item.get("items"), list):
            raise AnalysisInvalid("todo list items are missing")
    if thread_id is None or stage != 3 or terminal_usage is None:
        raise AnalysisInvalid("missing or unfinished lifecycle")
    if any(state != "completed" for _, state in item_states.values()):
        raise AnalysisInvalid("unfinished item")
    if not agent_messages:
        raise AnalysisInvalid("missing terminal agent message")
    return {
        "thread_id": thread_id, "usage": terminal_usage,
        "completed_commands": commands, "failed_commands": failed_commands,
        "failed_file_changes": failed_file_changes,
        "captured_command_output_bytes": output_bytes,
        "command_lines": command_lines, "final_message": agent_messages[-1],
        "agent_message_count": len(agent_messages),
    }


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("payload")
    if not isinstance(value, dict):
        raise AnalysisInvalid("rollout payload must be an object")
    return value


def validate_rollout(
    rollout: list[Any], exec_analysis: dict[str, Any], model: str, effort: str, version: str,
) -> dict[str, Any]:
    """Cross-check the fresh persisted rollout and final cumulative usage."""
    if not isinstance(rollout, list) or not rollout:
        raise AnalysisInvalid("missing rollout")
    session: dict[str, Any] | None = None
    contexts: list[dict[str, Any]] = []
    starts = completes = compactions = 0
    totals: list[dict[str, Any]] = []
    response_ids: set[str] = set()
    allowed_event_messages = {
        "task_started", "turn_started", "task_complete", "turn_complete",
        "token_count", "agent_message", "user_message", "agent_reasoning",
        "agent_reasoning_raw_content", "agent_reasoning_section_break", "session_configured",
        "context_compacted", "exec_command_begin", "exec_command_output_delta",
        "terminal_interaction", "exec_command_end", "patch_apply_begin", "patch_apply_updated",
        "patch_apply_end", "turn_diff", "plan_update", "raw_response_item",
        "raw_response_completed", "item_started", "item_completed",
        "agent_message_content_delta", "plan_delta", "reasoning_content_delta",
        "reasoning_raw_content_delta",
    }
    prohibited_event_messages = {
        "error", "warning", "guardian_warning", "model_reroute", "stream_error",
        "turn_aborted", "task_failed", "thread_rolled_back", "thread_settings_applied",
        "mcp_startup_update", "mcp_startup_complete", "mcp_tool_call_begin", "mcp_tool_call_end",
        "web_search_begin", "web_search_end", "image_generation_begin", "image_generation_end",
        "exec_approval_request", "request_permissions", "request_user_input",
        "dynamic_tool_call_request", "dynamic_tool_call_response", "elicitation_request",
        "apply_patch_approval_request", "guardian_assessment", "hook_started", "hook_completed",
        "collab_agent_spawn_begin", "collab_agent_spawn_end", "collab_agent_interaction_begin",
        "collab_agent_interaction_end", "collab_waiting_begin", "collab_waiting_end",
        "collab_close_begin", "collab_close_end", "collab_resume_begin", "collab_resume_end",
        "sub_agent_activity",
    }
    allowed_response_items = {
        "message", "reasoning", "local_shell_call", "function_call", "function_call_output",
        "custom_tool_call", "custom_tool_call_output", "compaction", "compaction_summary",
        "compaction_trigger", "context_compaction",
    }
    prohibited_response_items = {
        "additional_tools", "agent_message", "tool_search_call", "tool_search_output",
        "web_search_call", "image_generation_call", "mcp_tool_call", "other", "ghost_snapshot",
    }
    def validate_response_shape(item: dict[str, Any]) -> None:
        kind = item.get("type")
        if kind not in allowed_response_items or kind in prohibited_response_items:
            raise AnalysisInvalid("prohibited or unknown response item")
        required_text = {
            "function_call": ("name", "arguments", "call_id"),
            "custom_tool_call": ("name", "input", "call_id"),
            "custom_tool_call_output": ("call_id",),
            "compaction": ("encrypted_content",),
            "compaction_summary": ("encrypted_content",),
        }.get(kind, ())
        if any(not isinstance(item.get(key), str) for key in required_text):
            raise AnalysisInvalid("response item lacks required text fields")
        if kind == "message" and (
            not isinstance(item.get("role"), str) or not isinstance(item.get("content"), list)
            or not all(isinstance(part, dict) for part in item["content"])
        ):
            raise AnalysisInvalid("malformed response message")
        if kind == "reasoning" and not isinstance(item.get("summary"), list):
            raise AnalysisInvalid("malformed response reasoning")
        if kind in {"function_call_output", "custom_tool_call_output"} and not isinstance(item.get("output"), (str, list)):
            raise AnalysisInvalid("response tool output is missing")
        if kind == "local_shell_call" and (
            not isinstance(item.get("status"), str) or not isinstance(item.get("action"), dict)
        ):
            raise AnalysisInvalid("malformed local shell response")
        for name in (item.get("name"), item.get("namespace")):
            if name is not None and not isinstance(name, str):
                raise AnalysisInvalid("malformed response tool identity")
            if isinstance(name, str) and (
                name in {"send_input", "wait", "close_agent", "resume_agent"}
                or any(marker in name.lower() for marker in ("mcp", "web_search", "remote_tool", "app_tool", "plugin", "collaboration", "spawn_agent"))
            ):
                raise AnalysisInvalid("response contains a prohibited remote tool or child-agent tool")

    for record in rollout:
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            raise AnalysisInvalid("malformed rollout record")
        record_type = record["type"]
        payload = _payload(record)
        if record_type == "session_meta":
            if session is not None:
                raise AnalysisInvalid("duplicate rollout session metadata")
            session = payload
        elif record_type == "turn_context":
            contexts.append(payload)
        elif record_type == "event_msg":
            subtype = payload.get("type")
            if not isinstance(subtype, str) or not subtype:
                raise AnalysisInvalid("rollout event_msg type is missing")
            if subtype in prohibited_event_messages:
                raise AnalysisInvalid("rollout contains a prohibited lifecycle or remote event")
            if subtype not in allowed_event_messages:
                raise AnalysisInvalid(f"unknown rollout event_msg type: {subtype!r}")
            if subtype == "raw_response_item":
                raw_item = payload.get("item")
                if not isinstance(raw_item, dict) or not isinstance(raw_item.get("type"), str):
                    raise AnalysisInvalid("raw response item is malformed")
                validate_response_shape(raw_item)
            elif subtype in {"item_started", "item_completed"}:
                turn_item = payload.get("item")
                allowed_turn_items = {"user_message", "agent_message", "reasoning", "command_execution", "file_change", "todo_list"}
                if not isinstance(turn_item, dict) or turn_item.get("type") not in allowed_turn_items:
                    raise AnalysisInvalid("rollout turn item is prohibited, unknown, or malformed")
            if subtype in {"task_started", "turn_started"}:
                starts += 1
            elif subtype in {"task_complete", "turn_complete"}:
                if payload.get("error") is not None:
                    raise AnalysisInvalid("rollout turn completed with an error")
                completes += 1
            elif subtype == "token_count":
                info = payload.get("info")
                if not isinstance(info, dict) or not isinstance(info.get("total_token_usage"), dict):
                    raise AnalysisInvalid("rollout token_count lacks total_token_usage")
                totals.append(info["total_token_usage"])
            elif subtype == "context_compacted":
                compactions += 1
        elif record_type == "response_item":
            response_type = payload.get("type")
            if not isinstance(response_type, str) or not response_type:
                raise AnalysisInvalid("rollout response_item type is missing")
            if response_type in prohibited_response_items:
                raise AnalysisInvalid("rollout contains a prohibited response item")
            if response_type not in allowed_response_items:
                raise AnalysisInvalid(f"unknown rollout response_item type: {response_type!r}")
            item_id = payload.get("id")
            if item_id is not None:
                if not isinstance(item_id, str) or item_id in response_ids:
                    raise AnalysisInvalid("duplicate rollout response item id")
                response_ids.add(item_id)
            validate_response_shape(payload)
        elif record_type == "compacted":
            compactions += 1
        else:
            raise AnalysisInvalid(f"unknown rollout record type: {record_type!r}")
    if session is None:
        raise AnalysisInvalid("missing rollout session metadata")
    session_id = session.get("id")
    if session_id != exec_analysis.get("thread_id"):
        raise AnalysisInvalid("rollout thread id does not match exec")
    inherited_fields = (
        "parent", "parent_id", "parent_thread_id", "forked_from", "forked_from_id",
        "forked_from_ordinal_exclusive", "history_base", "subagent_history_start_ordinal",
        "agent_nickname", "agent_role", "agent_path",
    )
    if any(key in session and session.get(key) not in (None, "", 0) for key in inherited_fields):
        raise AnalysisInvalid("rollout is resumed, forked, or a child session")
    if session.get("source") != "exec":
        raise AnalysisInvalid("rollout session source is not exec")
    observed_version = session.get("cli_version", session.get("version"))
    expected_rollout_version = version.removeprefix("codex-cli ")
    if observed_version != expected_rollout_version:
        raise AnalysisInvalid("rollout Codex version mismatch")
    if len(contexts) != 1 or contexts[0].get("model") != model:
        raise AnalysisInvalid("rollout model mismatch")
    observed_effort = contexts[0].get("reasoning_effort", contexts[0].get("effort"))
    if observed_effort != effort:
        raise AnalysisInvalid("rollout reasoning effort mismatch")
    if contexts[0].get("multi_agent_version") not in (None, "disabled"):
        raise AnalysisInvalid("rollout turn context enabled multi-agent mode")
    if starts != 1 or completes != 1:
        raise AnalysisInvalid("rollout must contain exactly one started/completed turn")
    if len(totals) < 1:
        raise AnalysisInvalid("rollout has no cumulative token total")
    final_usage = _usage(totals[-1])
    # Repeated cumulative snapshots are legitimate.  A duplicate terminal total
    # after task completion is not.
    terminal_seen = False
    for record in rollout:
        payload = _payload(record) if isinstance(record, dict) else {}
        if record.get("type") == "event_msg" and payload.get("type") in {"task_complete", "turn_complete"}:
            terminal_seen = True
        elif terminal_seen and record.get("type") == "event_msg" and payload.get("type") == "token_count":
            raise AnalysisInvalid("token_count appears after terminal completion")
    expected = exec_analysis["usage"]
    for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
        if final_usage[key] != expected[key]:
            raise AnalysisInvalid("rollout cumulative usage does not match exec totals")
    return {"valid": True, "cumulative_snapshots": len(totals), "usage_matched": True, "compactions": compactions}


def _command_tokens(command: str) -> list[str]:
    """Best-effort routing audit; manual review remains authoritative for shell semantics."""
    try:
        outer = shlex.split(command)
    except ValueError:
        return []
    tokens = list(outer)
    for index, token in enumerate(outer[:-1]):
        if Path(token).name in {"bash", "sh", "zsh"} and outer[index + 1] in {"-c", "-lc"} and index + 2 < len(outer):
            try:
                tokens.extend(shlex.split(outer[index + 2]))
            except ValueError:
                pass
    return tokens


def _validate_arm_commands(commands: list[str], arm: str, sshai: str) -> None:
    for command in commands:
        tokens = _command_tokens(command)
        invokes_sshai = any(token == sshai or Path(token).name == "sshai" for token in tokens)
        if arm == "baseline" and invokes_sshai:
            raise AnalysisInvalid("baseline arm appears to invoke sshai")
        if arm == "sshai" and sshai not in tokens:
            raise AnalysisInvalid("sshai arm command does not contain the pinned executable token")


def _verify_frozen_sources(root: Path, manifest: dict[str, Any]) -> None:
    scan = _study_ancestor_scan(root)
    policy = manifest.get("ancestor_context_policy", {})
    if policy.get("checked") != scan or policy.get("result") != "none-present":
        raise ValueError("study ancestor context scan changed")
    if manifest.get("schema") != SCHEMA or manifest.get("digest") != _manifest_digest(manifest):
        raise ValueError("manifest digest mismatch")
    paths = {
        "harness": _HARNESS, "fixtures": _SOURCE_DIR / "benchmark_issue10_fixtures.py",
        "sandbox": _SOURCE_DIR / "benchmark_issue10_sandbox.py",
        "codex": Path(manifest["codex"]), "sshai": Path(manifest["sshai"]),
    }
    for name, path in paths.items():
        if manifest.get("sources", {}).get(name) != _file_digest(path):
            raise ValueError(f"frozen source changed: {name}")
    for name, digest in manifest.get("protocols", {}).items():
        if _file_digest(_REPO_DIR / "docs" / "benchmarks" / name) != digest:
            raise ValueError(f"frozen protocol changed: {name}")
    if manifest.get("task_ids") != list(TASK_IDS) or manifest.get("scope") != "local-macos":
        raise ValueError("manifest task inventory or scope changed")
    for task_id in TASK_IDS:
        frozen, _ = _task_freeze(task_id)
        if manifest.get("tasks", {}).get(task_id) != frozen:
            raise ValueError(f"frozen fixture implementation changed: {task_id}")
        source = root / "inputs" / task_id
        if _file_digest(source / "prompt.txt") != frozen["prompt_sha256"] or _file_digest(source / "answer-schema.json") != frozen["schema_sha256"]:
            raise ValueError(f"prepared task input changed: {task_id}")
        for relative, digest in frozen["files"].items():
            if _file_digest(source / "fixture" / relative) != digest:
                raise ValueError(f"prepared fixture changed: {task_id}/{relative}")
    expected_slots = []
    for replicate in range(manifest.get("repetitions", 0)):
        order = ("baseline", "sshai") if replicate % 2 == 0 else ("sshai", "baseline")
        for task_id in TASK_IDS:
            task_prompt = _read_bounded(root / "inputs" / task_id / "prompt.txt")
            expected_slots.extend({
                "task": task_id, "replicate": replicate, "arm": arm,
                "prepared_prompt_sha256": _sha(_branch_prompt(task_prompt, arm, manifest["sshai"])),
            } for arm in order)
    if manifest.get("slots") != expected_slots:
        raise ValueError("manifest schedule is not the frozen balanced order")


def _approval(manifest: dict[str, Any], path: Path, allow_paid: bool) -> dict[str, Any]:
    _regular(path, mode_0600=True)
    approval = _read_json(path)
    bindings = {
        "manifest_digest": manifest["digest"], "phase": manifest["phase"],
        "model": manifest["model"], "reasoning_effort": manifest["reasoning_effort"],
        "session_count": len(manifest["slots"]),
        "per_session_timeout_seconds": manifest["timeout_seconds"],
    }
    if any(approval.get(key) != value for key, value in bindings.items()):
        raise ValueError("approval does not exactly match manifest")
    if not allow_paid or approval.get("approved") is not True:
        raise ValueError("paid run requires approved true and --allow-paid-run")
    approved_at = approval.get("approved_at_utc")
    if not isinstance(approved_at, str) or not approved_at.strip():
        raise ValueError("approval requires an explicit approval time")
    if approval.get("budget_acknowledged") is not True or approval.get("no_hard_cap_acknowledged") is not True:
        raise ValueError("approval must acknowledge budget and absence of a hard cap")
    note = approval.get("operator_budget_note")
    if not isinstance(note, str) or not note.strip():
        raise ValueError("approval requires an operator budget note")
    if manifest["phase"] == "measurement":
        reference = approval.get("pilot_report_reference")
        if approval.get("pilot_usage_verified") is not True or not isinstance(reference, str) or not reference.strip():
            raise ValueError("measurement requires pilot verification and reference")
    return approval


def _run_name(slot: dict[str, Any]) -> str:
    return f"{slot['task']}-r{slot['replicate']}-{slot['arm']}"


def _materialize(root: Path, slot: dict[str, Any]) -> tuple[Path, Path, bytes, Path]:
    base = root / "runs" / _run_name(slot)
    _new_dir(base)
    workspace = base / "workspace"
    _mkdir_private(workspace)
    source = root / "inputs" / slot["task"]
    frozen = _read_json(root / "manifest.json")["tasks"][slot["task"]]
    for relative in frozen["files"]:
        destination = workspace / relative
        _make_private_tree(destination.parent)
        _write_new(destination, _read_bounded(source / "fixture" / relative))
    schema = workspace / ".answer-schema.json"
    _write_new(schema, _read_bounded(source / "answer-schema.json"))
    prompt = _read_bounded(source / "prompt.txt")
    return base, workspace, prompt, schema


def _environment_digest(environment: dict[str, str]) -> str:
    return _sha(json.dumps(environment, sort_keys=True).encode())


def _managed_mcp_preflight(codex: Path, workspace: Path, environment: dict[str, str]) -> dict[str, Any]:
    checker = _MCP_PREFLIGHT
    if checker is None:
        try:
            from benchmark_issue10_sandbox import verify_no_managed_mcp as checker
        except ImportError:
            checker = None
    if checker is None:
        raise ValueError("paid launch blocked: no verified offline system/managed MCP preflight is available")
    receipt = checker(codex, workspace, environment=environment)
    checks = receipt.get("checks") if isinstance(receipt, dict) else None
    sources = receipt.get("sources_checked") if isinstance(receipt, dict) else None
    if (not isinstance(receipt, dict) or receipt.get("verified_absent") is not True
            or not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values())
            or not isinstance(sources, list) or not sources or not all(isinstance(value, str) and value for value in sources)):
        raise ValueError("managed MCP preflight did not prove absence across named sources")
    copy = dict(receipt)
    digest = copy.pop("digest", None)
    if digest != _sha(json.dumps(copy, sort_keys=True).encode()):
        raise ValueError("managed MCP preflight receipt digest mismatch")
    if receipt.get("codex_sha256") != _file_digest(codex) or receipt.get("workspace") != str(workspace):
        raise ValueError("managed MCP preflight provenance mismatch")
    if receipt.get("environment_sha256") != _sha(_canon(environment)):
        raise ValueError("managed MCP preflight environment mismatch")
    return receipt


def _slot_environment(base: Path, workspace: Path) -> dict[str, str]:
    home = base / "home"
    return {
        "HOME": str(home), "CODEX_HOME": str(base / "codex-home"),
        "SSHAI_ROOT": str(workspace / ".sshai-root"), "TMPDIR": str(workspace / ".tmp"),
        "PATH": ENV_PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "SHELL": "/bin/bash", "USER": "sshai-benchmark", "LOGNAME": "sshai-benchmark",
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local-share"),
    }


def _qualify(codex: Path, workspace: Path, protected: list[Path], executable_pin: dict[str, str], environment: dict[str, str]) -> dict[str, Any]:
    qualifier = _QUALIFIER
    if qualifier is None:
        from benchmark_issue10_sandbox import qualify as qualifier
    receipt = qualifier(codex, workspace, protected, read_executables=[executable_pin], environment=environment)
    if not isinstance(receipt, dict) or not isinstance(receipt.get("config_overrides"), list) or not isinstance(receipt.get("digest"), str):
        raise ValueError("invalid sandbox qualification receipt")
    copy = dict(receipt)
    digest = copy.pop("digest")
    if digest != _sha(json.dumps(copy, sort_keys=True).encode()):
        raise ValueError("sandbox qualification receipt digest mismatch")
    if receipt.get("workspace") != str(workspace):
        raise ValueError("sandbox qualification receipt workspace mismatch")
    if receipt.get("read_executables") != [executable_pin]:
        raise ValueError("sandbox qualification executable pin mismatch")
    if receipt.get("environment_sha256") != _environment_digest(environment):
        raise ValueError("sandbox qualification receipt environment mismatch")
    return receipt


def _terminate_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the unique process group even when its original leader has exited."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _bounded_process(argv: list[str], prompt: bytes, env: dict[str, str], cwd: Path, timeout: int) -> dict[str, Any]:
    """Incrementally cap output and enforce a wall deadline even after pipe EOF."""
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    stdin_file = tempfile.TemporaryFile(mode="w+b")
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = overflow = interrupted = False
    start_error: str | None = None
    try:
        stdin_file.write(prompt)
        stdin_file.seek(0)
        process = _POPEN(
            argv, stdin=stdin_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = started + timeout
        drain_deadline: float | None = None
        group_cleaned = False
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if drain_deadline is None and now >= deadline:
                timed_out = True
                _terminate_group(process)
                group_cleaned = True
                drain_deadline = now + 5
            if process.poll() is not None and not group_cleaned:
                _terminate_group(process)
                group_cleaned = True
                drain_deadline = now + 5
            if drain_deadline is not None and now >= drain_deadline:
                break
            wait_until = drain_deadline if drain_deadline is not None else deadline
            if selector.get_map():
                ready = selector.select(min(max(wait_until - now, 0), 0.1))
            else:
                time.sleep(min(max(wait_until - now, 0), 0.05))
                ready = []
            for key, _ in ready:
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                target = captured[key.data]
                room = MAX_CAPTURE - len(target)
                target.extend(chunk[:max(room, 0)])
                if len(chunk) > room and not overflow:
                    overflow = True
                    _terminate_group(process)
                    group_cleaned = True
                    drain_deadline = time.monotonic() + 5
        if process.poll() is None:
            _terminate_group(process)
        else:
            process.wait(timeout=5)
    except KeyboardInterrupt:
        interrupted = True
        if process is not None:
            _terminate_group(process)
    except (OSError, subprocess.SubprocessError) as exc:
        start_error = f"{type(exc).__name__}: {exc}"
        if process is not None:
            _terminate_group(process)
    finally:
        stdin_file.close()
        if selector is not None:
            for key in list(selector.get_map().values()):
                try:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                except OSError:
                    pass
            selector.close()
        if process is not None:
            _terminate_group(process)
    return {
        "stdout": bytes(captured["stdout"]), "stderr": bytes(captured["stderr"]),
        "exit_code": process.returncode if process is not None else None,
        "timed_out": timed_out, "capture_overflow": overflow,
        "interrupted": interrupted, "start_error": start_error,
        "duration_seconds": time.monotonic() - started,
        "pid": process.pid if process is not None else None,
    }


def _workspace_integrity(workspace: Path, task_record: dict[str, Any]) -> dict[str, Any]:
    changed = []
    directories = {workspace}
    for relative in task_record["files"]:
        cursor = (workspace / relative).parent
        while cursor != workspace:
            directories.add(cursor)
            cursor = cursor.parent
    for directory in directories:
        if directory.is_symlink() or not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            changed.append(f"directory:{directory.relative_to(workspace) if directory != workspace else '.'}")
    for relative, expected in task_record["files"].items():
        path = workspace / relative
        try:
            actual = _file_digest(path)
            safe_mode = stat.S_IMODE(path.stat().st_mode) == 0o600
        except (OSError, ValueError):
            actual, safe_mode = None, False
        if actual != expected or not safe_mode:
            changed.append(relative)
    schema = workspace / ".answer-schema.json"
    if not schema.is_file() or schema.is_symlink() or stat.S_IMODE(schema.stat().st_mode) != 0o600 or _file_digest(schema) != task_record["schema_sha256"]:
        changed.append(".answer-schema.json")
    return {"passed": not changed, "changed": sorted(changed)}


def _save_evidence_index(base: Path, names: list[str], fixture_integrity: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    files = {name: _file_digest(base / name) for name in names}
    value = {"files": files, "manifest_sha256": _file_digest(manifest_path), "fixture_integrity": fixture_integrity}
    value["digest"] = _sha(_canon(value))
    _write_new(base / "provenance.json", _canon(value))
    return value


def _safe_failure(base: Path, slot_index: int, slot: dict[str, Any], failure: str, error: BaseException | str) -> None:
    for name in ("events.jsonl", "stderr.txt", "rollout.jsonl", "last-message.json"):
        if not (base / name).exists():
            _write_new(base / name, b"")
    process_path = base / "process.json"
    if not process_path.exists():
        process = {
            "launch_attempted": False, "exit_code": None, "timed_out": False,
            "capture_overflow": False, "interrupted": isinstance(error, KeyboardInterrupt),
            "start_error": None, "duration_seconds": 0.0, "failure": failure,
        }
        _write_new(process_path, _canon(process))
    result_path = base / "result.json"
    if not result_path.exists():
        result = {"slot": slot_index, "slot_spec": slot, "analysis": {"valid": False, "error": failure}, "quality": {"passed": False, "errors": [failure]}, "manual_review_required": True}
        _write_new(result_path, _canon(result))


def run_one(args: argparse.Namespace) -> int:
    root = _physical(Path(args.root))
    manifest = _read_json(root / "manifest.json")
    _verify_frozen_sources(root, manifest)
    approval_path = _physical(Path(args.approval))
    auth_path = _physical(Path(args.auth_file))
    _regular(auth_path, mode_0600=True)
    _approval(manifest, approval_path, args.allow_paid_run)
    index = args.slot
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(manifest["slots"]):
        raise ValueError("invalid slot")
    for prior in range(index):
        prior_base = root / "runs" / _run_name(manifest["slots"][prior])
        if not (prior_base / "process.json").is_file() or (prior_base / "process.json").is_symlink():
            raise ValueError("strict schedule requires immutable metadata for every prior slot")
    slot = manifest["slots"][index]
    base_path = root / "runs" / _run_name(slot)
    if base_path.exists() or base_path.is_symlink():
        raise ValueError("slot already attempted; continue with the next slot, never rerun it")
    lock = root / ".run.lock"
    try:
        _atomic_new(lock, _canon({"pid": os.getpid(), "slot": index, "time": int(time.time())}))
    except (FileExistsError, ValueError):
        raise ValueError("another run-one process holds the study lock") from None
    base: Path | None = None
    try:
        base, workspace, prompt_base, schema = _materialize(root, slot)
        home, codex_home = (base / name for name in ("home", "codex-home"))
        sshai_root, tmpdir = workspace / ".sshai-root", workspace / ".tmp"
        xdg_config, xdg_cache, xdg_data = (home / name for name in (".config", ".cache", ".local-share"))
        for directory in (home, codex_home, sshai_root, tmpdir):
            _mkdir_private(directory)
        for directory in (xdg_config, xdg_cache, xdg_data):
            _mkdir_private(directory)
        (codex_home / "auth.json").symlink_to(auth_path)
        env = _slot_environment(base, workspace)
        codex = Path(manifest["codex"])
        protected = [_physical(Path.home()), _physical(_REPO_DIR), root, approval_path, auth_path]
        try:
            mcp_receipt = _managed_mcp_preflight(codex, workspace, env)
            _write_new(base / "mcp-preflight.json", _canon(mcp_receipt))
            executable_pin = {
                "path": manifest["sshai"], "sha256": manifest["sources"]["sshai"],
                "help_sha256": manifest["sshai_help_sha256"],
            }
            receipt = _qualify(codex, workspace, protected, executable_pin, env)
        except Exception as exc:
            _safe_failure(base, index, slot, "launch preflight or sandbox qualification failed", str(exc))
            raise ValueError("launch preflight or sandbox qualification failed") from exc
        _write_new(base / "sandbox-receipt.json", _canon(receipt))
        prompt = _branch_prompt(prompt_base, slot["arm"], manifest["sshai"])
        if _sha(prompt) != slot.get("prepared_prompt_sha256"):
            raise ValueError("prepared slot prompt digest mismatch")
        _write_new(base / "prompt.txt", prompt)
        last_message = base / "last-message.json"
        argv = [
            str(codex), "exec", "--model", manifest["model"],
            "-c", f'model_reasoning_effort="{manifest["reasoning_effort"]}"',
            "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
            "-c", 'agents.enabled=false',
            "-c", 'features.multi_agent=false', "-c", 'features.multi_agent_v2=false',
            "-c", 'features.apps=false', "-c", 'features.plugins=false',
            "--ignore-user-config", "--ignore-rules", "--json", "--color", "never",
            "--skip-git-repo-check", "--output-schema", str(schema),
            "--output-last-message", str(last_message), "-C", str(workspace), "-",
        ]
        for override in receipt["config_overrides"]:
            if not isinstance(override, str):
                raise ValueError("sandbox config override is not text")
            argv.extend(["-c", override])
        process_capture = _bounded_process(argv, prompt, env, workspace, manifest["timeout_seconds"])
        _write_new(base / "events.jsonl", process_capture.pop("stdout"))
        _write_new(base / "stderr.txt", process_capture.pop("stderr"))
        process_meta = {
            "launch_attempted": True, **process_capture,
            "environment_sha256": _environment_digest(env),
            "sandbox_config_sha256": _sha(_canon(receipt["config_overrides"])),
            "argv_sha256": _sha(_canon(argv)),
            "approval_path": str(approval_path), "auth_source_path": str(auth_path),
        }
        _write_new(base / "process.json", _canon(process_meta))
        rollouts = [path for path in codex_home.rglob("*.jsonl") if path.is_file() and not path.is_symlink()]
        if len(rollouts) == 1:
            _write_new(base / "rollout.jsonl", _read_bounded(rollouts[0]))
        else:
            _write_new(base / "rollout.jsonl", b"")
        if not last_message.exists():
            _write_new(last_message, b"")
        else:
            _regular(last_message)
            last_message.chmod(0o600)
        analysis: dict[str, Any]
        answer: Any = None
        parsed_events: dict[str, Any] | None = None
        try:
            parsed_events = analyze_events(_parse_jsonl(_read_bounded(base / "events.jsonl"), "event"))
            _validate_arm_commands(parsed_events["command_lines"], slot["arm"], manifest["sshai"])
            rollout = _parse_jsonl(_read_bounded(base / "rollout.jsonl"), "rollout")
            parsed_events["rollout"] = validate_rollout(rollout, parsed_events, manifest["model"], manifest["reasoning_effort"], manifest["codex_version"])
            parsed_events["compactions"] = parsed_events["rollout"]["compactions"]
            answer = _read_json(last_message)
            try:
                event_answer = json.loads(parsed_events["final_message"])
            except json.JSONDecodeError as exc:
                raise AnalysisInvalid("final event message is not JSON") from exc
            if event_answer != answer:
                raise AnalysisInvalid("final answer does not match terminal agent message")
            if process_meta["exit_code"] != 0 or process_meta["timed_out"] or process_meta["capture_overflow"] or process_meta["interrupted"] or process_meta["start_error"]:
                raise AnalysisInvalid("process did not complete successfully")
            parsed_events["valid"] = True
            analysis = parsed_events
        except Exception as exc:
            analysis = {"valid": False, "error": str(exc)}
            if parsed_events is not None:
                for key in ("usage", "completed_commands", "captured_command_output_bytes", "compactions"):
                    if key in parsed_events:
                        analysis[key] = parsed_events[key]
            try:
                answer = _read_json(last_message)
            except Exception:
                answer = None
        try:
            quality = grade(slot["task"], answer)
        except Exception as exc:
            quality = {"passed": False, "errors": [f"grader failure: {exc}"]}
        fixture_integrity = _workspace_integrity(workspace, manifest["tasks"][slot["task"]])
        if not fixture_integrity["passed"]:
            analysis = {"valid": False, "error": "fixture changed during run", "usage": analysis.get("usage")}
        result = {
            "slot": index, "slot_spec": slot, "analysis": analysis,
            "quality": quality, "manual_review_required": True,
        }
        _write_new(base / "result.json", _canon(result))
        names = ["prompt.txt", "events.jsonl", "stderr.txt", "rollout.jsonl", "last-message.json", "process.json", "mcp-preflight.json", "sandbox-receipt.json", "result.json"]
        _save_evidence_index(base, names, fixture_integrity, root / "manifest.json")
        print(json.dumps({"slot": index, "process_ok": process_meta["exit_code"] == 0 and not process_meta["timed_out"] and not process_meta["capture_overflow"], "quality_passed": quality["passed"]}, sort_keys=True))
        return 0 if analysis.get("valid") is True else 1
    except BaseException as exc:
        if base is not None:
            _safe_failure(base, index, slot, "run setup or evidence finalization failed", exc)
        raise
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def _validate_run_receipts(base: Path, workspace: Path, root: Path, manifest: dict[str, Any], process: dict[str, Any]) -> None:
    sandbox = _read_json(base / "sandbox-receipt.json")
    sandbox_copy = dict(sandbox)
    sandbox_digest = sandbox_copy.pop("digest", None)
    if sandbox_digest != _sha(json.dumps(sandbox_copy, sort_keys=True).encode()):
        raise AnalysisInvalid("sandbox receipt digest mismatch")
    checks = sandbox.get("checks")
    if not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values()):
        raise AnalysisInvalid("sandbox receipt checks are not all successful")
    if sandbox.get("workspace") != str(workspace) or sandbox.get("codex_version") != manifest["codex_version"] or sandbox.get("codex_sha256") != manifest["sources"]["codex"]:
        raise AnalysisInvalid("sandbox receipt provenance mismatch")
    protected = set(sandbox.get("protected_roots", []))
    required_protected = {
        str(_physical(Path.home())), str(_physical(_REPO_DIR)), str(root),
        process.get("approval_path"), process.get("auth_source_path"),
    }
    if None in required_protected or protected != required_protected or manifest["codex"] in protected or manifest["sshai"] in protected:
        raise AnalysisInvalid("sandbox protected-root inventory mismatch")
    overrides = sandbox.get("config_overrides")
    if not isinstance(overrides, list) or not all(isinstance(value, str) for value in overrides):
        raise AnalysisInvalid("sandbox config overrides malformed")
    environment = _slot_environment(base, workspace)
    expected_pin = {
        "path": manifest["sshai"], "sha256": manifest["sources"]["sshai"],
        "help_sha256": manifest["sshai_help_sha256"],
    }
    if sandbox.get("read_executables") != [expected_pin]:
        raise AnalysisInvalid("sandbox executable pin mismatch")
    if (sandbox.get("environment_sha256") != _environment_digest(environment)
            or process.get("sandbox_config_sha256") != _sha(_canon(overrides))
            or process.get("environment_sha256") != _environment_digest(environment)):
        raise AnalysisInvalid("qualified config/environment was not execution config/environment")
    mcp = _read_json(base / "mcp-preflight.json")
    mcp_copy = dict(mcp)
    mcp_digest = mcp_copy.pop("digest", None)
    mcp_checks, mcp_sources = mcp.get("checks"), mcp.get("sources_checked")
    if (mcp_digest != _sha(json.dumps(mcp_copy, sort_keys=True).encode()) or mcp.get("verified_absent") is not True
            or not isinstance(mcp_checks, dict) or not mcp_checks or not all(value is True for value in mcp_checks.values())
            or not isinstance(mcp_sources, list) or not mcp_sources):
        raise AnalysisInvalid("managed MCP preflight receipt invalid")
    if mcp.get("codex_sha256") != manifest["sources"]["codex"] or mcp.get("workspace") != str(workspace) or mcp.get("environment_sha256") != _sha(_canon(environment)):
        raise AnalysisInvalid("managed MCP preflight provenance mismatch")


def _evidence(root: Path, manifest: dict[str, Any], index: int) -> dict[str, Any]:
    slot = manifest["slots"][index]
    base = root / "runs" / _run_name(slot)
    row: dict[str, Any] = {
        "slot": index, "slot_spec": slot, "state": "missing", "analysis": {"valid": False},
        "quality": {"passed": False, "errors": ["missing slot"]}, "review": {"complete": False},
    }
    if not base.is_dir() or base.is_symlink():
        return row
    row["state"] = "incomplete"
    process: dict[str, Any] | None = None
    events: dict[str, Any] | None = None
    process_path = base / "process.json"
    if process_path.is_file() and not process_path.is_symlink():
        try:
            process = _read_json(process_path)
            row["process"] = {key: process.get(key) for key in ("exit_code", "timed_out", "capture_overflow", "interrupted", "start_error", "duration_seconds", "failure")}
            row["state"] = "attempted_failed"
        except Exception:
            pass
    try:
        provenance = _read_json(base / "provenance.json")
        digest_copy = dict(provenance)
        digest = digest_copy.pop("digest", None)
        if digest != _sha(_canon(digest_copy)):
            raise AnalysisInvalid("provenance digest mismatch")
        required_files = {"prompt.txt", "events.jsonl", "stderr.txt", "rollout.jsonl", "last-message.json", "process.json", "mcp-preflight.json", "sandbox-receipt.json", "result.json"}
        files = provenance.get("files")
        if not isinstance(files, dict) or set(files) != required_files:
            raise AnalysisInvalid("provenance file inventory mismatch")
        if provenance.get("manifest_sha256") != _file_digest(root / "manifest.json"):
            raise AnalysisInvalid("evidence manifest hash mismatch")
        for name, expected in files.items():
            if _file_digest(base / name) != expected:
                raise AnalysisInvalid(f"evidence hash mismatch: {name}")
        workspace = base / "workspace"
        fixture_integrity = _workspace_integrity(workspace, manifest["tasks"][slot["task"]])
        if not fixture_integrity["passed"] or fixture_integrity != provenance.get("fixture_integrity"):
            raise AnalysisInvalid("fixture after-run integrity mismatch")
        if _file_digest(base / "prompt.txt") != slot["prepared_prompt_sha256"]:
            raise AnalysisInvalid("prepared prompt hash mismatch")
        process = _read_json(process_path)
        row["process"] = {key: process.get(key) for key in ("exit_code", "timed_out", "capture_overflow", "interrupted", "start_error", "duration_seconds", "failure")}
        _validate_run_receipts(base, workspace, root, manifest, process)
        events = analyze_events(_parse_jsonl(_read_bounded(base / "events.jsonl"), "event"))
        _validate_arm_commands(events["command_lines"], slot["arm"], manifest["sshai"])
        events["rollout"] = validate_rollout(_parse_jsonl(_read_bounded(base / "rollout.jsonl"), "rollout"), events, manifest["model"], manifest["reasoning_effort"], manifest["codex_version"])
        events["compactions"] = events["rollout"]["compactions"]
        answer = _read_json(base / "last-message.json")
        row["quality"] = grade(slot["task"], answer)
        if json.loads(events["final_message"]) != answer:
            raise AnalysisInvalid("answer/event mismatch")
        if process.get("exit_code") != 0 or any(process.get(key) for key in ("timed_out", "capture_overflow", "interrupted", "start_error")):
            raise AnalysisInvalid("process lifecycle invalid")
        row["analysis"] = {**events, "valid": True}
        row["state"] = "complete"
        review_path = base / "manual-review.json"
        if review_path.exists() and not review_path.is_symlink():
            review = _read_json(review_path)
            bound = review.get("provenance_digest") == provenance["digest"] and review.get("slot") == index
            flags = all(review.get(key) is True for key in ("adherence_passed", "integrity_passed", "quality_passed", "tool_surface_passed"))
            row["review"] = {"complete": bound and flags, "bound": bound, "passed": flags}
    except Exception as exc:
        retained = {"valid": False, "error": str(exc)}
        if events is not None:
            for key in ("usage", "completed_commands", "failed_commands", "failed_file_changes", "captured_command_output_bytes", "compactions"):
                if key in events:
                    retained[key] = events[key]
        row["analysis"] = retained
    return row


def _metric(values: list[float | int]) -> dict[str, Any]:
    return {
        "count": len(values), "mean": statistics.mean(values) if values else None,
        "range": [min(values), max(values)] if values else None,
        "stdev": statistics.stdev(values) if len(values) > 1 else None,
    }


def _invalidate_reused_threads(rows: list[dict[str, Any]]) -> None:
    by_thread: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        analysis = row.get("analysis", {}) if isinstance(row, dict) else {}
        thread_id = analysis.get("thread_id")
        if analysis.get("valid") is True:
            if not isinstance(thread_id, str) or not thread_id:
                row["analysis"] = {**analysis, "valid": False, "error": "valid slot lacks thread id"}
                row["state"] = "invalid"
            else:
                by_thread.setdefault(thread_id, []).append(row)
    for duplicated in by_thread.values():
        if len(duplicated) < 2:
            continue
        for row in duplicated:
            previous = row["analysis"]
            row["analysis"] = {**previous, "valid": False, "error": "thread id reused across manifest slots"}
            row["state"] = "invalid"


def paired_report(rows: list[dict[str, Any]], expected_slots: list[dict[str, Any]] | None = None, phase: str = "measurement") -> dict[str, Any]:
    if expected_slots is not None and len(rows) != len(expected_slots):
        raise AnalysisInvalid("report must include every manifest slot")
    _invalidate_reused_threads(rows)
    groups: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    states: dict[str, int] = {}
    slot_summaries: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise AnalysisInvalid("invalid report row")
        spec = row.get("slot_spec")
        if expected_slots is not None and spec != expected_slots[position]:
            raise AnalysisInvalid("report row does not match manifest schedule")
        if not isinstance(spec, dict) or spec.get("task") not in TASK_IDS or isinstance(spec.get("replicate"), bool) or not isinstance(spec.get("replicate"), int) or spec.get("arm") not in {"baseline", "sshai"}:
            raise AnalysisInvalid("invalid report slot")
        key = (spec["task"], spec["replicate"])
        if spec["arm"] in groups.setdefault(key, {}):
            raise AnalysisInvalid("duplicate report arm")
        groups[key][spec["arm"]] = row
        state = row.get("state", "unknown")
        states[state] = states.get(state, 0) + 1
        analysis = row.get("analysis", {})
        usage = analysis.get("usage") if isinstance(analysis.get("usage"), dict) else None
        process = row.get("process", {})
        slot_summaries.append({
            "slot": row.get("slot", position), "task": spec["task"], "replicate": spec["replicate"],
            "arm": spec["arm"], "state": state, "analysis_valid": analysis.get("valid") is True,
            "failure_class": None if analysis.get("valid") is True else "missing_or_invalid_evidence",
            "quality_passed": row.get("quality", {}).get("passed") is True,
            "review_complete": row.get("review", {}).get("complete") is True,
            "usage": usage,
            "captured_command_output_bytes": analysis.get("captured_command_output_bytes"),
            "completed_commands": analysis.get("completed_commands"),
            "compactions": analysis.get("compactions"),
            "duration_seconds": process.get("duration_seconds"),
            "timed_out": process.get("timed_out"), "capture_overflow": process.get("capture_overflow"),
            "interrupted": process.get("interrupted"), "exit_code": process.get("exit_code"),
        })
    pairs = []
    complete_differences: list[int] = []
    metrics = {name: [] for name in ("baseline_input_tokens", "sshai_input_tokens", "baseline_cached_input_tokens", "sshai_cached_input_tokens", "baseline_output_tokens", "sshai_output_tokens", "baseline_duration_seconds", "sshai_duration_seconds", "baseline_tool_output_bytes", "sshai_tool_output_bytes")}
    for key in sorted(groups):
        arms = groups[key]
        structurally_complete = all(
            arm in arms and arms[arm].get("analysis", {}).get("valid") is True
            for arm in ("baseline", "sshai")
        )
        quality_complete = structurally_complete and all(arms[arm].get("quality", {}).get("passed") is True for arm in ("baseline", "sshai"))
        reviews_complete = quality_complete and all(arms[arm].get("review", {}).get("complete") is True for arm in ("baseline", "sshai"))
        pair = {"task": key[0], "replicate": key[1], "data_complete": structurally_complete, "quality_complete": quality_complete, "reviews_complete": reviews_complete, "primary_complete": reviews_complete}
        if structurally_complete:
            for arm in ("baseline", "sshai"):
                usage = arms[arm]["analysis"]["usage"]
                metrics[f"{arm}_input_tokens"].append(usage["input_tokens"])
                metrics[f"{arm}_cached_input_tokens"].append(usage["cached_input_tokens"])
                metrics[f"{arm}_output_tokens"].append(usage["output_tokens"])
                metrics[f"{arm}_tool_output_bytes"].append(arms[arm]["analysis"]["captured_command_output_bytes"])
                duration = arms[arm].get("process", {}).get("duration_seconds")
                if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                    metrics[f"{arm}_duration_seconds"].append(duration)
            baseline = arms["baseline"]["analysis"]["usage"]["input_tokens"]
            sshai = arms["sshai"]["analysis"]["usage"]["input_tokens"]
            pair.update({"baseline_input_tokens": baseline, "sshai_input_tokens": sshai, "difference_baseline_minus_sshai": baseline - sshai, "relative_reduction": (baseline - sshai) / baseline})
            if reviews_complete:
                complete_differences.append(baseline - sshai)
        pairs.append(pair)
    all_primary = bool(pairs) and all(pair["primary_complete"] for pair in pairs)
    all_data_complete = bool(pairs) and all(pair["data_complete"] for pair in pairs)
    quality_regression = any(
        pair["data_complete"]
        and groups[(pair["task"], pair["replicate"])]["baseline"].get("quality", {}).get("passed") is True
        and groups[(pair["task"], pair["replicate"])]["sshai"].get("quality", {}).get("passed") is not True
        for pair in pairs
    )
    task_summaries = []
    for task_id in TASK_IDS:
        task_pairs = [pair for pair in pairs if pair["task"] == task_id]
        task_complete = bool(task_pairs) and all(pair["primary_complete"] for pair in task_pairs)
        values = [pair["difference_baseline_minus_sshai"] for pair in task_pairs] if task_complete else []
        task_summaries.append({"task": task_id, "expected_pair_count": len(task_pairs), "primary_population_complete": task_complete, "primary_difference_baseline_minus_sshai": _metric(values)})
    primary_population = complete_differences if all_primary else []
    if all_data_complete and quality_regression:
        decision = "quality regression"
    elif not all_primary:
        decision = "invalid" if not all_data_complete else "inconclusive"
    elif primary_population and statistics.mean(primary_population) > 0:
        decision = "descriptive reduction"
    elif primary_population and statistics.mean(primary_population) < 0:
        decision = "measured increase"
    else:
        decision = "no clear difference"
    return {
        "schema": REPORT_SCHEMA, "phase": phase, "slot_count": len(rows), "state_counts": dict(sorted(states.items())),
        "slots": slot_summaries, "pair_count": len(pairs),
        "primary_complete_pair_count": len(complete_differences),
        "pairs": pairs, "task_summaries": task_summaries,
        "metrics": {name: _metric(values) for name, values in sorted(metrics.items())},
        "primary_population_complete": all_primary,
        "primary_difference_baseline_minus_sshai": _metric(primary_population),
        "decision": decision,
        "positive_claim_allowed": phase == "measurement" and decision == "descriptive reduction" and all_primary,
        "statistical_claim_allowed": False,
        "limitations": ["Codex totals are reported counters, not proof of exact provider billing.", "Repeated deterministic fixtures do not represent population diversity.", "No global managed-MCP kill switch exists; launch requires a fail-closed offline absence preflight plus pilot tool-surface review.", "Dispersion is descriptive; no significance claim is made."],
    }


def analyze_root(args: argparse.Namespace) -> int:
    root = _physical(Path(args.root))
    manifest = _read_json(root / "manifest.json")
    _verify_frozen_sources(root, manifest)
    rows = [_evidence(root, manifest, index) for index in range(len(manifest["slots"]))]
    report = paired_report(rows, manifest["slots"], manifest["phase"])
    if args.out is not None:
        _write_new(_physical(Path(args.out), must_exist=False), _canon(report))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


def record_review(args: argparse.Namespace) -> int:
    root = _physical(Path(args.root))
    manifest = _read_json(root / "manifest.json")
    _verify_frozen_sources(root, manifest)
    if not 0 <= args.slot < len(manifest["slots"]):
        raise ValueError("invalid slot")
    base = root / "runs" / _run_name(manifest["slots"][args.slot])
    provenance = _read_json(base / "provenance.json")
    if not args.reviewer.strip():
        raise ValueError("reviewer is required")
    review = {
        "slot": args.slot, "reviewer": args.reviewer.strip(),
        "reviewed_at": int(time.time()), "provenance_digest": provenance["digest"],
        "adherence_passed": args.adherence_passed,
        "integrity_passed": args.integrity_passed,
        "quality_passed": args.quality_passed,
        "tool_surface_passed": args.tool_surface_passed,
        "note": args.note,
    }
    _write_new(base / "manual-review.json", _canon(review))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("root")
    command.add_argument("--codex", required=True)
    command.add_argument("--sshai", required=True)
    command.add_argument("--model", required=True)
    command.add_argument("--reasoning-effort", required=True)
    command.add_argument("--repetitions", type=int, required=True)
    command.add_argument("--timeout-seconds", type=int, required=True)
    command.add_argument("--phase", choices=("pilot", "measurement"), required=True)
    command.set_defaults(function=prepare)
    command = commands.add_parser("run-one")
    command.add_argument("root")
    command.add_argument("--approval", required=True)
    command.add_argument("--auth-file", required=True)
    command.add_argument("--slot", type=int, required=True)
    command.add_argument("--allow-paid-run", action="store_true")
    command.set_defaults(function=run_one)
    command = commands.add_parser("analyze")
    command.add_argument("root")
    command.add_argument("--out", help="optional new output file; stdout is always emitted")
    command.set_defaults(function=analyze_root)
    command = commands.add_parser("record-review", aliases=["review"])
    command.add_argument("root")
    command.add_argument("--slot", type=int, required=True)
    command.add_argument("--reviewer", required=True)
    command.add_argument("--adherence-passed", action="store_true")
    command.add_argument("--integrity-passed", action="store_true")
    command.add_argument("--quality-passed", action="store_true")
    command.add_argument("--tool-surface-passed", action="store_true")
    command.add_argument("--note", default="")
    command.set_defaults(function=record_review)
    try:
        args = parser.parse_args(argv)
        return args.function(args)
    except (AnalysisInvalid, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"benchmark_issue10: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
