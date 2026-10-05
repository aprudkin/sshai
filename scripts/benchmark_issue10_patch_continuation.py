#!/usr/bin/env python3
"""One pinned 7b201d1 prefix-four continuation; no retry or generic lineage."""
from pathlib import Path

import benchmark_issue10_local_pilot as p
import benchmark_issue10_local_series as s
import benchmark_issue10_local_series_recovery as recovery
import benchmark_issue10_intercepted_patch as patch

SCHEMA = "sshai-benchmark/issue10-local-series-patch-continuation-1"
PREDECESSOR_DIGEST = "55f9e9232aeffd4719b11c7a636b6d14821b35557463a5aa515033ce0b493e47"
PREDECESSOR_SOURCES = {**recovery.ORIGINAL_SOURCES,
    "docs/benchmarks/issue10-local-series.md": "024699ed46679d0f17c79eade74ae422b887c8fa08bc6313e005ccaa205030a7",
    "scripts/benchmark_issue10.py": "f3920fc752df757b9d4b878236cbd277befbdf340fe8b598fdab0f7efafbf16f",
    "scripts/benchmark_issue10_local_pilot.py": "2165bcdbd456335358d928f584fd0c251ee054f4f334cfa72e0939b49716e4cc",
    "scripts/benchmark_issue10_local_series.py": "61f1fc61fc2b92e1ccc1da9f76af9fe99d08ba815a8a15b4a1c2cfef48d90b28",
    "scripts/benchmark_issue10_local_series_recovery.py": "60c35448b7e50db6afe7280f7a4a03d0c4460b7487c21a75087a2723780a05d9",
    "scripts/benchmark_issue10_v3_capture.py": "16036306374e0fdbba1cba0429b94278252c2b70ff88426fe98fa10cee26f50f",
    "scripts/benchmark_issue10_v3_collector.py": "6348221b5cf5ce9718c621da503ce736bdf5b82c38f056cda89566781ab97094"}
SOURCE_CONTRACT = {key: patch.PROFILE[key] for key in ("cli_version", "revision")}
# Public producer pins, not study output. These bind the qualification's source
# interpretation without importing historical code or requesting the network.
_PRODUCERS = {
    "exec_command.rs": ("core/src/tools/handlers/unified_exec/exec_command.rs", "6f9830c9f5eaf06498d09e54548680b7b9442c092f149634f43c608e9b96e496", 21107),
    "apply_patch.rs": ("core/src/tools/handlers/apply_patch.rs", "52badcf510be9f13af138fddd3dc2556ae6b5029b48b0d4e5ad4a5a52cc7adce", 22015),
    "items.rs": ("protocol/src/items.rs", "124429e5639567c07038f91c9f9c9aa0758fd83131c299a3f9e66e8713678612", 26486),
    "stream_events.rs": ("core/src/stream_events_utils.rs", "7919187dae414aded83bc48adaa189c057f6f7ccd71d51e55f57216dce5b5a34", 19340),
    "tasks.rs": ("core/src/tasks/mod.rs", "e4be905fd1d588dad9468916c4d6c93c38b4c52cb9f48b501eb3377d41f76a6e", 37775),
    "jsonl.rs": ("exec/src/event_processor_with_jsonl_output.rs", "2f71fbf8a1b0a79bd342ed3c9caa414f1c5e06d9e52d6a94461799f304a9f255", 26744),
    "writer.rs": ("exec/src/event_processor.rs", "b23e5cff350f621247ca2d7e21d2458ddd515074becf8f7de599aa495459b2b4", 1546),
    "tool_events.rs": ("core/src/tools/events.rs", "d5019b0216bd78cd05a83b8103d311e24ec753e7a662035c00435829e4df6c2b", 30479)}
PRODUCER_SOURCES = {name: {"url": f"https://raw.githubusercontent.com/openai/codex/{SOURCE_CONTRACT['revision']}/codex-rs/{path}",
                           "sha256": digest, "bytes": size} for name, (path, digest, size) in _PRODUCERS.items()}


def owner(binding, root):
    identity = {key: value for key, value in binding.items() if key != "owner_sha256"}
    return {"schema": SCHEMA, "continuation_root": str(root), "binding_sha256": p._sha(p._encoded(identity)),
            "executable_slots": list(range(5, 37)), "one_shot": True, "launch_approval": False}


def qualification(path, predecessor, manifest, supplementary):
    path = p._private_regular(Path(path), "slot-4 qualification provenance")
    receipt = p._json_file(path, "slot-4 qualification provenance")
    if (receipt.get("schema") != "sshai-benchmark/issue10-local-live-qualification-1"
            or receipt.get("phase_manifest_digest") != manifest["digest"] or receipt.get("slot") != manifest["slots"][3]
            or receipt.get("source_contract") != SOURCE_CONTRACT or receipt.get("source_references") != PRODUCER_SOURCES
            or receipt.get("completion") != supplementary["completion"] or receipt.get("usage") != supplementary["report"]["usage"]):
        recovery.fail("qualification does not bind the pinned slot, producer, completion and usage evidence")
    base = predecessor / "slots/004"
    fixed = {"events": base / "evidence/attempt/events.jsonl", "rollout": base / "evidence/attempt/rollout.jsonl",
             "answer": base / "evidence/attempt/answer.txt", "process": base / "evidence/attempt/process.json",
             "original_result": base / "result.json", "original_delivery": base / "evidence/attempt/delivery.json",
             "original_audit": base / "evidence/tool-audit.json", "prompt": base / "evidence/prompt.txt",
             "reservation": base / "reservation.json", "manifest": predecessor / "manifest.json"}
    bindings = receipt.get("source_bindings")
    if not isinstance(bindings, dict) or not set(fixed) <= set(bindings) or set(bindings) - set(fixed) - {"scratch_script", "saved_artifact"}:
        recovery.fail("qualification source-binding inventory is missing or unsupported")
    for key, value in bindings.items():
        p._exact_object(value, {"path", "bytes", "sha256"}, "qualification source binding")
        if not isinstance(value["path"], str) or not Path(value["path"]).is_absolute():
            recovery.fail("qualification source path must be explicit and absolute")
        p._hex_digest(value["sha256"], "qualification source hash")
        source = p.legacy._physical(Path(value["path"]))
        if key in fixed:
            if source != fixed[key]:
                recovery.fail("qualification binding refers to a foreign source")
        elif not source.is_relative_to(base / "scratch"):
            recovery.fail("qualification auxiliary binding is outside the retained scratch evidence")
        data = p.legacy._read_bounded(source, s.CAPTURE_CAPACITY["capture_limit"])
        if type(value["bytes"]) is not int or value["bytes"] != len(data) or value["sha256"] != p._sha(data):
            recovery.fail("qualification source bytes or hash changed")
    data = p.legacy._read_bounded(path, p.capture.MAX_CAPTURE_BYTES)
    # Status/routing/approval booleans are deliberately not eligibility shortcuts.
    return {"path": str(path), "bytes": len(data), "sha256": p._sha(data)}


def binding(root, predecessor, snapshot, receipt, reason, authorization_note):
    if (root == predecessor or root.parent != predecessor.parent
            or any(not isinstance(text, str) or not text.strip() for text in (reason, authorization_note))):
        recovery.fail("patch continuation needs a new sibling, reason and actual preparation authorization")
    old = s.load_manifest(predecessor, _source_root=snapshot, _patch_predecessor=True)
    if old["digest"] != PREDECESSOR_DIGEST:
        recovery.fail("patch continuation accepts only the exact retained 7b measured root")
    proof, supplementary = recovery.prefix(predecessor, old, _patch_slot=True)
    value = {"schema": SCHEMA, "predecessor_root": str(predecessor), "source_snapshot": str(snapshot),
             "predecessor_digest": old["digest"], "predecessor_sources": dict(PREDECESSOR_SOURCES),
             "ancestor_binding_sha256": p._sha(p._encoded(old["capture_recovery"])), "prefix_evidence": proof,
             "readiness_evidence": {name: p.legacy._file_digest(p.legacy._physical(predecessor / "readiness" / name))
                 for name in ["result.json", *(f"access-{case}.json" for case in s.CASES)]},
             "inherited_slots": [1, 2, 3, 4], "executable_slots": list(range(5, 37)),
             "reason": reason, "authorization_note": authorization_note,
             "qualification": qualification(receipt, predecessor, old, supplementary),
             "supplementary": {label: p._sha(p._pretty(supplementary[label])) for label in ("report", "completion", "audit")}}
    value["owner_sha256"] = p._sha(p._pretty(owner(value, root)))
    return old, value, supplementary


def validate(root, manifest):
    value = manifest["patch_continuation"]
    p._exact_object(value, {"schema", "predecessor_root", "source_snapshot", "predecessor_digest", "predecessor_sources",
        "ancestor_binding_sha256", "prefix_evidence", "readiness_evidence", "inherited_slots", "executable_slots", "reason", "authorization_note",
        "qualification", "supplementary", "owner_sha256"}, "patch continuation")
    if (value["schema"] != SCHEMA or value["inherited_slots"] != [1, 2, 3, 4]
            or value["executable_slots"] != list(range(5, 37)) or "capture_recovery" in manifest
            or manifest.get("intercepted_patch_profile") != patch.PROFILE):
        recovery.fail("patch continuation allocation, profile or lineage changed")
    old_root = p.legacy._physical(Path(value["predecessor_root"]))
    old, expected, _ = binding(root, old_root, Path(value["source_snapshot"]), Path(value["qualification"]["path"]),
                              value["reason"], value["authorization_note"])
    if expected != value:
        recovery.fail("patch continuation retained source, evidence or provenance changed")
    for key in ("slots", "phase", "schedule_seed", "cases", "repetitions", "session_count", "budget", "config", "config_sha256",
                "codex", "sshai", "auth", "fixture_bundle", "model_catalog", "tool_overrides", "assessment_inputs", "qualification", "capture_capacity"):
        if manifest[key] != old[key]:
            recovery.fail("patch continuation changed the original task, model, rubric, schedule or budget")
    for number in range(1, 37):
        for key in ("fixture_files", "source_prompt_sha256"):
            if manifest["slot_material"][str(number)][key] != old["slot_material"][str(number)][key]:
                recovery.fail("patch continuation changed fixture or prompt semantics")
    owner_path = p.legacy._physical(old_root / "patch-continuation-owner.json")
    if p.legacy._file_digest(owner_path) != value["owner_sha256"] or p._json_file(owner_path, "patch continuation owner") != owner(value, root):
        recovery.fail("patch continuation ownership changed or belongs to another root")
    for label, filename in (("report", "capture-report.json"), ("completion", "completion-evidence.json"), ("audit", "tool-audit.json")):
        if p.legacy._file_digest(p.legacy._physical(root / "continuation/slot-004" / filename)) != value["supplementary"][label]:
            recovery.fail("retained patch supplementary evidence changed")
    if any((root / "slots" / f"{number:03}").exists() for number in (1, 2, 3, 4)):
        recovery.fail("patch continuation cannot reserve or replace inherited outcomes")
    return old_root
