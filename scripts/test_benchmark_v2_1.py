#!/usr/bin/env python3
"""Negative-control tests for the frozen v2.1 analyzer definition."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("benchmark_v2_1.py")
assert MODULE_PATH.exists(), "benchmark_v2_1.py must implement the frozen analyzer definition"
SPEC = importlib.util.spec_from_file_location("benchmark_v2_1", MODULE_PATH)
assert SPEC and SPEC.loader
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)


AMENDED_BRANCH_SCHEDULE = [
    ["raw-control", "raw", "fanout-control", "fanout"],
    ["raw", "raw-control", "fanout", "fanout-control"],
    ["fanout-control", "fanout", "raw-control", "raw"],
    ["fanout", "fanout-control", "raw", "raw-control"],
] * 2


def completed_command(command: str, output: str, exit_code: int = 0) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": command,
            "aggregated_output": output,
            "exit_code": exit_code,
        },
    }


def assert_invalid(events: list[dict[str, object]], expected: str) -> None:
    try:
        BENCH.analyze_command_population(
            events,
            "fanout",
            {"linux-01": ("L01", "L13")},
        )
    except BENCH.AnalysisInvalid as exc:
        assert expected in str(exc), str(exc)
    else:
        raise AssertionError(f"expected AnalysisInvalid containing {expected!r}")


def manifest_fixture(root: Path, codex_path: str = "/opt/benchmark/bin/codex") -> dict[str, object]:
    auth_source = root / "benchmark-auth.json"
    if not auth_source.exists():
        auth_source.write_text("test-auth\n", encoding="utf-8")
        auth_source.chmod(0o600)
    targets = [
        {"alias": "linux-a", "os": "linux", "shell": "bash"},
        {"alias": "linux-b", "os": "linux", "shell": "bash"},
        {"alias": "windows-a", "os": "windows", "shell": "pwsh-7"},
    ]
    qualification_probes = []
    qualification_results = []
    for index, target in enumerate(targets):
        command = (
            "printf 'SSHAI_BENCH_QUAL_V21\\n'; uname -s; printf 'shell=bash\\n'"
            if target["os"] == "linux"
            else "[Console]::Out.WriteLine('SSHAI_BENCH_QUAL_V21'); "
            "$PSVersionTable.PSVersion.ToString()"
        )
        output_path = root / f"qualification-{index}.stdout"
        expected_output_lines = (
            ["SSHAI_BENCH_QUAL_V21", "Linux", "shell=bash"]
            if target["os"] == "linux"
            else ["SSHAI_BENCH_QUAL_V21", target["shell"].removeprefix("pwsh-")]
        )
        output_bytes = ("\n".join(expected_output_lines) + "\n").encode()
        output_path.write_bytes(output_bytes)
        qualification_probes.append({
            "alias": target["alias"],
            "command": command,
            "command_sha256": hashlib.sha256((command + "\n").encode()).hexdigest(),
            "expected_exit": 0,
            "max_output_bytes": 4096,
            "expected_output_lines": expected_output_lines,
        })
        qualification_results.append({
            **target,
            "readonly": True,
            "probe": {
                "command_sha256": qualification_probes[-1]["command_sha256"],
                "exit": 0,
                "output_path": str(output_path),
                "output_sha256": hashlib.sha256(output_bytes).hexdigest(),
                "output_bytes": len(output_bytes),
            },
        })
    qualification = {
        "schema": "sshai-benchmark-qualification/v2.1",
        "ok": True,
        "target_order": [target["alias"] for target in targets],
        "results": qualification_results,
    }
    qualification_path = root / "qualification.json"
    qualification_bytes = (json.dumps(qualification) + "\n").encode()
    qualification_path.write_bytes(qualification_bytes)
    observations = []
    for target_index, target in enumerate(targets):
        prefix = "L" if target["os"] == "linux" else "W"
        start = target_index * 12 + 1 if prefix == "L" else 1
        for offset in range(12):
            observations.append({
                "id": f"{prefix}{start + offset:02d}",
                "class": offset + 1,
                "host": target["alias"],
                "os": target["os"],
                "body": f"printf observation-{offset + 1:02d}",
                "expected_exit": 7 if offset == 10 else 0,
                "delta": offset == 8,
            })
    return {
        "schema": "sshai-benchmark/v2.1",
        "frozen": True,
        "repo": str(root),
        "replicates": 3,
        "branch_order": ["raw-control", "raw", "fanout-control", "fanout"],
        "timeout_seconds": 180,
        "artifact_root": str(root / "artifacts"),
        "executables": {
            "python": sys.executable,
            "noop_helper": str(MODULE_PATH.with_name("benchmark_v2_1_noop.py")),
            "sshai": "/opt/benchmark/bin/sshai",
            "ssh": "/usr/bin/ssh",
            "watchdog": "/usr/bin/perl",
            "codex": codex_path,
        },
        "codex": {
            "version": "test-version",
            "model": "test-model",
            "reasoning_effort": "high",
            "home_root": str(root / "runtime-homes"),
            "auth_source": str(auth_source),
        },
        "qualification": {
            "status": "passed",
            "probes": qualification_probes,
            "evidence_path": str(qualification_path),
            "evidence_sha256": hashlib.sha256(qualification_bytes).hexdigest(),
        },
        "targets": targets,
        "observations": observations,
    }


def amended_manifest_fixture(
    root: Path, codex_path: str = "/opt/benchmark/bin/codex"
) -> dict[str, object]:
    manifest = manifest_fixture(root, codex_path)
    manifest["schema"] = "sshai-benchmark/v2.1-amendment-1"
    manifest["replicates"] = 8
    manifest["branch_schedule"] = [list(row) for row in AMENDED_BRANCH_SCHEDULE]
    manifest["decision_rule"] = {
        "minimum_defined_reductions": 6,
        "maximum_control_floor_pairs": 2,
        "median_reduction_threshold": 0.80,
    }
    return manifest


def amendment_2_manifest_fixture(
    root: Path, codex_path: str = "/opt/benchmark/bin/codex"
) -> dict[str, object]:
    manifest = amended_manifest_fixture(root, codex_path)
    manifest["schema"] = "sshai-benchmark/v2.1-amendment-2"
    return manifest


def attach_frozen_provenance(manifest: dict[str, object], sshai: Path) -> None:
    manifest["executables"]["sshai"] = str(sshai)
    fixture_root = Path(manifest["repo"])
    fixture_docs = fixture_root / "docs" / "benchmarks"
    fixture_scripts = fixture_root / "scripts"
    fixture_docs.mkdir(parents=True, exist_ok=True)
    fixture_scripts.mkdir(parents=True, exist_ok=True)
    fixture_protocol = fixture_docs / "v2.1-protocol.md"
    fixture_analyzer = fixture_docs / "v2.1-analyzer.md"
    fixture_tests = fixture_scripts / "test_benchmark_v2_1.py"
    fixture_protocol.write_bytes(
        (MODULE_PATH.parent.parent / "docs/benchmarks/v2.1-protocol.md").read_bytes()
    )
    fixture_analyzer.write_bytes(
        (MODULE_PATH.parent.parent / "docs/benchmarks/v2.1-analyzer.md").read_bytes()
    )
    fixture_tests.write_bytes(MODULE_PATH.with_name("test_benchmark_v2_1.py").read_bytes())

    def record(path: Path) -> dict[str, str]:
        return {
            "path": str(path.resolve()),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    manifest["provenance"] = {
        "source_revision": "abc123",
        "files": {
            "runner": record(MODULE_PATH),
            "helper": record(Path(manifest["executables"]["noop_helper"])),
            "protocol": record(fixture_protocol),
            "analyzer_definition": record(fixture_analyzer),
            "tests": record(fixture_tests),
        },
        "sshai": {
            **record(sshai),
            "vcs_revision": "abc123",
            "vcs_modified": False,
            "smoke_ok": True,
        },
        "codex": {
            **record(Path(manifest["executables"]["codex"])),
            "version": manifest["codex"]["version"],
        },
        "instruction_files": [record(MODULE_PATH)],
        "config_files": [record(MODULE_PATH)],
        "repository": {
            "head": "abc123",
            "upstream": "abc123",
            "clean": True,
        },
        "prompt_sha256": {
            branch: hashlib.sha256(BENCH.render_prompt(manifest, branch).encode()).hexdigest()
            for branch in manifest["branch_order"]
        },
    }
    manifest["provenance"]["execution_contract_sha256"] = (
        BENCH.execution_contract_digest(manifest)
    )


def write_manifest_with_lock(path: Path, manifest: dict[str, object]) -> None:
    data = json.dumps(manifest).encode()
    path.write_bytes(data)
    path.with_name(path.name + ".sha256").write_text(
        hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8"
    )


def test_exact_branch_call_maps() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = manifest_fixture(Path(directory))
        BENCH.validate_manifest(manifest)
        calls = {
            branch: BENCH.build_branch_calls(manifest, branch)
            for branch in manifest["branch_order"]
        }
        assert [len(calls[branch]) for branch in manifest["branch_order"]] == [36, 36, 24, 24]
        assert calls["raw"][0]["observations"] == ("L01",)
        assert calls["raw"][-1]["observations"] == ("W12",)
        assert calls["fanout"][0]["observations"] == ("L01", "L13")
        assert calls["fanout"][0]["hosts"] == ("linux-a", "linux-b")
        assert calls["fanout"][11]["observations"] == ("L12", "L24")
        assert calls["fanout"][12]["observations"] == ("W01",)
        assert calls["fanout-control"] == [
            {**call, "branch": "fanout-control"} for call in calls["fanout"]
        ]
        original_body = manifest["observations"][12]["body"]
        manifest["observations"][12]["body"] = "printf incompatible"
        try:
            BENCH.validate_manifest(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "fan-out-compatible" in str(exc)
        else:
            raise AssertionError("different Linux bodies must not share one fan-out call")
        manifest["observations"][12]["body"] = original_body
        qualification_path = Path(manifest["qualification"]["evidence_path"])
        valid_qualification = qualification_path.read_bytes()
        qualification_path.write_text("{}\n", encoding="utf-8")
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            qualification_path.read_bytes()
        ).hexdigest()
        try:
            BENCH.validate_measurement_ready(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "qualification evidence" in str(exc)
        else:
            raise AssertionError("arbitrary hashed bytes must not qualify targets")
        qualification_path.write_bytes(valid_qualification)
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            valid_qualification
        ).hexdigest()
        qualification_document = json.loads(valid_qualification)
        qualification_document["results"][0]["probe"]["command_sha256"] = "d" * 64
        drifted_bytes = (json.dumps(qualification_document) + "\n").encode()
        qualification_path.write_bytes(drifted_bytes)
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            drifted_bytes
        ).hexdigest()
        try:
            BENCH.validate_measurement_ready(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "frozen command" in str(exc)
        else:
            raise AssertionError("qualification must use the frozen safe probe")
        qualification_path.write_bytes(valid_qualification)
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            valid_qualification
        ).hexdigest()
        first_output = Path(
            json.loads(valid_qualification)["results"][0]["probe"]["output_path"]
        )
        original_output = first_output.read_bytes()
        first_output.write_bytes(b"tampered\n")
        try:
            BENCH.validate_measurement_ready(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "captured output" in str(exc)
        else:
            raise AssertionError("qualification output drift must fail closed")
        first_output.write_bytes(original_output)
        qualification_document = json.loads(valid_qualification)
        unrelated_output = b"unrelated-but-self-consistent\n"
        first_output.write_bytes(unrelated_output)
        first_probe = qualification_document["results"][0]["probe"]
        first_probe["output_sha256"] = hashlib.sha256(unrelated_output).hexdigest()
        first_probe["output_bytes"] = len(unrelated_output)
        unrelated_evidence = (json.dumps(qualification_document) + "\n").encode()
        qualification_path.write_bytes(unrelated_evidence)
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            unrelated_evidence
        ).hexdigest()
        try:
            BENCH.validate_measurement_ready(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "expected output" in str(exc)
        else:
            raise AssertionError("qualification output must substantiate OS and shell")
        first_output.write_bytes(original_output)
        qualification_path.write_bytes(valid_qualification)
        manifest["qualification"]["evidence_sha256"] = hashlib.sha256(
            valid_qualification
        ).hexdigest()
        manifest["qualification"] = {"status": "pending"}
        try:
            BENCH.validate_measurement_ready(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "qualification" in str(exc)
        else:
            raise AssertionError("pending target qualification must block measured branches")


def test_amended_manifest_freezes_balanced_adjacent_branch_schedule() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = amended_manifest_fixture(Path(directory))
        BENCH.validate_manifest(manifest)
        observed = [
            list(BENCH.branch_order_for_replicate(manifest, replicate))
            for replicate in range(1, 9)
        ]
        assert observed == AMENDED_BRANCH_SCHEDULE


def test_amended_manifest_rejects_nonadjacent_pair_schedule() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = amended_manifest_fixture(Path(directory))
        manifest["branch_schedule"][0] = [
            "raw-control", "fanout-control", "raw", "fanout",
        ]
        try:
            BENCH.validate_manifest(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "branch_schedule" in str(exc)
        else:
            raise AssertionError("nonadjacent workload/control pairs must fail closed")


def test_schedule_metadata_is_required_only_for_the_amendment() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        legacy = manifest_fixture(root)
        assert BENCH.branch_schedule_metadata(legacy, 1, "raw-control") == {}
        amended = amended_manifest_fixture(root)
        assert BENCH.branch_schedule_metadata(amended, 2, "raw-control") == {
            "branch_order": [
                "raw", "raw-control", "fanout", "fanout-control",
            ],
            "branch_index": 1,
        }


def test_noop_helper_is_deterministic_and_network_free() -> None:
    helper = MODULE_PATH.with_name("benchmark_v2_1_noop.py")
    audit_wrapper = """
import runpy
import sys

def deny_network(event, args):
    if event.startswith("socket."):
        raise RuntimeError("network audit event: " + event)

sys.addaudithook(deny_network)
script = sys.argv.pop(1)
runpy.run_path(script, run_name="__main__")
"""
    arguments = [
        sys.executable,
        "-c",
        audit_wrapper,
        str(helper),
        "run",
        "--body-file",
        "-",
        "--timeout",
        "180",
        "--result-format=json",
        "--branch",
        "fanout-control",
        "--call-id",
        "linux-01",
        "--observations",
        "L01,L13",
        "--hosts",
        "linux-a,linux-b",
        "--expected-exits",
        "0,0",
    ]
    body = "printf observation-01\n"
    first = subprocess.run(arguments, input=body, check=True, text=True, capture_output=True)
    second = subprocess.run(arguments, input=body, check=True, text=True, capture_output=True)
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    envelope = json.loads(first.stdout)
    assert envelope == {
        "schema": "sshai-benchmark-noop/v2.1",
        "branch": "fanout-control",
        "call_id": "linux-01",
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "results": [
            {
                "observation_id": "L01",
                "host": "linux-a",
                "exit": 0,
                "outcome": "success",
                "artifact_id": "noop-l01",
                "artifact_path": "",
                "transport_error": "",
            },
            {
                "observation_id": "L13",
                "host": "linux-b",
                "exit": 0,
                "outcome": "success",
                "artifact_id": "noop-l13",
                "artifact_path": "",
                "transport_error": "",
            },
        ],
    }


def test_rendered_prompts_have_exact_maps_and_control_boundaries() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = manifest_fixture(Path(directory))
        prompts = {
            branch: BENCH.render_prompt(manifest, branch)
            for branch in manifest["branch_order"]
        }
        for branch, expected in BENCH.EXPECTED_CALL_COUNTS.items():
            marker = f"BENCH_V21_CALL={branch}:"
            assert prompts[branch].count(marker) == expected
        assert all("<<'SSHAI_BENCH_V21_" not in prompt for prompt in prompts.values())
        assert "/usr/bin/ssh" not in prompts["raw-control"]
        assert "/opt/benchmark/bin/sshai" not in prompts["raw-control"]
        assert "/usr/bin/ssh" not in prompts["fanout-control"]
        assert "/opt/benchmark/bin/sshai" not in prompts["fanout-control"]
        assert str(MODULE_PATH.with_name("benchmark_v2_1_noop.py")) in prompts["raw-control"]
        assert "BENCH_V21_OBS=L01,L13" in prompts["fanout-control"]
        linux_body = str(Path(manifest["artifact_root"]) / "inputs" / "L01.body")
        windows_body = str(Path(manifest["artifact_root"]) / "inputs" / "W01.body")
        assert f"/usr/bin/ssh linux-a bash -s < {linux_body}" in prompts["raw"]
        assert (
            "/usr/bin/ssh windows-a pwsh -NoProfile -NonInteractive -File - "
            f"< {windows_body}"
        ) in prompts["raw"]
        assert (
            f"/opt/benchmark/bin/sshai run --body-file {linux_body} --timeout 180 "
            "--result-format=json linux-a linux-b"
        ) in prompts["fanout"]
        assert prompts["fanout"].find("linux-a linux-b") < prompts["fanout"].find("windows-a")
        fanout_calls = BENCH.build_branch_calls(manifest, "fanout")
        repeated = BENCH.render_call(manifest, fanout_calls[7])
        delta = BENCH.render_call(manifest, fanout_calls[8])
        assert "--ctx benchmark-v2.1-linux" in repeated
        assert "--delta" not in repeated
        assert "--ctx benchmark-v2.1-linux --delta" in delta


def test_amendment_2_enforces_one_fence_per_command_item() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = amendment_2_manifest_fixture(Path(directory))
        instruction = (
            "Submit each fenced command as its own command tool call. "
            "A command tool call must contain exactly one fence and exactly one "
            "BENCH_V21_CALL marker. Never batch, concatenate, loop over, or wrap "
            "commands from multiple fences in one shell invocation. Wait for each "
            "call to complete before submitting the next fence."
        )
        for branch, expected_fences in {
            "raw-control": 36,
            "raw": 36,
            "fanout-control": 24,
            "fanout": 24,
        }.items():
            prompt = BENCH.render_prompt(manifest, branch)
            assert instruction in prompt
            assert prompt.count("```bash\n") == expected_fences

        historical_prompt = BENCH.render_prompt(
            amended_manifest_fixture(Path(directory)), "fanout"
        )
        assert instruction not in historical_prompt

        calls = BENCH.build_branch_calls(manifest, "fanout")
        combined = "\n".join(BENCH.render_call(manifest, call) for call in calls[:2])
        try:
            BENCH.analyze_command_population(
                [completed_command(combined, "ok")],
                "fanout",
                {
                    "linux-01": ("L01", "L13"),
                    "linux-02": ("L02", "L14"),
                },
            )
        except BENCH.AnalysisInvalid as exc:
            assert "multiple v2.1 markers" in str(exc)
        else:
            raise AssertionError("multiple fences in one command item must fail closed")


def test_call_identity_survives_shell_comment_elision() -> None:
    """A Codex shell invocation must not lose identity when comments are omitted."""
    with tempfile.TemporaryDirectory() as directory:
        manifest = manifest_fixture(Path(directory))
        call = BENCH.build_branch_calls(manifest, "fanout-control")[0]
        rendered = BENCH.render_call(manifest, call)
        submitted = "\n".join(
            line for line in rendered.splitlines() if not line.startswith("#")
        )
        envelope = f"/bin/zsh -lc {shlex.quote(submitted)}"
        population = BENCH.analyze_command_population(
            [completed_command(envelope, "ok")],
            "fanout-control",
            {"linux-01": ("L01", "L13")},
        )
        assert population["marked_calls"] == 1
        assert population["observations"] == ["L01", "L13"]


def test_run_branch_writes_immutable_prompt_result_and_metadata() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        codex = root / "fake-codex"
        codex.write_text(
            """#!/usr/bin/env python3
import json
import sys
prompt = sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "thread-test"}))
print(json.dumps({"type": "turn.started"}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": len(prompt)}}))
""",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        sshai = root / "fake-sshai"
        sshai.write_bytes(b"fake-sshai")
        sshai.chmod(0o755)
        manifest_path = root / "manifest.json"
        manifest = manifest_fixture(root, str(codex))
        attach_frozen_provenance(manifest, sshai)
        write_manifest_with_lock(manifest_path, manifest)
        original_repository_validator = BENCH.validate_repository_state
        BENCH.validate_repository_state = lambda _manifest: None
        paths = BENCH.run_branch(manifest_path, 1, "raw-control")
        body_inputs = root / "artifacts" / "inputs"
        assert len(list(body_inputs.glob("*.body"))) == 36
        assert (body_inputs / "L07.body").read_text() == (
            "printf observation-07\n"
        )
        assert paths == {
            "prompt": root / "artifacts" / "replicate-01" / "raw-control.prompt.txt",
            "result": root / "artifacts" / "replicate-01" / "raw-control.jsonl",
            "stderr": root / "artifacts" / "replicate-01" / "raw-control.stderr.txt",
            "metadata": root / "artifacts" / "replicate-01" / "raw-control.meta.json",
        }
        prompt_bytes = paths["prompt"].read_bytes()
        result_bytes = paths["result"].read_bytes()
        metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        assert metadata["schema"] == "sshai-benchmark-run/v2.1"
        assert metadata["branch"] == "raw-control"
        assert metadata["replicate"] == 1
        assert "branch_order" not in metadata
        assert "branch_index" not in metadata
        assert metadata["process_exit"] == 0
        assert metadata["sandbox"] == "workspace-write"
        assert metadata["ignore_user_config"] is True
        expected_home = root / "runtime-homes" / "replicate-01" / "raw-control"
        assert metadata["codex_home"] == str(expected_home)
        assert expected_home.is_dir()
        assert (expected_home / "auth.json").is_symlink()
        assert set(path.name for path in expected_home.iterdir()) == {"auth.json"}
        assert metadata["prompt_sha256"] == hashlib.sha256(prompt_bytes).hexdigest()
        assert metadata["result_sha256"] == hashlib.sha256(result_bytes).hexdigest()
        assert metadata["stderr_bytes"] == 0
        assert metadata["stderr_sha256"] == hashlib.sha256(b"").hexdigest()
        assert metadata["stderr_truncated"] is False
        try:
            BENCH.run_branch(manifest_path, 1, "raw-control")
        except BENCH.AnalysisInvalid as exc:
            assert "refusing to overwrite" in str(exc)
        else:
            raise AssertionError("second branch run must fail closed")
        assert paths["prompt"].read_bytes() == prompt_bytes
        assert paths["result"].read_bytes() == result_bytes

        sessions_root = expected_home / "sessions"
        sessions = sessions_root / "2026" / "08" / "18"
        sessions.mkdir(parents=True)
        source_rollout = sessions / "rollout-2026-08-18-thread-test.jsonl"
        source_rollout.write_text(
            json.dumps({"type": "session_meta", "payload": {"id": "thread-test"}}) + "\n",
            encoding="utf-8",
        )
        captured = BENCH.capture_persisted_rollout(
            manifest_path, 1, "raw-control", sessions_root
        )
        assert captured["rollout"].read_bytes() == source_rollout.read_bytes()
        capture_meta = json.loads(captured["metadata"].read_text(encoding="utf-8"))
        assert capture_meta["thread_id"] == "thread-test"
        assert capture_meta["rollout_sha256"] == hashlib.sha256(
            source_rollout.read_bytes()
        ).hexdigest()
        try:
            BENCH.capture_persisted_rollout(
                manifest_path, 1, "raw-control", sessions_root
            )
        except BENCH.AnalysisInvalid as exc:
            assert "refusing to overwrite" in str(exc)
        else:
            raise AssertionError("second rollout capture must fail closed")
        try:
            BENCH.run_branch(manifest_path, 1, "raw")
        except BENCH.AnalysisInvalid as exc:
            assert "branch order" in str(exc)
        else:
            raise AssertionError("next branch must wait for immutable validation evidence")
        validation_path = root / "artifacts" / "replicate-01" / "raw-control.validation.json"
        validation_path.write_text("{}\n", encoding="utf-8")
        try:
            BENCH.run_branch(manifest_path, 1, "raw")
        except BENCH.AnalysisInvalid as exc:
            assert "validation evidence" in str(exc)
        else:
            raise AssertionError("empty validation file must not unlock the next branch")
        validation_path.unlink()
        try:
            BENCH.run_branch(manifest_path, 1, "fanout-control")
        except BENCH.AnalysisInvalid as exc:
            assert "branch order" in str(exc)
        else:
            raise AssertionError("runner must not skip the raw workload branch")
        BENCH.validate_repository_state = original_repository_validator


def test_fanout_evidence_requires_every_declared_host_result() -> None:
    call = {
        "branch": "fanout",
        "id": "linux-01",
        "observations": ("L01", "L13"),
        "hosts": ("linux-a", "linux-b"),
        "expected_exits": (0, 0),
    }
    incomplete = completed_command(
        "BENCH_V21_CALL=fanout:linux-01 BENCH_V21_OBS=L01,L13 printf ok",
        json.dumps({
            "schema_version": "v1",
            "batch_id": "a123",
            "summary": {
                "hosts": 1,
                "ok": 1,
                "failed": 0,
                "transport_errors": 0,
                "policy_denied": 0,
                "worst_exit": 0,
            },
            "runs": [{
                "id": "a1",
                "host": "linux-a",
                "exit": 0,
                "transport_error": "",
                "artifact_path": "/tmp/a1",
            }],
        }),
    )
    try:
        BENCH.validate_call_evidence(incomplete["item"], call)
    except BENCH.AnalysisInvalid as exc:
        assert "host results" in str(exc)
    else:
        raise AssertionError("incomplete fan-out envelope must be invalid")

    complete = json.loads(incomplete["item"]["aggregated_output"])
    complete["summary"]["hosts"] = 2
    complete["summary"]["ok"] = 2
    complete["runs"].append({
        "id": "a2",
        "host": "linux-b",
        "exit": 0,
        "transport_error": "",
        "artifact_path": "/tmp/a2",
    })
    incomplete["item"]["aggregated_output"] = json.dumps(complete)
    assert BENCH.validate_call_evidence(incomplete["item"], call) == [
        {
            "observation_id": "L01", "host": "linux-a", "exit": 0,
            "outcome": "success", "artifact_id": "a1",
        },
        {
            "observation_id": "L13", "host": "linux-b", "exit": 0,
            "outcome": "success", "artifact_id": "a2",
        },
    ]
    complete["runs"][0]["exit"] = False
    incomplete["item"]["aggregated_output"] = json.dumps(complete)
    try:
        BENCH.validate_call_evidence(incomplete["item"], call)
    except BENCH.AnalysisInvalid as exc:
        assert "exit shape" in str(exc)
    else:
        raise AssertionError("JSON boolean must not be accepted as an integer exit")

    raw_call = {
        "branch": "raw",
        "id": "linux-01",
        "observations": ("L01",),
        "hosts": ("linux-a",),
        "expected_exits": (0,),
    }
    try:
        BENCH.validate_call_evidence(
            completed_command("printf ok", "ok", False)["item"], raw_call
        )
    except BENCH.AnalysisInvalid as exc:
        assert "integer exit" in str(exc)
    else:
        raise AssertionError("raw boolean exit must fail closed")


def test_complete_branch_gate_cross_checks_thread_lifecycle_and_evidence() -> None:
    with tempfile.TemporaryDirectory() as directory:
        manifest = manifest_fixture(Path(directory))
        branch = "fanout-control"
        events: list[dict[str, object]] = [
            {"type": "thread.started", "thread_id": "thread-01"},
            {"type": "turn.started"},
        ]
        for call in BENCH.build_branch_calls(manifest, branch):
            command = BENCH.render_call(manifest, call)
            output = {
                "schema": "sshai-benchmark-noop/v2.1",
                "branch": branch,
                "call_id": call["id"],
                "body_sha256": call["body_sha256"],
                "results": [
                    {
                        "observation_id": observation,
                        "host": host,
                        "exit": expected_exit,
                        "outcome": "success",
                        "artifact_id": f"noop-{observation.lower()}",
                        "artifact_path": "",
                        "transport_error": "",
                    }
                    for observation, host, expected_exit in zip(
                        call["observations"], call["hosts"], call["expected_exits"], strict=True
                    )
                ],
            }
            events.append(completed_command(command, json.dumps(output)))
        events.append({
            "type": "turn.completed",
            "usage": {"input_tokens": 1000, "cached_input_tokens": 800},
        })
        persisted = [{"type": "session_meta", "payload": {"id": "thread-01"}}]
        report = BENCH.analyze_branch_events(manifest, branch, events, persisted)
        assert report["thread_id"] == "thread-01"
        assert report["marked_calls"] == 24
        assert report["observations"] == list(BENCH.OBSERVATION_IDS)
        assert len(report["observation_evidence"]) == 36
        assert report["usage"]["non_cached_input_tokens"] == 200
        assert report["compactions"]["matched"] is True

        skill_path = Path(directory) / "sshai-skill.md"
        skill_text = "frozen sshai skill\n"
        skill_path.write_text(skill_text, encoding="utf-8")
        manifest["provenance"] = {
            "instruction_files": [{
                "path": str(skill_path),
                "sha256": hashlib.sha256(skill_text.encode()).hexdigest(),
            }],
        }
        events.insert(-1, completed_command(
            shlex.join(["/bin/zsh", "-lc", f"cat {skill_path}"]),
            skill_text,
        ))
        setup_report = BENCH.analyze_branch_events(
            manifest, branch, events, persisted
        )
        assert setup_report["unmarked_calls"] == 1

        wrapped_events = json.loads(json.dumps(events))
        for event in wrapped_events:
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "command_execution"
                and "BENCH_V21_" in item["command"]
            ):
                item["command"] = shlex.join(["/bin/bash", "-c", item["command"]])
        wrapped_report = BENCH.analyze_branch_events(
            manifest, branch, wrapped_events, persisted
        )
        assert wrapped_report["marked_calls"] == 24
        sample = BENCH.render_call(
            manifest, BENCH.build_branch_calls(manifest, branch)[0]
        )
        assert BENCH.command_script(
            shlex.join(["/bin/zsh", "-c", sample])
        ) == sample

        reversed_events = events[:2] + list(reversed(events[2:-1])) + events[-1:]
        try:
            BENCH.analyze_branch_events(manifest, branch, reversed_events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "call order" in str(exc)
        else:
            raise AssertionError("reversed marked calls must invalidate the branch")

        cached_tokens = events[-1]["usage"].pop("cached_input_tokens")
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "cached_input_tokens" in str(exc)
        else:
            raise AssertionError("missing cached token evidence must invalidate the branch")
        events[-1]["usage"]["cached_input_tokens"] = cached_tokens

        first_output = json.loads(events[2]["item"]["aggregated_output"])
        events[2]["item"]["aggregated_output"] = json.dumps({
            **first_output, "body_sha256": "wrong",
        })
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "body digest" in str(exc)
        else:
            raise AssertionError("wrong control body digest must invalidate the branch")
        events[2]["item"]["aggregated_output"] = json.dumps(first_output)

        events.insert(-1, completed_command("ssh unlisted-host true", "network attempt"))
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "unmarked control" in str(exc)
        else:
            raise AssertionError("every unmarked control command must invalidate the branch")
        events.pop(-2)

        events.insert(-1, {
            "type": "item.completed",
            "item": {"type": "mcp_tool_call", "server": "exa", "tool": "web_search"},
        })
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "unexpected completed item type" in str(exc)
        else:
            raise AssertionError("non-command tool calls must invalidate the branch")
        events.pop(-2)

        events.append({"type": "compaction.future"})
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "lifecycle" in str(exc)
        else:
            raise AssertionError("unknown lifecycle-like type must invalidate the branch")
        events.pop()

        persisted[0]["payload"]["id"] = "other-thread"
        try:
            BENCH.analyze_branch_events(manifest, branch, events, persisted)
        except BENCH.AnalysisInvalid as exc:
            assert "thread identity" in str(exc)
        else:
            raise AssertionError("persisted thread mismatch must be invalid")


def test_frozen_provenance_detects_helper_and_prompt_drift() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        helper = root / "noop.py"
        helper.write_bytes(MODULE_PATH.with_name("benchmark_v2_1_noop.py").read_bytes())
        sshai = root / "sshai"
        sshai.write_bytes(b"frozen-sshai-binary")
        codex = root / "codex"
        codex.write_bytes(b"frozen-codex-binary")
        codex.chmod(0o755)
        manifest = manifest_fixture(root, str(codex))
        manifest["executables"]["noop_helper"] = str(helper)
        attach_frozen_provenance(manifest, sshai)
        BENCH.validate_frozen_provenance(manifest)
        original_model = manifest["codex"]["model"]
        manifest["codex"]["model"] = "drifted-model"
        try:
            BENCH.validate_frozen_provenance(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "execution contract" in str(exc)
        else:
            raise AssertionError("model drift must invalidate frozen provenance")
        manifest["codex"]["model"] = original_model
        manifest_path = root / "manifest.json"
        write_manifest_with_lock(manifest_path, manifest)
        validated = subprocess.run(
            [sys.executable, str(MODULE_PATH), "validate", "--manifest", str(manifest_path)],
            text=True,
            capture_output=True,
        )
        assert validated.returncode == 0, validated.stderr
        assert json.loads(validated.stdout) == {
            "schema": "sshai-benchmark-validation/v2.1",
            "ok": True,
        }
        helper.write_text("drift", encoding="utf-8")
        try:
            BENCH.validate_frozen_provenance(manifest)
        except BENCH.AnalysisInvalid as exc:
            assert "helper digest" in str(exc)
        else:
            raise AssertionError("helper drift must invalidate frozen provenance")
        rejected = subprocess.run(
            [sys.executable, str(MODULE_PATH), "validate", "--manifest", str(manifest_path)],
            text=True,
            capture_output=True,
        )
        assert rejected.returncode == 2
        assert "helper digest" in rejected.stderr


def test_three_replicate_decision_uses_paired_controls_and_control_floor() -> None:
    def branch(input_tokens: int, calls: int, p95: int = 100) -> dict[str, object]:
        return {
            "usage": {"input_tokens": input_tokens, "cached_input_tokens": input_tokens // 2},
            "marked_calls": calls,
            "observations": list(BENCH.OBSERVATION_IDS),
            "observation_evidence": [
                {"outcome": "success"} for _ in BENCH.OBSERVATION_IDS
            ],
            "marked_tool_response_est_tokens": {"p95": p95, "sum": 1, "max": p95},
            "compactions": {"codex_exec": 0, "persisted_rollout": 0, "matched": True},
        }

    replicates = []
    for fanout_tokens in (300, 350, 400):
        replicates.append({
            "raw-control": branch(200, 36),
            "raw": branch(1000, 36),
            "fanout-control": branch(200, 24),
            "fanout": branch(fanout_tokens, 24),
        })
    decision = BENCH.decide_replicates(replicates)
    assert decision["decision"] == "confirmed"
    assert decision["median_control_adjusted_input_reduction"] == 0.8125
    assert decision["call_reduction"] == 1 / 3

    replicates[1]["fanout"]["usage"]["input_tokens"] = 100
    decision = BENCH.decide_replicates(replicates)
    assert decision["decision"] == "inconclusive"
    assert decision["replicates"][1]["input_tokens"]["fanout_margin"] == -100
    assert decision["replicates"][1]["input_tokens"]["status"] == (
        "indistinguishable-from-control-floor"
    )
    assert decision["median_control_adjusted_input_reduction"] is None


def test_amended_decision_allows_at_most_two_predeclared_control_floor_pairs() -> None:
    def branch(input_tokens: int, calls: int) -> dict[str, object]:
        return {
            "usage": {"input_tokens": input_tokens, "cached_input_tokens": input_tokens // 2},
            "marked_calls": calls,
            "observations": list(BENCH.OBSERVATION_IDS),
            "observation_evidence": [
                {"outcome": "success"} for _ in BENCH.OBSERVATION_IDS
            ],
            "marked_tool_response_est_tokens": {"p95": 100, "sum": 1, "max": 100},
            "compactions": {"codex_exec": 0, "persisted_rollout": 0, "matched": True},
        }

    def replicate(fanout_tokens: int) -> dict[str, dict[str, object]]:
        return {
            "raw-control": branch(200, 36),
            "raw": branch(1000, 36),
            "fanout-control": branch(200, 24),
            "fanout": branch(fanout_tokens, 24),
        }

    with tempfile.TemporaryDirectory() as directory:
        manifest = amended_manifest_fixture(Path(directory))
        six_defined = (
            [replicate(300) for _ in range(6)]
            + [replicate(100) for _ in range(2)]
        )
        decision = BENCH.decide_replicates(six_defined, manifest)
        assert decision["schema"] == "sshai-benchmark-analysis/v2.1-amendment-1"
        assert decision["decision"] == "confirmed"
        assert decision["sampling"] == {
            "replicates": 8,
            "defined_reductions": 6,
            "control_floor_pairs": 2,
            "minimum_defined_reductions": 6,
            "maximum_control_floor_pairs": 2,
        }
        assert decision["median_control_adjusted_input_reduction"] == 0.875

        amendment_2 = amendment_2_manifest_fixture(Path(directory))
        amendment_2_decision = BENCH.decide_replicates(six_defined, amendment_2)
        assert amendment_2_decision["schema"] == (
            "sshai-benchmark-analysis/v2.1-amendment-2"
        )

        five_defined = (
            [replicate(300) for _ in range(5)]
            + [replicate(100) for _ in range(3)]
        )
        decision = BENCH.decide_replicates(five_defined, manifest)
        assert decision["decision"] == "inconclusive"
        assert decision["acceptance"]["sufficient_defined_reductions"] is False
        assert decision["acceptance"]["control_floor_pairs_within_cap"] is False

        below_target = [replicate(500) for _ in range(8)]
        decision = BENCH.decide_replicates(below_target, manifest)
        assert decision["decision"] == "needs-work"
        assert decision["median_control_adjusted_input_reduction"] == 0.625


def test_invalid_analysis_retains_all_branch_reasons_and_file_evidence() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        manifest_path = root / "manifest.json"
        manifest = manifest_fixture(root)
        write_manifest_with_lock(manifest_path, manifest)
        original_provenance_validator = BENCH.validate_frozen_provenance
        original_readiness_validator = BENCH.validate_measurement_ready
        BENCH.validate_frozen_provenance = lambda _manifest: None
        BENCH.validate_measurement_ready = lambda _manifest: None
        try:
            report = BENCH.analyze_all(manifest_path)
        finally:
            BENCH.validate_frozen_provenance = original_provenance_validator
            BENCH.validate_measurement_ready = original_readiness_validator
        assert report["schema"] == "sshai-benchmark-analysis/v2.1"
        assert report["valid"] is False
        assert report["decision"] == "invalid"
        assert len(report["reasons"]) == 12
        assert set(report["branches"][0]) == set(BENCH.BRANCHES)
        first = report["branches"][0]["raw-control"]
        assert first["valid"] is False
        assert "body input directory" in first["reason"]
        assert first["available_files"]["manifest"]["sha256"] == hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest()
        output_path = root / "invalid-analysis.json"
        analyzed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "analyze",
                "--manifest",
                str(manifest_path),
                "--output",
                str(output_path),
            ],
            text=True,
            capture_output=True,
        )
        assert analyzed.returncode == 0, analyzed.stderr
        published = json.loads(output_path.read_text(encoding="utf-8"))
        assert published["valid"] is False
        assert published["decision"] == "invalid"
        assert len(published["reasons"]) == 12
        invalid_jsonl = root / "invalid.jsonl"
        invalid_jsonl.write_bytes(b"\xff\xfe")
        try:
            BENCH.read_jsonl(invalid_jsonl)
        except BENCH.AnalysisInvalid as exc:
            assert "cannot decode JSONL" in str(exc)
        else:
            raise AssertionError("non-UTF-8 JSONL must become a reportable invalid reason")


def main() -> None:
    test_exact_branch_call_maps()
    test_amended_manifest_freezes_balanced_adjacent_branch_schedule()
    test_amended_manifest_rejects_nonadjacent_pair_schedule()
    test_schedule_metadata_is_required_only_for_the_amendment()
    test_noop_helper_is_deterministic_and_network_free()
    test_rendered_prompts_have_exact_maps_and_control_boundaries()
    test_amendment_2_enforces_one_fence_per_command_item()
    test_call_identity_survives_shell_comment_elision()
    test_run_branch_writes_immutable_prompt_result_and_metadata()
    test_fanout_evidence_requires_every_declared_host_result()
    test_complete_branch_gate_cross_checks_thread_lifecycle_and_evidence()
    test_frozen_provenance_detects_helper_and_prompt_drift()
    test_three_replicate_decision_uses_paired_controls_and_control_floor()
    test_amended_decision_allows_at_most_two_predeclared_control_floor_pairs()
    test_invalid_analysis_retains_all_branch_reasons_and_file_evidence()
    marker = "BENCH_V21_CALL=fanout:linux-01 BENCH_V21_OBS=L01,L13 printf ok"
    events = [
        completed_command(marker, "x" * 400),
        completed_command("printf setup", "BENCH_V21_CALL=fanout:fake " + "s" * 40_000),
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "No compact or truncated event occurred."},
        },
    ]
    population = BENCH.analyze_command_population(
        events,
        "fanout",
        {"linux-01": ("L01", "L13")},
    )
    assert population["marked_calls"] == 1
    assert population["unmarked_calls"] == 1
    assert population["observations"] == ["L01", "L13"]
    assert population["marked_tool_response_est_tokens"]["p95"] == 100
    assert population["all_tool_response_est_tokens"]["p95"] > 10_000

    assert_invalid([completed_command(marker, "ok"), completed_command(marker, "ok")], "duplicate")
    assert_invalid([], "missing")
    assert_invalid(
        [completed_command("BENCH_V21_CALL=fanout:linux-01 printf ok", "ok")],
        "malformed",
    )
    assert_invalid(
        [completed_command(
            "BENCH_V21_CALL=fanout:linux-01 BENCH_V21_OBS=L01,W01 printf ok",
            "ok",
        )],
        "observations",
    )
    assert_invalid(
        [completed_command(
            "BENCH_V21_CALL=fanout:unexpected BENCH_V21_OBS=L01,L13 printf ok",
            "ok",
        )],
        "unexpected",
    )

    exec_events = [
        {"type": "message.compaction_hint", "message": "compact prose"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "compacted"}},
        {"type": "compacted"},
    ]
    rollout_events = [
        {"type": "response_item", "payload": {"type": "message", "content": "compacted"}},
        {"type": "compacted"},
    ]
    assert BENCH.lifecycle_cross_check(exec_events, rollout_events) == {
        "codex_exec": 1,
        "persisted_rollout": 1,
        "matched": True,
    }
    assert BENCH.lifecycle_cross_check([], rollout_events)["matched"] is False

    adjusted = BENCH.control_adjusted(1000, 200, 400, 200)
    assert adjusted == {
        "raw_margin": 800,
        "fanout_margin": 200,
        "reduction": 0.75,
        "status": "defined",
    }
    assert BENCH.control_adjusted(1000, 200, 100, 200) == {
        "raw_margin": 800,
        "fanout_margin": -100,
        "reduction": None,
        "status": "indistinguishable-from-control-floor",
    }
    assert BENCH.control_adjusted(100, 200, 300, 100)["status"] == (
        "indistinguishable-from-control-floor"
    )
    print("benchmark_v2_1 tests: ok")


if __name__ == "__main__":
    main()
