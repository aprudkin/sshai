#!/usr/bin/env python3
"""Default-off Codex 0.151.0 intercepted empty scratch-helper update audit.

This is a recorded-lifecycle qualification, not routing or OS attestation. The
legacy capture report and pilot audit remain untouched. Only a prospective series
manifest explicitly binds this profile; it is never inferred from old roots.
"""
from __future__ import annotations
from pathlib import Path
import re

import benchmark_issue10_local_pilot as p

PROFILE = {"schema": "sshai-benchmark/issue10-intercepted-patch-profile-1",
           "cli_version": "0.151.0", "revision": "78c290807ce710180111df227df3b7a4fe845452",
           "shape": "literal-single-empty-scratch-helper-update"}


def _request(raw, scratch):
    if raw.get("type") != "function_call" or raw.get("name") != "exec_command":
        return None
    try:
        args = p.json.loads(raw["arguments"], object_pairs_hook=p._no_duplicate_keys,
                            parse_constant=p._reject_constant)
        if (not isinstance(args, dict) or args.get("workdir") != str(scratch)
                or set(args) - {"cmd", "workdir", "yield_time_ms", "max_output_tokens"}
                or any(type(args[key]) is not int or not 0 <= args[key] <= 1_000_000
                       for key in ("yield_time_ms", "max_output_tokens") if key in args)):
            return None
        command = args.get("cmd")
        if not isinstance(command, str) or len(command.encode()) > p.capture.MAX_LINE_BYTES:
            return None
        match = re.fullmatch(r"apply_patch <<'([A-Z][A-Z0-9_]*)'\n\*\*\* Begin Patch\n\*\*\* Update File: ([^\n]+)\n@@\n((?:\+[^\n]*\n)+)\*\*\* End Patch\n\1\n?", command)
        if not match:
            return None
        relative = Path(match[2])
        if (relative.is_absolute() or not relative.parts or any(part in {"..", "."} or part.startswith(".") for part in relative.parts)
                or str(relative) != match[2] or relative.parts[0] in {"sshai-root", "tmp"}):
            return None
        target = scratch / relative
        # Reject symlinks in every component, not just the final file. No
        # historical auth paths or credential contents are inspected here.
        physical = p.legacy._physical(target)
        body = "".join(line[1:] + "\n" for line in match[3].splitlines())
        if not physical.is_file() or p.legacy._read_bounded(physical, p.capture.MAX_LINE_BYTES) != body.encode():
            return None
        return str(target), match[3], body
    except (KeyError, TypeError, ValueError, OSError, p.PilotInputError):
        return None


def _native(observations, identity, target, additions):
    if [o["event"] for o in observations] not in (["item_completed"], ["item_started", "item_completed"]):
        return False
    for obs in observations:
        raw = obs["item"]
        if (set(raw) != {"type", "id", "changes", "status", "stdout", "stderr"}
                or raw["type"] != "FileChange" or raw["id"] != identity
                or raw["status"] != ("completed" if obs["event"] == "item_completed" else "in_progress")
                or not isinstance(raw["stdout"], str) or raw["stderr"] != ""
                or not isinstance(raw["changes"], dict) or set(raw["changes"]) != {target}):
            return False
        change = raw["changes"][target]
        if (not isinstance(change, dict) or set(change) != {"type", "unified_diff", "move_path"}
                or change["type"] != "update" or change["move_path"] is not None
                or not isinstance(change["unified_diff"], str)):
            return False
        lines = additions.count("\n")
        header = rf"@@ -0,0 \+1(?:,{lines})? @@\n" if lines == 1 else rf"@@ -0,0 \+1,{lines} @@\n"
        if not re.fullmatch(header + re.escape(additions), change["unified_diff"]):
            return False
    return True


def _cli(observations, target):
    if [o["event"] for o in observations] != ["item.started", "item.completed"]:
        return False
    identities = set()
    for obs in observations:
        raw = obs["item"]
        if (set(raw) != {"type", "id", "changes", "status"} or raw["type"] != "file_change"
                or not isinstance(raw["id"], str) or not raw["id"]
                or raw["status"] != ("completed" if obs["event"] == "item.completed" else "in_progress")
                or raw["changes"] != [{"kind": "update", "path": target}]):
            return False
        identities.add(raw["id"])
    return len(identities) == 1


def audit(report, config, scratch):
    """Qualify exact observed patch records; do not mutate the report/config.

    Native request IDs bind native lifecycles. CLI IDs stay source-local; target
    correspondence is only shape coverage, never a unique-call/identity join.
    Any ambiguous repeated target or unknown record remains unqualified.
    """
    result = p._audit(report, config)
    inventory = report["calls"]["inventory"]
    scratch = Path(scratch)
    requests, native, cli = {}, {}, {}
    for index, item in enumerate(inventory, 1):
        observations = item.get("raw_observations") or []
        if item["source"] == "rollout.response_item":
            for obs in observations:
                parsed = _request(obs["item"], scratch)
                identity = obs["item"].get("call_id")
                if parsed and isinstance(identity, str) and identity and len(observations) == 1:
                    requests.setdefault(identity, []).append((obs, parsed, index))
        elif item["source"] == "rollout.turn_item" and item["record_type"] == "FileChange":
            native.setdefault(item["call_id"], []).append((index, observations))
        elif item["source"] == "cli" and item["record_type"] == "file_change":
            for obs in observations:
                changes = obs["item"].get("changes")
                if isinstance(changes, list) and len(changes) == 1 and isinstance(changes[0], dict):
                    cli.setdefault(changes[0].get("path") if isinstance(changes[0].get("path"), str) else None, set()).add(index)
    qualified, covered, proofs = set(), set(), []
    targets = [values[0][1][0] for values in requests.values() if len(values) == 1]
    for identity, values in requests.items():
        if len(values) != 1 or len(native.get(identity, [])) != 1:
            continue
        obs, (target, additions, _), request_index = values[0]
        ni, no = native[identity][0]
        ci = cli.get(target, set())
        if (targets.count(target) != 1 or len(ci) != 1 or not result["entries"][request_index - 1]["allowed_signature"]
                or obs["record"] >= no[0]["record"] or not _native(no, identity, target, additions)):
            continue
        ci = next(iter(ci))
        co = inventory[ci - 1]["raw_observations"]
        if not _cli(co, target):
            continue
        qualified.update((ni, ci))
        covered.update(("rollout", row["record"], "unsupported_turn_item") for row in no)
        covered.update(("cli", row["record"], "unsupported_cli_item") for row in co)
        proofs.append({"request_id": identity, "request_record": obs["record"], "native_records": [r["record"] for r in no],
                       "cli_id": co[0]["item"]["id"], "cli_records": [r["record"] for r in co], "target": target})
    for index in qualified:
        result["entries"][index - 1]["allowed_signature"] = True
        result["entries"][index - 1]["qualification"] = PROFILE["schema"]
    result["unallowed_entries"] = [i for i in result["unallowed_entries"] if i not in qualified]
    result["unsupported_records"] = [i for i in result["unsupported_records"] if not (
        i.get("effect") == "uncertain" and (i.get("source"), i.get("record"), i.get("code")) in covered)]
    result["status"] = "unqualified" if result["unallowed_entries"] or result["unsupported_records"] else "bounded-recorded"
    result["intercepted_patch"] = {"profile": dict(PROFILE), "qualified_requests": proofs,
        "scope": "recorded lifecycle only; no cross-source identity join or semantic routing proof"}
    return result
