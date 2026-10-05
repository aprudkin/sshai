#!/usr/bin/env python3
"""Only the 4d283d7 three-slot measured prefix's observer-size recovery.

Historical source is hashed, never imported. Supplementary acquisition is late:
its hash is not a collector-time hash, and original qualification flags stay intact.
"""
from __future__ import annotations
import math
import os
from pathlib import Path
import stat

import benchmark_issue10_local_pilot as p
import benchmark_issue10_local_series as s

SCHEMA = "sshai-benchmark/issue10-local-series-capture-recovery-1"
ORIGINAL_SOURCES = {
    "docs/benchmarks/issue10-local-pilot-results.md": "d2437a095e945f22087be0fa1a52364b23ab3242585708c96c14cdf1dccb16e1",
    "docs/benchmarks/issue10-local-series.md": "4a59e4b22bf91cacecceb237ea7ae7163c5e0c19e2be59ffdee61b870b671ba9",
    "docs/benchmarks/issue10-methodology-amendment.md": "3b4d3d7ae1aba10efdef5acb1ab0b6b0a3266f2d24d32161b1cde3f5e9521f23",
    "docs/benchmarks/issue10-protocol.md": "f808f2d86aada10469a2870670843bf9cb21eae7dab0737d4fb23d78d777dc60",
    "scripts/benchmark_issue10.py": "178c12d2cb3612f354254dc8f6a5fa73724d973af2df7bdbc7958cd4460444ac",
    "scripts/benchmark_issue10_local_pilot.py": "2a24dbf3288c9323695641b551f5877d60b6b7ec992c1af9c2ce782e4dbe9f1f",
    "scripts/benchmark_issue10_local_series.py": "5e5830444c5571685dd54d9d81c26ccf799de3692766ba2e01dbdc55448064a7",
    "scripts/benchmark_issue10_sandbox.py": "43d18188ef1e1f9a24f38bb497f1bc48155a1674004998e2db28ff8aee88c9d5",
    "scripts/benchmark_issue10_v3.py": "c38b9a44ae8cefd1591db85f64ced671195d77e6c9d830cc7883bf3308909256",
    "scripts/benchmark_issue10_v3_capture.py": "3067f5c25f51feab87971748bc55942afd69de6129a8b9b7240853f837e16856",
    "scripts/benchmark_issue10_v3_cases.py": "a93f4c26850aecc0d65a6cc5fa805dccd9ee572e27611eabcbcd7fe47688367c",
    "scripts/benchmark_issue10_v3_collector.py": "24b54b7da05060f6d970d87dade7e2fa4fedd4dfebc7b734e32c38315d3839f8",
}
ACQUISITION_LIMIT = "late supplementary acquisition; no collector-time retained native byte hash exists for slot 3"
# Completed measured sessions only; do not widen the historical startup-auth census.
MEASURED_THREAD_HISTORY_COMPANIONS = {"thread_history_1.sqlite-wal", "thread_history_1.sqlite-shm"}


def fail(message):
    raise p.PilotInputError(message)


def owner(binding, root):
    identity = {key: value for key, value in binding.items() if key != "owner_sha256"}
    return {"schema": SCHEMA, "continuation_root": str(root), "binding_sha256": p._sha(p._encoded(identity)),
            "executable_slots": list(range(4, 37)), "one_shot": True, "launch_approval": False}


def _census(base, manifest, number, discovery, candidate):
    """Exact original producer tree; writable scratch regular files are retained, not interpreted."""
    material = manifest["slot_material"][str(number)]["fixture_files"]
    files = {"reservation.json", "result.json", "evidence/access-receipt.json", "evidence/prompt.txt",
             "evidence/capture-report.json", "evidence/completion-evidence.json", "evidence/tool-audit.json",
             "evidence/last-message.txt", "codex-home/model-catalog.json", str(candidate.relative_to(base))}
    files.update("fixture/" + name for name in material)
    files.update("evidence/attempt/" + name for name in
                 ("attempt.json", "events.jsonl", "stderr.txt", "process.json", "delivery.json", "rollout.jsonl", "answer.txt"))
    if number != 3:
        files.add("evidence/attempt/rollout-candidates/001.jsonl")
    runtime = discovery["native_runtime"]
    aliases = {"codex-home/auth.json": manifest["auth"]["path"]}
    for row in runtime["entries"]:
        relative = "codex-home/" + row["path"]
        if row["kind"] == "native-alias":
            aliases[relative] = str(p.EXPECTED_CODEX_PATH)
        elif row["kind"] == "lock":
            files.add(relative)
    metadata = {"codex-home/" + name for name in p.AUTH_NATIVE_METADATA | MEASURED_THREAD_HISTORY_COMPANIONS
                if (base / "codex-home" / name).exists()}
    directories = {"fixture", "scratch", "scratch/sshai-root", "scratch/tmp", "home", "home/.config",
                   "home/.cache", "home/.local-share", "codex-home", "evidence", "evidence/attempt"}
    directories.update("codex-home/" + row["path"] for row in runtime["entries"] if row["kind"] == "directory")
    for relative in files | metadata | set(aliases) | directories:
        directories.update(str(parent) for parent in Path(relative).parents if str(parent) != ".")
    inventory = {}
    for current, dirs, names in os.walk(base, followlinks=False, onerror=p._incomplete_scan):
        for name in sorted([*dirs, *names]):
            path = Path(current) / name
            relative = str(path.relative_to(base))
            mode = path.lstat().st_mode
            if len(inventory) >= p.MAX_DISCOVERY_ENTRIES:
                fail("original producer census exceeded entry bounds")
            if relative in aliases and stat.S_ISLNK(mode) and str(path.readlink()) == aliases[relative]:
                inventory[relative] = {"link": aliases[relative]}
            elif stat.S_ISDIR(mode) and (relative in directories or relative.startswith("scratch/")):
                inventory[relative] = {"directory": True}
            elif stat.S_ISREG(mode) and relative in metadata:
                details = {"bytes": path.stat().st_size, "mode": stat.S_IMODE(mode)}
                if relative.removeprefix("codex-home/") in MEASURED_THREAD_HISTORY_COMPANIONS:
                    database = base / "codex-home/thread_history_1.sqlite"
                    if (database.is_symlink() or not database.is_file()
                            or details["bytes"] > s.CAPTURE_CAPACITY["capture_limit"] or details["mode"] not in {0o600, 0o644}):
                        fail("measured thread-history companion type, mode or size is unsupported")
                    details["provenance"] = "known completed native thread_history_1 SQLite companion; contents not interpreted"
                inventory[relative] = {"native_metadata": details}
            elif stat.S_ISREG(mode) and (relative in files or relative.startswith("scratch/")):
                if path.stat().st_size > s.CAPTURE_CAPACITY["capture_limit"]:
                    fail("original producer file exceeds prospective bounded recovery capacity")
                inventory[relative] = {"sha256": p.legacy._file_digest(path)}
            else:
                fail("original producer census contains an unknown or unsafe entry")
    if not (files | directories | set(aliases) | metadata) <= set(inventory):
        fail("original producer census is incomplete")
    for name, expected in material.items():
        path = base / "fixture" / name
        if p.legacy._file_digest(path) != expected["sha256"] or stat.S_IMODE(path.stat().st_mode) != 0o600:
            fail("original fixture integrity or permissions changed")
    return inventory


def prefix(root, manifest, *, _patch_slot=False):
    # The sole caller of the slot-4 variant separately pins the exact 7b source,
    # digest and size-recovery ancestry. Defaults retain the original 1..3 proof.
    s.load_readiness(root, manifest)
    expected_slots = {"004"} if _patch_slot else {"001", "002", "003"}
    if {path.name for path in (root / "slots").iterdir()} != expected_slots:
        fail("recovery requires its exact consumed prefix and no later reservation")
    proofs, supplementary, threads = [], None, set()
    parse = {key: value for key, value in s.CAPTURE_CAPACITY.items() if key != "stream_limit"}
    file_limit = s.CAPTURE_CAPACITY["capture_limit"] if _patch_slot else p.capture.MAX_CAPTURE_BYTES
    stream_limit = s.CAPTURE_CAPACITY["stream_limit"] if _patch_slot else p.collector.MAX_STREAM_BYTES
    for number in ((4,) if _patch_slot else (1, 2, 3)):
        slot = manifest["slots"][number - 1]
        base = p.legacy._physical(root / "slots" / f"{number:03}")
        evidence, attempt, home = base / "evidence", base / "evidence/attempt", base / "codex-home"
        reservation = p._json_file(base / "reservation.json", "original reservation")
        p._exact_object(reservation, {"manifest_digest", "slot", "approval_sha256", "one_shot"}, "original reservation")
        if reservation["manifest_digest"] != manifest["digest"] or reservation["slot"] != slot or reservation["one_shot"] is not True:
            fail("original reservation differs from the pinned phase")
        p._hex_digest(reservation["approval_sha256"], "original approval hash")
        access = p._validated_access_receipt(p._json_file(evidence / "access-receipt.json", "original access"))
        prompt = p.legacy._read_bounded(evidence / "prompt.txt", p.collector.MAX_PROMPT_BYTES)
        if p._sha(prompt) != manifest["slot_material"][str(number)]["rendered_prompt_sha256"]:
            fail("original rendered prompt changed")
        if not (home / "auth.json").is_symlink() or (home / "auth.json").readlink() != Path(manifest["auth"]["path"]):
            fail("original auth provisioning changed")
        if p.legacy._file_digest(home / "model-catalog.json") != manifest["model_catalog"]["sha256"]:
            fail("original catalog provisioning changed")
        argv = p._model_argv(manifest, base, access)
        expected = p.collector._request_receipt(argv, prompt, p._environment(base, base / "scratch", manifest["config"]),
            base / "scratch", float(manifest["config"]["limits"]["timeout_seconds"]), [], evidence / "last-message.txt",
            **(s.CAPTURE_CAPACITY if _patch_slot else {}))
        if _patch_slot:
            expected["limits"]["jsonl_record_bytes"] = s.CAPTURE_CAPACITY["line_limit"]
        expected.update(schema=p.collector.ASSOCIATED_ATTEMPT_SCHEMA, association={
            "manifest_digest": manifest["digest"], "slot": slot, "approval_sha256": reservation["approval_sha256"],
            "access_receipt_digest": access["digest"], "model": manifest["config"]["model"],
            "history_mode": manifest["config"]["codex_exec"]["history_mode"], "config_sha256": manifest["config_sha256"],
            "argv_sha256": p._sha(p._encoded(argv))})
        request = p._json_file(attempt / "attempt.json", "original request")
        initial = p._exact_object(request.get("rollout_discovery"), {"root", "initial_inventory", "native_runtime", "entry_bound", "candidate_bound"}, "original discovery")
        p._recorded_initial_runtime(initial["native_runtime"])
        initial_names = ["auth.json", "model-catalog.json"] + (["tmp"] if initial["native_runtime"]["present"] else [])
        if ({key: value for key, value in request.items() if key != "rollout_discovery"} != expected
                or initial["root"] != str(home) or initial["initial_inventory"] != initial_names
                or initial["entry_bound"] != p.MAX_DISCOVERY_ENTRIES or initial["candidate_bound"] != p.collector.MAX_ROLLOUT_CANDIDATES):
            fail("original request is not the single pinned native invocation")
        events = p.legacy._read_bounded(attempt / "events.jsonl", stream_limit)
        stderr = p.legacy._read_bounded(attempt / "stderr.txt", stream_limit)
        process = p._json_file(attempt / "process.json", "original process")
        p._exact_object(process, {"schema", "capture_overflow", "duration_seconds", "execution", "exit_code", "interrupted", "pid", "start_error",
                                 "stderr_bytes", "stderr_limit_reached", "stdout_bytes", "stdout_limit_reached", "timed_out"}, "original process")
        if (process["schema"] != p.collector.PROCESS_SCHEMA or process["execution"] != "completed"
                or type(process["exit_code"]) is not int or process["exit_code"] != 0 or type(process["pid"]) is not int or process["pid"] <= 0
                or process["start_error"] is not None or any(process[key] is not False for key in
                    ("capture_overflow", "interrupted", "timed_out", "stdout_limit_reached", "stderr_limit_reached"))
                or type(process["duration_seconds"]) not in (int, float) or not math.isfinite(process["duration_seconds"])
                or not 0 < process["duration_seconds"] <= 600 or process["stdout_bytes"] != len(events) or process["stderr_bytes"] != len(stderr)
                or max(len(events), len(stderr)) >= stream_limit):
            fail("original native process is incomplete, interrupted, nonzero or overflowed")
        candidates, discovery = p._discover_rollouts(home)
        delivery = p._json_file(attempt / "delivery.json", "original delivery")
        p._exact_object(delivery, {"schema", "process_execution", "rollout_discovery", "rollout", "answer"}, "original delivery")
        if (len(candidates) != 1 or delivery["schema"] != p.collector.DELIVERY_SCHEMA
                or delivery["process_execution"] != "completed" or delivery["rollout_discovery"] != discovery):
            fail("original native candidate discovery is missing, changed or ambiguous")
        native = p.legacy._read_bounded(candidates[0], s.CAPTURE_CAPACITY["capture_limit"])
        copied = p.legacy._read_bounded(attempt / "rollout.jsonl", file_limit)
        answer = p.legacy._read_bounded(attempt / "answer.txt", p.capture.MAX_ANSWER_BYTES)
        if answer != p.legacy._read_bounded(evidence / "last-message.txt", p.capture.MAX_ANSWER_BYTES):
            fail("original answer copy differs from native delivery")
        expected_answer = {"state": "captured", "reason": "nonempty_explicit_answer_file", "source": str(evidence / "last-message.txt"),
                           "bytes": len(answer), "sha256": p._sha(answer), "retained": "answer.txt", "finality": "unknown",
                           "finality_reason": "requires version-bounded completion comparison"}
        if delivery["answer"] != expected_answer:
            fail("original answer receipt changed")
        thread, cli_problem = p.collector._cli_identity(events, **parse)
        native_thread, status, issues = p.collector._rollout_identity(native, **parse)
        if cli_problem or status != "usable" or issues or thread != native_thread or thread in threads:
            fail("original candidate identity is missing, mismatched or duplicated")
        threads.add(thread)
        report = p.capture.capture_bytes(events, native, p._encoded(process), answer, **parse)
        completion = p.capture.completion_evidence_bytes(events, native, answer, **parse)
        audit = p._audit(report, manifest["config"])
        if _patch_slot:
            import benchmark_issue10_intercepted_patch as patch
            audit = patch.audit(report, manifest["config"], base / "scratch")
        contexts = [row.get("payload") for row in p.capture.parse_jsonl(native, "recovery_model", **parse)["records"] if isinstance(row, dict) and row.get("type") == "turn_context"]
        errors = [row for row in p.capture.parse_jsonl(events, "recovery_cli", **parse)["records"] if isinstance(row, dict) and isinstance(row.get("item"), dict) and row["item"].get("type") == "error"]
        if ((report["issues"] and not _patch_slot) or not report["usage"]["totals_match"] or not report["usage"]["complete"] or report["compaction"]["observed"]
                or completion["status"] != "matched" or audit["status"] != "bounded-recorded" or errors or not contexts
                or any(not isinstance(ctx, dict) or ctx.get("model") != "gpt-5.6-sol" or ctx.get("effort") != "high" for ctx in contexts)):
            fail("recovery cannot excuse model, capture, usage, completion, compaction or unsupported-record failures")
        old_limits = parse if _patch_slot else {}
        old_report = p.capture.capture_bytes(events, copied, p._encoded(process), answer, **old_limits)
        old_completion = p.capture.completion_evidence_bytes(events, copied, answer, **old_limits)
        old_audit = p._audit(old_report, manifest["config"])
        retained_report = p.json.loads(p.legacy._read_bounded(evidence / "capture-report.json", file_limit),
                                      object_pairs_hook=p._no_duplicate_keys, parse_constant=p._reject_constant)
        retained_audit = p.json.loads(p.legacy._read_bounded(evidence / "tool-audit.json", file_limit),
                                     object_pairs_hook=p._no_duplicate_keys, parse_constant=p._reject_constant)
        if (retained_report != old_report
                or p._json_file(evidence / "completion-evidence.json", "original completion") != old_completion
                or retained_audit != old_audit):
            fail("original retained observer receipts changed")
        result = p._json_file(base / "result.json", "original result")
        core = {"schema": p.RESULT_SCHEMA, "slot": slot, "launch": "attempted", "execution": "completed",
                "usage": {key: old_report["usage"][key] for key in ("totals", "complete", "counting_method")},
                "completion_evidence": {"status": old_completion["status"], "finality": "unknown", "version_bounded": True},
                "experimental_savings_claim_eligible": False,
                "reviews": {"auditor": {"kind": "model", "grade": "unknown"}, "independent_assessor": {"kind": "model", "grade": "unknown"}}}
        p._exact_object(result, set(core) | {"access", "tool_audit", "continuation"}, "original result")
        if (any(result.get(key) != value for key, value in core.items()) or result.get("access", {}).get("status") != "passed"
                or result["access"].get("receipt_digest") != access["digest"] or result["access"].get("fixture_inventory_exact") is not True
                or result["access"].get("fixture_integrity") != {name: True for name in manifest["slot_material"][str(number)]["fixture_files"]}):
            fail("original retained result is not the unchanged accessed native outcome")
        if number != 3:
            expected_rollout = {"state": "captured", "reason": "matched_cli_thread_identity", "cli_thread_id": thread, "selected_candidate": 1,
                "bytes": len(native), "sha256": p._sha(native), "candidates": [{"index": 1, "source": str(candidates[0]), "status": "usable", "thread_id": thread,
                    "bytes": len(native), "sha256": p._sha(native), "retained": "rollout-candidates/001.jsonl", "parser_issue_codes": [], "identity_match": True}]}
            if (copied != native or p.legacy._read_bounded(attempt / "rollout-candidates/001.jsonl", file_limit) != native
                    or (old_report["issues"] and not _patch_slot)
                    or result.get("continuation") != ({"allowed": False, "blockers": ["unsupported_or_unallowed_tool_record"]}
                                                     if _patch_slot else {"allowed": True, "blockers": []})):
                fail("retained collection differs from its narrow size or patch qualification boundary")
            if _patch_slot:
                if old_audit["status"] != "unqualified" or not audit["intercepted_patch"]["qualified_requests"]:
                    fail("slot 4 must retain only the evidenced intercepted-patch audit defect")
                supplementary = {"native": native, "report": report, "completion": completion, "audit": audit}
        else:
            old_cli_id, old_cli_problem = p.collector._cli_identity(events)
            expected_rollout = {"state": "lost", "reason": old_cli_problem or "unresolved_candidate_identity", "cli_thread_id": old_cli_id,
                "selected_candidate": None, "bytes": 0, "sha256": p._sha(b""), "candidates": [{"index": 1, "source": str(candidates[0]), "status": "input_error",
                    "error": f"bounded file exceeds {p.capture.MAX_CAPTURE_BYTES} bytes: {candidates[0].name}"}]}
            blockers = ["observed_model_context_missing_or_mismatched", "rollout_capture_lost"]
            blockers.extend(sorted({f"capture_issue:{i['source']}:{i['code']}" for i in old_report["issues"] if i.get("effect") == "invalid"}))
            if old_audit["status"] != "bounded-recorded":
                blockers.append("unsupported_or_unallowed_tool_record")
            if (copied != b"" or len(native) <= p.capture.MAX_CAPTURE_BYTES
                    or not any(len(line) > p.capture.MAX_LINE_BYTES for line in events.splitlines())
                    or not any(i["source"] == "cli" and i["code"] == "jsonl_record_too_large" for i in old_report["issues"])
                    or result.get("continuation") != {"allowed": False, "blockers": blockers}):
                fail("slot 3 is not the exact size-only observer failure")
            supplementary = {"native": native, "report": report, "completion": completion, "audit": audit}
        if delivery["rollout"] != expected_rollout:
            fail("original rollout delivery is not the uniquely bound captured or oversize outcome")
        expected_audit = {"status": old_audit["status"], "entry_count": old_audit["entry_count"], "observation_count": old_audit["observation_count"],
                          "unique_tool_call_count": None, "os_execution_attestation": False, "semantic_routing": old_audit["semantic_routing"]}
        if result.get("tool_audit") != expected_audit:
            fail("original audit outcome changed")
        inventory = _census(base, manifest, number, discovery, candidates[0])
        proofs.append({"slot": number, "thread": thread, "inventory": inventory, "supplementary_native_sha256": p._sha(native),
                       "native_hash_provenance": "late acquisition, not earlier collector provenance" if number == 3 else "matches retained collector copies"})
    return {"slots": proofs, "acquisition_limit": ACQUISITION_LIMIT}, supplementary


def binding(root, predecessor, snapshot, reason, authorization_note):
    old = s.load_manifest(predecessor, _source_root=snapshot)
    proof, supplementary = prefix(predecessor, old)
    if root == predecessor or root.parent != predecessor.parent or any(not isinstance(text, str) or not text.strip() for text in (reason, authorization_note)):
        fail("capture recovery needs a new sibling, reason and actual preparation authorization")
    value = {"schema": SCHEMA, "predecessor_root": str(predecessor), "source_snapshot": str(snapshot), "predecessor_digest": old["digest"],
             "original_sources": dict(ORIGINAL_SOURCES), "prefix_evidence": proof, "inherited_slots": [1, 2, 3],
             "executable_slots": list(range(4, 37)), "reason": reason, "authorization_note": authorization_note,
             "supplementary": {"native": p._sha(supplementary["native"]), "report": p._sha(p._pretty(supplementary["report"])),
                               "completion": p._sha(p._pretty(supplementary["completion"])), "audit": p._sha(p._pretty(supplementary["audit"]))}}
    value["owner_sha256"] = p._sha(p._pretty(owner(value, root)))
    return old, value, supplementary


def validate(root, manifest):
    value = manifest["capture_recovery"]
    p._exact_object(value, {"schema", "predecessor_root", "source_snapshot", "predecessor_digest", "original_sources", "prefix_evidence", "inherited_slots", "executable_slots", "reason", "authorization_note", "supplementary", "owner_sha256"}, "capture recovery")
    if value["schema"] != SCHEMA or value["inherited_slots"] != [1, 2, 3] or value["executable_slots"] != list(range(4, 37)):
        fail("capture recovery allocation changed")
    old_root = p.legacy._physical(Path(value["predecessor_root"]))
    old, expected, _ = binding(root, old_root, Path(value["source_snapshot"]), value["reason"], value["authorization_note"])
    if expected != value:
        fail("capture recovery predecessor or supplementary provenance changed")
    for key in ("slots", "phase", "schedule_seed", "cases", "repetitions", "session_count", "budget", "config", "config_sha256", "codex", "sshai", "auth", "fixture_bundle", "model_catalog", "tool_overrides", "assessment_inputs", "qualification"):
        if manifest[key] != old[key]:
            fail("capture recovery changed the original task, model, rubric, schedule or budget")
    for number in range(1, 37):
        for key in ("fixture_files", "source_prompt_sha256"):
            if manifest["slot_material"][str(number)][key] != old["slot_material"][str(number)][key]:
                fail("capture recovery changed fixture or source prompt semantics")
    owner_path = old_root / "capture-recovery-owner.json"
    if p.legacy._file_digest(p.legacy._physical(owner_path)) != value["owner_sha256"] or p._json_file(owner_path, "capture recovery ownership") != owner(value, root):
        fail("capture recovery ownership changed or belongs to another root")
    for label, filename in (("native", "native-rollout.jsonl"), ("report", "capture-report.json"), ("completion", "completion-evidence.json"), ("audit", "tool-audit.json")):
        if p.legacy._file_digest(p.legacy._physical(root / "recovery/slot-003" / filename)) != value["supplementary"][label]:
            fail("retained supplementary evidence changed")
    if any((root / "slots" / f"{number:03}").exists() for number in (1, 2, 3)):
        fail("capture recovery cannot reserve or replace inherited outcomes")
    return old_root
