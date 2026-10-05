#!/usr/bin/env python3
"""Prospective one-shot local Issue 10 measurement controller, never a pilot replacement.

Preparation/preflight launch no model. A real phase requires coordinator-reviewed pilot
prerequisites, a new manifest-bound task approval and explicit --allow-model-run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import stat
import sys
from typing import Any, Callable

import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_v3 as coordinator

legacy = pilot.legacy
MANIFEST_SCHEMA = "sshai-benchmark/issue10-local-series-manifest-1"
APPROVAL_SCHEMA = "sshai-benchmark/issue10-local-series-approval-1"
READINESS_SCHEMA = "sshai-benchmark/issue10-local-series-readiness-1"
SUMMARY_SCHEMA = "sshai-benchmark/issue10-local-series-summary-1"
RUN_SCHEMA = "sshai-benchmark/issue10-local-series-result-summary-1"
PHASE = "local-measurement-amendment-1"
CASES = tuple(f"M{number:02}" for number in range(1, 7))
SESSION_COUNT = 36
SEED = 1010
CAPTURE_CAPACITY = {"stream_limit": 8 * 1024 * 1024, "capture_limit": 8 * 1024 * 1024,
                    "line_limit": 4 * 1024 * 1024}
BUDGET = {"local_pilot_consumed": 4, "local_measurement_allocated": 36,
          "remote_pilot_allocated": 8, "remote_measurement_allocated": 72}
SOURCE_PATHS = pilot.SOURCE_PATHS | {
    "scripts/benchmark_issue10_local_series.py", "scripts/benchmark_issue10_v3.py",
    "scripts/benchmark_issue10_v3_cases.py", "docs/benchmarks/issue10-protocol.md",
    "docs/benchmarks/issue10-local-series.md", "docs/benchmarks/issue10-local-pilot-results.md",
    "scripts/benchmark_issue10_local_series_recovery.py",
    "scripts/benchmark_issue10_intercepted_patch.py", "scripts/benchmark_issue10_patch_continuation.py",
}
_BINARY_PROBE: Callable[..., dict[str, Any]] | None = None
_ACCESS_QUALIFIER: Callable[..., dict[str, Any]] | None = None
_ATTEMPT_COLLECTOR: Callable[..., dict[str, Any]] | None = None


def schedule() -> list[dict[str, Any]]:
    local = [slot for slot in coordinator.schedule("measurement", SEED) if slot["series"] == "M"]
    return [{**slot, "protocol_slot": slot["slot"], "slot": index}
            for index, slot in enumerate(local, 1)]


def approval_template(manifest: dict[str, Any]) -> dict[str, Any]:
    return {"schema": APPROVAL_SCHEMA, "manifest_digest": manifest["digest"], "phase": PHASE,
            "session_count": SESSION_COUNT, "original_diagnostic_session_ceiling": 120,
            "config_sha256": manifest["config_sha256"], "approved": False,
            "approved_at_utc": "", "authorization_note": "",
            "local_pilot_review": {"status": "pending", "provenance": ""}}


def _material(root: Path, slots: list[dict[str, Any]], selected: dict[str, dict[str, bytes]],
              sshai: str) -> dict[str, Any]:
    material = {}
    for slot in slots:
        files = selected[slot["case_id"]]
        base = root / "slots" / f"{slot['slot']:03}"
        prompt = pilot._render_prompt(files["__prompt__"], base / "fixture", base / "scratch", slot["arm"], sshai)
        material[str(slot["slot"])] = {
            "fixture_files": {name: {"bytes": len(data), "sha256": pilot._sha(data)}
                              for name, data in sorted(files.items()) if name != "__prompt__"},
            "source_prompt_sha256": pilot._sha(files["__prompt__"]),
            "rendered_prompt_sha256": pilot._sha(prompt),
        }
    return material


def prepare(root: Path, fixture_bundle: Path, codex_path: Path, sshai_path: Path,
            config_path: Path, model_catalog_path: Path, tool_overrides_path: Path,
            auth_path: Path, assessment_instructions_path: Path,
            assessment_rubric_path: Path, *, _recovery_binding=None, _patch_binding=None, _supplementary=None) -> dict[str, Any]:
    root = legacy._physical(Path(root), must_exist=False)
    bundle = legacy._physical(Path(fixture_bundle))
    codex = pilot._private_regular(codex_path, "Codex", executable=True)
    sshai = pilot._private_regular(sshai_path, "sshai", executable=True)
    auth = pilot._private_regular(auth_path, "dedicated authentication file")
    if codex != pilot.EXPECTED_CODEX_PATH or stat.S_IMODE(auth.stat().st_mode) != 0o600 or root.parent != auth.parent.parent:
        raise pilot.PilotInputError("series requires the selected native binary and a new dedicated-private-parent child")
    config = pilot._validate_config(pilot._json_file(config_path, "series configuration"))
    catalog_bytes = legacy._read_bounded(pilot._private_regular(model_catalog_path, "model catalog"), pilot.capture.MAX_CAPTURE_BYTES)
    catalog = pilot._validate_model_catalog(pilot._json_file(model_catalog_path, "model catalog"), config["model"]["id"])
    controls_bytes = legacy._read_bounded(pilot._private_regular(tool_overrides_path, "tool controls"), pilot.capture.MAX_CAPTURE_BYTES)
    try:
        controls = json.loads(controls_bytes.decode("utf-8"), object_pairs_hook=pilot._no_duplicate_keys,
                              parse_constant=pilot._reject_constant)
    except (ValueError, UnicodeDecodeError) as exc:
        raise pilot.PilotInputError("invalid tool controls") from exc
    if controls != pilot.REQUIRED_CODEX_OVERRIDES:
        raise pilot.PilotInputError("series controls differ from the qualified local controls")
    assessment = {}
    for label, path in (("instructions", assessment_instructions_path), ("rubric", assessment_rubric_path)):
        data = legacy._read_bounded(pilot._private_regular(path, f"assessment {label}"), pilot.capture.MAX_CAPTURE_BYTES)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise pilot.PilotInputError("assessment inputs must be UTF-8") from exc
        if not text.strip() or pilot._sha(data) != config["assessment"][f"{label}_sha256"]:
            raise pilot.PilotInputError("assessment input differs from its frozen configuration")
        assessment[label] = data
    fixture_manifest, selected = pilot._fixture_inventory(bundle, cases=CASES)
    probes = (_BINARY_PROBE or pilot._probe_binaries)(codex, sshai, config)
    slots = schedule()
    manifest = {
        "schema": MANIFEST_SCHEMA, "phase": PHASE, "approval_schema": APPROVAL_SCHEMA,
        "schedule_seed": SEED, "slots": slots, "cases": list(CASES), "repetitions": 3,
        "random_generator": "Python random.Random / MT19937; existing v3 M-series projection",
        "session_count": SESSION_COUNT, "capture_capacity": dict(CAPTURE_CAPACITY),
        "original_diagnostic_session_ceiling": 120,
        "assessor_context_ceiling": 24, "local_assessment_contexts": 6, "budget": dict(BUDGET),
        "no_retry_no_resume": True, "local_only": True,
        "fixture_bundle": {"path": str(bundle), "manifest_sha256": legacy._file_digest(bundle / "manifest.json"),
                           "schema_version": fixture_manifest["schema_version"]},
        "slot_material": _material(root, slots, selected, str(sshai)),
        "codex": {"path": str(codex), **config["codex"], "version_output_sha256": probes["codex_version_output_sha256"]},
        "sshai": {"path": str(sshai), **config["sshai"]},
        "auth": {"path": str(auth), "required_mode": "0600", "content_read_or_retained": False},
        "config": config, "config_sha256": pilot._sha(pilot._encoded(config)),
        "model_catalog": {"sha256": pilot._sha(catalog_bytes), "bytes": len(catalog_bytes),
                          "selected_slug": catalog["models"][0]["slug"], "backend_availability": "unverified"},
        "tool_overrides": {"sha256": pilot._sha(controls_bytes), "bytes": len(controls_bytes)},
        "assessment_inputs": {label: {"bytes": len(data), "sha256": pilot._sha(data),
                                      "retained": f"prepared/assessment/{label}.md"} for label, data in assessment.items()},
        "sources": {name: legacy._file_digest(legacy._physical(pilot.REPO / name)) for name in sorted(SOURCE_PATHS)},
        "qualification": {"local_pilot_review": "required", "access": "required-per-slot", "tool_audit": "bounded-records",
                          "answer_finality": "version-bounded-evidence-only", "independent_model_assessment": "unknown"},
        "experimental_savings_claim_eligible": False,
    }
    if _recovery_binding is not None:
        manifest["capture_recovery"] = _recovery_binding
    if _patch_binding is not None:
        import benchmark_issue10_intercepted_patch as patch
        manifest["patch_continuation"] = _patch_binding
        manifest["intercepted_patch_profile"] = dict(patch.PROFILE)
    manifest["digest"] = pilot._digest_object(manifest)
    if len(pilot._pretty(manifest)) > pilot.capture.MAX_CAPTURE_BYTES:
        raise pilot.PilotInputError("series manifest exceeds its bounded input contract")
    if _recovery_binding is not None:
        import benchmark_issue10_local_series_recovery as recovery
        try:
            legacy._write_new(Path(_recovery_binding["predecessor_root"]) / "capture-recovery-owner.json",
                              pilot._pretty(recovery.owner(_recovery_binding, root)))
        except FileExistsError as exc:
            raise pilot.PilotInputError("original series capture recovery is already claimed") from exc
    if _patch_binding is not None:
        import benchmark_issue10_patch_continuation as continuation
        try:
            legacy._write_new(Path(_patch_binding["predecessor_root"]) / "patch-continuation-owner.json",
                              pilot._pretty(continuation.owner(_patch_binding, root)))
        except FileExistsError as exc:
            raise pilot.PilotInputError("measured patch continuation is already claimed") from exc
    legacy._new_dir(root)
    legacy._mkdir_private(root / "slots")
    legacy._mkdir_private(root / "prepared")
    for case, files in selected.items():
        for name, data in sorted(files.items()):
            relative = f"prompts/{case}.md" if name == "__prompt__" else f"inputs/{case}/{name}"
            legacy._write_new(root / "prepared" / relative, data, mode=0o400)
    for label, data in assessment.items():
        legacy._write_new(root / f"prepared/assessment/{label}.md", data, mode=0o400)
    for directory in sorted([p for p in (root / "prepared").rglob("*") if p.is_dir()], key=lambda p: len(p.parts), reverse=True):
        directory.chmod(0o500)
    (root / "prepared").chmod(0o500)
    for name, data in (("config.json", pilot._pretty(config)), ("model-catalog.json", catalog_bytes),
                       ("tool-overrides.json", controls_bytes), ("manifest.json", pilot._pretty(manifest)),
                       ("approval-template.json", pilot._pretty(approval_template(manifest)))):
        legacy._write_new(root / name, data, mode=0o400 if name in {"model-catalog.json", "tool-overrides.json"} else 0o600)
    if _recovery_binding is not None:
        for label, filename in (("native", "native-rollout.jsonl"), ("report", "capture-report.json"),
                                ("completion", "completion-evidence.json"), ("audit", "tool-audit.json")):
            data = _supplementary[label] if label == "native" else pilot._pretty(_supplementary[label])
            legacy._write_new(root / "recovery/slot-003" / filename, data, mode=0o400)
    if _patch_binding is not None:
        for label, filename in (("report", "capture-report.json"), ("completion", "completion-evidence.json"), ("audit", "tool-audit.json")):
            legacy._write_new(root / "continuation/slot-004" / filename, pilot._pretty(_supplementary[label]), mode=0o400)
    return manifest


def prepare_capture_recovery(root: Path, predecessor: Path, source_snapshot: Path, *,
                             reason: str, authorization_note: str) -> dict[str, Any]:
    import benchmark_issue10_local_series_recovery as recovery
    root = legacy._physical(Path(root), must_exist=False)
    predecessor = legacy._physical(Path(predecessor))
    snapshot = legacy._physical(Path(source_snapshot))
    if (predecessor / "capture-recovery-owner.json").exists() or (predecessor / "capture-recovery-owner.json").is_symlink():
        raise pilot.PilotInputError("original series capture recovery is already claimed")
    old, binding, supplementary = recovery.binding(root, predecessor, snapshot, reason, authorization_note)
    return prepare(root, Path(old["fixture_bundle"]["path"]), Path(old["codex"]["path"]), Path(old["sshai"]["path"]),
                   predecessor / "config.json", predecessor / "model-catalog.json", predecessor / "tool-overrides.json",
                   Path(old["auth"]["path"]), predecessor / "prepared/assessment/instructions.md",
                   predecessor / "prepared/assessment/rubric.md", _recovery_binding=binding, _supplementary=supplementary)


def prepare_patch_continuation(root: Path, predecessor: Path, source_snapshot: Path, *,
                               qualification: Path, reason: str, authorization_note: str) -> dict[str, Any]:
    import benchmark_issue10_patch_continuation as continuation
    root = legacy._physical(Path(root), must_exist=False)
    predecessor = legacy._physical(Path(predecessor))
    snapshot = legacy._physical(Path(source_snapshot))
    owner = predecessor / "patch-continuation-owner.json"
    if owner.exists() or owner.is_symlink():
        raise pilot.PilotInputError("measured patch continuation is already claimed")
    old, binding, supplementary = continuation.binding(root, predecessor, snapshot, qualification, reason, authorization_note)
    return prepare(root, Path(old["fixture_bundle"]["path"]), Path(old["codex"]["path"]), Path(old["sshai"]["path"]),
                   predecessor / "config.json", predecessor / "model-catalog.json", predecessor / "tool-overrides.json",
                   Path(old["auth"]["path"]), predecessor / "prepared/assessment/instructions.md",
                   predecessor / "prepared/assessment/rubric.md", _patch_binding=binding, _supplementary=supplementary)


def _slots_inventory(root: Path) -> None:
    for path in legacy._physical(root / "slots").iterdir():
        if path.name not in {f"{number:03}" for number in range(1, 37)} or not legacy._physical(path).is_dir():
            raise pilot.PilotInputError("foreign or unsafe slot outside the fixed series allocation")


def load_manifest(root: Path, *, _source_root: Path | None = None, _patch_predecessor: bool = False) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = pilot._json_file(root / "manifest.json", "series manifest")
    expected = {"schema": MANIFEST_SCHEMA, "phase": PHASE, "approval_schema": APPROVAL_SCHEMA,
                "slots": schedule(), "schedule_seed": SEED, "cases": list(CASES), "repetitions": 3,
                "session_count": 36, "budget": BUDGET, "capture_capacity": CAPTURE_CAPACITY,
                "original_diagnostic_session_ceiling": 120,
                "assessor_context_ceiling": 24, "local_assessment_contexts": 6,
                "no_retry_no_resume": True, "local_only": True, "experimental_savings_claim_eligible": False}
    source_root = pilot.REPO
    source_inventory = SOURCE_PATHS
    if _patch_predecessor and _source_root is None:
        raise pilot.PilotInputError("7b predecessor requires its explicit source snapshot")
    if _source_root is not None:
        import benchmark_issue10_local_series_recovery as recovery
        source_root = legacy._physical(Path(_source_root))
        if _patch_predecessor:
            import benchmark_issue10_patch_continuation as continuation
            source_inventory = set(continuation.PREDECESSOR_SOURCES)
            if (manifest.get("sources") != continuation.PREDECESSOR_SOURCES
                    or manifest.get("digest") != continuation.PREDECESSOR_DIGEST
                    or "capture_recovery" not in manifest or "patch_continuation" in manifest):
                raise pilot.PilotInputError("historical patch predecessor must be the exact 7b size-recovery root")
        else:
            source_inventory = set(recovery.ORIGINAL_SOURCES)
            expected.pop("capture_capacity")
            if (manifest.get("sources") != recovery.ORIGINAL_SOURCES
                    or "capture_capacity" in manifest or "capture_recovery" in manifest):
                raise pilot.PilotInputError("historical source exception is only the original 4d283d7 series")
    if "intercepted_patch_profile" in manifest and "patch_continuation" not in manifest:
        raise pilot.PilotInputError("intercepted patch profile requires the pinned prospective continuation")
    if any(manifest.get(key) != value for key, value in expected.items()) or manifest.get("digest") != pilot._digest_object(manifest):
        raise pilot.PilotInputError("series manifest policy, schedule or digest changed")
    sources = manifest.get("sources")
    if not isinstance(sources, dict) or set(sources) != source_inventory:
        raise pilot.PilotInputError("series source pin inventory changed")
    for name, digest in sources.items():
        pilot._hex_digest(digest, "series source pin")
        if legacy._file_digest(legacy._physical(source_root / name)) != digest:
            raise pilot.PilotInputError("series source/protocol changed since preparation")
    config = pilot._validate_config(manifest.get("config"))
    if pilot._sha(pilot._encoded(config)) != manifest.get("config_sha256") or pilot._json_file(root / "config.json", "retained configuration") != config:
        raise pilot.PilotInputError("series configuration changed")
    for label in ("codex", "sshai"):
        info = manifest[label]
        executable = pilot._private_regular(Path(info["path"]), label, executable=True)
        if (legacy._file_digest(executable) != info["sha256"]
                or any(info.get(key) != value for key, value in config[label].items())
                or (label == "codex" and executable != pilot.EXPECTED_CODEX_PATH)):
            raise pilot.PilotInputError("series binary or binary configuration changed")
    auth = pilot._private_regular(Path(manifest["auth"]["path"]), "dedicated authentication file")
    if (stat.S_IMODE(auth.stat().st_mode) != 0o600 or root.parent != auth.parent.parent
            or manifest["auth"] != {"path": str(auth), "required_mode": "0600", "content_read_or_retained": False}):
        raise pilot.PilotInputError("series authentication path/mode changed")
    for name, label in (("model-catalog.json", "model_catalog"), ("tool-overrides.json", "tool_overrides")):
        data = legacy._read_bounded(root / name, pilot.capture.MAX_CAPTURE_BYTES)
        if len(data) != manifest[label]["bytes"] or pilot._sha(data) != manifest[label]["sha256"]:
            raise pilot.PilotInputError("series catalog or tool controls changed")
    pilot._validate_model_catalog(pilot._json_file(root / "model-catalog.json", "retained catalog"), config["model"]["id"])
    for label in ("instructions", "rubric"):
        info = manifest["assessment_inputs"][label]
        if info.get("retained") != f"prepared/assessment/{label}.md":
            raise pilot.PilotInputError("unsafe assessment input path")
        data = legacy._read_bounded(legacy._physical(root / info["retained"]), pilot.capture.MAX_CAPTURE_BYTES)
        if len(data) != info["bytes"] or pilot._sha(data) != info["sha256"] or info["sha256"] != config["assessment"][f"{label}_sha256"]:
            raise pilot.PilotInputError("series assessment inputs changed")
    bundle = legacy._physical(Path(manifest["fixture_bundle"]["path"]))
    if legacy._file_digest(bundle / "manifest.json") != manifest["fixture_bundle"]["manifest_sha256"]:
        raise pilot.PilotInputError("series fixture manifest changed")
    _, selected = pilot._fixture_inventory(bundle, cases=CASES)
    if manifest.get("slot_material") != _material(root, manifest["slots"], selected, manifest["sshai"]["path"]):
        raise pilot.PilotInputError("series fixture, prompt or rendered guidance changed")
    for case, files in selected.items():
        for name, expected_data in files.items():
            relative = f"prompts/{case}.md" if name == "__prompt__" else f"inputs/{case}/{name}"
            if legacy._read_bounded(legacy._physical(root / "prepared" / relative), pilot.capture.MAX_CAPTURE_BYTES) != expected_data:
                raise pilot.PilotInputError("retained series material changed")
    _slots_inventory(root)
    if "capture_recovery" in manifest:
        import benchmark_issue10_local_series_recovery as recovery
        recovery.validate(root, manifest)
    if "patch_continuation" in manifest:
        import benchmark_issue10_patch_continuation as continuation
        continuation.validate(root, manifest)
    return manifest


def preflight(root: Path) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    if any((root / "slots").iterdir()):
        raise pilot.PilotInputError("preflight cannot follow a diagnostic reservation")
    readiness = root / "readiness"
    if readiness.exists() or readiness.is_symlink():
        raise pilot.PilotInputError("series preflight is one-shot")
    legacy._new_dir(readiness)
    legacy._mkdir_private(readiness / "cases")
    rows = []
    case = None
    try:
        for case in CASES:
            slot = next(s for s in manifest["slots"] if s["case_id"] == case)
            base = readiness / "cases" / case
            legacy._mkdir_private(base)
            fixture, scratch = base / "fixture", base / "scratch"
            pilot._copy_fixture(manifest, root, slot, fixture)
            for path in (scratch, base / "home", base / "codex-home", scratch / "sshai-root", scratch / "tmp",
                         base / "home/.config", base / "home/.cache", base / "home/.local-share"):
                legacy._mkdir_private(path)
            auth = Path(manifest["auth"]["path"])
            protected = [auth.parent.parent, Path.home(), Path(manifest["fixture_bundle"]["path"]),
                         root / "approval-template.json", auth]
            receipt = pilot._validated_access_receipt((_ACCESS_QUALIFIER or pilot._qualify_access)(
                Path(manifest["codex"]["path"]), Path(manifest["sshai"]["path"]), scratch, fixture, protected,
                pilot._environment(base, scratch, manifest["config"]), manifest["slot_material"][str(slot["slot"])]["fixture_files"]))
            legacy._write_new(readiness / f"access-{case}.json", pilot._pretty(receipt))
            rows.append({"case_id": case, "status": "passed", "receipt_digest": receipt["digest"]})
    except BaseException as exc:
        failed = {"schema": READINESS_SCHEMA, "manifest_digest": manifest["digest"], "phase": PHASE,
                  "status": "blocked", "case_id": case, "reason": f"{type(exc).__name__}: {exc}",
                  "model_launches": 0, "scheduled_slots_consumed": 0, "cases": rows}
        failed["digest"] = pilot._digest_object(failed)
        legacy._write_new(readiness / "result.json", pilot._pretty(failed))
        raise
    result = {"schema": READINESS_SCHEMA, "phase": PHASE, "manifest_digest": manifest["digest"], "status": "passed",
              "model_launches": 0, "scheduled_slots_consumed": 0, "cases": rows,
              "advertised_tools": manifest["config"]["codex_exec"]["advertised_tools"],
              "limitations": ["No model/backend, usage, finality, semantic routing or pilot assessment was qualified.",
                              "Access canaries do not attest complete confinement."]}
    result["digest"] = pilot._digest_object(result)
    legacy._write_new(readiness / "result.json", pilot._pretty(result))
    return {"schema": READINESS_SCHEMA, "phase": PHASE, "status": "passed", "model_launches": 0,
            "scheduled_slots_consumed": 0, "cases": [{"case_id": case, "status": "passed"} for case in CASES],
            "limitations": result["limitations"]}


def load_readiness(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    result = pilot._json_file(root / "readiness/result.json", "series readiness")
    if (result.get("schema") != READINESS_SCHEMA or result.get("phase") != PHASE or result.get("status") != "passed"
            or result.get("manifest_digest") != manifest["digest"] or result.get("digest") != pilot._digest_object(result)
            or type(result.get("model_launches")) is not int or result["model_launches"] != 0
            or type(result.get("scheduled_slots_consumed")) is not int or result["scheduled_slots_consumed"] != 0
            or result.get("advertised_tools") != manifest["config"]["codex_exec"]["advertised_tools"]):
        raise pilot.PilotInputError("series readiness is absent, unsuccessful or changed")
    rows = result.get("cases")
    if not isinstance(rows, list) or len(rows) != 6 or any(not isinstance(row, dict) for row in rows) or [row.get("case_id") for row in rows] != list(CASES):
        raise pilot.PilotInputError("series readiness case inventory changed")
    for row in rows:
        pilot._exact_object(row, {"case_id", "status", "receipt_digest"}, "readiness case")
        receipt = pilot._validated_access_receipt(pilot._json_file(root / "readiness" / f"access-{row['case_id']}.json", "readiness access"))
        if row["status"] != "passed" or receipt["digest"] != row["receipt_digest"]:
            raise pilot.PilotInputError("series readiness access receipt changed")
    return result


def _approval(manifest: dict[str, Any], path: Path, allow: bool) -> dict[str, Any]:
    path = pilot._private_regular(path, "series approval")
    value = pilot._json_file(path, "series approval")
    template = approval_template(manifest)
    pilot._exact_object(value, set(template), "series approval")
    fixed = {key: val for key, val in template.items() if key not in {"approved", "approved_at_utc", "authorization_note", "local_pilot_review"}}
    review = pilot._exact_object(value["local_pilot_review"], {"status", "provenance"}, "local pilot review provenance")
    if (not allow or stat.S_IMODE(path.stat().st_mode) != 0o600 or value["approved"] is not True
            or any(value[key] != val for key, val in fixed.items()) or review["status"] != "passed"
            or any(not isinstance(text, str) or not text.strip() for text in
                   (value["approved_at_utc"], value["authorization_note"], review["provenance"]))):
        raise pilot.PilotInputError("series requires current task approval, passed pilot-review provenance and --allow-model-run")
    return {"path": path, "value": value, "sha256": legacy._file_digest(path)}


def run_slot(root: Path, number: int, approval_path: Path, *, allow_model_run: bool = False) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    load_readiness(root, manifest)
    approval = _approval(manifest, approval_path, allow_model_run)
    if type(number) is not int or not 1 <= number <= SESSION_COUNT:
        raise pilot.PilotInputError("series slot must be an integer from 1 through 36")
    slot = manifest["slots"][number - 1]
    inherited = manifest.get("patch_continuation", manifest.get("capture_recovery", {})).get("inherited_slots", [])
    if (number in inherited or any((root / name).exists() or (root / name).is_symlink()
                                  for name in ("capture-recovery-owner.json", "patch-continuation-owner.json"))):
        raise pilot.PilotInputError("original slot launch authority was exclusively delegated; no inherited retry")
    for prior in range(1, number):
        if prior in inherited:
            continue
        value = pilot._json_file(root / "slots" / f"{prior:03}" / "result.json", "prior series result")
        if value.get("schema") != pilot.RESULT_SCHEMA or value.get("slot") != manifest["slots"][prior - 1] or value.get("continuation", {}).get("allowed") is not True:
            raise pilot.PilotInputError("series is stopped by a missing or blocked original prior slot")
    base = root / "slots" / f"{number:03}"
    if base.exists() or base.is_symlink():
        raise pilot.PilotInputError("series slot is already consumed; no retry, overwrite or resume")
    legacy._new_dir(base)
    legacy._write_new(base / "reservation.json", pilot._pretty({"manifest_digest": manifest["digest"], "slot": slot,
                                                              "approval_sha256": approval["sha256"], "one_shot": True}))
    result = pilot.collect_reserved_slot(root, manifest, slot, approval,
                                         qualifier=_ACCESS_QUALIFIER, attempt_collector=_ATTEMPT_COLLECTOR)
    return {"schema": RUN_SCHEMA, "phase": PHASE, **result}


def summarize(root: Path) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    rows = []
    for slot in manifest["slots"]:
        base = root / "slots" / f"{slot['slot']:03}"
        lineage = manifest.get("patch_continuation", manifest.get("capture_recovery", {}))
        inherited = slot["slot"] in lineage.get("inherited_slots", [])
        if inherited:
            predecessor = Path(lineage["predecessor_root"])
            if "patch_continuation" in manifest and slot["slot"] < 4:
                previous = pilot._json_file(predecessor / "manifest.json", "retained size-recovery manifest")
                predecessor = Path(previous["capture_recovery"]["predecessor_root"])
            base = predecessor / "slots" / f"{slot['slot']:03}"
        path = base / "result.json"
        if not path.exists() and not path.is_symlink():
            row = {"slot": slot["slot"], "case_id": slot["case_id"], "replicate": slot["replicate"], "arm": slot["arm"],
                   "state": "unattempted", "auditor_grade": "unknown", "independent_model_assessment": "unknown"}
            if base.exists():
                row.update(state="reserved-incomplete", launch="attempted-or-unknown", execution="unknown", retryable=False,
                           continuation={"allowed": False, "blockers": ["reserved_slot_missing_result"]})
        else:
            value = pilot._json_file(path, "series result")
            if value.get("schema") != pilot.RESULT_SCHEMA or value.get("slot") != slot:
                raise pilot.PilotInputError("series result differs from its original slot")
            row = {**pilot._result_summary(value), "replicate": slot["replicate"], "retryable": False}
        if inherited:
            row["inherited_original_outcome"] = True
            if slot["slot"] == 3:
                import benchmark_issue10_local_series_recovery as recovery
                row["supplementary_capture"] = {"status": "size-only supplementary evidence; original flags unchanged",
                    "acquisition_limit": recovery.ACQUISITION_LIMIT, "finality": "unknown", "quality": "unknown"}
            if slot["slot"] == 4 and "patch_continuation" in manifest:
                row["supplementary_patch_audit"] = {"status": "prospective lifecycle qualification; original flags unchanged",
                    "finality": "unknown", "semantic_routing": "unknown", "quality": "unknown"}
        rows.append(row)
    readiness = root / "readiness/result.json"
    status = "not-run"
    if readiness.exists() or readiness.is_symlink():
        value = pilot._json_file(readiness, "series readiness")
        status = value.get("status", "unknown")
        if status == "passed":
            load_readiness(root, manifest)
    return {"schema": SUMMARY_SCHEMA, "phase": PHASE, "scheduled_sessions": 36,
            "original_diagnostic_session_ceiling": 120, "budget": manifest["budget"], "readiness": status,
            "slots": rows, "raw_outputs_included": False, "private_paths_included": False,
            "comparative_savings_claim": None, "experimental_savings_claim_eligible": False}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = commands.add_parser("prepare", help="freeze a new local 36-slot plan; no model launch")
    prepare_cmd.add_argument("root", type=Path)
    for name in ("fixtures", "codex", "sshai", "config", "model-catalog", "tool-overrides", "auth-file", "assessment-instructions", "assessment-rubric"):
        prepare_cmd.add_argument("--" + name, type=Path, required=True)
    recover = commands.add_parser("prepare-capture-recovery", help="only original 4d283d7 slots 1..3 size-only capture recovery")
    recover.add_argument("root", type=Path)
    recover.add_argument("--predecessor", type=Path, required=True)
    recover.add_argument("--source-snapshot", type=Path, required=True)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--authorization-note", required=True)
    patch_cmd = commands.add_parser("prepare-patch-continuation", help="only pinned 7b prefix 1..4; original slots 5..36, no retry")
    patch_cmd.add_argument("root", type=Path)
    for name in ("predecessor", "source-snapshot", "qualification"):
        patch_cmd.add_argument("--" + name, type=Path, required=True)
    patch_cmd.add_argument("--reason", required=True)
    patch_cmd.add_argument("--authorization-note", required=True)
    commands.add_parser("preflight", help="one-shot no-model access qualification").add_argument("root", type=Path)
    run = commands.add_parser("run-slot", help="attempt the next approved original slot once")
    run.add_argument("root", type=Path)
    run.add_argument("--slot", type=int, required=True)
    run.add_argument("--approval", type=Path, required=True)
    run.add_argument("--allow-model-run", action="store_true")
    commands.add_parser("summary", help="sanitized full planned inventory, no savings/grades").add_argument("root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare(args.root, args.fixtures, args.codex, args.sshai, args.config, args.model_catalog,
                           args.tool_overrides, args.auth_file, args.assessment_instructions, args.assessment_rubric)
        output = {"schema": MANIFEST_SCHEMA, "phase": PHASE, "manifest_digest": manifest["digest"],
                  "scheduled_sessions": 36, "model_launches": 0, "approval_required": True,
                  "pilot_review_required": True, "experimental_savings_claim_eligible": False}
    elif args.command == "prepare-capture-recovery":
        manifest = prepare_capture_recovery(args.root, args.predecessor, args.source_snapshot,
                    reason=args.reason, authorization_note=args.authorization_note)
        output = {"schema": MANIFEST_SCHEMA, "phase": PHASE, "manifest_digest": manifest["digest"], "scheduled_sessions": 36,
                  "inherited_slots": [1, 2, 3], "executable_slots": list(range(4, 37)), "model_launches": 0,
                  "approval_required": True, "experimental_savings_claim_eligible": False}
    elif args.command == "prepare-patch-continuation":
        manifest = prepare_patch_continuation(args.root, args.predecessor, args.source_snapshot,
                    qualification=args.qualification, reason=args.reason, authorization_note=args.authorization_note)
        output = {"schema": MANIFEST_SCHEMA, "phase": PHASE, "manifest_digest": manifest["digest"], "scheduled_sessions": 36,
                  "inherited_slots": [1, 2, 3, 4], "executable_slots": list(range(5, 37)), "model_launches": 0,
                  "approval_required": True, "experimental_savings_claim_eligible": False}
    elif args.command == "preflight":
        output = preflight(args.root)
    elif args.command == "run-slot":
        output = run_slot(args.root, args.slot, args.approval, allow_model_run=args.allow_model_run)
    else:
        output = summarize(args.root)
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except pilot.PilotInputError as exc:
        print(f"local series blocked: {exc}", file=sys.stderr)
        raise SystemExit(2)
