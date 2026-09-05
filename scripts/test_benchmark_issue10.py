#!/usr/bin/env python3
"""Comprehensive offline tests for the Issue 10 runner.

All Codex executions use a synthetic subprocess and synthetic auth file.  The
suite never reads the user's auth, calls a model, or uses the network.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

SCRIPT = Path(__file__).with_name("benchmark_issue10.py")
SPEC = importlib.util.spec_from_file_location("benchmark_issue10_tested", SCRIPT)
assert SPEC and SPEC.loader
B = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(B)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_invalid(function, text: str) -> None:
    try:
        function()
    except (B.AnalysisInvalid, ValueError) as exc:
        check(text in str(exc), f"expected {text!r}, received {exc!r}")
    else:
        raise AssertionError(f"invalid case was accepted: {text}")


def good_answer(task: str) -> dict[str, Any]:
    return B.build_task(task)["expected_answer"]


def events(thread: str = "thread-1", task: str = "incident", *, output: int = 2) -> list[dict[str, Any]]:
    answer = json.dumps(good_answer(task), sort_keys=True, separators=(",", ":"))
    return [
        {"type": "thread.started", "thread_id": thread},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "msg-progress", "type": "agent_message", "text": "Investigating"}},
        {"type": "item.started", "item": {"id": "todo-1", "type": "todo_list", "items": [{"text": "inspect", "completed": False}]}},
        {"type": "item.updated", "item": {"id": "todo-1", "type": "todo_list", "items": [{"text": "inspect", "completed": True}]}},
        {"type": "item.completed", "item": {"id": "todo-1", "type": "todo_list", "items": [{"text": "inspect", "completed": True}]}},
        {"type": "item.started", "item": {"id": "cmd-1", "type": "command_execution", "command": "grep fixture", "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "cmd-1", "type": "command_execution", "status": "completed", "exit_code": 0, "command": "grep fixture", "aggregated_output": "abc"}},
        {"type": "item.completed", "item": {"id": "msg-1", "type": "agent_message", "text": answer}},
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": output}},
    ]


def rollout(thread: str = "thread-1", model: str = "test-model", effort: str = "high", *, output: int = 2) -> list[dict[str, Any]]:
    return [
        {"type": "session_meta", "payload": {"id": thread, "cli_version": "0.151.0", "source": "exec"}},
        {"type": "turn_context", "payload": {"model": model, "reasoning_effort": effort}},
        {"type": "response_item", "payload": {"id": "rs-1", "type": "reasoning", "summary": []}},
        {"type": "response_item", "payload": {"id": "msg-rollout", "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "working"}]}},
        {"type": "response_item", "payload": {"id": "fc-1", "type": "function_call", "name": "exec_command", "arguments": "{}", "call_id": "call-1"}},
        {"type": "response_item", "payload": {"id": "fco-1", "type": "function_call_output", "call_id": "call-1", "output": "ok"}},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "event_msg", "payload": {"type": "agent_message", "message": "normal final"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": output}}}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]


FAKE_CODEX = r'''#!/usr/bin/python3
import json, os, pathlib, sys
if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.151.0")
    raise SystemExit(0)
args = sys.argv[1:]
base = pathlib.Path(args[args.index("--output-last-message") + 1]).parent
workspace = pathlib.Path(args[args.index("-C") + 1])
prompt = sys.stdin.read()
if (workspace / "logs" / "api.log").exists():
    task = "incident"
    answer = {"pool":"primary","error_code":"pool_timeout","in_use":40,"max":40,"endpoint":"/v1/checkout","evidence":[{"file":"logs/api.log","line":422},{"file":"logs/api.log","line":423}]}
elif (workspace / "inventory" / "hosts.csv").exists():
    task = "config-drift"
    answer = {"host":"worker-152","changes":[{"key":"tls_verify","before":"true","after":"false"},{"key":"batch_size","before":"500","after":"5000"}],"evidence":[{"file":"baseline/telemetry-agent.yaml","line":5},{"file":"hosts/worker-152/telemetry-agent.yaml","line":5},{"file":"baseline/telemetry-agent.yaml","line":6},{"file":"hosts/worker-152/telemetry-agent.yaml","line":6}]}
else:
    task = "snapshot-diff"
    answer = {"path":"/etc/payments/routing.json","before_hash":"0aa17c5e9d4b82f1","after_hash":"f09b4e0c218ac673","before_size":812,"after_size":812,"evidence":[{"file":"snapshots/before.manifest","line":704},{"file":"snapshots/after.manifest","line":704}]}
thread = "fake-" + base.name
text = json.dumps(answer, sort_keys=True, separators=(",", ":"))
command = "grep fixture"
if "sshai branch: execute commands only through " in prompt:
    pinned = prompt.split("sshai branch: execute commands only through ", 1)[1].split(" local --shell bash", 1)[0]
    command = pinned + " local --shell bash -- 'grep fixture'"
ev = [{"type":"thread.started","thread_id":thread},{"type":"turn.started"},{"type":"item.started","item":{"id":"cmd","type":"command_execution"}},{"type":"item.completed","item":{"id":"cmd","type":"command_execution","status":"completed","exit_code":0,"command":command,"aggregated_output":"filtered"}},{"type":"item.completed","item":{"id":"msg","type":"agent_message","text":text}},{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":3,"output_tokens":2}}]
for item in ev: print(json.dumps(item, separators=(",", ":")), flush=True)
pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(text)
roll = pathlib.Path(os.environ["CODEX_HOME"]) / "sessions" / "fake.jsonl"
roll.parent.mkdir(mode=0o700)
records = [{"type":"session_meta","payload":{"id":thread,"cli_version":"0.151.0","source":"exec"}},{"type":"turn_context","payload":{"model":"test-model","reasoning_effort":"high"}},{"type":"event_msg","payload":{"type":"task_started"}},{"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"cached_input_tokens":3,"output_tokens":2}}}},{"type":"event_msg","payload":{"type":"task_complete"}}]
roll.write_text("".join(json.dumps(x, separators=(",", ":"))+"\n" for x in records))
(base / "fake-invocation.json").write_text(json.dumps({"argv":sys.argv[1:],"env":dict(os.environ),"prompt":prompt,"auth_link":str((pathlib.Path(os.environ["CODEX_HOME"])/"auth.json").readlink())}, sort_keys=True))
'''

FAKE_SSHAI = "#!/bin/sh\necho 'sshai offline-test'\n"


class Study:
    def __init__(self, parent: Path, phase: str = "pilot") -> None:
        parent.mkdir(mode=0o700)
        self.root = parent / "study"
        self.codex = parent / "codex"
        self.sshai = parent / "sshai"
        self.codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.sshai.write_text(FAKE_SSHAI, encoding="utf-8")
        self.codex.chmod(0o700)
        self.sshai.chmod(0o700)
        original_native = B._native_codex
        B._native_codex = lambda path: None
        try:
            rc = B.main(["prepare", str(self.root), "--codex", str(self.codex), "--sshai", str(self.sshai), "--model", "test-model", "--reasoning-effort", "high", "--repetitions", "1", "--timeout-seconds", "2", "--phase", phase])
        finally:
            B._native_codex = original_native
        check(rc == 0, "prepare failed")
        self.approval = self.root / "approval-template.json"
        approval = json.loads(self.approval.read_text())
        self.default_approval_was_false = approval.get("approved") is False
        approval.update({"approved": True, "approved_at_utc": "2026-09-05T00:00:00Z", "budget_acknowledged": True, "no_hard_cap_acknowledged": True, "operator_budget_note": "offline synthetic test; zero provider budget"})
        if phase == "measurement":
            approval.update({"pilot_usage_verified": True, "pilot_report_reference": "sha256:test-pilot"})
        self.approval.write_bytes(B._canon(approval))
        self.approval.chmod(0o600)
        self.auth = parent / "synthetic-auth.json"
        self.auth.write_text('{"synthetic":true}\n', encoding="utf-8")
        self.auth.chmod(0o600)

    def run(self, slot: int, *, paid: bool = True) -> int:
        argv = ["run-one", str(self.root), "--approval", str(self.approval), "--auth-file", str(self.auth), "--slot", str(slot)]
        if paid:
            argv.append("--allow-paid-run")
        return B.main(argv)


def fake_mcp_preflight(codex: Path, workspace: Path, *, environment: dict[str, str]) -> dict[str, Any]:
    value = {
        "verified_absent": True, "workspace": str(workspace),
        "codex_sha256": B._file_digest(codex),
        "environment_sha256": B._sha(B._canon(environment)),
        "sources_checked": ["synthetic-offline-system-config"],
        "checks": {"synthetic_system_managed_cloud_absent": True},
    }
    value["digest"] = B._sha(json.dumps(value, sort_keys=True).encode())
    return value


def fake_qualifier(codex: Path, workspace: Path, protected: list[Path], *, read_executables: list[dict[str, str]], environment: dict[str, str]) -> dict[str, Any]:
    expected_keys = {"HOME", "CODEX_HOME", "SSHAI_ROOT", "TMPDIR", "PATH", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"}
    check(set(environment) == expected_keys, f"environment allowlist differs: {set(environment)}")
    check(Path(environment["SSHAI_ROOT"]).is_dir(), "SSHAI_ROOT is not writable directory")
    probe = Path(environment["SSHAI_ROOT"]) / "write-probe"
    probe.write_text("ok")
    check(Path.home() in protected and B._REPO_DIR in protected, "home/repository not protected")
    study = next(path for path in protected if path.name == "study")
    check(workspace.is_relative_to(study), "study root does not protect workspace ancestor")
    check(len(read_executables) == 1 and read_executables[0]["path"].endswith("/sshai"), "missing exact sshai executable pin")
    value = {"schema": "fake", "workspace": str(workspace), "codex_version": B.CODEX_VERSION, "codex_sha256": B._file_digest(codex), "environment_sha256": B._environment_digest(environment), "protected_roots": sorted(str(path) for path in protected), "read_executables": read_executables, "config_overrides": ["default_permissions=\"issue10\"", "permissions.issue10.workspace=\"write\""], "checks": {"offline": True, "pinned_executable_0": True}}
    value["digest"] = B._sha(json.dumps(value, sort_keys=True).encode())
    return value


def test_parsers() -> None:
    analysis = B.analyze_events(events())
    check(analysis["usage"]["input_tokens"] == 10, "input usage")
    check(analysis["usage"]["non_cached_input_tokens"] == 7, "non-cached usage")
    check(analysis["captured_command_output_bytes"] == 3, "aggregated output counted once")
    check(analysis["agent_message_count"] == 2, "normal progress agent message rejected")
    B._validate_arm_commands(["cd /private/tmp/sshai-study && grep fixture"], "baseline", "/tmp/sshai")
    recovered = events()
    recovered[-3]["item"]["status"] = "failed"
    recovered[-3]["item"]["exit_code"] = 1
    check(B.analyze_events(recovered)["failed_commands"] == 1, "honest failed command was rejected")
    check(B.validate_rollout(rollout(), analysis, "test-model", "high", B.CODEX_VERSION)["usage_matched"], "rollout match")

    malformed_cases = [
        (events()[1:], "out-of-order"),
        (events() + [{"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 0}}], "duplicate"),
        (events()[:-1], "unfinished"),
        ([*events()[:-1], {"type": "wat"}], "unknown"),
        ([*events()[:-1], {"type": "error", "message": "bad"}], "error"),
        ([*events()[:-1], {"type": "turn.completed", "usage": {"input_tokens": 2, "output_tokens": 0}}], "cached_input_tokens"),
        ([*events()[:-1], {"type": "turn.completed", "usage": {"input_tokens": 2, "cached_input_tokens": 0, "output_tokens": -1}}], "output_tokens"),
        ([*events()[:-1], events()[-2], events()[-1]], "duplicate item"),
    ]
    for sample, message in malformed_cases:
        expect_invalid(lambda sample=sample: B.analyze_events(sample), message)

    bad_rollouts = []
    sample = rollout(); sample[0]["payload"]["id"] = "other"; bad_rollouts.append((sample, "thread id"))
    sample = rollout(); sample.insert(1, dict(sample[0])); bad_rollouts.append((sample, "duplicate"))
    sample = rollout(); sample[0]["payload"]["parent_id"] = "old"; bad_rollouts.append((sample, "forked"))
    sample = rollout(); sample[0]["payload"]["cli_version"] = "wrong"; bad_rollouts.append((sample, "version"))
    sample = rollout(); sample[1]["payload"]["model"] = "wrong"; bad_rollouts.append((sample, "model"))
    sample = rollout(); sample[1]["payload"]["reasoning_effort"] = "low"; bad_rollouts.append((sample, "effort"))
    sample = [record for record in rollout() if record.get("payload", {}).get("type") != "token_count"]; bad_rollouts.append((sample, "token"))
    sample = rollout(); sample.insert(-1, {"type":"event_msg","payload":{"type":"collab_agent_spawn_begin"}}); bad_rollouts.append((sample, "prohibited lifecycle"))
    sample = rollout(); sample.append({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":10,"cached_input_tokens":3,"output_tokens":2}}}}); bad_rollouts.append((sample, "after terminal"))
    sample = rollout(); sample.insert(2, {"type":"event_msg","payload":"not-an-object"}); bad_rollouts.append((sample, "payload"))
    sample = rollout(); sample.insert(2, {"type":"event_msg","payload":{"type":"future_unknown"}}); bad_rollouts.append((sample, "unknown rollout"))
    sample = rollout(); sample.insert(2, {"type":"response_item","payload":{}}); bad_rollouts.append((sample, "response_item type"))
    sample = rollout(); sample.insert(2, {"type":"response_item","payload":{"id":"ws-1","type":"web_search_call","status":"completed"}}); bad_rollouts.append((sample, "prohibited response"))
    sample = rollout(); sample.insert(2, {"type":"response_item","payload":{"id":"fc-web","type":"function_call","name":"web_search","arguments":"{}","call_id":"call-web"}}); bad_rollouts.append((sample, "remote tool"))
    sample = rollout(); sample.insert(2, {"type":"event_msg","payload":{"type":"raw_response_item","item":{"id":"ws-raw","type":"web_search_call"}}}); bad_rollouts.append((sample, "prohibited"))
    sample = rollout(); sample.insert(2, {"type":"event_msg","payload":{"type":"item_started","item":{"id":"mcp-raw","type":"mcp_tool_call"}}}); bad_rollouts.append((sample, "turn item"))
    for shape, message in (
        ({"type": "function_call"}, "required text"),
        ({"type": "message"}, "malformed response message"),
        ({"type": "reasoning"}, "malformed response reasoning"),
        ({"type": "function_call_output"}, "output is missing"),
        ({"type": "local_shell_call"}, "malformed local shell"),
    ):
        for raw in (False, True):
            sample = rollout()
            record = {"type": "response_item", "payload": shape}
            if raw:
                record = {"type": "event_msg", "payload": {"type": "raw_response_item", "item": shape}}
            sample.insert(2, record)
            bad_rollouts.append((sample, message))
    for sample, message in bad_rollouts:
        expect_invalid(lambda sample=sample: B.validate_rollout(sample, analysis, "test-model", "high", B.CODEX_VERSION), message)


def test_prepare_and_gates(parent: Path) -> None:
    blocked = parent / "blocked-context"
    blocked.mkdir(mode=0o700)
    (blocked / "AGENTS.md").write_text("synthetic context\n")
    check(B.main(["prepare", str(blocked / "study"), "--codex", str(blocked / "none"), "--sshai", str(blocked / "none"), "--model", "m", "--reasoning-effort", "high", "--repetitions", "1", "--timeout-seconds", "1", "--phase", "pilot"]) == 2, "ancestor agent context was accepted")
    study = Study(parent / "gates")
    check(study.default_approval_was_false, "approval template was not closed by default")
    check(stat.S_IMODE(study.root.stat().st_mode) == 0o700, "root permissions")
    check(stat.S_IMODE((study.root / "manifest.json").stat().st_mode) == 0o600, "file permissions")
    atomic_dir = parent / "atomic"
    atomic_dir.mkdir(mode=0o700)
    atomic_target = atomic_dir / "published"
    original_link = B.os.link
    def interrupted_link(source, destination):
        raise OSError("synthetic publication interruption")
    B.os.link = interrupted_link
    try:
        try:
            B._write_new(atomic_target, b"never-partial")
        except OSError:
            pass
        else:
            raise AssertionError("atomic publication interruption was ignored")
    finally:
        B.os.link = original_link
    check(not atomic_target.exists() and not list(atomic_dir.glob(".new-*")), "partial atomic publication remained")
    check(study.run(0, paid=False) == 2, "missing paid flag accepted")
    check(study.run(1) == 2, "strict schedule accepted a later slot")
    check(study.run(0) == 2, "missing managed-MCP preflight did not block paid launch")
    process = json.loads((study.root / "runs" / "incident-r0-baseline" / "process.json").read_text())
    check(process["launch_attempted"] is False, "blocked preflight launched Codex")
    approval = json.loads(study.approval.read_text())
    approval["manifest_digest"] = "wrong"
    study.approval.write_bytes(B._canon(approval))
    check(study.run(0) == 2, "approval mismatch accepted")

    link = parent / "auth-link"
    link.symlink_to(study.auth)
    check(B.main(["run-one", str(study.root), "--approval", str(study.approval), "--auth-file", str(link), "--slot", "0", "--allow-paid-run"]) == 2, "auth symlink accepted")
    root_link = parent / "root-link"
    root_link.symlink_to(study.root)
    check(B.main(["analyze", str(root_link)]) == 2, "root symlink accepted")


def test_success_and_exact_launch(parent: Path) -> Study:
    study = Study(parent / "success")
    old, old_mcp = B._QUALIFIER, B._MCP_PREFLIGHT
    B._QUALIFIER, B._MCP_PREFLIGHT = fake_qualifier, fake_mcp_preflight
    try:
        check(study.run(0) == 0, "fake run-one failed")
    finally:
        B._QUALIFIER, B._MCP_PREFLIGHT = old, old_mcp
    base = study.root / "runs" / "incident-r0-baseline"
    invocation = json.loads((base / "fake-invocation.json").read_text())
    argv = invocation["argv"]
    required = ["exec", "--model", "test-model", "--ignore-user-config", "--ignore-rules", "--json", "--color", "never", "--skip-git-repo-check", "--output-schema", "--output-last-message", "-C", "-"]
    for value in required:
        check(value in argv, f"missing CLI argument {value}")
    check("--sandbox" not in argv and "-P" not in argv, "exec used forbidden sandbox flags")
    for setting in ('agents.enabled=false', 'features.multi_agent=false', 'features.multi_agent_v2=false', 'features.apps=false', 'features.plugins=false'):
        check(setting in argv, f"missing defensive disable: {setting}")
    check('approval_policy="never"' in argv and 'web_search="disabled"' in argv, "policy flags missing")
    env = invocation["env"]
    check("SSH_AUTH_SOCK" not in env and "OPENAI_API_KEY" not in env, "secret environment inherited")
    check(invocation["auth_link"] == str(study.auth), "auth link is not absolute source link")
    check("ordinary local Bash tools" in invocation["prompt"] and "Do not invoke sshai" in invocation["prompt"], "baseline guidance missing")
    check((base / "workspace" / ".sshai-root" / "write-probe").read_text() == "ok", "sshai root not writable")
    check(stat.S_IMODE((base / "events.jsonl").stat().st_mode) == 0o600, "evidence permissions")
    check(study.run(0) == 2, "completed slot rerun accepted")
    (study.root / ".run.lock").write_text("held")
    check(study.run(1) == 2, "exclusive lock ignored")
    (study.root / ".run.lock").unlink()
    old, old_mcp = B._QUALIFIER, B._MCP_PREFLIGHT
    B._QUALIFIER, B._MCP_PREFLIGHT = fake_qualifier, fake_mcp_preflight
    try:
        check(study.run(1) == 0, "sshai branch fake run failed")
    finally:
        B._QUALIFIER, B._MCP_PREFLIGHT = old, old_mcp
    sshai_base = study.root / "runs" / "incident-r0-sshai"
    sshai_invocation = json.loads((sshai_base / "fake-invocation.json").read_text())
    check(" local --shell bash" in sshai_invocation["prompt"] and "sshai q, diff, and --delta" in sshai_invocation["prompt"], "sshai branch guidance missing")
    sshai_result = json.loads((sshai_base / "result.json").read_text())
    check(sshai_result["analysis"]["valid"] is True, "pinned sshai command was rejected")
    return study


def scenario_popen(code: str):
    def launch(argv, **kwargs):
        return subprocess.Popen([sys.executable, "-c", code], **kwargs)
    return launch


def test_process_failures(parent: Path) -> None:
    scenarios = [
        ("import time; time.sleep(60)", "timed_out"),
        (f"import os; os.write(1,b'x'*{B.MAX_CAPTURE + 4096}); import time; time.sleep(60)", "capture_overflow"),
    ]
    for number, (code, field) in enumerate(scenarios):
        study = Study(parent / f"failure-{number}")
        old_q, old_mcp, old_popen = B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN
        B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = fake_qualifier, fake_mcp_preflight, scenario_popen(code)
        try:
            check(study.run(0) == 1, f"{field} run did not fail")
        finally:
            B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = old_q, old_mcp, old_popen
        base = study.root / "runs" / "incident-r0-baseline"
        process = json.loads((base / "process.json").read_text())
        check(process[field] is True, f"{field} metadata missing")
        check(process["pid"] is not None, "child pid missing")
        try:
            os.kill(process["pid"], 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError(f"{field} child process survived group cleanup")
        check((base / "result.json").is_file(), f"{field} result missing")

    study = Study(parent / "failure-descendant")
    descendant_code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True)"
    old_q, old_mcp, old_popen = B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN
    B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = fake_qualifier, fake_mcp_preflight, scenario_popen(descendant_code)
    try:
        check(study.run(0) == 1, "descendant cleanup evidence run unexpectedly valid")
    finally:
        B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = old_q, old_mcp, old_popen
    child_pid = int((study.root / "runs" / "incident-r0-baseline" / "events.jsonl").read_text().strip())
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError("descendant survived after process-group leader exited")

    for label, exception, expected_field in (
        ("start", OSError("synthetic start failure"), "start_error"),
        ("interrupt", KeyboardInterrupt(), "interrupted"),
    ):
        study = Study(parent / f"failure-{label}")
        def raising(*args, exception=exception, **kwargs):
            raise exception
        old_q, old_mcp, old_popen = B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN
        B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = fake_qualifier, fake_mcp_preflight, raising
        try:
            check(study.run(0) == 1, f"{label} run did not retain failure")
        finally:
            B._QUALIFIER, B._MCP_PREFLIGHT, B._POPEN = old_q, old_mcp, old_popen
        process = json.loads((study.root / "runs" / "incident-r0-baseline" / "process.json").read_text())
        value = process[expected_field]
        check(value is True if expected_field == "interrupted" else "synthetic start failure" in value, f"{label} metadata missing")


def report_row(task: str, replicate: int, arm: str, tokens: int, *, quality: bool = True, reviewed: bool = True) -> dict[str, Any]:
    return {
        "slot_spec": {"task": task, "replicate": replicate, "arm": arm}, "state": "complete",
        "analysis": {"valid": True, "thread_id": f"{task}-{replicate}-{arm}", "usage": {"input_tokens": tokens, "cached_input_tokens": 1, "output_tokens": 2}, "captured_command_output_bytes": 4},
        "quality": {"passed": quality}, "review": {"complete": reviewed}, "process": {"duration_seconds": 1.5},
    }


def test_report_and_recomputation(parent: Path, successful: Study) -> None:
    rows = [report_row("incident", 0, "baseline", 10, quality=False), report_row("incident", 0, "sshai", 7)]
    report = B.paired_report(rows)
    check(report["metrics"]["baseline_input_tokens"]["mean"] == 10, "usage discarded on quality failure")
    check(report["primary_complete_pair_count"] == 0 and not report["positive_claim_allowed"], "quality failure yielded positive claim")
    expect_invalid(lambda: B.paired_report(rows[:1], [row["slot_spec"] for row in rows]), "every manifest slot")
    complete = [report_row("incident", 0, "baseline", 10), report_row("incident", 0, "sshai", 7)]
    pilot = B.paired_report(complete, phase="pilot")
    check(pilot["decision"] == "descriptive reduction" and pilot["positive_claim_allowed"] is False, "pilot emitted positive claim")
    increase = B.paired_report([report_row("incident", 0, "baseline", 7), report_row("incident", 0, "sshai", 10)])
    check(increase["decision"] == "measured increase", "measured token increase hidden")
    reused = [report_row("incident", 0, "baseline", 10), report_row("incident", 0, "sshai", 7)]
    reused[1]["analysis"]["thread_id"] = reused[0]["analysis"]["thread_id"]
    reuse_report = B.paired_report(reused)
    check(reuse_report["slots"][0]["analysis_valid"] is False and reuse_report["slots"][1]["analysis_valid"] is False, "cross-slot thread reuse accepted")

    base = successful.root / "runs" / "incident-r0-baseline"
    receipt_path = base / "sandbox-receipt.json"
    original_receipt = receipt_path.read_bytes()
    receipt = json.loads(original_receipt)
    receipt["checks"] = {"offline": False}
    unsigned = dict(receipt)
    unsigned.pop("digest", None)
    receipt["digest"] = B._sha(json.dumps(unsigned, sort_keys=True).encode())
    receipt_path.write_bytes(B._canon(receipt))
    process = json.loads((base / "process.json").read_text())
    expect_invalid(lambda: B._validate_run_receipts(base, base / "workspace", successful.root, json.loads((successful.root / "manifest.json").read_text()), process), "not all successful")
    receipt_path.write_bytes(original_receipt)
    receipt = json.loads(original_receipt)
    receipt["environment_sha256"] = "0" * 64
    unsigned = dict(receipt)
    unsigned.pop("digest", None)
    receipt["digest"] = B._sha(json.dumps(unsigned, sort_keys=True).encode())
    receipt_path.write_bytes(B._canon(receipt))
    expect_invalid(lambda: B._validate_run_receipts(base, base / "workspace", successful.root, json.loads((successful.root / "manifest.json").read_text()), process), "config/environment")
    receipt_path.write_bytes(original_receipt)

    forged = json.loads((base / "result.json").read_text())
    forged["analysis"] = {"valid": True, "usage": {"input_tokens": 1}}
    (base / "result.json").write_bytes(B._canon(forged))
    # Provenance still binds the original result: analysis must reject the edit,
    # rather than trusting its attractive counter.
    report_path = successful.root / "reports" / "tamper.json"
    check(B.main(["analyze", str(successful.root), "--out", str(report_path)]) == 0, "analysis command failed")
    report = json.loads(report_path.read_text())
    check(report["slot_count"] == 6 and report["state_counts"].get("missing") == 4, "missing slots omitted")
    check(report["primary_complete_pair_count"] == 0 and not report["positive_claim_allowed"], "forged result/review enabled claim")
    check(not (successful.root / "report.json").exists(), "analyze unexpectedly wrote a default report")
    check(B.main(["analyze", str(successful.root)]) == 0, "repeat stdout-only analysis failed")


def main() -> None:
    test_parsers()
    with tempfile.TemporaryDirectory(prefix="issue10-offline-tests-") as temporary:
        parent = Path(temporary).resolve()
        test_prepare_and_gates(parent)
        successful = test_success_and_exact_launch(parent)
        test_process_failures(parent)
        test_report_and_recomputation(parent, successful)
    print("benchmark_issue10 offline tests passed")


if __name__ == "__main__":
    main()
