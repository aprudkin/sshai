#!/usr/bin/env python3
"""Deterministic local fixtures and objective evaluator for benchmark issue 10.

This module only produces fixture bytes and grades structured objective facts. A
harness decides which returned fields are exposed to an agent and materializes
``files`` in an isolated workspace. It deliberately grades only objective facts.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any

TASK_IDS = ("incident", "config-drift", "snapshot-diff")


def _text(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


def _line(lines: list[str], text: str) -> int:
    return lines.index(text) + 1


def _incident() -> tuple[dict[str, str], str, dict[str, Any]]:
    app = ["2026-09-05T05:00:00Z INFO api boot complete version=4.18.2"]
    for i in range(1, 421):
        app.append(
            f"2026-09-05T05:{i // 60:02d}:{i % 60:02d}Z INFO request complete "
            f"route=/v1/orders status=200 latency_ms={18 + i % 31} trace=tr-{i:04d}"
        )
    failure = (
        "2026-09-05T05:07:13Z ERROR checkout database acquire failed "
        "error=pool_timeout pool=primary in_use=40 max=40 trace=tr-0421"
    )
    response = "2026-09-05T05:07:13Z WARN checkout returning 503 route=/v1/checkout trace=tr-0421"
    app.extend((failure, response))
    for i in range(422, 691):
        status = 503 if i % 3 else 200
        app.append(
            f"2026-09-05T05:{7 + (i - 422) // 60:02d}:{(i - 422) % 60:02d}Z "
            f"{'WARN' if status == 503 else 'INFO'} request complete route=/v1/checkout "
            f"status={status} trace=tr-{i:04d}"
        )
    app.append("2026-09-05T05:12:00Z INFO deploy completed version=4.18.2")
    nginx = ["# nginx access log"]
    for i in range(1, 531):
        route = "/v1/checkout" if i % 5 == 0 else "/v1/catalog"
        status = 503 if route == "/v1/checkout" and i >= 355 and i % 3 else 200
        nginx.append(
            f"10.8.0.{i % 17} - - [05/Sep/2026:05:{i // 60:02d}:{i % 60:02d} +0000] "
            f"\"POST {route} HTTP/1.1\" {status} 312 \"-\" \"edge-probe\""
        )
    expected = {
        "pool": "primary",
        "error_code": "pool_timeout",
        "in_use": 40,
        "max": 40,
        "endpoint": "/v1/checkout",
        "evidence": [
            {"file": "logs/api.log", "line": _line(app, failure)},
            {"file": "logs/api.log", "line": _line(app, response)},
        ],
    }
    prompt = (
        "Investigate the checkout incident using the supplied logs. Return the pool, error code, "
        "observed in-use and maximum counts, and affected endpoint as JSON with exact file-and-line "
        "evidence."
    )
    return {"logs/api.log": _text(app), "logs/nginx-access.log": _text(nginx)}, prompt, expected


def _config_drift() -> tuple[dict[str, str], str, dict[str, Any]]:
    base = [
        "# managed by fleet-config", "service:", "  name: telemetry-agent",
        "  endpoint: https://ingest.prod.example/v2", "  tls_verify: true",
        "  batch_size: 500", "  retry_limit: 5",
    ]
    current = base.copy()
    current[4] = "  tls_verify: false"
    current[5] = "  batch_size: 5000"
    for i in range(1, 301):
        base.append(f"  tag_{i:03d}: region-use1")
        current.append(f"  tag_{i:03d}: region-use1")
    inventory = ["host,role,config_revision"]
    inventory.extend(f"worker-{i:03d},telemetry,r2026.09.05" for i in range(1, 151))
    inventory.append("worker-152,telemetry,manual-hotfix")
    expected = {
        "host": "worker-152",
        "changes": [
            {"key": "tls_verify", "before": "true", "after": "false"},
            {"key": "batch_size", "before": "500", "after": "5000"},
        ],
        "evidence": [
            {"file": "baseline/telemetry-agent.yaml", "line": _line(base, "  tls_verify: true")},
            {"file": "hosts/worker-152/telemetry-agent.yaml", "line": _line(current, "  tls_verify: false")},
            {"file": "baseline/telemetry-agent.yaml", "line": _line(base, "  batch_size: 500")},
            {"file": "hosts/worker-152/telemetry-agent.yaml", "line": _line(current, "  batch_size: 5000")},
        ],
    }
    prompt = (
        "Compare the managed telemetry configuration with worker-152. Return the host and an "
        "array of every changed key with its exact before and after scalar values, plus exact "
        "file-and-line evidence."
    )
    return {
        "baseline/telemetry-agent.yaml": _text(base),
        "hosts/worker-152/telemetry-agent.yaml": _text(current),
        "inventory/hosts.csv": _text(inventory),
    }, prompt, expected


def _snapshot_diff() -> tuple[dict[str, str], str, dict[str, Any]]:
    before = ["snapshot_id=before-20260905", "host=payments-03", "captured_at=2026-09-05T04:00:00Z"]
    after = ["snapshot_id=after-20260905", "host=payments-03", "captured_at=2026-09-05T06:00:00Z"]
    for i in range(1, 701):
        digest = hashlib.sha256(f"stable-{i}".encode()).hexdigest()[:16]
        before.append(f"/srv/payments/cache/item-{i:04d} size=4096 sha256={digest}")
        after.append(f"/srv/payments/cache/item-{i:04d} size=4096 sha256={digest}")
    old = "/etc/payments/routing.json size=812 sha256=0aa17c5e9d4b82f1"
    new = "/etc/payments/routing.json size=812 sha256=f09b4e0c218ac673"
    before.extend((old, "/var/lib/payments/reconcile.cursor size=24 sha256=bc9010ef44a82210"))
    after.extend((new, "/var/lib/payments/reconcile.cursor size=24 sha256=bc9010ef44a82210"))
    journal = ["2026-09-05T05:30:01Z INFO backup snapshot started"]
    journal.extend(f"2026-09-05T05:30:{i % 60:02d}Z INFO cache scan item={i:04d}" for i in range(1, 181))
    journal.append("2026-09-05T05:31:12Z INFO routing config reloaded source=controller")
    expected = {
        "path": "/etc/payments/routing.json",
        "before_hash": "0aa17c5e9d4b82f1",
        "after_hash": "f09b4e0c218ac673",
        "before_size": 812,
        "after_size": 812,
        "evidence": [
            {"file": "snapshots/before.manifest", "line": _line(before, old)},
            {"file": "snapshots/after.manifest", "line": _line(after, new)},
        ],
    }
    prompt = (
        "Compare the two filesystem snapshot manifests. Return the changed path and exact before "
        "and after hashes and sizes as JSON with precise file-and-line evidence."
    )
    return {
        "snapshots/before.manifest": _text(before),
        "snapshots/after.manifest": _text(after),
        "logs/backup.log": _text(journal),
    }, prompt, expected


_BUILDERS = {"incident": _incident, "config-drift": _config_drift, "snapshot-diff": _snapshot_diff}


def _field_schema(value: Any) -> dict[str, Any]:
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list) and value and "file" in value[0]:
        return {
            "type": "array",
            "items": {
                "type": "object", "required": ["file", "line"],
                "properties": {"file": {"type": "string"}, "line": {"type": "integer"}},
                "additionalProperties": False,
            },
        }
    return {
        "type": "array",
        "items": {
            "type": "object", "required": ["key", "before", "after"],
            "properties": {
                "key": {"type": "string"}, "before": {"type": "string"},
                "after": {"type": "string"},
            },
            "additionalProperties": False,
        },
    }


def _schema(expected: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(expected),
        "properties": {
            **{key: _field_schema(value) for key, value in expected.items()},
        },
        "additionalProperties": False,
    }


def build_task(task_id: str) -> dict[str, Any]:
    """Build one deterministic fixture; ``expected_answer`` is harness-private data."""
    if task_id not in _BUILDERS:
        raise ValueError(f"unknown task id: {task_id}")
    files, prompt, expected = _BUILDERS[task_id]()
    return {
        "files": files,
        "prompt": prompt,
        "answer_schema": _schema(expected),
        "expected_answer": copy.deepcopy(expected),
    }


def _valid_citations(files: dict[str, str], citations: Any, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(citations, list):
        errors.append("evidence must be an array")
        return []
    valid = []
    for index, citation in enumerate(citations):
        if not isinstance(citation, dict) or set(citation) != {"file", "line"}:
            errors.append(f"evidence[{index}] must contain only file and line")
            continue
        filename, line = citation["file"], citation["line"]
        if not isinstance(filename, str) or isinstance(line, bool) or not isinstance(line, int):
            errors.append(f"evidence[{index}] has invalid file or line type")
        elif filename not in files or not 1 <= line <= len(files[filename].splitlines()):
            errors.append(f"evidence[{index}] cites a nonexistent file or line")
        else:
            valid.append(citation)
    return valid


def grade(task_id: str, answer: Any) -> dict[str, Any]:
    """Grade objective facts and generated-file evidence."""
    task = build_task(task_id)
    expected = task["expected_answer"]
    errors: list[str] = []
    if not isinstance(answer, dict):
        return {"passed": False, "errors": ["answer must be an object"]}
    for key in sorted(set(expected) - set(answer)):
        errors.append(f"missing field: {key}")
    for key in sorted(set(answer) - set(expected)):
        errors.append(f"unexpected field: {key}")
    for key, value in expected.items():
        if key not in answer:
            continue
        if key == "evidence":
            citations = _valid_citations(task["files"], answer[key], errors)
            actual_citations = [(item["file"], item["line"]) for item in citations]
            expected_citations = {(item["file"], item["line"]) for item in value}
            if len(actual_citations) != len(set(actual_citations)):
                errors.append("evidence must not contain duplicate citations")
            if not expected_citations.issubset(actual_citations):
                errors.append("evidence does not provide the required precise citations")
        elif key == "changes":
            if not isinstance(answer[key], list):
                errors.append("changes must be an array")
                continue
            actual_changes: list[tuple[str, str, str]] = []
            malformed = False
            for item in answer[key]:
                if not isinstance(item, dict) or set(item) != {"key", "before", "after"}:
                    malformed = True
                    continue
                change = (item["key"], item["before"], item["after"])
                if not all(isinstance(part, str) for part in change):
                    malformed = True
                    continue
                actual_changes.append(change)
            expected_changes = [(item["key"], item["before"], item["after"]) for item in value]
            if (
                malformed
                or len(actual_changes) != len(set(actual_changes))
                or sorted(actual_changes) != sorted(expected_changes)
            ):
                errors.append("changes must contain the required key, before, and after values")
        elif isinstance(value, int):
            if isinstance(answer[key], bool) or not isinstance(answer[key], int):
                errors.append(f"{key} must be an integer")
            elif answer[key] != value:
                errors.append(f"incorrect {key}")
        elif not isinstance(answer[key], str):
            errors.append(f"{key} must be a string")
        elif answer[key] != value:
            errors.append(f"incorrect {key}")
    return {"passed": not errors, "errors": errors}
