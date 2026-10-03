#!/usr/bin/env python3
"""One-shot controller for the four-session Issue 10 local diagnostic pilot.

Preparation is offline apart from bounded executable version/help probes.  ``run-slot``
is the only model-launch path.  It requires an approval bound to the immutable pilot
manifest and a fresh no-model access qualification receipt.  Raw evidence stays in the
private pilot root; stdout contains only a compact path-free summary.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable, Mapping, Sequence

import benchmark_issue10 as legacy
import benchmark_issue10_sandbox as sandbox
import benchmark_issue10_v3_capture as capture
import benchmark_issue10_v3_collector as collector

MANIFEST_SCHEMA = "sshai-benchmark/issue10-local-pilot-manifest-1"
CONFIG_SCHEMA = "sshai-benchmark/issue10-local-pilot-config-1"
APPROVAL_SCHEMA = "sshai-benchmark/issue10-local-pilot-approval-1"
RESULT_SCHEMA = "sshai-benchmark/issue10-local-pilot-result-1"
ACCESS_SCHEMA = "sshai-benchmark/issue10-local-pilot-access-1"
READINESS_SCHEMA = "sshai-benchmark/issue10-local-pilot-readiness-1"
SUMMARY_SCHEMA = "sshai-benchmark/issue10-local-pilot-summary-1"
PHASE = "local-diagnostic-pilot-amendment-1"
CASES = ("M01", "M02")
ARMS = ("baseline", "sshai")
SESSION_COUNT = 4
ORIGINAL_DIAGNOSTIC_SESSION_CEILING = 120
ASSESSOR_CONTEXT_CEILING = 24
DEFAULT_SEED = 1010
MAX_DISCOVERY_ENTRIES = 512
EXPECTED_CODEX_PATH = Path("/opt/homebrew/lib/node_modules/@openai/codex/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex")
REPO = Path(__file__).resolve(strict=True).parent.parent
AMENDMENT = REPO / "docs/benchmarks/issue10-methodology-amendment.md"
SOURCE_CONTRACT = {
    "codex_version": "codex-cli 0.151.0",
    "revision": "78c290807ce710180111df227df3b7a4fe845452",
    "history_mode": "paginated",
}
REQUIRED_DISABLED_CAPABILITIES = {
    "network", "web", "mcp", "dynamic_tools", "extensions", "collaboration",
    "images", "file_changes", "apps", "plugins",
}
REQUIRED_CODEX_OVERRIDES = [
    'cli_auth_credentials_store="file"', 'approval_policy="never"',
    'allow_login_shell=false', 'web_search="disabled"',
    'include_apps_instructions=false', 'include_collaboration_mode_instructions=false',
    'mcp_servers={}', 'plugins={}', 'history.persistence="save-all"',
    'tools.update_plan.enabled=false', 'tools.experimental_request_user_input.enabled=false',
    'agents.enabled=false', 'orchestrator.skills.enabled=false',
    'orchestrator.mcp.enabled=false', 'skills.include_instructions=false',
    'skills.bundled.enabled=false', 'apps._default.enabled=false',
    'features.shell_tool=true', 'features.unified_exec=true',
    'features.shell_snapshot=false', 'features.view_image=false', 'features.hooks=false',
    'features.code_mode=false', 'features.code_mode_host=false', 'features.code_mode_only=false',
    'features.standalone_web_search=false', 'features.request_permissions_tool=false',
    'features.deferred_executor=false', 'features.token_budget=false',
    'features.current_time_reminder=false', 'features.multi_agent=false',
    'features.multi_agent_v2=false', 'features.apps=false', 'features.enable_mcp_apps=false',
    'features.tool_suggest=false', 'features.plugins=false',
    'features.recommended_plugins=false', 'features.remote_plugin=false',
    'features.plugin_sharing=false', 'features.image_generation=false',
    'features.skill_mcp_dependency_install=false', 'features.skill_search=false',
    'features.browser_use=false', 'features.browser_use_external=false',
    'features.browser_use_full_cdp_access=false', 'features.computer_use=false',
]
ENVIRONMENT_KEYS = sandbox.ENVIRONMENT_KEYS
CONTINUATION_SCHEMA = "sshai-benchmark/issue10-local-pilot-continuation-1"
# Exact ad1532b controller: its inventory guard precedes attempt-directory creation
# and _bounded_process; run_slot preserves model-attempt-requested uncertainty.
PREPROCESS_CONTROLLER_SHA256 = "f7741cd99ad17de4c852802d2ffb8e294102416fa036b9353ea26abc2219fafc"
AUTH_CONTINUATION_SCHEMA = "sshai-benchmark/issue10-local-pilot-startup-auth-continuation-1"
AUTH_CONTROLLER_SHA256 = "ed50774e3771da26413dfb41056c80241b70ac6c0392672cb847b8873bb5ac4f"
STARTUP_AUTH_ERROR = "Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in again."
# Evidenced Codex 0.151.0 startup files. Names/types only; never inspect SQLite contents.
AUTH_NATIVE_METADATA = {
    "config.toml", "installation_id", "thread_history_1.sqlite", "thread-writer-locks/.coordination.lock",
    *(name + suffix for name in ("goals_1.sqlite", "logs_2.sqlite", "memories_1.sqlite", "queue_1.sqlite", "state_5.sqlite")
      for suffix in ("", "-shm", "-wal")),
}
SOURCE_PATHS = {
    "scripts/benchmark_issue10_local_pilot.py", "scripts/benchmark_issue10_v3_capture.py",
    "scripts/benchmark_issue10_v3_collector.py", "scripts/benchmark_issue10_sandbox.py",
    "scripts/benchmark_issue10.py", "docs/benchmarks/issue10-methodology-amendment.md",
}

# Injectable only for synthetic tests; production uses the concrete implementations.
_BINARY_PROBE: Callable[[Path, Path, dict[str, Any]], dict[str, Any]] | None = None
_ACCESS_QUALIFIER: Callable[..., dict[str, Any]] | None = None
_ATTEMPT_COLLECTOR: Callable[..., dict[str, Any]] | None = None


class PilotInputError(ValueError):
    """A pilot boundary, pin, manifest, or approval is unsafe or inconsistent."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _digest_object(value: dict[str, Any]) -> str:
    copy = dict(value)
    copy.pop("digest", None)
    return _sha(_encoded(copy))


def _exact_object(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise PilotInputError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _json_file(path: Path, label: str, limit: int = capture.MAX_CAPTURE_BYTES) -> dict[str, Any]:
    try:
        data = legacy._read_bounded(legacy._physical(path), limit)
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                           parse_constant=_reject_constant)
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotInputError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PilotInputError(f"{label} must be a JSON object")
    return value


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _hex_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise PilotInputError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _safe_relative(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise PilotInputError(f"{label} must be text")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in ("", ".", "..") for part in path.parts)
            or any(character in value for character in "\\:\n\r\x00")):
        raise PilotInputError(f"unsafe {label}")
    return value


def _private_regular(path: Path, label: str, *, executable: bool = False) -> Path:
    try:
        physical = legacy._physical(Path(path))
        legacy._regular(physical)
    except (OSError, ValueError) as exc:
        raise PilotInputError(f"unsafe {label}: {exc}") from exc
    if executable and not os.access(physical, os.X_OK):
        raise PilotInputError(f"{label} is not executable")
    return physical


def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
    _exact_object(config, {
        "schema", "codex", "sshai", "model", "assessment", "codex_exec",
        "environment", "limits", "evidence_handling",
    }, "pilot config")
    if config["schema"] != CONFIG_SCHEMA:
        raise PilotInputError("unsupported pilot config schema")
    codex = _exact_object(config["codex"], {"version", "sha256", "source_revision"}, "codex config")
    if codex["version"] != SOURCE_CONTRACT["codex_version"]:
        raise PilotInputError("Codex version differs from the bounded adapter/source contract")
    if codex["source_revision"] != SOURCE_CONTRACT["revision"]:
        raise PilotInputError("Codex source revision differs from the bounded adapter/source contract")
    _hex_digest(codex["sha256"], "codex.sha256")
    sshai = _exact_object(config["sshai"], {"sha256", "help_sha256", "revision"}, "sshai config")
    _hex_digest(sshai["sha256"], "sshai.sha256")
    _hex_digest(sshai["help_sha256"], "sshai.help_sha256")
    if not isinstance(sshai["revision"], str) or not sshai["revision"].strip():
        raise PilotInputError("sshai.revision is required")
    model = _exact_object(config["model"], {"id", "version", "reasoning_effort"}, "model config")
    if any(not isinstance(model[key], str) or not model[key].strip() for key in model):
        raise PilotInputError("model id, version, and reasoning effort are required")
    if model["id"] != "gpt-5.6-sol" or model["reasoning_effort"] != "high":
        raise PilotInputError("the local pilot requires the selected gpt-5.6-sol/high diagnostic model")
    assessment = _exact_object(config["assessment"], {
        "model", "version", "reasoning_effort", "max_contexts", "batching",
        "instructions_sha256", "rubric_sha256",
    }, "assessment config")
    if any(not isinstance(assessment[key], str) or not assessment[key].strip()
           for key in ("model", "version", "reasoning_effort", "batching")):
        raise PilotInputError("independent model assessment settings must be explicit")
    if (assessment["max_contexts"] != ASSESSOR_CONTEXT_CEILING
            or assessment["batching"] != "same-task-both-arms-all-repetitions"):
        raise PilotInputError("assessment must use the selected 24-context same-task batching ceiling")
    _hex_digest(assessment["instructions_sha256"], "assessment.instructions_sha256")
    _hex_digest(assessment["rubric_sha256"], "assessment.rubric_sha256")
    execution = _exact_object(config["codex_exec"], {
        "history_mode", "config_overrides", "advertised_tools",
        "allowed_audit_signatures", "disabled_capabilities",
    }, "codex_exec config")
    if execution["history_mode"] != SOURCE_CONTRACT["history_mode"]:
        raise PilotInputError("only the reviewed paginated history mode is supported")
    overrides = execution["config_overrides"]
    if overrides != REQUIRED_CODEX_OVERRIDES:
        raise PilotInputError("codex_exec.config_overrides must exactly match the qualified local tool controls")
    tools = execution["advertised_tools"]
    if not isinstance(tools, list) or not tools:
        raise PilotInputError("an explicit advertised tool surface is required")
    tool_names = set()
    for index, tool in enumerate(tools):
        _exact_object(tool, {"name", "name_provenance", "schema_sha256", "schema_provenance"},
                      f"advertised tool {index}")
        if not isinstance(tool["name"], str) or not tool["name"] or tool["name"] in tool_names:
            raise PilotInputError("advertised tool names must be nonempty and unique")
        tool_names.add(tool["name"])
        if tool["name_provenance"] not in {"observed", "expected-from-pinned-config"}:
            raise PilotInputError("advertised tool name provenance is unsupported")
        if tool["schema_provenance"] == "observed":
            _hex_digest(tool["schema_sha256"], "advertised tool schema_sha256")
        elif tool["schema_provenance"] == "unavailable":
            if tool["schema_sha256"] is not None:
                raise PilotInputError("an unavailable advertised tool schema must not have a digest")
        else:
            raise PilotInputError("advertised tool schema provenance is unsupported")
    signatures = execution["allowed_audit_signatures"]
    if not isinstance(signatures, list) or not signatures:
        raise PilotInputError("allowed_audit_signatures must explicitly describe the bounded records")
    for index, signature in enumerate(signatures):
        _exact_object(signature, {"source", "record_type", "tool_name", "evidence_kind"},
                      f"audit signature {index}")
        if any(not isinstance(value, str) or not value for value in signature.values()):
            raise PilotInputError("audit signature fields must be nonempty strings")
    disabled = execution["disabled_capabilities"]
    if (not isinstance(disabled, list) or set(disabled) != REQUIRED_DISABLED_CAPABILITIES
            or len(disabled) != len(REQUIRED_DISABLED_CAPABILITIES)):
        raise PilotInputError("disabled_capabilities must exactly cover the non-command pilot surface")
    environment = config["environment"]
    if (not isinstance(environment, dict) or set(environment) - ENVIRONMENT_KEYS
            or not {"PATH", "LANG", "LC_ALL", "SHELL", "USER", "LOGNAME"} <= set(environment)
            or any(not isinstance(k, str) or not isinstance(v, str) or "\x00" in k + v
                   for k, v in environment.items())):
        raise PilotInputError("environment must be an explicit non-secret allowlisted string mapping")
    limits = _exact_object(config["limits"], {"timeout_seconds"}, "limits")
    timeout = limits["timeout_seconds"]
    if timeout != 600:
        raise PilotInputError("the local pilot diagnostic timeout must be exactly 600 seconds")
    handling = _exact_object(config["evidence_handling"], {
        "private", "raw_publication", "retention_days_after_report_or_termination",
        "automatic_deletion",
    }, "evidence handling")
    if handling["private"] is not True or handling["raw_publication"] != "prohibited":
        raise PilotInputError("raw evidence must remain private and unpublished")
    if (handling["retention_days_after_report_or_termination"] != 90
            or handling["automatic_deletion"] is not False):
        raise PilotInputError("pilot evidence requires 90-day retention without automatic deletion")
    return config


def _validate_model_catalog(value: dict[str, Any], model_id: str) -> dict[str, Any]:
    _exact_object(value, {"models"}, "model catalog")
    models = value["models"]
    if not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict):
        raise PilotInputError("model catalog must contain exactly one full model object")
    model = models[0]
    if (model.get("slug") != model_id or model.get("shell_type") != "unified_exec"
            or model.get("apply_patch_tool_type") is not None
            or model.get("tool_mode") != "direct"
            or model.get("supports_search_tool") is not False
            or model.get("experimental_supported_tools") != []
            or model.get("multi_agent_version") != "v2"):
        raise PilotInputError("model catalog does not match the qualified direct unified-exec surface")
    levels = model.get("supported_reasoning_levels")
    if (not isinstance(levels, list)
            or not any(isinstance(level, dict) and level.get("effort") == "high" for level in levels)):
        raise PilotInputError("model catalog does not support high reasoning")
    # Requiring the fields observed in the full catalog prevents a reduced tool-only stub.
    required_metadata = {"display_name", "description", "model_messages", "context_window",
                         "truncation_policy", "input_modalities", "default_reasoning_level"}
    if not required_metadata <= set(model):
        raise PilotInputError("model catalog omits required full-object metadata")
    return value


def _probe_binaries(codex_path: Path, sshai_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    legacy._native_codex(codex_path)
    if legacy._file_digest(codex_path) != config["codex"]["sha256"]:
        raise PilotInputError("Codex binary digest does not match supplied config")
    if legacy._file_digest(sshai_path) != config["sshai"]["sha256"]:
        raise PilotInputError("sshai binary digest does not match supplied config")
    codex_version, version_sha = legacy._offline_probe(
        codex_path, ["--version"], config["codex"]["version"])
    _, help_sha = legacy._offline_probe(sshai_path, ["help"])
    if help_sha != config["sshai"]["help_sha256"]:
        raise PilotInputError("sshai help output does not match supplied config")
    return {"codex_version": codex_version, "codex_version_output_sha256": version_sha,
            "sshai_help_sha256": help_sha}


def schedule(seed: int = DEFAULT_SEED) -> list[dict[str, Any]]:
    """Return two paired cases with exactly balanced arm-first order."""
    if type(seed) is not int:
        raise PilotInputError("schedule seed must be an integer")
    rng = random.Random(seed)
    cases = list(CASES)
    first_arms = list(ARMS)
    rng.shuffle(cases)
    rng.shuffle(first_arms)
    pairs = []
    for case_id, first in zip(cases, first_arms, strict=True):
        second = "sshai" if first == "baseline" else "baseline"
        pairs.append((case_id, [first, second]))
    rng.shuffle(pairs)
    slots: list[dict[str, Any]] = []
    for pair_number, (case_id, arms) in enumerate(pairs, 1):
        for arm in arms:
            slots.append({"slot": len(slots) + 1, "pair_id": f"{case_id}-r1",
                          "pair_order": pair_number, "case_id": case_id,
                          "replicate": 1, "arm": arm})
    return slots


def _fixture_inventory(bundle_root: Path, *, cases: Sequence[str] = CASES) -> tuple[dict[str, Any], dict[str, dict[str, bytes]]]:
    if (not isinstance(cases, (tuple, list)) or not 1 <= len(cases) <= 6
            or any(not isinstance(case, str) or case not in {f"M{number:02}" for number in range(1, 7)} for case in cases)
            or len(set(cases)) != len(cases)):
        raise PilotInputError("fixture selection requires one to six unique known local cases")
    manifest = _json_file(bundle_root / "manifest.json", "fixture manifest")
    if manifest.get("schema_version") != "issue10-synthetic-v3-draft-2":
        raise PilotInputError("unsupported fixture bundle schema")
    case_inventory = manifest.get("cases")
    entries = manifest.get("files")
    if not isinstance(case_inventory, list) or not isinstance(entries, list):
        raise PilotInputError("fixture manifest inventory is malformed")
    listed: dict[str, tuple[int, str]] = {}
    for item in entries:
        _exact_object(item, {"path", "bytes", "sha256"}, "fixture file entry")
        name = _safe_relative(item["path"], "fixture path")
        if name in listed or type(item["bytes"]) is not int or item["bytes"] < 0:
            raise PilotInputError("duplicate or invalid fixture file entry")
        listed[name] = (item["bytes"], _hex_digest(item["sha256"], "fixture file digest"))
    selected: dict[str, dict[str, bytes]] = {}
    for case_id in cases:
        case_rows = [row for row in case_inventory if isinstance(row, dict) and row.get("case_id") == case_id]
        if len(case_rows) != 1 or not isinstance(case_rows[0].get("files"), list):
            raise PilotInputError(f"fixture manifest has no unique {case_id} inventory")
        selected[case_id] = {}
        names = []
        for row in case_rows[0]["files"]:
            if not isinstance(row, dict) or not isinstance(row.get("file"), str):
                raise PilotInputError(f"malformed {case_id} case inventory")
            names.append(_safe_relative(row["file"], f"{case_id} input path"))
        for relative in [*names, f"../prompt:{case_id}"]:
            source_name = (f"inputs/{case_id}/{relative}" if not relative.startswith("../prompt:")
                           else f"prompts/{case_id}.md")
            if source_name not in listed:
                raise PilotInputError(f"fixture manifest omits {source_name}")
            source = legacy._physical(bundle_root / source_name)
            data = legacy._read_bounded(source, capture.MAX_CAPTURE_BYTES)
            expected_bytes, expected_sha = listed[source_name]
            if len(data) != expected_bytes or _sha(data) != expected_sha:
                raise PilotInputError(f"fixture bytes differ from manifest: {source_name}")
            selected[case_id]["__prompt__" if relative.startswith("../prompt:") else relative] = data
    return manifest, selected


def _branch_guidance(arm: str, sshai_path: str) -> str:
    common = (
        "This is a bounded diagnostic pilot. Use only the supplied fixture directory and writable "
        "scratch area. Do not inspect unrelated host data, use network access, spawn subagents, "
        "change fixtures, or execute a proposed remedy."
    )
    if arm == "baseline":
        branch = "Use the available local command tool for fixture diagnostics. Do not invoke sshai."
    else:
        branch = (
            f"Route every new fixture diagnostic through exactly {sshai_path} local --shell bash. "
            "Ordinary local processing may inspect an already-saved sshai artifact in SSHAI_ROOT; "
            "do not use SSH or bypass sshai to read source fixtures."
        )
    return common + "\n" + branch + "\n"


def _render_prompt(source: bytes, fixture: Path, scratch: Path, arm: str, sshai_path: str) -> bytes:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PilotInputError("fixture prompt is not UTF-8") from exc
    if text.count("{fixture_root}") != 1:
        raise PilotInputError("fixture prompt must contain exactly one fixture_root placeholder")
    rendered = text.replace("{fixture_root}", str(fixture))
    rendered += "\nAuthorized writable scratch directory: " + str(scratch) + "\n"
    rendered += _branch_guidance(arm, sshai_path)
    return rendered.encode("utf-8")


def prepare(root: Path, fixture_bundle: Path, codex_path: Path, sshai_path: Path,
            config_path: Path, model_catalog_path: Path, tool_overrides_path: Path,
            auth_path: Path, assessment_instructions_path: Path,
            assessment_rubric_path: Path, *, seed: int = DEFAULT_SEED,
            _continuation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a new immutable private four-slot plan without launching a model."""
    root = legacy._physical(Path(root), must_exist=False)
    bundle_root = legacy._physical(Path(fixture_bundle))
    if not bundle_root.is_dir():
        raise PilotInputError("fixture bundle must be a directory")
    codex_path = _private_regular(codex_path, "Codex binary", executable=True)
    if codex_path != EXPECTED_CODEX_PATH:
        raise PilotInputError("Codex path is not the selected native installed binary")
    sshai_path = _private_regular(sshai_path, "sshai binary", executable=True)
    auth_path = _private_regular(auth_path, "Codex auth source")
    if stat.S_IMODE(auth_path.stat().st_mode) != 0o600:
        raise PilotInputError("Codex auth source must have mode 0600")
    private_parent = auth_path.parent.parent
    if root.parent != private_parent:
        raise PilotInputError("pilot root must be a new direct child of the dedicated private parent")
    config_source = _private_regular(config_path, "pilot config")
    config = _validate_config(_json_file(config_source, "pilot config"))
    catalog_source = _private_regular(model_catalog_path, "model catalog")
    catalog_bytes = legacy._read_bounded(catalog_source, capture.MAX_CAPTURE_BYTES)
    catalog = _validate_model_catalog(_json_file(catalog_source, "model catalog"), config["model"]["id"])
    controls_source = _private_regular(tool_overrides_path, "tool overrides")
    controls_bytes = legacy._read_bounded(controls_source, capture.MAX_CAPTURE_BYTES)
    try:
        controls = json.loads(controls_bytes.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PilotInputError(f"invalid tool overrides: {exc}") from exc
    if controls != REQUIRED_CODEX_OVERRIDES or controls != config["codex_exec"]["config_overrides"]:
        raise PilotInputError("tool overrides file differs from the qualified canonical controls")
    assessment_inputs = {}
    for label, source_path, expected in (
            ("instructions", assessment_instructions_path,
             config["assessment"]["instructions_sha256"]),
            ("rubric", assessment_rubric_path, config["assessment"]["rubric_sha256"])):
        source = _private_regular(source_path, f"assessment {label}")
        data = legacy._read_bounded(source, capture.MAX_CAPTURE_BYTES)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PilotInputError(f"assessment {label} is not UTF-8") from exc
        if not text.strip() or _sha(data) != expected:
            raise PilotInputError(f"assessment {label} is empty or differs from its config digest")
        assessment_inputs[label] = data
    fixture_manifest, selected = _fixture_inventory(bundle_root)
    probes = (_BINARY_PROBE or _probe_binaries)(codex_path, sshai_path, config)
    slots = schedule(seed)

    manifests: dict[str, Any] = {}
    for slot in slots:
        case_id, arm = slot["case_id"], slot["arm"]
        fixture_path = root / "slots" / f"{slot['slot']:03}" / "fixture"
        scratch_path = root / "slots" / f"{slot['slot']:03}" / "scratch"
        prompt = _render_prompt(selected[case_id]["__prompt__"], fixture_path, scratch_path,
                                arm, str(sshai_path))
        manifests[str(slot["slot"])] = {
            "fixture_files": {name: {"bytes": len(data), "sha256": _sha(data)}
                              for name, data in sorted(selected[case_id].items())
                              if name != "__prompt__"},
            "source_prompt_sha256": _sha(selected[case_id]["__prompt__"]),
            "rendered_prompt_sha256": _sha(prompt),
        }
    source_pins = {
        f"scripts/{name}": legacy._file_digest(REPO / "scripts" / name)
        for name in ("benchmark_issue10_local_pilot.py", "benchmark_issue10_v3_capture.py",
                     "benchmark_issue10_v3_collector.py", "benchmark_issue10_sandbox.py",
                     "benchmark_issue10.py")
    }
    source_pins["docs/benchmarks/issue10-methodology-amendment.md"] = legacy._file_digest(AMENDMENT)
    manifest: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA, "phase": PHASE, "schedule_seed": seed,
        "random_generator": "Python random.Random / MT19937; stored schedule is authoritative",
        "slots": slots, "session_count": SESSION_COUNT,
        "original_diagnostic_session_ceiling": ORIGINAL_DIAGNOSTIC_SESSION_CEILING,
        "assessor_context_ceiling": ASSESSOR_CONTEXT_CEILING,
        "no_retry_no_resume": True, "local_only": True,
        "fixture_bundle": {"path": str(bundle_root),
                           "manifest_sha256": legacy._file_digest(bundle_root / "manifest.json"),
                           "schema_version": fixture_manifest["schema_version"]},
        "slot_material": manifests,
        "codex": {"path": str(codex_path), **config["codex"],
                  "version_output_sha256": probes["codex_version_output_sha256"]},
        "sshai": {"path": str(sshai_path), **config["sshai"]},
        "auth": {"path": str(auth_path), "required_mode": "0600",
                 "content_read_or_retained": False},
        "model_catalog": {"source_path": str(catalog_source), "sha256": _sha(catalog_bytes),
                          "bytes": len(catalog_bytes), "selected_slug": catalog["models"][0]["slug"],
                          "backend_availability": "unverified"},
        "tool_overrides": {"source_path": str(controls_source), "sha256": _sha(controls_bytes),
                           "bytes": len(controls_bytes), "exact_canonical_controls": True},
        "config": config, "config_sha256": _sha(_encoded(config)),
        "assessment_inputs": {
            label: {"bytes": len(data), "sha256": _sha(data),
                    "retained": f"prepared/assessment/{label}.md"}
            for label, data in assessment_inputs.items()
        },
        "sources": source_pins,
        "qualification": {"access": "required-per-slot", "tool_audit": "bounded-records",
                          "answer_finality": "version-bounded-evidence-only",
                          "auditor_grade": "unknown", "independent_model_assessment": "unknown"},
        "experimental_savings_claim_eligible": False,
    }
    if _continuation is not None:
        predecessor = _bound_predecessor(_continuation, root, require_owner=False)
        _same_continuation_plan(manifest, predecessor, root, Path(_continuation["predecessor_root"]))
        if root.exists() or root.is_symlink():
            raise PilotInputError("continuation requires a fresh root")
        owner = _continuation_owner(_continuation, root)
        try:
            legacy._write_new(Path(_continuation["predecessor_root"]) / "continuation-owner.json",
                              _pretty(owner))
        except ValueError as exc:
            raise PilotInputError("predecessor continuation is already claimed") from exc
        _continuation["owner_sha256"] = _sha(_pretty(owner))
        manifest["continuation"] = _continuation
    manifest["digest"] = _digest_object(manifest)
    legacy._new_dir(root)
    legacy._mkdir_private(root / "slots")
    legacy._mkdir_private(root / "prepared")
    for case_id in CASES:
        for name, data in sorted(selected[case_id].items()):
            relative = (f"prompts/{case_id}.md" if name == "__prompt__"
                        else f"inputs/{case_id}/{name}")
            legacy._write_new(root / "prepared" / relative, data, mode=0o400)
    for label, data in assessment_inputs.items():
        legacy._write_new(root / "prepared" / "assessment" / f"{label}.md", data, mode=0o400)
    for directory in sorted(
            [path for path in (root / "prepared").rglob("*") if path.is_dir()],
            key=lambda path: len(path.parts), reverse=True):
        directory.chmod(0o500)
    (root / "prepared").chmod(0o500)
    legacy._write_new(root / "config.json", _pretty(config))
    legacy._write_new(root / "model-catalog.json", catalog_bytes, mode=0o400)
    legacy._write_new(root / "tool-overrides.json", controls_bytes, mode=0o400)
    legacy._write_new(root / "manifest.json", _pretty(manifest))
    approval_template = {
        "schema": APPROVAL_SCHEMA, "manifest_digest": manifest["digest"], "phase": PHASE,
        "session_count": SESSION_COUNT,
        "original_diagnostic_session_ceiling": ORIGINAL_DIAGNOSTIC_SESSION_CEILING,
        "config_sha256": manifest["config_sha256"], "approved": False,
        "approved_at_utc": "", "authorization_note": "",
    }
    legacy._write_new(root / "approval-template.json", _pretty(approval_template))
    return manifest


def load_manifest(root: Path, *, _source_root: Path | None = None) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = _json_file(root / "manifest.json", "pilot manifest")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("digest") != _digest_object(manifest):
        raise PilotInputError("pilot manifest schema or digest mismatch")
    if (manifest.get("phase") != PHASE or manifest.get("slots") != schedule(manifest.get("schedule_seed"))
            or manifest.get("session_count") != SESSION_COUNT
            or manifest.get("original_diagnostic_session_ceiling") != ORIGINAL_DIAGNOSTIC_SESSION_CEILING
            or manifest.get("assessor_context_ceiling") != ASSESSOR_CONTEXT_CEILING
            or manifest.get("no_retry_no_resume") is not True
            or manifest.get("experimental_savings_claim_eligible") is not False):
        raise PilotInputError("pilot manifest policy or schedule changed")
    config = _validate_config(manifest.get("config"))
    if manifest.get("config_sha256") != _sha(_encoded(config)):
        raise PilotInputError("pilot config digest mismatch")
    if _json_file(root / "config.json", "retained pilot config") != config:
        raise PilotInputError("retained pilot config changed")
    source_root = REPO if _source_root is None else legacy._physical(_source_root)
    if set(manifest.get("sources", {})) != SOURCE_PATHS:
        raise PilotInputError("source pin inventory changed")
    for name, expected in manifest["sources"].items():
        _hex_digest(expected, "source pin")
        if legacy._file_digest(legacy._physical(source_root / name)) != expected:
            raise PilotInputError(f"controller source changed since preparation: {name}")
    if Path(manifest["codex"]["path"]) != EXPECTED_CODEX_PATH:
        raise PilotInputError("frozen Codex path changed from the selected native binary")
    for label in ("codex", "sshai"):
        path = _private_regular(Path(manifest[label]["path"]), f"frozen {label}", executable=True)
        if legacy._file_digest(path) != manifest[label]["sha256"]:
            raise PilotInputError(f"frozen {label} binary changed")
    auth = _private_regular(Path(manifest["auth"]["path"]), "frozen auth source")
    if (stat.S_IMODE(auth.stat().st_mode) != 0o600
            or manifest["auth"] != {"path": str(auth), "required_mode": "0600",
                                    "content_read_or_retained": False}):
        raise PilotInputError("frozen auth source is not the dedicated private regular file")
    catalog_data = legacy._read_bounded(root / "model-catalog.json", capture.MAX_CAPTURE_BYTES)
    if (len(catalog_data) != manifest["model_catalog"]["bytes"]
            or _sha(catalog_data) != manifest["model_catalog"]["sha256"]):
        raise PilotInputError("retained model catalog changed")
    _validate_model_catalog(_json_file(root / "model-catalog.json", "retained model catalog"),
                            config["model"]["id"])
    for label in ("instructions", "rubric"):
        metadata = manifest.get("assessment_inputs", {}).get(label)
        if (not isinstance(metadata, dict)
                or set(metadata) != {"bytes", "sha256", "retained"}
                or metadata["retained"] != f"prepared/assessment/{label}.md"):
            raise PilotInputError("retained assessment input metadata is malformed")
        data = legacy._read_bounded(root / metadata["retained"], capture.MAX_CAPTURE_BYTES)
        if len(data) != metadata["bytes"] or _sha(data) != metadata["sha256"]:
            raise PilotInputError(f"retained assessment {label} changed")
        if metadata["sha256"] != config["assessment"][f"{label}_sha256"]:
            raise PilotInputError(f"assessment {label} digest differs from config")
    controls_data = legacy._read_bounded(root / "tool-overrides.json", capture.MAX_CAPTURE_BYTES)
    if (len(controls_data) != manifest["tool_overrides"]["bytes"]
            or _sha(controls_data) != manifest["tool_overrides"]["sha256"]):
        raise PilotInputError("retained tool overrides changed")
    try:
        controls = json.loads(controls_data.decode("utf-8"), object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise PilotInputError(f"invalid retained tool overrides: {exc}") from exc
    if controls != REQUIRED_CODEX_OVERRIDES:
        raise PilotInputError("retained tool overrides are not the qualified canonical controls")
    bundle = legacy._physical(Path(manifest["fixture_bundle"]["path"]))
    if legacy._file_digest(bundle / "manifest.json") != manifest["fixture_bundle"]["manifest_sha256"]:
        raise PilotInputError("fixture source manifest changed")
    for slot in manifest["slots"]:
        material = manifest["slot_material"][str(slot["slot"])]
        prompt = root / "prepared" / "prompts" / f"{slot['case_id']}.md"
        if legacy._file_digest(prompt) != material["source_prompt_sha256"]:
            raise PilotInputError("prepared prompt changed")
        for name, expected in material["fixture_files"].items():
            source = root / "prepared" / "inputs" / slot["case_id"] / name
            if (legacy._file_digest(source) != expected["sha256"]
                    or source.stat().st_size != expected["bytes"]):
                raise PilotInputError("prepared fixture changed")
    if "continuation" in manifest:
        if not isinstance(manifest["continuation"], dict):
            raise PilotInputError("continuation receipt must be an object")
        if _source_root is not None and (
                manifest["continuation"].get("schema") != CONTINUATION_SCHEMA
                or manifest["sources"]["scripts/benchmark_issue10_local_pilot.py"] != AUTH_CONTROLLER_SHA256):
            raise PilotInputError("only the exact first continuation source snapshot may be inherited")
        predecessor = _bound_predecessor(manifest["continuation"], root)
        _same_continuation_plan(manifest, predecessor, root, Path(manifest["continuation"]["predecessor_root"]))
        for number in manifest["continuation"]["inherited_slots"]:
            path = root / "slots" / f"{number:03}"
            if path.exists() or path.is_symlink():
                raise PilotInputError("inherited consumed slot cannot exist in the prospective root")
    return manifest


def _preprocess_prefix(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Only the original slot-1 arg0 inventory failure; not general recovery."""
    if "continuation" in manifest:
        raise PilotInputError("nested continuation predecessors are unsupported")
    if manifest["sources"]["scripts/benchmark_issue10_local_pilot.py"] != PREPROCESS_CONTROLLER_SHA256:
        raise PilotInputError("predecessor source does not prove the reviewed guard-before-process contract")
    load_readiness(root, manifest)
    _fixture_inventory(Path(manifest["fixture_bundle"]["path"]))
    if {path.name for path in (root / "slots").iterdir()} != {"001"}:
        raise PilotInputError("only consumed slot 1 and wholly absent slots 2..4 are eligible")
    base = legacy._physical(root / "slots" / "001")
    reservation = _json_file(base / "reservation.json", "predecessor reservation")
    if (set(reservation) != {"manifest_digest", "slot", "approval_sha256", "one_shot"}
            or reservation["manifest_digest"] != manifest["digest"]
            or reservation["slot"] != manifest["slots"][0] or reservation["one_shot"] is not True):
        raise PilotInputError("predecessor reservation is malformed")
    _hex_digest(reservation["approval_sha256"], "predecessor approval hash")
    expected = _failure_result(manifest["slots"][0], "model-attempt-requested")
    expected["launch"] = "attempted-or-unknown"
    if _json_file(base / "result.json", "predecessor failure") != expected:
        raise PilotInputError("predecessor is not the retained blocked pre-process outcome")
    home = base / "codex-home"
    runtime = _native_runtime_inventory(home)
    if not runtime["present"] or not runtime["entries"]:
        raise PilotInputError("predecessor has no recognized arg0 inventory-guard trigger")
    auth = home / "auth.json"
    if not auth.is_symlink() or auth.readlink() != Path(manifest["auth"]["path"]):
        raise PilotInputError("predecessor provisioned auth link changed")
    if legacy._file_digest(home / "model-catalog.json") != manifest["model_catalog"]["sha256"]:
        raise PilotInputError("predecessor provisioned catalog changed")
    receipt = _validated_access_receipt(_json_file(base / "evidence/access-receipt.json", "predecessor access"))
    if legacy._file_digest(base / "evidence/prompt.txt") != manifest["slot_material"]["1"]["rendered_prompt_sha256"]:
        raise PilotInputError("predecessor rendered prompt changed")
    inventory = _provisioned_census(base, manifest, 1, runtime)
    return {"slot_inventory": inventory, "access_receipt_digest": receipt["digest"]}


def _provisioned_census(base: Path, manifest: dict[str, Any], number: int,
                        runtime: dict[str, Any], *, extra_files: set[str] | None = None,
                        native_metadata: set[str] | None = None) -> dict[str, Any]:
    """Exact bounded typed census; only the auth proof supplies captured-file additions."""
    allowed = {"reservation.json", "result.json", "fixture", "scratch", "evidence", "home",
               "codex-home", "scratch/sshai-root", "scratch/tmp", "home/.config", "home/.cache",
               "home/.local-share", "codex-home/auth.json", "codex-home/model-catalog.json",
               "evidence/access-receipt.json", "evidence/prompt.txt"}
    directory_paths = {"fixture", "scratch", "evidence", "home", "codex-home", "scratch/sshai-root",
                       "scratch/tmp", "home/.config", "home/.cache", "home/.local-share"}
    if runtime["present"]:
        allowed.add("codex-home/tmp")
        directory_paths.add("codex-home/tmp")
    material = manifest["slot_material"][str(number)]["fixture_files"]
    for name, metadata in material.items():
        relative = Path("fixture") / _safe_relative(name, "predecessor fixture")
        allowed.add(str(relative))
        parents = {str(parent) for parent in relative.parents if str(parent) != "."}
        allowed.update(parents)
        directory_paths.update(parents)
        if legacy._file_digest(base / relative) != metadata["sha256"]:
            raise PilotInputError("predecessor fixture changed")
    aliases = {"codex-home/" + row["path"] for row in runtime["entries"]
               if row["kind"] == "native-alias"}
    allowed.update("codex-home/" + row["path"] for row in runtime["entries"])
    directory_paths.update("codex-home/" + row["path"] for row in runtime["entries"]
                           if row["kind"] == "directory")
    for relative in (extra_files or set()) | (native_metadata or set()):
        relative = _safe_relative(relative, "captured proof path")
        allowed.add(relative)
        parents = {str(parent) for parent in Path(relative).parents if str(parent) != "."}
        allowed.update(parents)
        directory_paths.update(parents)
    inventory = {}
    for current, directories, files in os.walk(base, followlinks=False, onerror=_incomplete_scan):
        for name in sorted([*directories, *files]):
            path = Path(current) / name
            relative = str(path.relative_to(base))
            if len(inventory) >= MAX_DISCOVERY_ENTRIES or relative not in allowed:
                raise PilotInputError("predecessor contains attempt or unsupported evidence")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) and relative in aliases | {"codex-home/auth.json"}:
                inventory[relative] = {"link": str(path.readlink())}
            elif stat.S_ISDIR(mode) and relative in directory_paths:
                inventory[relative] = {"directory": True}
            elif stat.S_ISREG(mode) and relative not in directory_paths | aliases | {"codex-home/auth.json"}:
                if relative in (native_metadata or set()):
                    inventory[relative] = {"native_metadata": {"bytes": path.lstat().st_size,
                                                               "mode": stat.S_IMODE(mode)}}
                else:
                    inventory[relative] = {"sha256": legacy._file_digest(path)}
            else:
                raise PilotInputError("predecessor contains unsupported evidence type")
    if set(inventory) != allowed:
        raise PilotInputError("predecessor pre-process inventory is incomplete")
    return inventory


def _continuation_owner(binding: dict[str, Any], root: Path) -> dict[str, Any]:
    identity = dict(binding)
    identity.pop("owner_sha256", None)
    return {"schema": binding["schema"], "continuation_root": str(root),
            "binding_sha256": _sha(_encoded(identity)), "executable_slots": binding["executable_slots"],
            "one_shot": True, "launch_approval": False}


def _bound_predecessor(binding: dict[str, Any], root: Path, *, require_owner: bool = True) -> dict[str, Any]:
    auth = isinstance(binding, dict) and binding.get("schema") == AUTH_CONTINUATION_SCHEMA
    fields = {"schema", "predecessor_root", "source_snapshot", "predecessor_digest",
              "original_sources", "original_hashes", "prefix_evidence", "inherited_slots",
              "executable_slots", "reason", "authorization_note"}
    if require_owner:
        fields.add("owner_sha256")
    if auth:
        fields.add("auth_repair_note")
    _exact_object(binding, fields, "continuation receipt")
    text_fields = ("reason", "authorization_note", "auth_repair_note") if auth else ("reason", "authorization_note")
    if (binding["schema"] not in {CONTINUATION_SCHEMA, AUTH_CONTINUATION_SCHEMA}
            or binding["inherited_slots"] != ([1, 2] if auth else [1])
            or binding["executable_slots"] != ([3, 4] if auth else [2, 3, 4])
            or any(not isinstance(binding[key], str) or not binding[key].strip() for key in text_fields)):
        raise PilotInputError("unsupported continuation receipt")
    old_root = legacy._physical(Path(binding["predecessor_root"]))
    if old_root == root or old_root.parent != root.parent:
        raise PilotInputError("continuation must be a fresh sibling of its predecessor")
    old = load_manifest(old_root, _source_root=Path(binding["source_snapshot"]))
    if old["digest"] != binding["predecessor_digest"] or old["sources"] != binding["original_sources"]:
        raise PilotInputError("bound predecessor manifest changed")
    hashes = _predecessor_hashes(old_root, 2 if auth else 1)
    proof = _auth_prefix(old_root, old) if auth else _preprocess_prefix(old_root, old)
    if hashes != binding["original_hashes"] or proof != binding["prefix_evidence"]:
        raise PilotInputError("bound predecessor evidence changed")
    if require_owner:
        owner_path = old_root / "continuation-owner.json"
        if (legacy._file_digest(legacy._physical(owner_path)) != binding["owner_sha256"]
                or _json_file(owner_path, "continuation ownership") != _continuation_owner(binding, root)):
            raise PilotInputError("continuation ownership changed or belongs to another root")
    return old


def _same_continuation_plan(manifest: dict[str, Any], old: dict[str, Any],
                            root: Path, old_root: Path) -> None:
    for key in ("phase", "schedule_seed", "random_generator", "slots", "session_count",
                "original_diagnostic_session_ceiling", "assessor_context_ceiling", "no_retry_no_resume",
                "local_only", "fixture_bundle", "codex", "sshai", "auth", "config", "config_sha256",
                "assessment_inputs", "qualification", "experimental_savings_claim_eligible"):
        if manifest[key] != old[key]:
            raise PilotInputError(f"continuation changed original plan: {key}")
    for key in ("model_catalog", "tool_overrides"):
        if {k: v for k, v in manifest[key].items() if k != "source_path"} != {
                k: v for k, v in old[key].items() if k != "source_path"}:
            raise PilotInputError(f"continuation changed original plan: {key}")
    for number in ("1", "2", "3", "4"):
        for key in ("fixture_files", "source_prompt_sha256"):
            if manifest["slot_material"][number][key] != old["slot_material"][number][key]:
                raise PilotInputError("continuation changed fixture or prompt")
    # Re-render at BOTH roots. Equal source-prompt hashes alone would not catch
    # changed branch guidance. Only the fixture/scratch path substitutions differ.
    for slot in old["slots"]:
        number = str(slot["slot"])
        source = legacy._read_bounded(old_root / "prepared/prompts" / f"{slot['case_id']}.md",
                                      collector.MAX_PROMPT_BYTES)
        for plan, plan_root in ((old, old_root), (manifest, root)):
            base = plan_root / "slots" / f"{slot['slot']:03}"
            rendered = _render_prompt(source, base / "fixture", base / "scratch",
                                      slot["arm"], plan["sshai"]["path"])
            if _sha(rendered) != plan["slot_material"][number]["rendered_prompt_sha256"]:
                raise PilotInputError("continuation changed frozen rendered instructions")


def _startup_auth_records(events: bytes, rollout: bytes, manifest: dict[str, Any]) -> str:
    """A bounded raw-record recipe, not legacy-parser validity or OS attestation."""
    cli = capture.parse_jsonl(events, "startup_auth_cli")
    history = capture.parse_jsonl(rollout, "startup_auth_rollout")
    if not cli["complete"] or not history["complete"] or len(cli["records"]) != 4 or len(history["records"]) != 9:
        raise PilotInputError("startup auth requires complete exact four-event/nine-record captures")
    first = _exact_object(cli["records"][0], {"type", "thread_id"}, "startup thread")
    thread = first["thread_id"]
    if (first["type"] != "thread.started" or not isinstance(thread, str) or not thread
            or cli["records"][1:] != [{"type": "turn.started"},
                                      {"type": "error", "message": STARTUP_AUTH_ERROR},
                                      {"type": "turn.failed", "error": {"message": STARTUP_AUTH_ERROR}}]):
        raise PilotInputError("CLI is not the exact revoked-refresh-token startup failure")
    kinds = ["session_meta", "event_msg", "response_item", "response_item", "world_state",
             "turn_context", "response_item", "event_msg", "event_msg"]
    payloads = []
    for index, (record, kind) in enumerate(zip(history["records"], kinds, strict=True)):
        _exact_object(record, {"timestamp", "ordinal", "type", "payload"}, "startup rollout record")
        if (record["type"] != kind or type(record["ordinal"]) is not int or record["ordinal"] != index
                or not isinstance(record["timestamp"], str) or not record["timestamp"]
                or not isinstance(record["payload"], dict)):
            raise PilotInputError("startup rollout record kind, order or shape is unsupported")
        payloads.append(record["payload"])
    meta, started, developer, user, world, context, user2, item, complete = payloads
    if (meta.get("id") != thread or meta.get("session_id") != thread
            or meta.get("cli_version") != "0.151.0" or meta.get("history_mode") != "paginated"
            or meta.get("source") != "exec" or meta.get("model_provider") != "openai"):
        raise PilotInputError("startup session metadata differs from the pinned CLI contract")
    _exact_object(started, {"type", "turn_id", "started_at", "model_context_window", "collaboration_mode_kind"}, "startup task")
    turn = started["turn_id"]
    if (started["type"] != "task_started" or not isinstance(turn, str) or not turn
            or type(started["started_at"]) is not int or type(started["model_context_window"]) is not int
            or started["collaboration_mode_kind"] != "default"):
        raise PilotInputError("startup task identity or shape is unsupported")
    for message, role in ((developer, "developer"), (user, "user"), (user2, "user")):
        _exact_object(message, {"type", "id", "role", "content", "internal_chat_message_metadata_passthrough"}, "startup input message")
        content, metadata = message["content"], message["internal_chat_message_metadata_passthrough"]
        if (message["type"] != "message" or message["role"] != role
                or not isinstance(message["id"], str) or not message["id"]
                or not isinstance(metadata, dict) or metadata.get("turn_id") != turn
                or not isinstance(content, list) or not content):
            raise PilotInputError("startup input message is not typed non-assistant text")
        for text in content:
            _exact_object(text, {"type", "text"}, "startup message content")
            if text["type"] != "input_text" or not isinstance(text["text"], str):
                raise PilotInputError("startup input contains unsupported content")
    _exact_object(world, {"full", "state"}, "startup world state")
    if world["full"] is not True or not isinstance(world["state"], dict):
        raise PilotInputError("startup world state shape is unsupported")
    # WorldState state values and native context permissions/instructions are
    # metadata, not tool-call audit units; do not interpret embedded strings.
    if (context.get("turn_id") != turn or context.get("model") != manifest["config"]["model"]["id"]
            or context.get("effort") != manifest["config"]["model"]["reasoning_effort"]):
        raise PilotInputError("startup turn context differs from frozen model settings")
    _exact_object(item, {"type", "thread_id", "turn_id", "item", "started_at_ms", "completed_at_ms"}, "startup input lifecycle")
    input_item = _exact_object(item["item"], {"type", "id", "content"}, "startup user item")
    if (item["type"] != "item_completed" or item["thread_id"] != thread or item["turn_id"] != turn
            or input_item["type"] != "UserMessage" or not isinstance(input_item["id"], str)
            or not input_item["id"] or not isinstance(input_item["content"], list) or not input_item["content"]
            or any(type(item[key]) is not int for key in ("started_at_ms", "completed_at_ms"))):
        raise PilotInputError("startup lifecycle is not the input UserMessage")
    for text in input_item["content"]:
        _exact_object(text, {"type", "text", "text_elements"}, "startup user content")
        if text["type"] != "text" or not isinstance(text["text"], str) or text["text_elements"] != []:
            raise PilotInputError("startup UserMessage content is unsupported")
    _exact_object(complete, {"type", "turn_id", "last_agent_message", "error", "started_at", "completed_at", "duration_ms"}, "startup auth completion")
    if (complete["type"] != "task_complete" or complete["turn_id"] != turn
            or complete["last_agent_message"] is not None
            or complete["error"] != {"message": STARTUP_AUTH_ERROR, "codex_error_info": "unauthorized"}
            or any(type(complete[key]) is not int for key in ("started_at", "completed_at", "duration_ms"))):
        raise PilotInputError("startup completion is not the same unauthorized failure without an answer")
    return thread


def _model_argv(manifest: dict[str, Any], base: Path, access: dict[str, Any]) -> list[str]:
    config = manifest["config"]
    argv = [manifest["codex"]["path"], "exec", "--model", config["model"]["id"],
            "-c", f'model_reasoning_effort={json.dumps(config["model"]["reasoning_effort"])}',
            "-c", f'model_catalog_json={json.dumps(str(base / "codex-home/model-catalog.json"))}']
    for override in [*config["codex_exec"]["config_overrides"], *access["effective_config_overrides"]]:
        argv.extend(["-c", override])
    argv.extend(["--ignore-user-config", "--ignore-rules", "--json", "--color", "never",
                 "--skip-git-repo-check", "--output-last-message", str(base / "evidence/last-message.txt"),
                 "-C", str(base / "scratch"), "-"])
    return argv


def _recorded_initial_runtime(native: dict[str, Any]) -> None:
    """Validate the retained six-row receipt without requiring its ephemeral name to survive."""
    if (native["classification"] != "pinned-arg0-layout" or type(native["present"]) is not bool
            or not isinstance(native["entries"], list)):
        raise PilotInputError("startup initial runtime metadata is unsupported")
    if not native["present"]:
        if native["entries"] != []:
            raise PilotInputError("absent startup runtime must have an empty receipt")
        return
    rows = native["entries"]
    if len(rows) != 6:
        raise PilotInputError("startup initial runtime must have exactly six pinned-layout entries")
    for row in rows:
        _exact_object(row, {"path", "kind"}, "startup initial runtime entry")
        if not isinstance(row["path"], str) or not isinstance(row["kind"], str):
            raise PilotInputError("startup initial runtime entry is malformed")
    directories = [row["path"] for row in rows if row["kind"] == "directory" and row["path"] != "tmp/arg0"]
    if len(directories) != 1:
        raise PilotInputError("startup initial runtime must describe one common helper directory")
    helper = directories[0]
    prefix = "tmp/arg0/codex-arg0"
    suffix = helper.removeprefix(prefix)
    if (not helper.startswith(prefix) or len(suffix) != 6
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" for character in suffix)):
        raise PilotInputError("startup initial runtime helper name is unsupported")
    expected = [{"path": "tmp/arg0", "kind": "directory"}, {"path": helper, "kind": "directory"},
                {"path": helper + "/.lock", "kind": "lock"}]
    expected.extend({"path": helper + "/" + name, "kind": "native-alias"}
                    for name in ("apply_patch", "applypatch", "codex-execve-wrapper"))
    if sorted(rows, key=lambda row: row["path"]) != sorted(expected, key=lambda row: row["path"]):
        raise PilotInputError("startup initial runtime paths or kinds differ from the pinned layout")


def _auth_prefix(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    binding = manifest.get("continuation", {})
    if (binding.get("schema") != CONTINUATION_SCHEMA
            or manifest["sources"]["scripts/benchmark_issue10_local_pilot.py"] != AUTH_CONTROLLER_SHA256):
        raise PilotInputError("startup auth exception requires the exact 32ddcc7 first continuation")
    load_readiness(root, manifest)
    _fixture_inventory(Path(manifest["fixture_bundle"]["path"]))
    if {path.name for path in (root / "slots").iterdir()} != {"002"}:
        raise PilotInputError("startup auth continuation requires only consumed slot 2 and absent slots 3..4")
    base = legacy._physical(root / "slots/002")
    reservation = _json_file(base / "reservation.json", "startup auth reservation")
    _exact_object(reservation, {"manifest_digest", "slot", "approval_sha256", "one_shot"}, "startup reservation")
    if (reservation["manifest_digest"] != manifest["digest"] or reservation["slot"] != manifest["slots"][1]
            or reservation["one_shot"] is not True):
        raise PilotInputError("startup reservation differs from the first continuation")
    _hex_digest(reservation["approval_sha256"], "startup approval digest")
    evidence, attempt, home = base / "evidence", base / "evidence/attempt", base / "codex-home"
    access = _validated_access_receipt(_json_file(evidence / "access-receipt.json", "startup access"))
    prompt = legacy._read_bounded(evidence / "prompt.txt", collector.MAX_PROMPT_BYTES)
    if _sha(prompt) != manifest["slot_material"]["2"]["rendered_prompt_sha256"]:
        raise PilotInputError("startup rendered prompt changed")
    if (not (home / "auth.json").is_symlink()
            or (home / "auth.json").readlink() != Path(manifest["auth"]["path"])
            or legacy._file_digest(home / "model-catalog.json") != manifest["model_catalog"]["sha256"]):
        raise PilotInputError("startup provisioning changed")
    request = _json_file(attempt / "attempt.json", "startup request")
    argv = _model_argv(manifest, base, access)
    expected_request = collector._request_receipt(argv, prompt, _environment(base, base / "scratch", manifest["config"]),
                                                 base / "scratch", float(manifest["config"]["limits"]["timeout_seconds"]), [],
                                                 evidence / "last-message.txt")
    expected_request.update(schema=collector.ASSOCIATED_ATTEMPT_SCHEMA, association={
        "manifest_digest": manifest["digest"], "slot": manifest["slots"][1],
        "approval_sha256": reservation["approval_sha256"], "access_receipt_digest": access["digest"],
        "model": manifest["config"]["model"], "history_mode": manifest["config"]["codex_exec"]["history_mode"],
        "config_sha256": manifest["config_sha256"], "argv_sha256": _sha(_encoded(argv)),
    })
    initial = request.get("rollout_discovery")
    _exact_object(initial, {"root", "initial_inventory", "native_runtime", "entry_bound", "candidate_bound"}, "startup initial discovery")
    if ({k: v for k, v in request.items() if k != "rollout_discovery"} != expected_request
            or initial["root"] != str(home) or initial["entry_bound"] != MAX_DISCOVERY_ENTRIES
            or initial["candidate_bound"] != collector.MAX_ROLLOUT_CANDIDATES
            or initial["initial_inventory"] not in (["auth.json", "model-catalog.json"], ["auth.json", "model-catalog.json", "tmp"])):
        raise PilotInputError("startup request is not the pinned single-slot invocation")
    native = _exact_object(initial["native_runtime"], {"classification", "present", "entries"}, "startup initial runtime")
    _recorded_initial_runtime(native)
    current_native = _native_runtime_inventory(home)
    # Pinned startup can garbage-collect the canary's randomized helper directory
    # and create a new one. Validate both layouts, not ephemeral name persistence.
    if ((native["present"] and not current_native["present"])
            or initial["initial_inventory"] != (["auth.json", "model-catalog.json", "tmp"] if native["present"] else ["auth.json", "model-catalog.json"])):
        raise PilotInputError("startup initial runtime metadata is unsupported")
    events = legacy._read_bounded(attempt / "events.jsonl", capture.MAX_CAPTURE_BYTES)
    rollout = legacy._read_bounded(attempt / "rollout.jsonl", capture.MAX_CAPTURE_BYTES)
    stderr = legacy._read_bounded(attempt / "stderr.txt", capture.MAX_CAPTURE_BYTES)
    thread = _startup_auth_records(events, rollout, manifest)
    process = _json_file(attempt / "process.json", "startup process")
    _exact_object(process, {"schema", "capture_overflow", "duration_seconds", "execution", "exit_code", "interrupted",
                            "pid", "start_error", "stderr_bytes", "stderr_limit_reached", "stdout_bytes",
                            "stdout_limit_reached", "timed_out"}, "startup process")
    if (process["schema"] != collector.PROCESS_SCHEMA or process["execution"] != "failed"
            or type(process["exit_code"]) is not int or process["exit_code"] != 1
            or type(process["pid"]) is not int or process["pid"] <= 0 or process["start_error"] is not None
            or any(process[key] is not False for key in ("capture_overflow", "interrupted", "timed_out", "stdout_limit_reached", "stderr_limit_reached"))
            or type(process["duration_seconds"]) not in (int, float) or not math.isfinite(process["duration_seconds"])
            or not 0 < process["duration_seconds"] <= manifest["config"]["limits"]["timeout_seconds"]
            or process["stdout_bytes"] != len(events) or process["stderr_bytes"] != len(stderr)
            or max(len(events), len(stderr)) >= collector.MAX_STREAM_BYTES):
        raise PilotInputError("startup process is missing, ambiguous, interrupted or truncated")
    delivery = _json_file(attempt / "delivery.json", "startup delivery")
    _exact_object(delivery, {"schema", "process_execution", "rollout_discovery", "rollout", "answer"}, "startup delivery")
    candidates, discovery = _discover_rollouts(home)
    if delivery["schema"] != collector.DELIVERY_SCHEMA or delivery["process_execution"] != "failed" or delivery["rollout_discovery"] != discovery or len(candidates) != 1:
        raise PilotInputError("startup rollout discovery is missing or ambiguous")
    retained = attempt / "rollout-candidates/001.jsonl"
    if legacy._read_bounded(retained, capture.MAX_CAPTURE_BYTES) != rollout or legacy._read_bounded(candidates[0], capture.MAX_CAPTURE_BYTES) != rollout:
        raise PilotInputError("startup selected rollout differs from retained or discovered source")
    expected_rollout = {"state": "captured", "reason": "matched_cli_thread_identity", "cli_thread_id": thread,
                        "selected_candidate": 1, "bytes": len(rollout), "sha256": _sha(rollout),
                        "candidates": [{"index": 1, "source": str(candidates[0]), "status": "usable", "thread_id": thread,
                                        "bytes": len(rollout), "sha256": _sha(rollout), "retained": "rollout-candidates/001.jsonl",
                                        "parser_issue_codes": [], "identity_match": True}]}
    answer = evidence / "last-message.txt"
    expected_answer = {"state": "lost", "reason": "answer_input_error", "source": str(answer),
                       "error": f"path does not exist: {answer}", "finality": "unknown",
                       "finality_reason": "requires version-bounded completion comparison"}
    if delivery["rollout"] != expected_rollout or delivery["answer"] != expected_answer or answer.exists() or answer.is_symlink():
        raise PilotInputError("startup answer/rollout delivery does not prove the narrow failure")
    result = _json_file(base / "result.json", "startup result")
    report = _json_file(evidence / "capture-report.json", "startup capture report")
    audit = _json_file(evidence / "tool-audit.json", "startup audit")
    for value, sections in ((result, ("usage", "access", "continuation", "completion_evidence", "tool_audit")),
                            (report, ("calls", "usage", "answer"))):
        if any(not isinstance(value.get(section), dict) for section in sections):
            raise PilotInputError("startup retained outcome has a missing or malformed section")
    if not isinstance(result["tool_audit"].get("semantic_routing"), dict):
        raise PilotInputError("startup retained routing uncertainty is malformed")
    if (result.get("schema") != RESULT_SCHEMA or result.get("slot") != manifest["slots"][1]
            or result.get("launch") != "attempted" or result.get("execution") != "failed"
            or result.get("usage", {}).get("totals") is not None or result.get("usage", {}).get("complete") is not False
            or result.get("access", {}).get("status") != "passed"
            or result.get("continuation", {}).get("allowed") is not False
            or result.get("experimental_savings_claim_eligible") is not False
            or result.get("completion_evidence") != {"status": "unavailable", "finality": "unknown", "version_bounded": True}
            or report.get("schema") != capture.SCHEMA or report.get("session_id") != thread
            or report.get("calls", {}).get("inventory") != [] or report.get("calls", {}).get("inventory_entry_count") != 0
            or report.get("usage", {}).get("totals") is not None or report.get("usage", {}).get("complete") is not False
            or any(report.get("usage", {}).get(key) != 0 for key in ("cli_snapshot_count", "rollout_snapshot_count"))
            or report.get("answer", {}).get("cli_agent_messages") != [] or report.get("answer", {}).get("text") is not None
            or audit.get("entries") != [] or audit.get("entry_count") != 0 or audit.get("observation_count") != 0
            or result.get("tool_audit", {}).get("entry_count") != 0 or result.get("tool_audit", {}).get("observation_count") != 0
            or result.get("tool_audit", {}).get("status") != "unqualified"
            or result.get("tool_audit", {}).get("os_execution_attestation") is not False
            or result.get("tool_audit", {}).get("semantic_routing", {}).get("status") != "unknown"):
        raise PilotInputError("startup retained outcome is not a failed answer/usage-free zero-call capture")
    extra_files = {"evidence/attempt/" + name for name in
                   ("attempt.json", "events.jsonl", "stderr.txt", "process.json", "delivery.json", "rollout.jsonl", "rollout-candidates/001.jsonl")}
    extra_files.update({"evidence/capture-report.json", "evidence/completion-evidence.json", "evidence/tool-audit.json",
                        str(candidates[0].relative_to(base))})
    metadata = {"codex-home/" + name for name in AUTH_NATIVE_METADATA
                if (home / name).exists() or (home / name).is_symlink()}
    inventory = _provisioned_census(base, manifest, 2, discovery["native_runtime"],
                                    extra_files=extra_files, native_metadata=metadata)
    return {"slot_inventory": inventory, "slot_inventory_sha256": _sha(_encoded(inventory)),
            "native_metadata_provenance": "evidenced Codex 0.151.0 startup names/types; contents not interpreted",
            "access_receipt_digest": access["digest"],
            "raw_recipe": "exact-revoked-refresh-startup-auth-1", "recorded_tool_entries": 0,
            "os_execution_attestation": False, "backend_availability": "unverified"}


def prepare_auth_continuation(root: Path, predecessor: Path, source_snapshot: Path, *,
                              reason: str, authorization_note: str, auth_repair_note: str) -> dict[str, Any]:
    """Only the exact first continuation's startup-auth failure; original slots 3..4."""
    root = legacy._physical(Path(root), must_exist=False)
    predecessor = legacy._physical(Path(predecessor))
    snapshot = legacy._physical(Path(source_snapshot))
    old = load_manifest(predecessor, _source_root=snapshot)
    proof = _auth_prefix(predecessor, old)
    if (predecessor / "continuation-owner.json").exists() or (predecessor / "continuation-owner.json").is_symlink():
        raise PilotInputError("predecessor continuation is already claimed")
    binding = {"schema": AUTH_CONTINUATION_SCHEMA, "predecessor_root": str(predecessor),
               "source_snapshot": str(snapshot), "predecessor_digest": old["digest"],
               "original_sources": old["sources"], "original_hashes": _predecessor_hashes(predecessor, 2),
               "prefix_evidence": proof, "inherited_slots": [1, 2], "executable_slots": [3, 4],
               "reason": reason, "authorization_note": authorization_note, "auth_repair_note": auth_repair_note}
    return _prepare_bound_continuation(root, predecessor, old, binding)


def _predecessor_hashes(root: Path, number: int) -> dict[str, str]:
    return {name: legacy._file_digest(root / name) for name in
            ("manifest.json", f"slots/{number:03}/reservation.json", f"slots/{number:03}/result.json",
             "readiness/result.json", "readiness/access-M01.json", "readiness/access-M02.json")}


def prepare_continuation(root: Path, predecessor: Path, source_snapshot: Path, *,
                         reason: str, authorization_note: str) -> dict[str, Any]:
    """Prepare remaining slots 2..4, retaining consumed slot 1 without retry."""
    root = legacy._physical(Path(root), must_exist=False)
    predecessor = legacy._physical(Path(predecessor))
    snapshot = legacy._physical(Path(source_snapshot))
    old = load_manifest(predecessor, _source_root=snapshot)
    prefix = _preprocess_prefix(predecessor, old)
    if (predecessor / "continuation-owner.json").exists() or (predecessor / "continuation-owner.json").is_symlink():
        raise PilotInputError("predecessor continuation is already claimed")
    binding = {
        "schema": CONTINUATION_SCHEMA, "predecessor_root": str(predecessor),
        "source_snapshot": str(snapshot), "predecessor_digest": old["digest"],
        "original_sources": old["sources"], "prefix_evidence": prefix,
        "original_hashes": _predecessor_hashes(predecessor, 1),
        "inherited_slots": [1], "executable_slots": [2, 3, 4],
        "reason": reason, "authorization_note": authorization_note,
    }
    return _prepare_bound_continuation(root, predecessor, old, binding)


def _prepare_bound_continuation(root: Path, predecessor: Path, old: dict[str, Any],
                                binding: dict[str, Any]) -> dict[str, Any]:
    return prepare(root, Path(old["fixture_bundle"]["path"]), Path(old["codex"]["path"]),
                   Path(old["sshai"]["path"]), predecessor / "config.json",
                   predecessor / "model-catalog.json", predecessor / "tool-overrides.json",
                   Path(old["auth"]["path"]), predecessor / "prepared/assessment/instructions.md",
                   predecessor / "prepared/assessment/rubric.md", seed=old["schedule_seed"],
                   _continuation=binding)


def _approval(manifest: dict[str, Any], path: Path, allow_model_run: bool) -> dict[str, Any]:
    approval_path = _private_regular(path, "pilot approval")
    if stat.S_IMODE(approval_path.stat().st_mode) != 0o600:
        raise PilotInputError("pilot approval must have mode 0600")
    approval = _json_file(approval_path, "pilot approval")
    _exact_object(approval, {
        "schema", "manifest_digest", "phase", "session_count",
        "original_diagnostic_session_ceiling", "config_sha256", "approved",
        "approved_at_utc", "authorization_note",
    }, "pilot approval")
    expected = {
        "schema": APPROVAL_SCHEMA, "manifest_digest": manifest["digest"], "phase": PHASE,
        "session_count": SESSION_COUNT,
        "original_diagnostic_session_ceiling": ORIGINAL_DIAGNOSTIC_SESSION_CEILING,
        "config_sha256": manifest["config_sha256"],
    }
    if any(approval.get(key) != value for key, value in expected.items()):
        raise PilotInputError("pilot approval does not match the current manifest")
    if (not allow_model_run or approval.get("approved") is not True
            or not isinstance(approval.get("approved_at_utc"), str)
            or not approval["approved_at_utc"].strip()
            or not isinstance(approval.get("authorization_note"), str)
            or not approval["authorization_note"].strip()):
        raise PilotInputError("run-slot requires current explicit approval and --allow-model-run")
    return {"path": approval_path, "value": approval,
            "sha256": legacy._file_digest(approval_path)}


def _slot(manifest: dict[str, Any], number: int) -> dict[str, Any]:
    if type(number) is not int:
        raise PilotInputError("slot must be an integer")
    matches = [slot for slot in manifest["slots"] if slot["slot"] == number]
    if len(matches) != 1:
        raise PilotInputError("slot is not in the four-session schedule")
    return matches[0]


def _copy_fixture(manifest: dict[str, Any], root: Path, slot: dict[str, Any],
                  destination: Path) -> None:
    source_root = legacy._physical(root / "prepared" / "inputs" / slot["case_id"])
    frozen = manifest["slot_material"][str(slot["slot"])]["fixture_files"]
    legacy._mkdir_private(destination)
    made_dirs = {destination}
    for relative, expected in sorted(frozen.items()):
        _safe_relative(relative, "frozen fixture path")
        source = legacy._physical(source_root / relative)
        data = legacy._read_bounded(source, capture.MAX_CAPTURE_BYTES)
        if len(data) != expected["bytes"] or _sha(data) != expected["sha256"]:
            raise PilotInputError("selected fixture changed since preparation")
        target = destination / relative
        legacy._write_new(target, data, mode=0o600)
        cursor = target.parent
        while cursor != destination.parent:
            made_dirs.add(cursor)
            if cursor == destination:
                break
            cursor = cursor.parent
    for directory in sorted(made_dirs, key=lambda item: len(item.parts), reverse=True):
        directory.chmod(0o700)


def _environment(base: Path, scratch: Path, config: dict[str, Any]) -> dict[str, str]:
    env = dict(config["environment"])
    home = base / "home"
    env.update({
        "HOME": str(home), "CODEX_HOME": str(base / "codex-home"),
        "SSHAI_ROOT": str(scratch / "sshai-root"), "TMPDIR": str(scratch / "tmp"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local-share"),
    })
    if set(env) - ENVIRONMENT_KEYS:
        raise PilotInputError("runtime environment escaped its allowlist")
    return env


def access_overrides(scratch: Path, fixture: Path, protected: Sequence[Path],
                     sshai_path: Path) -> list[str]:
    """Build the named profile with one exact read-only fixture and scratch writes."""
    base = sandbox.config_overrides(scratch, list(protected), [sshai_path])
    prefix = f"permissions.{sandbox.PROFILE}.filesystem={{"
    indexes = [index for index, item in enumerate(base) if item.startswith(prefix) and item.endswith("}")]
    if len(indexes) != 1:
        raise PilotInputError("sandbox helper did not return one named filesystem table")
    fixture = sandbox._physical(fixture)
    entry = f'{json.dumps(str(fixture))}="read"'
    index = indexes[0]
    base[index] = base[index][:-1] + "," + entry + "}"
    if entry not in base[index] or '":workspace_roots"={"."="write"}' not in base[index]:
        raise PilotInputError("failed to bind read-only fixture and writable scratch")
    return base


_ACCESS_PROBE = r'''
import errno, hashlib, json, os, pathlib, socket, subprocess, sys, tempfile
spec=json.loads(sys.argv[1]); out={}
for rel, expected in spec["fixture_files"].items():
 p=pathlib.Path(spec["fixture"])/rel
 try: data=p.read_bytes()
 except OSError: out["fixture_read:"+rel]=False
 else: out["fixture_read:"+rel]=hashlib.sha256(data).hexdigest()==expected
probe=pathlib.Path(spec["fixture_probe"])
try: fd=os.open(probe, os.O_WRONLY)
except OSError as e: out["fixture_write_denied"]=e.errno in (errno.EPERM,errno.EACCES,errno.EROFS)
else: os.close(fd); out["fixture_write_denied"]=False
try: fd=os.open(spec["fixture_create"],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
except OSError as e: out["fixture_create_denied"]=e.errno in (errno.EPERM,errno.EACCES,errno.EROFS)
else: os.close(fd); out["fixture_create_denied"]=False
for name,path in spec["denied"].items():
 try: fd=os.open(path,os.O_RDONLY)
 except OSError as e: out["denied:"+name]=e.errno in (errno.EPERM,errno.EACCES)
 else: os.close(fd); out["denied:"+name]=False
try: pathlib.Path(spec["scratch_write"]).write_text("synthetic\n"); out["scratch_write"]=True
except OSError: out["scratch_write"]=False
try:
 with tempfile.TemporaryFile(dir=pathlib.Path(spec["scratch_write"]).parent) as stream:
  run=subprocess.run([spec["sshai"],"help"],stdout=stream,stderr=subprocess.DEVNULL,timeout=10)
  stream.seek(0); help_bytes=stream.read(1000001)
 out["sshai_exact_executable"]=(run.returncode==0 and len(help_bytes)<=1000000 and hashlib.sha256(help_bytes).hexdigest()==spec["sshai_help_sha256"])
except (OSError,subprocess.SubprocessError): out["sshai_exact_executable"]=False
s=socket.socket(); s.settimeout(2)
try: s.connect(("127.0.0.1",spec["port"])); out["network_denied"]=False
except OSError as e: out["network_denied"]=e.errno in (errno.EPERM,errno.EACCES)
finally: s.close()
print(json.dumps(out,sort_keys=True))
'''


def _qualify_access(codex_path: Path, sshai_path: Path, scratch: Path, fixture: Path,
                    protected: Sequence[Path], environment: dict[str, str],
                    fixture_files: dict[str, Any]) -> dict[str, Any]:
    """Run the reviewed canary plus a no-model exact fixture/access probe."""
    executable_pin = {
        "path": str(sshai_path), "sha256": legacy._file_digest(sshai_path),
        "help_sha256": legacy._offline_probe(sshai_path, ["help"])[1],
    }
    base = sandbox.qualify(codex_path, scratch, list(protected), environment=environment,
                           read_executables=[executable_pin])
    effective = access_overrides(scratch, fixture, protected, sshai_path)
    denied: dict[str, str] = {}
    temporary_canaries: list[Path] = []
    try:
        for index, root in enumerate(protected):
            root = legacy._physical(root)
            if root.is_file():
                denied[f"protected_{index}"] = str(root)
            elif root.is_dir():
                directory = Path(tempfile.mkdtemp(prefix=".issue10-local-access-", dir=root))
                directory.chmod(0o700)
                temporary_canaries.append(directory)
                sentinel = directory / "canary"
                legacy._write_new(sentinel, b"synthetic-only\n")
                denied[f"protected_{index}"] = str(sentinel)
        first = next(iter(sorted(fixture_files)))
        spec = {
            "fixture": str(fixture),
            "fixture_files": {name: item["sha256"] for name, item in fixture_files.items()},
            "fixture_probe": str(fixture / first),
            "fixture_create": str(fixture / ".forbidden-access-probe-create"), "denied": denied,
            "scratch_write": str(scratch / ".access-probe-write"),
            "sshai": str(sshai_path), "sshai_help_sha256": executable_pin["help_sha256"],
        }
        python = Path(sys.executable).resolve(strict=True)
        command = [str(codex_path), "sandbox", "-P", sandbox.PROFILE,
                   "--include-managed-config", "-C", str(scratch)]
        for override in effective:
            command.extend(["-c", override])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            spec["port"] = listener.getsockname()[1]
            command.extend(["--", str(python), "-I", "-c", _ACCESS_PROBE, json.dumps(spec)])
            result = subprocess.run(command, cwd=scratch, env=environment, capture_output=True,
                                    text=True, timeout=30, check=False)
    finally:
        for directory in reversed(temporary_canaries):
            shutil.rmtree(directory)
        (scratch / ".access-probe-write").unlink(missing_ok=True)
        (fixture / ".forbidden-access-probe-create").unlink(missing_ok=True)
    try:
        checks = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise PilotInputError("access probe returned malformed output") from None
    expected = ({f"fixture_read:{name}" for name in fixture_files}
                | {"fixture_write_denied", "fixture_create_denied", "scratch_write",
                   "sshai_exact_executable", "network_denied"}
                | {f"denied:{name}" for name in denied})
    if (result.returncode != 0 or not isinstance(checks, dict) or set(checks) != expected
            or not all(value is True for value in checks.values())):
        raise PilotInputError("fresh access qualification failed")
    receipt = {
        "schema": ACCESS_SCHEMA, "base_receipt": base,
        "effective_config_overrides": effective,
        "fixture_access": "read-only", "scratch_access": "write",
        "network_access": "denied", "checks": checks,
        "limitations": [
            "The canary verifies named accesses, not complete OS confinement.",
            "Tool records do not independently attest process execution or resource reads.",
        ],
    }
    receipt["digest"] = _digest_object(receipt)
    return receipt


def _validated_access_receipt(receipt: Any) -> dict[str, Any]:
    if (not isinstance(receipt, dict) or receipt.get("schema") != ACCESS_SCHEMA
            or receipt.get("digest") != _digest_object(receipt)
            or not isinstance(receipt.get("effective_config_overrides"), list)
            or not receipt["effective_config_overrides"]
            or not isinstance(receipt.get("checks"), dict)
            or not receipt["checks"]
            or not all(value is True for value in receipt["checks"].values())):
        raise PilotInputError("fresh sandbox receipt is malformed or unsuccessful")
    return receipt


def preflight(root: Path) -> dict[str, Any]:
    """Run and retain no-model readiness canaries without reserving a pilot slot."""
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    slots_root = root / "slots"
    if any(slots_root.iterdir()):
        raise PilotInputError("preflight is only valid before any scheduled slot is reserved")
    readiness = root / "readiness"
    if readiness.exists() or readiness.is_symlink():
        raise PilotInputError("readiness preflight is one-shot and already exists")
    legacy._new_dir(readiness)
    legacy._mkdir_private(readiness / "cases")
    rows: list[dict[str, Any]] = []
    stage = "initialization"
    current_case: str | None = None
    result: dict[str, Any] | None = None
    try:
        config = manifest["config"]
        codex_path = Path(manifest["codex"]["path"])
        sshai_path = Path(manifest["sshai"]["path"])
        auth_path = _private_regular(Path(manifest["auth"]["path"]), "frozen auth source")
        bundle = Path(manifest["fixture_bundle"]["path"])
        protected = [auth_path.parent.parent, Path.home(), bundle,
                     root / "approval-template.json", auth_path]
        qualifier = _ACCESS_QUALIFIER or _qualify_access
        for case_id in CASES:
            current_case = case_id
            stage = f"access-qualification-{case_id}"
            slot = next(item for item in manifest["slots"] if item["case_id"] == case_id)
            case_root = readiness / "cases" / case_id
            legacy._mkdir_private(case_root)
            fixture, scratch = case_root / "fixture", case_root / "scratch"
            _copy_fixture(manifest, root, slot, fixture)
            for directory in (scratch, case_root / "home", case_root / "codex-home"):
                legacy._mkdir_private(directory)
            for directory in (scratch / "sshai-root", scratch / "tmp",
                              case_root / "home" / ".config", case_root / "home" / ".cache",
                              case_root / "home" / ".local-share"):
                legacy._mkdir_private(directory)
            environment = _environment(case_root, scratch, config)
            receipt = _validated_access_receipt(qualifier(
                codex_path, sshai_path, scratch, fixture, protected, environment,
                manifest["slot_material"][str(slot["slot"])]["fixture_files"]))
            legacy._write_new(readiness / f"access-{case_id}.json", _pretty(receipt))
            rows.append({"case_id": case_id, "status": "passed",
                         "receipt_digest": receipt["digest"]})
        result = {
            "schema": READINESS_SCHEMA, "phase": PHASE,
            "manifest_digest": manifest["digest"], "status": "passed",
            "model_launches": 0, "scheduled_slots_consumed": 0,
            "cases": rows,
            "advertised_tools": config["codex_exec"]["advertised_tools"],
            "permission_parity": (
                "same access-profile builder and protected-root policy; fresh path-bound "
                "qualification is still required for every diagnostic slot"
            ),
            "limitations": [
                "Synthetic canaries do not prove complete confinement.",
                "Advertised tool schemas marked unavailable were not observed by this preflight.",
                "No live usage, answer finality, semantic routing, or backend availability was tested.",
            ],
        }
        result["digest"] = _digest_object(result)
        legacy._write_new(readiness / "result.json", _pretty(result))
    except BaseException as exc:
        if result is None and not (readiness / "result.json").exists():
            failure = {
                "schema": READINESS_SCHEMA, "phase": PHASE,
                "manifest_digest": manifest["digest"], "status": "blocked",
                "stage": stage, "case_id": current_case,
                "model_launches": 0, "scheduled_slots_consumed": 0,
                "reason": f"{type(exc).__name__}: {exc}", "cases": rows,
            }
            failure["digest"] = _digest_object(failure)
            legacy._write_new(readiness / "result.json", _pretty(failure))
        raise
    return {
        "schema": READINESS_SCHEMA, "phase": PHASE, "status": "passed",
        "model_launches": 0, "scheduled_slots_consumed": 0,
        "cases": [{"case_id": row["case_id"], "status": row["status"]} for row in rows],
        "advertised_tools": result["advertised_tools"],
        "limitations": result["limitations"],
    }


def load_readiness(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    readiness = _json_file(root / "readiness" / "result.json", "readiness result")
    if (readiness.get("schema") != READINESS_SCHEMA
            or readiness.get("digest") != _digest_object(readiness)
            or readiness.get("phase") != PHASE
            or readiness.get("manifest_digest") != manifest["digest"]
            or readiness.get("status") != "passed"
            or readiness.get("model_launches") != 0
            or readiness.get("scheduled_slots_consumed") != 0
            or readiness.get("advertised_tools")
            != manifest["config"]["codex_exec"]["advertised_tools"]):
        raise PilotInputError("retained readiness result is missing, changed, or unsuccessful")
    rows = readiness.get("cases")
    if (not isinstance(rows, list) or len(rows) != len(CASES)
            or {row.get("case_id") for row in rows if isinstance(row, dict)} != set(CASES)
            or any(set(row) != {"case_id", "status", "receipt_digest"}
                   or row["status"] != "passed" for row in rows if isinstance(row, dict))
            or not all(isinstance(row, dict) for row in rows)):
        raise PilotInputError("retained readiness case inventory is malformed")
    for row in rows:
        receipt = _validated_access_receipt(_json_file(
            root / "readiness" / f"access-{row['case_id']}.json",
            f"{row['case_id']} readiness access receipt"))
        if receipt["digest"] != row["receipt_digest"]:
            raise PilotInputError("retained readiness access receipt changed")
    return readiness


def _incomplete_scan(error: OSError) -> None:
    raise PilotInputError("incomplete CODEX_HOME traversal") from error


def _native_runtime_inventory(codex_home: Path) -> dict[str, Any]:
    """Recognize only Darwin arg0 aliases from the pinned Codex source.

    sandbox startup can leave tmp/arg0 behind before the model process starts.
    These are runtime aliases, not rollouts or proof that a tool was invoked.
    Source: codex-rs/arg0/src/lib.rs:327-445 at SOURCE_CONTRACT['revision'].
    """
    runtime = codex_home / "tmp"
    entries = []
    present = runtime.exists() or runtime.is_symlink()
    if present:
        if runtime.is_symlink() or not runtime.is_dir():
            raise PilotInputError("unexpected native runtime root")
        for current, directories, files in os.walk(runtime, followlinks=False,
                                                   onerror=_incomplete_scan):
            for name in sorted([*directories, *files]):
                if len(entries) >= MAX_DISCOVERY_ENTRIES:
                    raise PilotInputError("native runtime inventory entry bound exceeded")
                path = Path(current) / name
                parts = path.relative_to(runtime).parts
                mode = path.lstat().st_mode
                kind = None
                if ((len(parts) == 1 and parts[0] == "arg0")
                        or (len(parts) == 2 and parts[0] == "arg0"
                            and parts[1].startswith("codex-arg0")
                            and parts[1] != "codex-arg0")):
                    if stat.S_ISDIR(mode):
                        kind = "directory"
                elif len(parts) == 3 and parts[0] == "arg0":
                    if (parts[2] == ".lock" and stat.S_ISREG(mode)
                            and path.stat().st_size == 0):
                        kind = "lock"
                    elif (parts[2] in {"apply_patch", "applypatch", "codex-execve-wrapper"}
                          and stat.S_ISLNK(mode)
                          and path.readlink() == EXPECTED_CODEX_PATH):
                        kind = "native-alias"
                if kind is None:
                    raise PilotInputError("unexpected native runtime entry")
                entries.append({"path": str(path.relative_to(codex_home)), "kind": kind})
    return {"classification": "pinned-arg0-layout", "present": present,
            "entries": sorted(entries, key=lambda row: row["path"])}


def _discover_rollouts(codex_home: Path) -> tuple[list[Path], dict[str, Any]]:
    native_runtime = _native_runtime_inventory(codex_home)
    native_aliases = {codex_home / row["path"] for row in native_runtime["entries"]
                      if row["kind"] == "native-alias"}
    candidates: list[Path] = []
    entry_count = 0
    for current, directories, files in os.walk(codex_home, topdown=True, followlinks=False,
                                               onerror=_incomplete_scan):
        directories.sort()
        files.sort()
        current_path = Path(current)
        for name in [*directories, *files]:
            entry_count += 1
            if entry_count > MAX_DISCOVERY_ENTRIES:
                raise PilotInputError("isolated CODEX_HOME discovery entry bound exceeded")
            path = current_path / name
            if path.is_symlink():
                if path == codex_home / "auth.json" or path in native_aliases:
                    continue
                raise PilotInputError("unexpected symlink in isolated CODEX_HOME")
        for name in files:
            path = current_path / name
            if path.suffix == ".jsonl" and not path.is_symlink():
                legacy._regular(path)
                candidates.append(path)
    candidates.sort(key=lambda path: str(path.relative_to(codex_home)))
    if len(candidates) > collector.MAX_ROLLOUT_CANDIDATES:
        raise PilotInputError("isolated CODEX_HOME produced too many rollout candidates")
    return candidates, {"root": str(codex_home), "entry_count": entry_count,
                        "candidate_count": len(candidates), "native_runtime": native_runtime,
                        "method": "bounded recursive scan of this slot's initially empty CODEX_HOME only"}


def _collect_local_attempt(attempt_dir: Path, argv: Sequence[str], *, prompt: bytes,
                           env: Mapping[str, str], cwd: Path, timeout_seconds: float,
                           codex_home: Path, answer_path: Path,
                           association: dict[str, Any]) -> dict[str, Any]:
    """Collector specialization whose only post-spawn discovery root is fresh CODEX_HOME."""
    (output, command, environment, work, timeout, _, answer) = collector._validate_request(
        attempt_dir, argv, prompt, env, cwd, timeout_seconds, (), answer_path)
    native_runtime = _native_runtime_inventory(codex_home)
    expected_inventory = {codex_home / "auth.json", codex_home / "model-catalog.json"}
    if native_runtime["present"]:
        expected_inventory.add(codex_home / "tmp")
    if set(codex_home.iterdir()) != expected_inventory:
        raise PilotInputError("dedicated CODEX_HOME inventory changed after controlled provisioning")
    request = collector._request_receipt(command, prompt, environment, work, timeout, [], answer)
    try:
        association_bytes = json.dumps(association, allow_nan=False).encode()
        if len(association_bytes) > 16384:
            raise ValueError("association exceeds 16 KiB")
        retained_association = json.loads(association_bytes)
    except (TypeError, ValueError) as exc:
        raise collector.CollectorInputError(str(exc)) from exc
    request.update({"schema": collector.ASSOCIATED_ATTEMPT_SCHEMA,
                    "association": retained_association,
                    "rollout_discovery": {"root": str(codex_home),
                                          "initial_inventory": sorted(path.name for path in expected_inventory),
                                          "native_runtime": native_runtime,
                                          "entry_bound": MAX_DISCOVERY_ENTRIES,
                                          "candidate_bound": collector.MAX_ROLLOUT_CANDIDATES}})
    attempt = collector._private_new_directory(output)
    legacy._write_new(attempt / "attempt.json", legacy._canon(request))
    captured = legacy._bounded_process(command, prompt, environment, work, timeout)
    events = captured.pop("stdout")
    stderr = captured.pop("stderr")
    process_receipt = {
        "schema": collector.PROCESS_SCHEMA, **captured,
        "execution": collector._execution(captured), "stdout_bytes": len(events),
        "stderr_bytes": len(stderr),
        "stdout_limit_reached": len(events) == collector.MAX_STREAM_BYTES,
        "stderr_limit_reached": len(stderr) == collector.MAX_STREAM_BYTES,
    }
    legacy._write_new(attempt / "events.jsonl", events)
    legacy._write_new(attempt / "stderr.txt", stderr)
    legacy._write_new(attempt / "process.json", legacy._canon(process_receipt))
    process_started = process_receipt["pid"] is not None
    discovery: dict[str, Any]
    try:
        candidates, discovery = _discover_rollouts(codex_home) if process_started else ([], {
            "root": str(codex_home), "entry_count": 2, "candidate_count": 0,
            "method": "not scanned because process did not start"})
        rollout = collector._collect_rollout(attempt, events, candidates, process_started)
    except Exception as exc:
        discovery = {"root": str(codex_home), "state": "invalid", "reason": str(exc)}
        legacy._write_new(attempt / "rollout.jsonl", b"")
        rollout = {"state": "lost", "reason": "isolated_rollout_discovery_failed",
                   "bytes": 0, "sha256": _sha(b""), "candidates": []}
    answer_receipt = collector._collect_answer(attempt, answer, process_started)
    delivery = {
        "schema": collector.DELIVERY_SCHEMA, "process_execution": process_receipt["execution"],
        "rollout_discovery": discovery, "rollout": rollout,
        "answer": {**answer_receipt, "finality": "unknown",
                   "finality_reason": "requires version-bounded completion comparison"},
    }
    legacy._write_new(attempt / "delivery.json", legacy._canon(delivery))
    return {"attempt_dir": attempt, "attempt": request, "process": process_receipt,
            "delivery": delivery}


def _observation_calls(source: str, observation: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse the pinned parser's signatures before source-local grouping loses correlations."""
    raw = observation["item"]
    if source == "cli":
        return capture._parse_cli([{"type": observation["event"], "item": raw}])["calls"]
    if source == "rollout.response_item":
        record = {"type": "response_item", "payload": raw}
    elif source == "rollout.raw_response_item":
        record = {"type": "event_msg", "payload": {"type": "raw_response_item", "item": raw}}
    elif source == "rollout.turn_item":
        record = {"type": "event_msg", "payload": {"type": observation["event"], "item": raw}}
    elif source == "rollout.event_msg":
        record = {"type": "event_msg", "payload": raw}
    else:
        return []
    # Full-stream parser issues are handled by _audit/run_slot. A single record
    # intentionally has no surrounding turn/usage lifecycle; only its calls are used.
    return capture._parse_rollout([record])["calls"]


def _audit(report: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    allowed = {tuple(signature[key] for key in ("source", "record_type", "tool_name", "evidence_kind"))
               for signature in config["codex_exec"]["allowed_audit_signatures"]}
    rows = []
    unallowed = []
    identity_issues = []
    for index, item in enumerate(report["calls"]["inventory"], 1):
        kinds = item.get("evidence_kinds") or [item.get("evidence_kind")]
        signatures = []
        observations = item.get("raw_observations") or []
        permitted = bool(observations)
        for observation in observations:
            source = item["source"]
            raw = observation["item"]
            calls = _observation_calls(source, observation)
            actual = [(call["source"], call["record_type"], call["tool_name"], call["evidence_kind"])
                      for call in calls]
            signatures.extend(actual)
            permitted = permitted and bool(actual) and all(signature in allowed for signature in actual)
            # Adapter fallback IDs preserve an inventory position, not request
            # identity. Response-item IDs also cannot replace these call IDs.
            identity_fields = (("call_id",) if raw.get("type") in {
                "function_call", "custom_tool_call", "local_shell_call",
            } else ("call_id", "id", "request_id"))
            if not any(isinstance(raw.get(key), str) and raw[key] for key in identity_fields):
                identity_issues.append({
                    "source": source, "record": observation["record"],
                    "code": "missing_tool_identity", "effect": "invalid",
                    "detail": f"{raw.get('type')!r} requires a nonempty identity in {identity_fields!r}",
                })
        row = {
            "entry": index, "source": item.get("source"),
            "record_type": item.get("record_type"), "tool_name": item.get("tool_name"),
            "evidence_kinds": kinds, "observations": item.get("observations"),
            "reported_status": item.get("status"), "allowed_signature": permitted,
            "observed_signatures": [dict(zip(
                ("source", "record_type", "tool_name", "evidence_kind"), signature, strict=True,
            )) for signature in signatures],
            "execution_attested": False,
            "raw_observations": item.get("raw_observations"),
        }
        rows.append(row)
        if not permitted:
            unallowed.append(index)
    # A malformed record can disappear from the call inventory entirely. Do not
    # qualify the remaining entries as a complete bounded audit in that case.
    unsupported = [issue for issue in report["issues"] if (
        issue.get("source") in {"cli", "rollout"} and issue.get("effect") == "invalid"
    ) or issue.get("code") in {
        "unsupported_cli_item", "unsupported_turn_item", "unknown_event_type",
        "unknown_record_type",
    }]
    unsupported.extend(identity_issues)
    status = "bounded-recorded" if not unallowed and not unsupported else "unqualified"
    return {
        "unit": "source-local recorded request or lifecycle entry",
        "status": status, "entries": rows, "entry_count": len(rows),
        "observation_count": sum((row["observations"] or 0) for row in rows),
        "unallowed_entries": unallowed, "unsupported_records": unsupported,
        "unique_tool_call_count": None, "os_execution_attestation": False,
        "cross_source_joining": False, "shell_substring_classification": False,
        "semantic_routing": {
            "status": "unknown",
            "reason": "recorded arguments require a separate semantic classification",
        },
        "limitations": [
            "Recorded requests and lifecycles are not OS execution attestations.",
            "Source-local entries are not summed or joined into unique calls.",
            "Arguments and command substrings are not used to infer routing compliance.",
        ],
    }


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "slot": result["slot"]["slot"], "case_id": result["slot"]["case_id"],
        "arm": result["slot"]["arm"], "launch": result["launch"],
        "execution": result["execution"], "usage": result["usage"],
        "completion_evidence": result["completion_evidence"],
        "tool_audit": result["tool_audit"], "access": result["access"],
        "continuation": result["continuation"],
        "auditor_grade": "unknown", "independent_model_assessment": "unknown",
        "experimental_savings_claim_eligible": False,
    }


def _failure_result(slot: dict[str, Any], stage: str) -> dict[str, Any]:
    return {
        "schema": RESULT_SCHEMA, "slot": slot, "launch": "not-started",
        "execution": "unavailable", "usage": {"totals": None, "complete": False},
        "completion_evidence": {"status": "unavailable", "finality": "unknown",
                                "version_bounded": True},
        "tool_audit": {"status": "unqualified", "entry_count": 0,
                       "unique_tool_call_count": None, "os_execution_attestation": False,
                       "semantic_routing": {"status": "unknown", "reason": "slot failed"}},
        "access": {"status": "blocked", "stage": stage},
        "continuation": {"allowed": False, "blockers": [stage]},
        "reviews": {"auditor": {"kind": "model", "grade": "unknown"},
                    "independent_assessor": {"kind": "model", "grade": "unknown"}},
        "experimental_savings_claim_eligible": False,
    }


def run_slot(root: Path, number: int, approval_path: Path, *,
             allow_model_run: bool = False) -> dict[str, Any]:
    """Attempt exactly one scheduled slot.  Any reservation or failure makes it non-retryable."""
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    load_readiness(root, manifest)
    approval = _approval(manifest, approval_path, allow_model_run)
    slot = _slot(manifest, number)
    inherited = manifest.get("continuation", {}).get("inherited_slots", [])
    if number in inherited:
        raise PilotInputError("inherited consumed slot cannot be executed or retried")
    if (root / "continuation-owner.json").exists() or (root / "continuation-owner.json").is_symlink():
        raise PilotInputError("remaining slots belong to the prospective continuation")
    for prior in range(1, number):
        if prior in inherited:
            continue  # Only the manifest-bound, revalidated pre-process exception.
        prior_result = root / "slots" / f"{prior:03}" / "result.json"
        if not prior_result.is_file() or prior_result.is_symlink():
            raise PilotInputError("fixed schedule requires a retained result for every prior slot")
        prior_value = _json_file(prior_result, f"prior slot {prior} result")
        if (prior_value.get("schema") != RESULT_SCHEMA
                or prior_value.get("slot") != manifest["slots"][prior - 1]):
            raise PilotInputError("prior slot result is malformed or schedule-mismatched")
        if prior_value.get("continuation", {}).get("allowed") is not True:
            raise PilotInputError("pilot is stopped by a retained prior-slot blocker")
    base = root / "slots" / f"{number:03}"
    if base.exists() or base.is_symlink():
        raise PilotInputError("slot is already reserved; retry, overwrite, and resume are forbidden")
    legacy._new_dir(base)
    legacy._write_new(base / "reservation.json", _pretty({
        "manifest_digest": manifest["digest"], "slot": slot,
        "approval_sha256": approval["sha256"], "one_shot": True,
    }))
    return collect_reserved_slot(root, manifest, slot, approval)


def collect_reserved_slot(root: Path, manifest: dict[str, Any], slot: dict[str, Any],
                          approval: dict[str, Any], *, qualifier: Callable[..., dict[str, Any]] | None = None,
                          attempt_collector: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """Collect a controller-gated reservation once; never load/approve/reserve a population."""
    root = legacy._physical(Path(root))
    if (_json_file(root / "manifest.json", "reserved manifest") != manifest
            or manifest.get("digest") != _digest_object(manifest)):
        raise PilotInputError("reserved collection manifest is not the retained immutable plan")
    number = slot.get("slot")
    if _slot(manifest, number) != slot:
        raise PilotInputError("reserved collection slot differs from the retained schedule")
    base = legacy._physical(root / "slots" / f"{number:03}")
    if not base.is_dir() or {path.name for path in base.iterdir()} != {"reservation.json"}:
        raise PilotInputError("reserved collection is one-shot; existing collection evidence cannot resume")
    _exact_object(approval, {"path", "value", "sha256"}, "reserved approval binding")
    approval_path = _private_regular(Path(approval["path"]), "reserved approval")
    value = _json_file(approval_path, "reserved approval")
    expected = {"schema": manifest.get("approval_schema", APPROVAL_SCHEMA),
                "manifest_digest": manifest["digest"], "phase": manifest["phase"],
                "session_count": manifest["session_count"],
                "original_diagnostic_session_ceiling": manifest["original_diagnostic_session_ceiling"],
                "config_sha256": manifest["config_sha256"]}
    if (stat.S_IMODE(approval_path.stat().st_mode) != 0o600 or value != approval["value"]
            or legacy._file_digest(approval_path) != approval["sha256"]
            or any(value.get(key) != item for key, item in expected.items())
            or value.get("approved") is not True
            or any(not isinstance(value.get(key), str) or not value[key].strip()
                   for key in ("approved_at_utc", "authorization_note"))
            or _json_file(base / "reservation.json", "reserved slot") != {
                "manifest_digest": manifest["digest"], "slot": slot,
                "approval_sha256": approval["sha256"], "one_shot": True}):
        raise PilotInputError("reserved collection requires matching manifest/slot/approval/reservation")
    result: dict[str, Any] | None = None
    stage = "reserved-before-preflight"
    try:
        fixture, scratch, evidence = base / "fixture", base / "scratch", base / "evidence"
        _copy_fixture(manifest, root, slot, fixture)
        for directory in (scratch, evidence, base / "home", base / "codex-home"):
            legacy._mkdir_private(directory)
        for directory in (scratch / "sshai-root", scratch / "tmp", base / "home" / ".config",
                          base / "home" / ".cache", base / "home" / ".local-share"):
            legacy._mkdir_private(directory)
        auth_path = _private_regular(Path(manifest["auth"]["path"]), "frozen auth source")
        codex_home = base / "codex-home"
        (codex_home / "auth.json").symlink_to(auth_path)
        catalog_data = legacy._read_bounded(root / "model-catalog.json", capture.MAX_CAPTURE_BYTES)
        if _sha(catalog_data) != manifest["model_catalog"]["sha256"]:
            raise PilotInputError("model catalog changed before slot provisioning")
        legacy._write_new(codex_home / "model-catalog.json", catalog_data, mode=0o400)
        config = manifest["config"]
        environment = _environment(base, scratch, config)
        codex_path = Path(manifest["codex"]["path"])
        sshai_path = Path(manifest["sshai"]["path"])
        bundle = Path(manifest["fixture_bundle"]["path"])
        protected = [auth_path.parent.parent, Path.home(), bundle, approval["path"], auth_path]
        qualifier = qualifier or _ACCESS_QUALIFIER or _qualify_access
        stage = "fresh-access-qualification"
        access_receipt = _validated_access_receipt(qualifier(
            codex_path, sshai_path, scratch, fixture, protected, environment,
            manifest["slot_material"][str(number)]["fixture_files"]))
        legacy._write_new(evidence / "access-receipt.json", _pretty(access_receipt))
        source_prompt = legacy._read_bounded(
            root / "prepared" / "prompts" / f"{slot['case_id']}.md",
            collector.MAX_PROMPT_BYTES)
        prompt = _render_prompt(source_prompt, fixture, scratch, slot["arm"], str(sshai_path))
        if _sha(prompt) != manifest["slot_material"][str(number)]["rendered_prompt_sha256"]:
            raise PilotInputError("rendered prompt changed since preparation")
        legacy._write_new(evidence / "prompt.txt", prompt)
        answer_path = evidence / "last-message.txt"
        argv = _model_argv(manifest, base, access_receipt)
        association = {
            "manifest_digest": manifest["digest"], "slot": slot,
            "approval_sha256": approval["sha256"], "access_receipt_digest": access_receipt["digest"],
            "model": config["model"], "history_mode": config["codex_exec"]["history_mode"],
            "config_sha256": manifest["config_sha256"], "argv_sha256": _sha(_encoded(argv)),
        }
        collect = attempt_collector or _ATTEMPT_COLLECTOR or _collect_local_attempt
        stage = "model-attempt-requested"
        attempt = collect(evidence / "attempt", argv, prompt=prompt, env=environment, cwd=scratch,
                          timeout_seconds=config["limits"]["timeout_seconds"],
                          codex_home=codex_home, answer_path=answer_path,
                          association=association)
        attempt_dir = attempt["attempt_dir"]
        delivery = _json_file(attempt_dir / "delivery.json", "attempt delivery")
        events = legacy._read_bounded(attempt_dir / "events.jsonl", capture.MAX_CAPTURE_BYTES)
        rollout = legacy._read_bounded(attempt_dir / "rollout.jsonl", capture.MAX_CAPTURE_BYTES)
        process_data = legacy._read_bounded(attempt_dir / "process.json", capture.MAX_CAPTURE_BYTES)
        answer = (legacy._read_bounded(attempt_dir / "answer.txt", capture.MAX_ANSWER_BYTES)
                  if (attempt_dir / "answer.txt").exists() else None)
        report = capture.capture_bytes(
            events, rollout, process_data, answer,
            answer_state="captured" if answer is not None else "lost",
        )
        completion = capture.completion_evidence_bytes(events, rollout, answer)
        audit = _audit(report, config)
        legacy._write_new(evidence / "capture-report.json", _pretty(report))
        legacy._write_new(evidence / "completion-evidence.json", _pretty(completion))
        legacy._write_new(evidence / "tool-audit.json", _pretty(audit))
        fixture_integrity = {}
        for name, expected in manifest["slot_material"][str(number)]["fixture_files"].items():
            path = fixture / name
            fixture_integrity[name] = (
                path.is_file() and not path.is_symlink()
                and legacy._file_digest(path) == expected["sha256"]
                and stat.S_IMODE(path.stat().st_mode) == 0o600)
        actual_fixture_files = {
            str(path.relative_to(fixture)) for path in fixture.rglob("*") if path.is_file()
        }
        fixture_inventory_exact = actual_fixture_files == set(
            manifest["slot_material"][str(number)]["fixture_files"])
        access_status = "passed" if all(fixture_integrity.values()) and fixture_inventory_exact else "failed"
        blockers = []
        # CodexModelRerouted and generic warnings/errors use non-tool error items;
        # requested turn_context alone cannot exclude an observable substitution.
        cli_records = capture.parse_jsonl(events, "local_fixed_model_cli")["records"]
        if any(isinstance(row, dict) and isinstance(row.get("item"), dict)
               and row["item"].get("type") == "error" for row in cli_records):
            blockers.append("cli_error_item")
        contexts = [row.get("payload") for row in capture.parse_jsonl(rollout, "local_fixed_model_rollout")["records"]
                    if isinstance(row, dict) and row.get("type") == "turn_context"]
        if not contexts or any(not isinstance(context, dict) or context.get("model") != config["model"]["id"]
                               or context.get("effort") != config["model"]["reasoning_effort"] for context in contexts):
            blockers.append("observed_model_context_missing_or_mismatched")
        # Delivery failures and invalid capture evidence consume the slot and
        # stop later reservations. Intentional qualification uncertainty is not
        # capture loss; do not gate on usage.complete, finality, or an unknown
        # instrumentation status. Detailed reasons remain in the private receipts.
        if delivery.get("rollout_discovery", {}).get("state") == "invalid":
            blockers.append("rollout_discovery_failed")
        if delivery.get("rollout", {}).get("state") != "captured":
            blockers.append("rollout_capture_lost")
        if delivery.get("answer", {}).get("state") != "captured":
            blockers.append("answer_capture_lost")
        blockers.extend(sorted({
            f"capture_issue:{issue['source']}:{issue['code']}"
            for issue in report["issues"] if issue.get("effect") == "invalid"
        }))
        if report["execution"]["capture_overflow"]:
            blockers.append("capture_overflow")
        if access_status != "passed":
            blockers.append("fixture_access_or_integrity_failure")
        if audit["status"] != "bounded-recorded":
            blockers.append("unsupported_or_unallowed_tool_record")
        result = {
            "schema": RESULT_SCHEMA, "slot": slot, "launch": "attempted",
            "execution": report["execution"]["execution"],
            "usage": {"totals": report["usage"]["totals"],
                      "complete": report["usage"]["complete"],
                      "counting_method": report["usage"]["counting_method"]},
            "completion_evidence": {"status": completion["status"],
                                    "finality": "unknown", "version_bounded": True},
            "tool_audit": {"status": audit["status"], "entry_count": audit["entry_count"],
                           "observation_count": audit["observation_count"],
                           "unique_tool_call_count": None, "os_execution_attestation": False,
                           "semantic_routing": audit["semantic_routing"]},
            "access": {"status": access_status, "fixture_integrity": fixture_integrity,
                       "fixture_inventory_exact": fixture_inventory_exact,
                       "receipt_digest": access_receipt["digest"]},
            "continuation": {"allowed": not blockers, "blockers": blockers},
            "reviews": {"auditor": {"kind": "model", "grade": "unknown"},
                        "independent_assessor": {"kind": "model", "grade": "unknown"}},
            "experimental_savings_claim_eligible": False,
        }
        legacy._write_new(base / "result.json", _pretty(result))
    except BaseException as exc:
        if result is None and not (base / "result.json").exists():
            result = _failure_result(slot, stage)
            if stage == "model-attempt-requested":
                result["launch"] = "attempted-or-unknown"
            legacy._write_new(base / "result.json", _pretty(result))
        raise
    return _result_summary(result)


def summarize(root: Path) -> dict[str, Any]:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    rows = []
    for slot in manifest["slots"]:
        if slot["slot"] in manifest.get("continuation", {}).get("inherited_slots", []):
            binding = manifest["continuation"]
            old_root = Path(binding["predecessor_root"])
            if binding["schema"] == AUTH_CONTINUATION_SCHEMA and slot["slot"] == 1:
                old_manifest = _json_file(old_root / "manifest.json", "first continuation manifest")
                old_root = Path(old_manifest["continuation"]["predecessor_root"])
            retained = _json_file(old_root / "slots" / f"{slot['slot']:03}" / "result.json", "inherited failure")
            row = _result_summary(retained)
            row.update({"state": "retained-failure", "retryable": False})
            rows.append(row)
            continue
        base = root / "slots" / f"{slot['slot']:03}"
        path = base / "result.json"
        if not path.exists() and not path.is_symlink():
            row = {"slot": slot["slot"], "case_id": slot["case_id"], "arm": slot["arm"],
                   "state": "unattempted", "auditor_grade": "unknown",
                   "independent_model_assessment": "unknown"}
            # Directory creation itself consumes a slot, even if interruption
            # precedes reservation.json. Missing result evidence cannot prove
            # that no child ran, and summary must not repair or retry the slot.
            if base.exists() or base.is_symlink():
                row.update({
                    "state": "reserved-incomplete", "launch": "attempted-or-unknown",
                    "execution": "unknown", "retryable": False,
                    "continuation": {"allowed": False,
                                     "blockers": ["reserved_slot_missing_result"]},
                })
            rows.append(row)
            continue
        result = _json_file(path, "slot result")
        if result.get("schema") != RESULT_SCHEMA or result.get("slot") != slot:
            raise PilotInputError("slot result is malformed or schedule-mismatched")
        rows.append(_result_summary(result))
    readiness_path = root / "readiness" / "result.json"
    readiness_status = (load_readiness(root, manifest)["status"]
                        if readiness_path.is_file() and not readiness_path.is_symlink()
                        else "not-run")
    return {
        "schema": SUMMARY_SCHEMA, "phase": PHASE, "scheduled_sessions": SESSION_COUNT,
        "original_diagnostic_session_ceiling": ORIGINAL_DIAGNOSTIC_SESSION_CEILING,
        "assessor_context_ceiling": ASSESSOR_CONTEXT_CEILING,
        "readiness": readiness_status,
        "slots": rows, "raw_outputs_included": False, "private_paths_included": False,
        "comparative_savings_claim": None, "experimental_savings_claim_eligible": False,
    }


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="prepare a new private four-session pilot")
    prep.add_argument("root", type=Path)
    prep.add_argument("--fixtures", type=Path, required=True)
    prep.add_argument("--codex", type=Path, required=True)
    prep.add_argument("--sshai", type=Path, required=True)
    prep.add_argument("--config", type=Path, required=True)
    prep.add_argument("--model-catalog", type=Path, required=True)
    prep.add_argument("--tool-overrides", type=Path, required=True)
    prep.add_argument("--auth-file", type=Path, required=True)
    prep.add_argument("--assessment-instructions", type=Path, required=True)
    prep.add_argument("--assessment-rubric", type=Path, required=True)
    prep.add_argument("--seed", type=int, default=DEFAULT_SEED)
    continuation = commands.add_parser("prepare-continuation", help="prepare only original unreserved slots 2..4")
    continuation.add_argument("root", type=Path)
    continuation.add_argument("--predecessor", type=Path, required=True)
    continuation.add_argument("--predecessor-sources", type=Path, required=True)
    continuation.add_argument("--reason", required=True)
    continuation.add_argument("--authorization-note", required=True)
    auth_continuation = commands.add_parser("prepare-auth-continuation", help="inherit the exact startup-auth failure; prepare original slots 3..4")
    auth_continuation.add_argument("root", type=Path)
    auth_continuation.add_argument("--predecessor", type=Path, required=True)
    auth_continuation.add_argument("--predecessor-sources", type=Path, required=True)
    auth_continuation.add_argument("--reason", required=True)
    auth_continuation.add_argument("--authorization-note", required=True)
    auth_continuation.add_argument("--auth-repair-note", required=True)
    ready = commands.add_parser("preflight", help="run retained no-model readiness canaries")
    ready.add_argument("root", type=Path)
    run = commands.add_parser("run-slot", help="attempt one approved one-shot slot")
    run.add_argument("root", type=Path)
    run.add_argument("--slot", type=int, required=True)
    run.add_argument("--approval", type=Path, required=True)
    run.add_argument("--allow-model-run", action="store_true")
    summary = commands.add_parser("summary", help="emit a sanitized path-free summary")
    summary.add_argument("root", type=Path)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        manifest = prepare(args.root, args.fixtures, args.codex, args.sshai,
                           args.config, args.model_catalog, args.tool_overrides,
                           args.auth_file, args.assessment_instructions,
                           args.assessment_rubric, seed=args.seed)
        output = {"schema": MANIFEST_SCHEMA, "phase": manifest["phase"],
                  "manifest_digest": manifest["digest"], "scheduled_sessions": SESSION_COUNT,
                  "model_launches": 0, "approval_required": True}
    elif args.command == "prepare-continuation":
        manifest = prepare_continuation(args.root, args.predecessor, args.predecessor_sources,
                                        reason=args.reason, authorization_note=args.authorization_note)
        output = {"schema": MANIFEST_SCHEMA, "phase": manifest["phase"],
                  "manifest_digest": manifest["digest"], "scheduled_sessions": SESSION_COUNT,
                  "inherited_consumed_slots": [1], "executable_slots": [2, 3, 4],
                  "model_launches": 0, "approval_required": True}
    elif args.command == "prepare-auth-continuation":
        manifest = prepare_auth_continuation(args.root, args.predecessor, args.predecessor_sources,
                                             reason=args.reason, authorization_note=args.authorization_note,
                                             auth_repair_note=args.auth_repair_note)
        output = {"schema": MANIFEST_SCHEMA, "phase": manifest["phase"],
                  "manifest_digest": manifest["digest"], "scheduled_sessions": SESSION_COUNT,
                  "inherited_consumed_slots": [1, 2], "executable_slots": [3, 4],
                  "model_launches": 0, "approval_required": True, "backend_availability": "unverified"}
    elif args.command == "preflight":
        output = preflight(args.root)
    elif args.command == "run-slot":
        output = run_slot(args.root, args.slot, args.approval,
                          allow_model_run=args.allow_model_run)
    else:
        output = summarize(args.root)
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_main())
    except PilotInputError as exc:
        print(f"pilot blocked: {exc}", file=sys.stderr)
        raise SystemExit(2)
