#!/usr/bin/env python3
"""Population, lifecycle, and control math for sshai benchmark protocol v2.1."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


BRANCHES = ("raw-control", "raw", "fanout-control", "fanout")
AMENDMENT_SCHEMA = "sshai-benchmark/v2.1-amendment-1"
AMENDMENT_ANALYSIS_SCHEMA = "sshai-benchmark-analysis/v2.1-amendment-1"
AMENDMENT_BRANCH_SCHEDULE = (
    ("raw-control", "raw", "fanout-control", "fanout"),
    ("raw", "raw-control", "fanout", "fanout-control"),
    ("fanout-control", "fanout", "raw-control", "raw"),
    ("fanout", "fanout-control", "raw", "raw-control"),
) * 2
AMENDMENT_DECISION_RULE = {
    "minimum_defined_reductions": 6,
    "maximum_control_floor_pairs": 2,
    "median_reduction_threshold": 0.80,
}
OBSERVATION_IDS = tuple(
    [f"L{index:02d}" for index in range(1, 25)]
    + [f"W{index:02d}" for index in range(1, 13)]
)
EXPECTED_CALL_COUNTS = {
    "raw-control": 36,
    "raw": 36,
    "fanout-control": 24,
    "fanout": 24,
}


MARKER_RE = re.compile(
    r"^BENCH_V21_CALL=(raw-control|raw|fanout-control|fanout):"
    r"([A-Za-z0-9][A-Za-z0-9._-]*) BENCH_V21_OBS="
    r"([LW][0-9]{2}(?:,[LW][0-9]{2})*)(?=\s)",
    re.MULTILINE,
)


class AnalysisInvalid(ValueError):
    """The measured input violates the frozen analyzer contract."""


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate the structural population contract needed to render branches."""
    schema = manifest.get("schema")
    if (
        schema not in {"sshai-benchmark/v2.1", AMENDMENT_SCHEMA}
        or manifest.get("frozen") is not True
    ):
        raise AnalysisInvalid(
            "manifest must be frozen sshai-benchmark/v2.1 or v2.1-amendment-1"
        )
    if manifest.get("branch_order") != list(BRANCHES):
        raise AnalysisInvalid(f"branch_order must be {list(BRANCHES)!r}")
    if schema == AMENDMENT_SCHEMA:
        if manifest.get("replicates") != len(AMENDMENT_BRANCH_SCHEDULE):
            raise AnalysisInvalid("amended manifest must freeze exactly eight replicates")
        schedule = manifest.get("branch_schedule")
        if schedule != [list(row) for row in AMENDMENT_BRANCH_SCHEDULE]:
            raise AnalysisInvalid(
                "amended branch_schedule must freeze two exact balanced four-replicate cycles"
            )
        if manifest.get("decision_rule") != AMENDMENT_DECISION_RULE:
            raise AnalysisInvalid("amended decision_rule differs from the frozen contract")
    elif manifest.get("replicates") != 3:
        raise AnalysisInvalid("manifest must freeze exactly three replicates")
    if manifest.get("timeout_seconds") != 180:
        raise AnalysisInvalid("manifest timeout_seconds must be 180")

    targets = manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != 3:
        raise AnalysisInvalid("manifest must contain exactly three targets")
    target_shapes = [
        (target.get("os"), target.get("shell"))
        for target in targets
        if isinstance(target, dict)
    ]
    if target_shapes[:2] != [("linux", "bash"), ("linux", "bash")]:
        raise AnalysisInvalid("the first two targets must be Linux/bash")
    if (
        len(target_shapes) != 3
        or target_shapes[2][0] != "windows"
        or not str(target_shapes[2][1]).startswith("pwsh")
    ):
        raise AnalysisInvalid("the third target must be Windows/PowerShell 7")

    observations = manifest.get("observations")
    if not isinstance(observations, list) or len(observations) != len(OBSERVATION_IDS):
        raise AnalysisInvalid("manifest must contain exactly 36 observations")
    ids = [item.get("id") for item in observations if isinstance(item, dict)]
    if ids != list(OBSERVATION_IDS):
        raise AnalysisInvalid("observation IDs must be L01..L24,W01..W12 in order")
    aliases = [target.get("alias") for target in targets]
    qualification = manifest.get("qualification")
    if not isinstance(qualification, dict):
        raise AnalysisInvalid("manifest qualification must be an object")
    probes = qualification.get("probes")
    if not isinstance(probes, list) or len(probes) != len(targets):
        raise AnalysisInvalid("manifest must freeze one qualification probe per target")
    for target, probe in zip(targets, probes, strict=True):
        command = probe.get("command") if isinstance(probe, dict) else None
        expected_output_lines = (
            ["SSHAI_BENCH_QUAL_V21", "Linux", "shell=bash"]
            if target.get("os") == "linux"
            else ["SSHAI_BENCH_QUAL_V21", str(target.get("shell")).removeprefix("pwsh-")]
        )
        if (
            not isinstance(probe, dict)
            or probe.get("alias") != target.get("alias")
            or not isinstance(command, str)
            or not command
            or probe.get("command_sha256")
            != sha256_bytes((command + "\n").encode("utf-8"))
            or type(probe.get("expected_exit")) is not int
            or probe["expected_exit"] != 0
            or type(probe.get("max_output_bytes")) is not int
            or probe["max_output_bytes"] not in range(1, 4097)
            or probe.get("expected_output_lines") != expected_output_lines
        ):
            raise AnalysisInvalid("qualification probes are not frozen-valid")
    expected_hosts = [aliases[0]] * 12 + [aliases[1]] * 12 + [aliases[2]] * 12
    hosts = [item.get("host") for item in observations if isinstance(item, dict)]
    if hosts != expected_hosts:
        raise AnalysisInvalid("observation hosts do not match ordered target populations")
    classes = [item.get("class") for item in observations if isinstance(item, dict)]
    if classes != list(range(1, 13)) * 3:
        raise AnalysisInvalid("each target must declare observation classes 1..12 in order")
    for item in observations:
        if (
            not isinstance(item.get("body"), str)
            or not item["body"]
            or not isinstance(item.get("expected_exit"), int)
            or not isinstance(item.get("delta"), bool)
        ):
            raise AnalysisInvalid("every observation must freeze body, expected exit, and delta")
    for index in range(12):
        first = observations[index]
        second = observations[index + 12]
        comparable_fields = ("class", "body", "expected_exit", "delta", "os")
        if any(first[field] != second[field] for field in comparable_fields):
            raise AnalysisInvalid(
                f"Linux observations {first['id']} and {second['id']} are not fan-out-compatible"
            )


def branch_order_for_replicate(
    manifest: dict[str, Any], replicate: int
) -> tuple[str, ...]:
    """Return the frozen execution order for one complete replicate."""
    if replicate not in range(1, int(manifest["replicates"]) + 1):
        raise AnalysisInvalid(f"replicate must be in range 1..{manifest['replicates']}")
    if manifest.get("schema") == AMENDMENT_SCHEMA:
        return tuple(manifest["branch_schedule"][replicate - 1])
    return BRANCHES


def build_branch_calls(manifest: dict[str, Any], branch: str) -> list[dict[str, Any]]:
    """Return the exact ordered call map for one manifest branch."""
    if branch not in BRANCHES:
        raise AnalysisInvalid(f"unexpected branch {branch!r}")
    observations = manifest["observations"]
    is_fanout = branch in {"fanout-control", "fanout"}
    if not is_fanout:
        return [
            {
                "branch": branch,
                "id": str(item["id"]).lower(),
                "observations": (item["id"],),
                "hosts": (item["host"],),
                "expected_exits": (int(item["expected_exit"]),),
                "body_sha256": sha256_bytes((item["body"] + "\n").encode("utf-8")),
            }
            for item in observations
        ]

    calls: list[dict[str, Any]] = []
    for index in range(12):
        first = observations[index]
        second = observations[index + 12]
        calls.append({
            "branch": branch,
            "id": f"linux-{index + 1:02d}",
            "observations": (first["id"], second["id"]),
            "hosts": (first["host"], second["host"]),
            "expected_exits": (int(first["expected_exit"]), int(second["expected_exit"])),
            "body_sha256": sha256_bytes((first["body"] + "\n").encode("utf-8")),
        })
    for index, item in enumerate(observations[24:], 1):
        calls.append({
            "branch": branch,
            "id": f"windows-{index:02d}",
            "observations": (item["id"],),
            "hosts": (item["host"],),
            "expected_exits": (int(item["expected_exit"]),),
            "body_sha256": sha256_bytes((item["body"] + "\n").encode("utf-8")),
        })
    return calls


def _observation_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in manifest["observations"]}


def _marker(call: dict[str, Any]) -> str:
    observations = ",".join(call["observations"])
    return f"BENCH_V21_CALL={call['branch']}:{call['id']} BENCH_V21_OBS={observations}"


def body_input_path(manifest: dict[str, Any], observation_id: str) -> Path:
    return Path(str(manifest["artifact_root"])) / "inputs" / f"{observation_id}.body"


def expected_body_inputs(manifest: dict[str, Any]) -> dict[str, bytes]:
    return {
        f"{item['id']}.body": (item["body"] + "\n").encode("utf-8")
        for item in manifest["observations"]
    }


def validate_body_inputs(manifest: dict[str, Any]) -> None:
    directory = Path(str(manifest["artifact_root"])) / "inputs"
    expected = expected_body_inputs(manifest)
    if not directory.is_dir() or directory.is_symlink():
        raise AnalysisInvalid("benchmark body input directory is not a regular directory")
    actual_names = {path.name for path in directory.iterdir()}
    if actual_names != set(expected):
        raise AnalysisInvalid("benchmark body input inventory differs from the manifest")
    for name, data in expected.items():
        path = directory / name
        if not path.is_file() or path.is_symlink() or read_bytes(path, "body input") != data:
            raise AnalysisInvalid(f"benchmark body input differs from manifest: {path}")


def materialize_body_inputs(manifest: dict[str, Any]) -> None:
    directory = Path(str(manifest["artifact_root"])) / "inputs"
    expected = expected_body_inputs(manifest)
    if not directory.exists():
        directory.mkdir(mode=0o700, parents=True)
        for name, data in expected.items():
            path = directory / name
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o400)
    validate_body_inputs(manifest)


def _control_command(manifest: dict[str, Any], call: dict[str, Any]) -> str:
    executables = manifest["executables"]
    observations = _observation_by_id(manifest)
    first = observations[call["observations"][0]]
    helper_arguments = [
        executables["python"],
        executables["noop_helper"],
        "run",
        "--body-file",
        str(body_input_path(manifest, call["observations"][0])),
        "--timeout",
        str(manifest["timeout_seconds"]),
        "--result-format=json",
    ]
    if first["class"] in {8, 9}:
        helper_arguments.extend([
            "--ctx", f"benchmark-v2.1-{call['id'].split('-')[0]}"
        ])
    if first.get("delta"):
        helper_arguments.append("--delta")
    helper_arguments.extend([
        "--branch",
        call["branch"],
        "--call-id",
        call["id"],
        "--observations",
        ",".join(call["observations"]),
        "--hosts",
        ",".join(call["hosts"]),
        "--expected-exits",
        ",".join(str(value) for value in call["expected_exits"]),
    ])
    if call["branch"] == "raw-control":
        arguments = [
            executables["watchdog"], "-e", "alarm shift; exec @ARGV",
            str(manifest["timeout_seconds"]), *helper_arguments,
        ]
    else:
        arguments = helper_arguments
    return " ".join(shlex.quote(str(argument)) for argument in arguments)


def _workload_command(manifest: dict[str, Any], call: dict[str, Any]) -> str:
    executables = manifest["executables"]
    observations = _observation_by_id(manifest)
    first = observations[call["observations"][0]]
    body_path = str(body_input_path(manifest, call["observations"][0]))

    if call["branch"] == "raw":
        if first["os"] == "linux":
            remote = [executables["ssh"], call["hosts"][0], "bash", "-s"]
        else:
            remote = [
                executables["ssh"], call["hosts"][0], "pwsh",
                "-NoProfile", "-NonInteractive", "-File", "-",
            ]
        command = [
            executables["watchdog"], "-e", "alarm shift; exec @ARGV",
            str(manifest["timeout_seconds"]), *remote,
        ]
    else:
        command = [
            executables["sshai"], "run", "--body-file", body_path, "--timeout",
            str(manifest["timeout_seconds"]), "--result-format=json",
        ]
        if first["class"] in {8, 9}:
            command.extend(["--ctx", f"benchmark-v2.1-{call['id'].split('-')[0]}"])
        if first.get("delta"):
            command.append("--delta")
        command.extend(call["hosts"])
    rendered = " ".join(shlex.quote(str(argument)) for argument in command)
    if call["branch"] == "raw":
        rendered += " < " + shlex.quote(body_path)
    return rendered


def render_call(manifest: dict[str, Any], call: dict[str, Any]) -> str:
    control = str(call["branch"]).endswith("-control")
    command = _control_command(manifest, call) if control else _workload_command(manifest, call)
    return f"{_marker(call)} {command}"


def render_prompt(manifest: dict[str, Any], branch: str) -> str:
    """Render one frozen branch prompt from its manifest-declared calls."""
    validate_manifest(manifest)
    executables = manifest.get("executables")
    if not isinstance(executables, dict):
        raise AnalysisInvalid("manifest executables must be an object")
    required = {"codex", "python", "noop_helper", "sshai", "ssh", "watchdog"}
    if set(executables) != required:
        raise AnalysisInvalid(f"manifest executables must contain exactly {sorted(required)!r}")
    if any(not str(path).startswith("/") for path in executables.values()):
        raise AnalysisInvalid("every executable path must be absolute")

    rendered_calls = []
    for call in build_branch_calls(manifest, branch):
        rendered_calls.append(f"```bash\n{render_call(manifest, call)}\n```")
    return (
        f"Run frozen sshai benchmark v2.1 branch `{branch}` for aimem#743.\n\n"
        "Execute every fenced command exactly once, sequentially, in the displayed order. "
        "Do not add probes, retries, output filtering, artifact reads, or repository changes. "
        "Return only a compact completion summary after all marked calls.\n\n"
        + "\n\n".join(rendered_calls)
        + "\n"
    )


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest_bytes = path.read_bytes()
        value = json.loads(manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInvalid(f"cannot read manifest {path}: {exc}") from exc
    lock_path = path.with_name(path.name + ".sha256")
    try:
        locked_digest = lock_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise AnalysisInvalid(f"cannot read manifest lock {lock_path}: {exc}") from exc
    actual_digest = sha256_bytes(manifest_bytes)
    if locked_digest != actual_digest:
        raise AnalysisInvalid("manifest bytes differ from the frozen SHA-256 lock")
    if not isinstance(value, dict):
        raise AnalysisInvalid("manifest must contain one JSON object")
    validate_manifest(value)
    return value


def manifest_digest(path: Path) -> str:
    return sha256_bytes(read_bytes(path, "manifest"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def execution_contract_digest(manifest: dict[str, Any]) -> str:
    contract = {key: value for key, value in manifest.items() if key != "provenance"}
    encoded = json.dumps(
        contract, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256_bytes(encoded)


def canonical_json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return sha256_bytes(encoded)


def read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise AnalysisInvalid(f"cannot read {label} {path}: {exc}") from exc


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise AnalysisInvalid(f"cannot decode JSONL {path} as UTF-8: {exc}") from exc
    except OSError as exc:
        raise AnalysisInvalid(f"cannot read JSONL {path}: {exc}") from exc
    events = []
    for number, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisInvalid(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise AnalysisInvalid(f"{path}:{number}: event must be an object")
        events.append(event)
    return events


def branch_artifact_paths(
    manifest: dict[str, Any], replicate: int, branch: str
) -> dict[str, Path]:
    if replicate not in range(1, int(manifest["replicates"]) + 1):
        raise AnalysisInvalid(f"replicate must be in range 1..{manifest['replicates']}")
    if branch not in BRANCHES:
        raise AnalysisInvalid(f"unexpected branch {branch!r}")
    root = Path(str(manifest["artifact_root"]))
    if not root.is_absolute():
        raise AnalysisInvalid("artifact_root must be absolute")
    directory = root / f"replicate-{replicate:02d}"
    return {
        "prompt": directory / f"{branch}.prompt.txt",
        "result": directory / f"{branch}.jsonl",
        "stderr": directory / f"{branch}.stderr.txt",
        "metadata": directory / f"{branch}.meta.json",
    }


def _complete_branch_paths(
    manifest: dict[str, Any], replicate: int, branch: str
) -> list[Path]:
    paths = branch_artifact_paths(manifest, replicate, branch)
    directory = paths["result"].parent
    return [
        *paths.values(),
        directory / f"{branch}.rollout.jsonl",
        directory / f"{branch}.rollout.meta.json",
        directory / f"{branch}.validation.json",
    ]


def validate_branch_order(
    manifest_path: Path, manifest: dict[str, Any], replicate: int, branch: str
) -> None:
    current_order = branch_order_for_replicate(manifest, replicate)
    branch_index = current_order.index(branch)
    required: list[Path] = []
    for prior_replicate in range(1, replicate):
        for prior_branch in branch_order_for_replicate(manifest, prior_replicate):
            required.extend(_complete_branch_paths(manifest, prior_replicate, prior_branch))
    for prior_branch in current_order[:branch_index]:
        required.extend(_complete_branch_paths(manifest, replicate, prior_branch))
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise AnalysisInvalid(
            f"branch order precondition is incomplete; first missing artifact: {missing[0]}"
        )
    current_manifest_digest = manifest_digest(manifest_path)
    for prior_replicate in range(1, replicate + 1):
        prior_order = branch_order_for_replicate(manifest, prior_replicate)
        last_index = len(prior_order) if prior_replicate < replicate else branch_index
        for prior_branch in prior_order[:last_index]:
            metadata_path = branch_artifact_paths(
                manifest, prior_replicate, prior_branch
            )["metadata"]
            metadata = _load_json_object(metadata_path, "prior run metadata")
            if metadata.get("manifest_sha256") != current_manifest_digest:
                raise AnalysisInvalid(
                    f"branch order manifest drift in {metadata_path}"
                )
            validation_path = metadata_path.with_name(
                f"{prior_branch}.validation.json"
            )
            validation = _load_json_object(
                validation_path, "prior branch validation evidence"
            )
            expected_validation = {
                "schema": "sshai-benchmark-branch-validation/v2.1",
                "valid": True,
                "replicate": prior_replicate,
                "branch": prior_branch,
                "manifest_sha256": current_manifest_digest,
            }
            if any(
                validation.get(key) != value
                for key, value in expected_validation.items()
            ):
                raise AnalysisInvalid(
                    f"prior branch validation evidence is invalid: {validation_path}"
                )
            fresh_report = analyze_branch_files(
                manifest_path, prior_replicate, prior_branch
            )
            if (
                validation.get("report_sha256") != canonical_json_digest(fresh_report)
                or validation.get("report") != fresh_report
            ):
                raise AnalysisInvalid(
                    f"prior branch validation evidence is stale: {validation_path}"
                )
    future_paths = []
    for future_branch in current_order[branch_index + 1:]:
        future_paths.extend(_complete_branch_paths(manifest, replicate, future_branch))
    existing_future = [str(path) for path in future_paths if path.exists()]
    if existing_future:
        raise AnalysisInvalid(
            f"branch order drift: future artifact already exists: {existing_future[0]}"
        )


def validate_runtime_paths(manifest: dict[str, Any]) -> None:
    for name, path_value in manifest["executables"].items():
        path = Path(path_value)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise AnalysisInvalid(f"frozen executable {name} is missing or not executable: {path}")
    auth_source = Path(str(manifest["codex"]["auth_source"]))
    if (
        not auth_source.is_absolute()
        or not auth_source.is_file()
        or auth_source.is_symlink()
        or auth_source.stat().st_mode & 0o077
    ):
        raise AnalysisInvalid("frozen Codex auth source must be a private regular file")


def branch_codex_home(manifest: dict[str, Any], replicate: int, branch: str) -> Path:
    root = Path(str(manifest["codex"]["home_root"]))
    if not root.is_absolute():
        raise AnalysisInvalid("frozen Codex home root must be absolute")
    return root / f"replicate-{replicate:02d}" / branch


def run_branch(manifest_path: Path, replicate: int, branch: str) -> dict[str, Path]:
    """Run one fresh Codex branch and publish immutable local evidence."""
    manifest = load_manifest(manifest_path)
    validate_frozen_provenance(manifest)
    validate_measurement_ready(manifest)
    validate_runtime_paths(manifest)
    validate_branch_order(manifest_path, manifest, replicate, branch)
    paths = branch_artifact_paths(manifest, replicate, branch)
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing:
        raise AnalysisInvalid(
            f"refusing to overwrite existing branch artifacts: {', '.join(existing)}"
        )
    materialize_body_inputs(manifest)
    codex_home = branch_codex_home(manifest, replicate, branch)
    if codex_home.exists() or codex_home.is_symlink():
        raise AnalysisInvalid(f"fresh branch Codex home already exists: {codex_home}")
    codex_home.mkdir(mode=0o700, parents=True)
    (codex_home / "auth.json").symlink_to(manifest["codex"]["auth_source"])
    if set(path.name for path in codex_home.iterdir()) != {"auth.json"}:
        raise AnalysisInvalid("fresh branch Codex home inventory is not minimal")
    paths["prompt"].parent.mkdir(parents=True, exist_ok=True)
    prompt = render_prompt(manifest, branch)
    prompt_bytes = prompt.encode("utf-8")
    with paths["prompt"].open("xb") as stream:
        stream.write(prompt_bytes)

    codex = manifest.get("codex")
    if not isinstance(codex, dict):
        raise AnalysisInvalid("manifest codex field must be an object")
    sandbox = "workspace-write" if branch.endswith("-control") else "danger-full-access"
    command = [
        manifest["executables"]["codex"],
        "exec",
        "--ignore-user-config",
        "--json",
        "--color",
        "never",
        "--sandbox",
        sandbox,
        "--model",
        str(codex["model"]),
        "-c",
        f'model_reasoning_effort="{codex["reasoning_effort"]}"',
        "-C",
        str(manifest["repo"]),
        "-",
    ]
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    started = time.monotonic()
    with paths["result"].open("x", encoding="utf-8") as stream:
        process = subprocess.run(
            command,
            input=prompt,
            text=True,
            stdout=stream,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
    elapsed = time.monotonic() - started
    result_bytes = read_bytes(paths["result"], "branch result")
    stderr_bytes = process.stderr.encode("utf-8")
    with paths["stderr"].open("xb") as stream:
        stream.write(stderr_bytes)
    replicate_branch_order = branch_order_for_replicate(manifest, replicate)
    metadata = {
        "schema": "sshai-benchmark-run/v2.1",
        "replicate": replicate,
        "branch": branch,
        "branch_order": list(replicate_branch_order),
        "branch_index": replicate_branch_order.index(branch),
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": manifest_digest(manifest_path),
        "prompt": str(paths["prompt"].resolve()),
        "result": str(paths["result"].resolve()),
        "prompt_sha256": sha256_bytes(prompt_bytes),
        "result_sha256": sha256_bytes(result_bytes),
        "stderr_sha256": sha256_bytes(stderr_bytes),
        "stderr_bytes": len(stderr_bytes),
        "stderr_truncated": False,
        "process_exit": process.returncode,
        "sandbox": sandbox,
        "ignore_user_config": True,
        "codex_home": str(codex_home),
        "codex": {
            "path": manifest["executables"]["codex"],
            "sha256": manifest["provenance"]["codex"]["sha256"],
            "version": codex["version"],
            "model": codex["model"],
            "reasoning_effort": codex["reasoning_effort"],
        },
        "repository": manifest["provenance"]["repository"],
        "elapsed_seconds": round(elapsed, 3),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    with paths["metadata"].open("x", encoding="utf-8") as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    if process.returncode != 0:
        raise AnalysisInvalid(
            f"codex branch {branch} failed with exit {process.returncode}; see {paths['metadata']}"
        )
    return paths


def capture_persisted_rollout(
    manifest_path: Path,
    replicate: int,
    branch: str,
    sessions_root: Path,
) -> dict[str, Path]:
    """Copy the unique persisted rollout for a completed branch immutably."""
    manifest = load_manifest(manifest_path)
    validate_frozen_provenance(manifest)
    validate_measurement_ready(manifest)
    paths = branch_artifact_paths(manifest, replicate, branch)
    expected_sessions_root = branch_codex_home(
        manifest, replicate, branch
    ) / "sessions"
    if sessions_root != expected_sessions_root:
        raise AnalysisInvalid(
            f"sessions root is not the frozen branch Codex home: {sessions_root}"
        )
    if not paths["result"].is_file():
        raise AnalysisInvalid(f"branch result does not exist: {paths['result']}")
    started = _single_event(read_jsonl(paths["result"]), "thread.started")
    thread_id = started.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise AnalysisInvalid("branch result has no thread identity")

    candidates = []
    for candidate in sessions_root.rglob(f"*{thread_id}*.jsonl"):
        events = read_jsonl(candidate)
        identities = [
            event.get("payload", {}).get("id")
            for event in events
            if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict)
        ]
        if identities == [thread_id]:
            candidates.append(candidate)
    if len(candidates) != 1:
        raise AnalysisInvalid(
            f"expected one persisted rollout for thread {thread_id}, found {len(candidates)}"
        )

    directory = paths["result"].parent
    destinations = {
        "rollout": directory / f"{branch}.rollout.jsonl",
        "metadata": directory / f"{branch}.rollout.meta.json",
    }
    existing = [str(path) for path in destinations.values() if path.exists()]
    if existing:
        raise AnalysisInvalid(
            f"refusing to overwrite persisted rollout artifacts: {', '.join(existing)}"
        )
    rollout_bytes = read_bytes(candidates[0], "persisted rollout source")
    with destinations["rollout"].open("xb") as stream:
        stream.write(rollout_bytes)
    capture_metadata = {
        "schema": "sshai-benchmark-rollout/v2.1",
        "replicate": replicate,
        "branch": branch,
        "thread_id": thread_id,
        "source": str(candidates[0].resolve()),
        "rollout": str(destinations["rollout"].resolve()),
        "rollout_sha256": sha256_bytes(rollout_bytes),
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    with destinations["metadata"].open("x", encoding="utf-8") as stream:
        json.dump(capture_metadata, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return destinations


def _json_output(item: dict[str, Any], label: str) -> dict[str, Any]:
    try:
        value = json.loads(str(item.get("aggregated_output") or ""))
    except json.JSONDecodeError as exc:
        raise AnalysisInvalid(f"{label} output is not one JSON document: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisInvalid(f"{label} output must be one JSON object")
    return value


def validate_call_evidence(
    item: dict[str, Any], call: dict[str, Any]
) -> list[dict[str, Any]]:
    """Validate one marked call's consumer-visible per-observation evidence."""
    branch = call["branch"]
    if branch == "raw":
        observed_exit = item.get("exit_code")
        expected_exit = call["expected_exits"][0]
        if type(observed_exit) is not int:
            raise AnalysisInvalid(f"raw call {call['id']} has no integer exit code")
        if not str(item.get("aggregated_output") or ""):
            raise AnalysisInvalid(f"raw call {call['id']} has no observation output")
        return [{
            "observation_id": call["observations"][0],
            "host": call["hosts"][0],
            "exit": observed_exit,
            "outcome": "success" if observed_exit == expected_exit else "failed",
            "artifact_id": "",
        }]

    envelope = _json_output(item, f"{branch} call {call['id']}")
    if branch.endswith("-control"):
        if envelope.get("schema") != "sshai-benchmark-noop/v2.1":
            raise AnalysisInvalid(f"control call {call['id']} has wrong helper schema")
        if envelope.get("branch") != branch or envelope.get("call_id") != call["id"]:
            raise AnalysisInvalid(f"control call {call['id']} envelope identity mismatch")
        if envelope.get("body_sha256") != call["body_sha256"]:
            raise AnalysisInvalid(f"control call {call['id']} body digest mismatch")
        results = envelope.get("results")
    else:
        if envelope.get("schema_version") != "v1":
            raise AnalysisInvalid(f"fan-out call {call['id']} has wrong result schema")
        results = envelope.get("runs")

    if not isinstance(results, list) or len(results) != len(call["hosts"]):
        raise AnalysisInvalid(
            f"call {call['id']} host results do not cover all declared hosts"
        )
    evidence = []
    for index, (result, observation, host, expected_exit) in enumerate(
        zip(
            results,
            call["observations"],
            call["hosts"],
            call["expected_exits"],
            strict=True,
        )
    ):
        if not isinstance(result, dict):
            raise AnalysisInvalid(f"call {call['id']} result {index} must be an object")
        if branch.endswith("-control") and result.get("observation_id") != observation:
            raise AnalysisInvalid(f"control call {call['id']} observation identity mismatch")
        if result.get("host") != host or type(result.get("exit")) is not int:
            raise AnalysisInvalid(
                f"call {call['id']} host or exit shape mismatch at result {index}"
            )
        transport_error = result.get("transport_error")
        if not isinstance(transport_error, str):
            raise AnalysisInvalid(f"call {call['id']} transport error must be a string")
        artifact_id = result.get("artifact_id") if branch.endswith("-control") else result.get("id")
        if not isinstance(artifact_id, str) or not artifact_id:
            raise AnalysisInvalid(f"call {call['id']} result {index} lacks artifact identity")
        if not branch.endswith("-control") and not result.get("artifact_path"):
            raise AnalysisInvalid(f"call {call['id']} result {index} lacks artifact path")
        success = result["exit"] == expected_exit and not transport_error
        if branch.endswith("-control") and (not success or result.get("outcome") != "success"):
            raise AnalysisInvalid(
                f"control call {call['id']} result {index} is not deterministic success"
            )
        evidence.append({
            "observation_id": observation,
            "host": host,
            "exit": result["exit"],
            "outcome": "success" if success else "failed",
            "artifact_id": artifact_id,
        })
    return evidence


def _single_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    matches = [event for event in events if event.get("type") == event_type]
    if len(matches) != 1:
        raise AnalysisInvalid(f"branch must contain exactly one {event_type} event")
    return matches[0]


def analyze_branch_events(
    manifest: dict[str, Any],
    branch: str,
    events: list[dict[str, Any]],
    persisted_rollout_events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply the complete in-memory branch population and lifecycle gates."""
    validate_manifest(manifest)
    allowed_completed_items = {"command_execution", "agent_message", "reasoning"}
    for event in events:
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            item_type = item.get("type")
            if item_type not in allowed_completed_items:
                raise AnalysisInvalid(
                    f"unexpected completed item type {item_type!r} invalidates the branch"
                )
    calls = build_branch_calls(manifest, branch)
    declared_calls = {call["id"]: call["observations"] for call in calls}
    population = analyze_command_population(events, branch, declared_calls)
    if population["marked_calls"] != EXPECTED_CALL_COUNTS[branch]:
        raise AnalysisInvalid(f"branch {branch} has wrong marked-call count")
    if set(population["observations"]) != set(OBSERVATION_IDS):
        raise AnalysisInvalid(f"branch {branch} does not cover all declared observations")

    started = _single_event(events, "thread.started")
    _single_event(events, "turn.started")
    completed_turn = _single_event(events, "turn.completed")
    thread_id = started.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise AnalysisInvalid("thread.started must contain a non-empty thread_id")
    session_ids = [
        event.get("payload", {}).get("id")
        for event in persisted_rollout_events
        if event.get("type") == "session_meta" and isinstance(event.get("payload"), dict)
    ]
    if session_ids != [thread_id]:
        raise AnalysisInvalid("persisted rollout thread identity does not match Codex exec")
    for source, source_events in (
        ("Codex exec", events),
        ("persisted rollout", persisted_rollout_events),
    ):
        for event in source_events:
            event_type = event.get("type")
            if (
                isinstance(event_type, str)
                and "compact" in event_type.lower()
                and event_type != "compacted"
            ):
                raise AnalysisInvalid(
                    f"unrecognized lifecycle-like type {event_type!r} in {source}"
                )
    compactions = lifecycle_cross_check(events, persisted_rollout_events)
    if not compactions["matched"]:
        raise AnalysisInvalid("Codex exec and persisted compaction counts do not match")

    calls_by_id = {call["id"]: call for call in calls}
    expected_commands = {call["id"]: render_call(manifest, call) for call in calls}
    evidence_by_observation: dict[str, dict[str, Any]] = {}
    for event in events:
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "command_execution"
        ):
            continue
        command = command_script(str(item.get("command") or ""))
        marker = MARKER_RE.search(command)
        if marker is None:
            if branch.endswith("-control") and not is_frozen_local_setup(item, manifest):
                raise AnalysisInvalid("unmarked control command invalidates the branch")
            remote_paths = (manifest["executables"]["ssh"], manifest["executables"]["sshai"])
            if any(path in command for path in remote_paths):
                raise AnalysisInvalid("unmarked remote command invalidates the branch")
            continue
        call_id = marker.group(2)
        if command != expected_commands[call_id]:
            raise AnalysisInvalid(f"call {call_id} command differs from frozen manifest")
        for observation in validate_call_evidence(item, calls_by_id[call_id]):
            evidence_by_observation[observation["observation_id"]] = observation
    if set(evidence_by_observation) != set(OBSERVATION_IDS):
        raise AnalysisInvalid("per-host evidence does not cover all declared observations")

    usage = completed_turn.get("usage")
    if not isinstance(usage, dict):
        raise AnalysisInvalid("turn.completed must contain a usage object")
    input_tokens = usage.get("input_tokens")
    cached_tokens = usage.get("cached_input_tokens")
    if (
        type(input_tokens) is not int
        or type(cached_tokens) is not int
        or input_tokens < 0
        or cached_tokens < 0
        or cached_tokens > input_tokens
    ):
        raise AnalysisInvalid(
            "usage input_tokens and cached_input_tokens must be non-negative integers "
            "with cached_input_tokens <= input_tokens"
        )
    report_usage = dict(usage)
    report_usage["non_cached_input_tokens"] = input_tokens - cached_tokens
    return {
        **population,
        "thread_id": thread_id,
        "observations": list(OBSERVATION_IDS),
        "observation_evidence": [evidence_by_observation[item] for item in OBSERVATION_IDS],
        "usage": report_usage,
        "compactions": compactions,
    }


def _validate_file_record(label: str, record: Any) -> Path:
    if not isinstance(record, dict):
        raise AnalysisInvalid(f"{label} provenance record must be an object")
    path_value = record.get("path")
    expected_digest = record.get("sha256")
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        raise AnalysisInvalid(f"{label} provenance path must be absolute")
    path = Path(path_value)
    try:
        actual_digest = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise AnalysisInvalid(f"cannot read frozen {label} path {path}: {exc}") from exc
    if actual_digest != expected_digest:
        raise AnalysisInvalid(f"{label} digest differs from frozen provenance")
    return path


def validate_frozen_provenance(manifest: dict[str, Any]) -> None:
    """Recompute every locally stable frozen digest before a branch can run."""
    validate_manifest(manifest)
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise AnalysisInvalid("manifest provenance must be an object")
    source_revision = provenance.get("source_revision")
    if not isinstance(source_revision, str) or not source_revision:
        raise AnalysisInvalid("provenance source_revision must be non-empty")
    if provenance.get("execution_contract_sha256") != execution_contract_digest(manifest):
        raise AnalysisInvalid("execution contract differs from frozen provenance")
    files = provenance.get("files")
    expected_files = {
        "runner", "helper", "protocol", "analyzer_definition", "tests",
    }
    if not isinstance(files, dict) or set(files) != expected_files:
        raise AnalysisInvalid(
            "provenance files must freeze runner, helper, protocol, analyzer definition, and tests"
        )
    runner_path = _validate_file_record("runner", files["runner"])
    helper_path = _validate_file_record("helper", files["helper"])
    protocol_path = _validate_file_record("protocol", files["protocol"])
    analyzer_path = _validate_file_record(
        "analyzer definition", files["analyzer_definition"]
    )
    tests_path = _validate_file_record("tests", files["tests"])
    if runner_path.resolve() != Path(__file__).resolve():
        raise AnalysisInvalid("runner path does not identify the executing analyzer")
    if helper_path.resolve() != Path(manifest["executables"]["noop_helper"]).resolve():
        raise AnalysisInvalid("helper path does not match manifest executable")
    repository_root = Path(str(manifest["repo"])).resolve()
    expected_repository_files = {
        protocol_path.resolve(): repository_root / "docs/benchmarks/v2.1-protocol.md",
        analyzer_path.resolve(): repository_root / "docs/benchmarks/v2.1-analyzer.md",
        tests_path.resolve(): repository_root / "scripts/test_benchmark_v2_1.py",
    }
    if any(actual != expected.resolve() for actual, expected in expected_repository_files.items()):
        raise AnalysisInvalid("protocol, analyzer definition, or tests path is not canonical")

    sshai = provenance.get("sshai")
    sshai_path = _validate_file_record("sshai", sshai)
    if sshai_path.resolve() != Path(manifest["executables"]["sshai"]).resolve():
        raise AnalysisInvalid("sshai path does not match manifest executable")
    if (
        sshai.get("vcs_revision") != source_revision
        or sshai.get("vcs_modified") is not False
        or sshai.get("smoke_ok") is not True
    ):
        raise AnalysisInvalid("sshai build provenance or no-SSH smoke is not frozen-valid")

    codex_provenance = provenance.get("codex")
    codex_path = _validate_file_record("codex", codex_provenance)
    if codex_path.resolve() != Path(manifest["executables"]["codex"]).resolve():
        raise AnalysisInvalid("codex path does not match manifest executable")
    codex = manifest.get("codex")
    if (
        not isinstance(codex, dict)
        or codex_provenance.get("version") != codex.get("version")
        or not isinstance(codex.get("model"), str)
        or not codex["model"]
        or codex.get("reasoning_effort")
        not in {"low", "medium", "high", "xhigh", "max", "ultra"}
        or not isinstance(codex.get("home_root"), str)
        or not Path(codex["home_root"]).is_absolute()
        or not isinstance(codex.get("auth_source"), str)
        or not Path(codex["auth_source"]).is_absolute()
    ):
        raise AnalysisInvalid("codex version, model, or reasoning effort is not frozen-valid")

    for category in ("instruction_files", "config_files"):
        records = provenance.get(category)
        if not isinstance(records, list):
            raise AnalysisInvalid(f"provenance {category} must be a list")
        for index, record in enumerate(records):
            _validate_file_record(f"{category}[{index}]", record)
    repository = provenance.get("repository")
    if (
        not isinstance(repository, dict)
        or not isinstance(repository.get("head"), str)
        or not isinstance(repository.get("upstream"), str)
        or not isinstance(repository.get("clean"), bool)
    ):
        raise AnalysisInvalid("repository provenance is incomplete")

    prompt_digests = provenance.get("prompt_sha256")
    if not isinstance(prompt_digests, dict) or set(prompt_digests) != set(BRANCHES):
        raise AnalysisInvalid("provenance must freeze all four prompt digests")
    for branch in BRANCHES:
        actual = sha256_bytes(render_prompt(manifest, branch).encode("utf-8"))
        if prompt_digests.get(branch) != actual:
            raise AnalysisInvalid(f"{branch} prompt digest differs from frozen provenance")


def validate_measurement_ready(manifest: dict[str, Any]) -> None:
    qualification = manifest.get("qualification")
    if not isinstance(qualification, dict) or qualification.get("status") != "passed":
        raise AnalysisInvalid("target qualification is not passed")
    evidence_path = qualification.get("evidence_path")
    if not isinstance(evidence_path, str) or not Path(evidence_path).is_absolute():
        raise AnalysisInvalid("target qualification evidence path must be absolute")
    try:
        evidence_bytes = Path(evidence_path).read_bytes()
    except OSError as exc:
        raise AnalysisInvalid(f"cannot read target qualification evidence: {exc}") from exc
    evidence_digest = sha256_bytes(evidence_bytes)
    if evidence_digest != qualification.get("evidence_sha256"):
        raise AnalysisInvalid("target qualification evidence digest mismatch")
    try:
        evidence = json.loads(evidence_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInvalid(
            f"target qualification evidence is not valid JSON: {exc}"
        ) from exc
    targets = manifest["targets"]
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema") != "sshai-benchmark-qualification/v2.1"
        or evidence.get("ok") is not True
        or evidence.get("target_order")
        != [target["alias"] for target in targets]
    ):
        raise AnalysisInvalid("target qualification evidence header is invalid")
    results = evidence.get("results")
    if not isinstance(results, list) or len(results) != len(targets):
        raise AnalysisInvalid("target qualification evidence results are incomplete")
    digest_re = re.compile(r"[0-9a-f]{64}")
    for target, frozen_probe, result in zip(
        targets, qualification["probes"], results, strict=True
    ):
        if (
            not isinstance(result, dict)
            or any(result.get(field) != target[field] for field in ("alias", "os", "shell"))
            or result.get("readonly") is not True
        ):
            raise AnalysisInvalid(
                "target qualification evidence target identity is invalid"
            )
        probe = result.get("probe")
        output_path_value = probe.get("output_path") if isinstance(probe, dict) else None
        if (
            not isinstance(probe, dict)
            or type(probe.get("exit")) is not int
            or probe["exit"] != frozen_probe["expected_exit"]
            or not isinstance(probe.get("command_sha256"), str)
            or digest_re.fullmatch(probe["command_sha256"]) is None
            or not isinstance(output_path_value, str)
            or not Path(output_path_value).is_absolute()
            or not isinstance(probe.get("output_sha256"), str)
            or digest_re.fullmatch(probe["output_sha256"]) is None
            or type(probe.get("output_bytes")) is not int
            or probe["output_bytes"] < 0
            or probe["output_bytes"] > frozen_probe["max_output_bytes"]
        ):
            raise AnalysisInvalid("target qualification evidence probe is invalid")
        if probe["command_sha256"] != frozen_probe["command_sha256"]:
            raise AnalysisInvalid(
                "target qualification evidence differs from frozen command"
            )
        try:
            output_bytes = Path(output_path_value).read_bytes()
        except OSError as exc:
            raise AnalysisInvalid(
                f"cannot read target qualification captured output: {exc}"
            ) from exc
        if (
            len(output_bytes) != probe["output_bytes"]
            or sha256_bytes(output_bytes) != probe["output_sha256"]
        ):
            raise AnalysisInvalid(
                "target qualification captured output differs from evidence"
            )
        try:
            output_lines = output_bytes.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            raise AnalysisInvalid(
                f"target qualification captured output is not UTF-8: {exc}"
            ) from exc
        if output_lines != frozen_probe["expected_output_lines"]:
            raise AnalysisInvalid(
                "target qualification captured output does not match expected output"
            )
    repository = manifest["provenance"]["repository"]
    if repository["clean"] is not True or repository["head"] != repository["upstream"]:
        raise AnalysisInvalid(
            "measurement requires frozen clean repository provenance with HEAD equal to upstream"
        )
    validate_repository_state(manifest)


def validate_repository_state(manifest: dict[str, Any]) -> None:
    repo = str(manifest["repo"])

    def git(*args: str) -> str:
        process = subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=False
        )
        if process.returncode != 0:
            raise AnalysisInvalid(
                f"cannot verify repository state with git {' '.join(args)}: "
                f"{process.stderr.strip()}"
            )
        return process.stdout.strip()

    current = {
        "head": git("rev-parse", "HEAD"),
        "upstream": git("rev-parse", "@{upstream}"),
        "clean": not bool(git("status", "--porcelain")),
    }
    if current != manifest["provenance"]["repository"]:
        raise AnalysisInvalid(
            f"repository state differs from frozen provenance: current={current!r}"
        )


def percentile_nearest_rank(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def token_summary(values: list[int]) -> dict[str, int]:
    return {
        "p95": percentile_nearest_rank(values, 0.95),
        "max": max(values, default=0),
        "sum": sum(values),
    }


def estimated_tokens(output: str) -> int:
    return (len(output.encode("utf-8")) + 3) // 4


def command_script(command: str) -> str:
    """Return the submitted script from Codex's fixed local shell envelopes."""
    try:
        arguments = shlex.split(command)
    except ValueError:
        return command
    if len(arguments) == 3 and arguments[:2] in (
        ["/bin/zsh", "-lc"],
        ["/bin/zsh", "-c"],
        ["/bin/bash", "-c"],
    ):
        return arguments[2]
    return command


def is_frozen_local_setup(item: dict[str, Any], manifest: dict[str, Any]) -> bool:
    script = command_script(str(item.get("command") or ""))
    try:
        arguments = shlex.split(script)
    except ValueError:
        return False
    records = manifest.get("provenance", {}).get("instruction_files", [])
    for record in records:
        path = str(record.get("path") or "") if isinstance(record, dict) else ""
        if arguments not in (["cat", path], ["sed", "-n", "1,240p", path]):
            continue
        output = str(item.get("aggregated_output") or "").encode("utf-8")
        return (
            type(item.get("exit_code")) is int
            and item["exit_code"] == 0
            and sha256_bytes(output) == record.get("sha256")
        )
    return False


def analyze_command_population(
    events: list[dict[str, Any]],
    branch: str,
    declared_calls: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    """Analyze only completed command items and fail closed on marker drift."""
    seen: set[str] = set()
    observed_call_ids: list[str] = []
    observations: list[str] = []
    all_tokens: list[int] = []
    marked_tokens: list[int] = []
    unmarked_calls = 0

    for event in events:
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or item.get("type") != "command_execution"
        ):
            continue
        command = command_script(str(item.get("command") or ""))
        output = str(item.get("aggregated_output") or "")
        output_tokens = estimated_tokens(output)
        all_tokens.append(output_tokens)
        matches = list(MARKER_RE.finditer(command))
        if not matches:
            if "BENCH_V21_" in command:
                raise AnalysisInvalid("malformed v2.1 command marker")
            unmarked_calls += 1
            continue
        if len(matches) != 1:
            raise AnalysisInvalid("command contains multiple v2.1 markers")
        marker_branch, call_id, observation_text = matches[0].groups()
        if marker_branch != branch:
            raise AnalysisInvalid(
                f"marker branch {marker_branch!r} does not match analyzed branch {branch!r}"
            )
        if call_id not in declared_calls:
            raise AnalysisInvalid(f"unexpected call ID {call_id!r}")
        if call_id in seen:
            raise AnalysisInvalid(f"duplicate call ID {call_id!r}")
        declared_observations = declared_calls[call_id]
        observed = tuple(observation_text.split(","))
        if observed != declared_observations:
            raise AnalysisInvalid(
                f"call {call_id!r} observations {observed!r} do not match "
                f"manifest {declared_observations!r}"
            )
        seen.add(call_id)
        observed_call_ids.append(call_id)
        observations.extend(observed)
        marked_tokens.append(output_tokens)

    missing = sorted(set(declared_calls) - seen)
    if missing:
        raise AnalysisInvalid(f"missing declared call IDs: {', '.join(missing)}")
    if observed_call_ids != list(declared_calls):
        raise AnalysisInvalid("marked call order differs from the frozen manifest")
    if len(observations) != len(set(observations)):
        raise AnalysisInvalid("duplicate declared observation assignment")
    return {
        "all_command_calls": len(all_tokens),
        "marked_calls": len(marked_tokens),
        "unmarked_calls": unmarked_calls,
        "observations": observations,
        "all_tool_response_est_tokens": token_summary(all_tokens),
        "marked_tool_response_est_tokens": token_summary(marked_tokens),
    }


def lifecycle_cross_check(
    codex_exec_events: list[dict[str, Any]],
    persisted_rollout_events: list[dict[str, Any]],
) -> dict[str, int | bool]:
    codex_exec = sum(1 for event in codex_exec_events if event.get("type") == "compacted")
    persisted = sum(
        1 for event in persisted_rollout_events if event.get("type") == "compacted"
    )
    return {
        "codex_exec": codex_exec,
        "persisted_rollout": persisted,
        "matched": codex_exec == persisted,
    }


def control_adjusted(
    raw: int,
    raw_control: int,
    fanout: int,
    fanout_control: int,
) -> dict[str, int | float | str | None]:
    raw_margin = raw - raw_control
    fanout_margin = fanout - fanout_control
    if raw_margin <= 0:
        status = "indistinguishable-from-control-floor"
        reduction = None
    elif fanout_margin <= 0:
        status = "indistinguishable-from-control-floor"
        reduction = None
    else:
        status = "defined"
        reduction = (raw_margin - fanout_margin) / raw_margin
    return {
        "raw_margin": raw_margin,
        "fanout_margin": fanout_margin,
        "reduction": reduction,
        "status": status,
    }


def _success_rate(branch: dict[str, Any]) -> float:
    evidence = branch["observation_evidence"]
    return sum(item.get("outcome") == "success" for item in evidence) / len(OBSERVATION_IDS)


def decide_replicates(
    replicates: list[dict[str, dict[str, Any]]],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the frozen base or amended decision rule to validated branches."""
    amended = manifest is not None and manifest.get("schema") == AMENDMENT_SCHEMA
    if manifest is not None:
        validate_manifest(manifest)
    required_replicates = int(manifest["replicates"]) if manifest is not None else 3
    if len(replicates) != required_replicates:
        raise AnalysisInvalid(
            f"the decision requires exactly {required_replicates} complete replicates"
        )
    decision_rule = (
        manifest["decision_rule"] if amended else {
            "minimum_defined_reductions": 3,
            "maximum_control_floor_pairs": 0,
            "median_reduction_threshold": 0.80,
        }
    )
    replicate_reports = []
    reductions: list[float] = []
    acceptance = {
        "exact_call_populations": True,
        "complete_observation_coverage": True,
        "fanout_success_ge_raw": True,
        "fanout_p95_lt_500": True,
        "zero_compactions": True,
    }
    for branches in replicates:
        if set(branches) != set(BRANCHES):
            raise AnalysisInvalid("each replicate must contain all four branches")
        counts = {name: branches[name]["marked_calls"] for name in BRANCHES}
        acceptance["exact_call_populations"] &= counts == EXPECTED_CALL_COUNTS
        acceptance["complete_observation_coverage"] &= all(
            branch["observations"] == list(OBSERVATION_IDS) for branch in branches.values()
        )
        raw_success = _success_rate(branches["raw"])
        fanout_success = _success_rate(branches["fanout"])
        acceptance["fanout_success_ge_raw"] &= fanout_success >= raw_success
        acceptance["fanout_p95_lt_500"] &= (
            branches["fanout"]["marked_tool_response_est_tokens"]["p95"] < 500
        )
        acceptance["zero_compactions"] &= all(
            branch["compactions"]["codex_exec"] == 0
            and branch["compactions"]["persisted_rollout"] == 0
            and branch["compactions"]["matched"] is True
            for branch in branches.values()
        )
        adjusted = {}
        for field in ("input_tokens", "cached_input_tokens", "non_cached_input_tokens"):
            values = {
                name: int(branches[name]["usage"].get(field, 0))
                for name in BRANCHES
            }
            adjusted[field] = control_adjusted(
                values["raw"],
                values["raw-control"],
                values["fanout"],
                values["fanout-control"],
            )
        input_adjusted = adjusted["input_tokens"]
        if input_adjusted["status"] == "defined":
            reductions.append(float(input_adjusted["reduction"]))
        replicate_reports.append({
            **adjusted,
            "raw_success_rate": raw_success,
            "fanout_success_rate": fanout_success,
        })

    defined_reductions = len(reductions)
    control_floor_pairs = required_replicates - defined_reductions
    sufficient_defined = (
        defined_reductions >= decision_rule["minimum_defined_reductions"]
    )
    control_floor_within_cap = (
        control_floor_pairs <= decision_rule["maximum_control_floor_pairs"]
    )
    if amended:
        median_reduction = statistics.median(reductions) if reductions else None
        acceptance["sufficient_defined_reductions"] = sufficient_defined
        acceptance["control_floor_pairs_within_cap"] = control_floor_within_cap
    else:
        median_reduction = (
            statistics.median(reductions)
            if defined_reductions == required_replicates
            else None
        )
    acceptance["median_control_adjusted_reduction_ge_80pct"] = (
        median_reduction is not None
        and median_reduction >= decision_rule["median_reduction_threshold"]
    )
    if not sufficient_defined or not control_floor_within_cap:
        decision = "inconclusive"
    elif all(acceptance.values()):
        decision = "confirmed"
    else:
        decision = "needs-work"
    return {
        "schema": (
            AMENDMENT_ANALYSIS_SCHEMA if amended else "sshai-benchmark-analysis/v2.1"
        ),
        "valid": True,
        "decision": decision,
        "call_reduction": (
            EXPECTED_CALL_COUNTS["raw"] - EXPECTED_CALL_COUNTS["fanout"]
        ) / EXPECTED_CALL_COUNTS["raw"],
        "median_control_adjusted_input_reduction": median_reduction,
        **(
            {
                "sampling": {
                    "replicates": required_replicates,
                    "defined_reductions": defined_reductions,
                    "control_floor_pairs": control_floor_pairs,
                    "minimum_defined_reductions": decision_rule[
                        "minimum_defined_reductions"
                    ],
                    "maximum_control_floor_pairs": decision_rule[
                        "maximum_control_floor_pairs"
                    ],
                },
            }
            if amended
            else {}
        ),
        "acceptance": acceptance,
        "replicates": replicate_reports,
    }


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalysisInvalid(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AnalysisInvalid(f"{label} must contain one JSON object")
    return value


def analyze_branch_files(
    manifest_path: Path, replicate: int, branch: str
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    validate_frozen_provenance(manifest)
    validate_measurement_ready(manifest)
    validate_body_inputs(manifest)
    paths = branch_artifact_paths(manifest, replicate, branch)
    rollout = paths["result"].parent / f"{branch}.rollout.jsonl"
    rollout_meta_path = paths["result"].parent / f"{branch}.rollout.meta.json"
    metadata_bytes = read_bytes(paths["metadata"], "run metadata")
    rollout_metadata_bytes = read_bytes(rollout_meta_path, "rollout metadata")
    metadata = _load_json_object(paths["metadata"], "run metadata")
    rollout_metadata = _load_json_object(rollout_meta_path, "rollout metadata")
    expected_identity = {
        "schema": "sshai-benchmark-run/v2.1",
        "replicate": replicate,
        "branch": branch,
        "branch_order": list(branch_order_for_replicate(manifest, replicate)),
        "branch_index": branch_order_for_replicate(manifest, replicate).index(branch),
        "process_exit": 0,
        "manifest_sha256": manifest_digest(manifest_path),
        "sandbox": "workspace-write" if branch.endswith("-control") else "danger-full-access",
        "ignore_user_config": True,
        "codex_home": str(branch_codex_home(manifest, replicate, branch)),
        "codex": {
            "path": manifest["executables"]["codex"],
            "sha256": manifest["provenance"]["codex"]["sha256"],
            "version": manifest["codex"]["version"],
            "model": manifest["codex"]["model"],
            "reasoning_effort": manifest["codex"]["reasoning_effort"],
        },
        "repository": manifest["provenance"]["repository"],
    }
    if any(metadata.get(key) != value for key, value in expected_identity.items()):
        raise AnalysisInvalid(f"run metadata identity or process exit mismatch for {branch}")
    if (
        not isinstance(metadata.get("elapsed_seconds"), (int, float))
        or metadata["elapsed_seconds"] < 0
    ):
        raise AnalysisInvalid(f"run metadata elapsed time is invalid for {branch}")
    if (
        rollout_metadata.get("schema") != "sshai-benchmark-rollout/v2.1"
        or rollout_metadata.get("replicate") != replicate
        or rollout_metadata.get("branch") != branch
    ):
        raise AnalysisInvalid(f"rollout metadata identity mismatch for {branch}")
    prompt_bytes = read_bytes(paths["prompt"], "branch prompt")
    result_bytes = read_bytes(paths["result"], "branch result")
    stderr_bytes = read_bytes(paths["stderr"], "branch stderr")
    rollout_bytes = read_bytes(rollout, "captured persisted rollout")
    if metadata.get("prompt_sha256") != sha256_bytes(prompt_bytes):
        raise AnalysisInvalid(f"{branch} prompt bytes differ from run metadata")
    if metadata.get("result_sha256") != sha256_bytes(result_bytes):
        raise AnalysisInvalid(f"{branch} result bytes differ from run metadata")
    if (
        metadata.get("stderr_sha256") != sha256_bytes(stderr_bytes)
        or metadata.get("stderr_bytes") != len(stderr_bytes)
        or metadata.get("stderr_truncated") is not False
    ):
        raise AnalysisInvalid(f"{branch} stderr coverage differs from run metadata")
    if rollout_metadata.get("rollout_sha256") != sha256_bytes(rollout_bytes):
        raise AnalysisInvalid(f"{branch} rollout bytes differ from capture metadata")
    if metadata["prompt_sha256"] != manifest["provenance"]["prompt_sha256"][branch]:
        raise AnalysisInvalid(f"{branch} prompt digest differs from frozen manifest")
    report = analyze_branch_events(
        manifest,
        branch,
        read_jsonl(paths["result"]),
        read_jsonl(rollout),
    )
    if rollout_metadata.get("thread_id") != report["thread_id"]:
        raise AnalysisInvalid(f"rollout metadata thread identity mismatch for {branch}")
    report["files"] = {
        "manifest": {
            "path": str(manifest_path.resolve()),
            "sha256": manifest_digest(manifest_path),
        },
        "manifest_lock": {
            "path": str(manifest_path.with_name(manifest_path.name + ".sha256").resolve()),
            "sha256": sha256_bytes(
                read_bytes(
                    manifest_path.with_name(manifest_path.name + ".sha256"),
                    "manifest digest lock",
                )
            ),
        },
        "prompt": {"path": str(paths["prompt"]), "sha256": sha256_bytes(prompt_bytes)},
        "result": {"path": str(paths["result"]), "sha256": sha256_bytes(result_bytes)},
        "stderr": {"path": str(paths["stderr"]), "sha256": sha256_bytes(stderr_bytes)},
        "run_metadata": {
            "path": str(paths["metadata"]),
            "sha256": sha256_bytes(metadata_bytes),
        },
        "rollout": {"path": str(rollout), "sha256": sha256_bytes(rollout_bytes)},
        "rollout_metadata": {
            "path": str(rollout_meta_path),
            "sha256": sha256_bytes(rollout_metadata_bytes),
        },
        "body_inputs": [
            {
                "path": str(body_input_path(manifest, item["id"])),
                "sha256": sha256_bytes(
                    body_input_path(manifest, item["id"]).read_bytes()
                ),
            }
            for item in manifest["observations"]
        ],
    }
    report["elapsed_seconds"] = metadata.get("elapsed_seconds")
    report["stderr"] = {
        "bytes": len(stderr_bytes),
        "sha256": sha256_bytes(stderr_bytes),
        "truncated": False,
    }
    report["retries"] = 0
    return report


def validate_branch_artifacts(
    manifest_path: Path, replicate: int, branch: str
) -> Path:
    manifest = load_manifest(manifest_path)
    paths = branch_artifact_paths(manifest, replicate, branch)
    output = paths["result"].parent / f"{branch}.validation.json"
    report = analyze_branch_files(manifest_path, replicate, branch)
    validation = {
        "schema": "sshai-benchmark-branch-validation/v2.1",
        "valid": True,
        "replicate": replicate,
        "branch": branch,
        "manifest_sha256": manifest_digest(manifest_path),
        "report_sha256": canonical_json_digest(report),
        "validated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "report": report,
    }
    _write_json_exclusive(output, validation)
    return output


def available_branch_file_evidence(
    manifest_path: Path, manifest: dict[str, Any], replicate: int, branch: str
) -> dict[str, dict[str, Any]]:
    paths = branch_artifact_paths(manifest, replicate, branch)
    directory = paths["result"].parent
    candidates = {
        "manifest": manifest_path,
        "manifest_lock": manifest_path.with_name(manifest_path.name + ".sha256"),
        **paths,
        "rollout": directory / f"{branch}.rollout.jsonl",
        "rollout_metadata": directory / f"{branch}.rollout.meta.json",
        "validation": directory / f"{branch}.validation.json",
    }
    evidence = {}
    for name, path in candidates.items():
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        evidence[name] = {
            "path": str(path.resolve()),
            "bytes": len(data),
            "sha256": sha256_bytes(data),
        }
    return evidence


def analyze_all(manifest_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    replicates = []
    reasons = []
    for replicate in range(1, int(manifest["replicates"]) + 1):
        branches = {}
        for branch in BRANCHES:
            try:
                branches[branch] = analyze_branch_files(
                    manifest_path, replicate, branch
                )
            except AnalysisInvalid as exc:
                reason = f"replicate {replicate} {branch}: {exc}"
                reasons.append(reason)
                branches[branch] = {
                    "valid": False,
                    "reason": str(exc),
                    "available_files": available_branch_file_evidence(
                        manifest_path, manifest, replicate, branch
                    ),
                }
        replicates.append(branches)
    if reasons:
        return {
            "schema": (
                AMENDMENT_ANALYSIS_SCHEMA
                if manifest.get("schema") == AMENDMENT_SCHEMA
                else "sshai-benchmark-analysis/v2.1"
            ),
            "valid": False,
            "decision": "invalid",
            "manifest": str(manifest_path.resolve()),
            "manifest_sha256": manifest_digest(manifest_path),
            "reasons": reasons,
            "branches": replicates,
        }
    report = decide_replicates(replicates, manifest)
    report["manifest"] = str(manifest_path.resolve())
    report["branches"] = replicates
    return report


def _write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise AnalysisInvalid(f"refusing to overwrite existing output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--manifest", type=Path, required=True)

    prompt_parser = subparsers.add_parser("prompt")
    prompt_parser.add_argument("--manifest", type=Path, required=True)
    prompt_parser.add_argument("--branch", choices=BRANCHES, required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--manifest", type=Path, required=True)
    run_parser.add_argument("--replicate", type=int, required=True)
    run_parser.add_argument("--branch", choices=BRANCHES, required=True)

    capture_parser = subparsers.add_parser("capture-rollout")
    capture_parser.add_argument("--manifest", type=Path, required=True)
    capture_parser.add_argument("--replicate", type=int, required=True)
    capture_parser.add_argument("--branch", choices=BRANCHES, required=True)
    capture_parser.add_argument("--sessions-root", type=Path, required=True)

    branch_parser = subparsers.add_parser("validate-branch")
    branch_parser.add_argument("--manifest", type=Path, required=True)
    branch_parser.add_argument("--replicate", type=int, required=True)
    branch_parser.add_argument("--branch", choices=BRANCHES, required=True)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--manifest", type=Path, required=True)
    analyze_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    try:
        if args.command == "validate":
            manifest = load_manifest(args.manifest)
            validate_frozen_provenance(manifest)
            print(json.dumps({
                "schema": "sshai-benchmark-validation/v2.1",
                "ok": True,
            }, separators=(",", ":"), sort_keys=True))
        elif args.command == "prompt":
            manifest = load_manifest(args.manifest)
            validate_frozen_provenance(manifest)
            sys.stdout.write(render_prompt(manifest, args.branch))
        elif args.command == "run":
            run_branch(args.manifest, args.replicate, args.branch)
        elif args.command == "capture-rollout":
            capture_persisted_rollout(
                args.manifest, args.replicate, args.branch, args.sessions_root
            )
        elif args.command == "validate-branch":
            validate_branch_artifacts(args.manifest, args.replicate, args.branch)
        else:
            _write_json_exclusive(args.output, analyze_all(args.manifest))
    except AnalysisInvalid as exc:
        print(f"benchmark v2.1: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
