#!/usr/bin/env python3
"""Synthetic-only checks for the Issue 10 local pilot controller."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_v3_capture as capture
from test_issue10_v3_call_evidence import CallEvidenceTests
from test_issue10_v3_completion import ANSWER, completion_streams
from test_issue10_v3_capture import jsonl


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def synthetic_bundle(root: Path) -> Path:
    files: dict[str, bytes] = {}
    cases = []
    for case_id in pilot.CASES:
        case_files = {
            "context.txt": f"case={case_id}\nsynthetic=true\n".encode(),
            "observations.tsv": b"time\tstatus\n10:00\thealthy\n",
        }
        inventory = []
        for name, data in case_files.items():
            files[f"inputs/{case_id}/{name}"] = data
            inventory.append({"file": name, "bytes": len(data), "lines": len(data.splitlines()),
                              "sha256": sha(data)})
        prompt = ("Investigate only {fixture_root}. Cite exact lines and do not repair.\n").encode()
        files[f"prompts/{case_id}.md"] = prompt
        cases.append({"case_id": case_id, "files": inventory})
    manifest = {
        "schema_version": "issue10-synthetic-v3-draft-2", "cases": cases,
        "files": [{"path": name, "bytes": len(data), "sha256": sha(data)}
                  for name, data in sorted(files.items())],
    }
    for name, data in files.items():
        write(root / name, data)
    write(root / "manifest.json", (json.dumps(manifest, sort_keys=True) + "\n").encode())
    return root


def make_config(codex: Path, sshai: Path) -> dict:
    return {
        "schema": pilot.CONFIG_SCHEMA,
        "codex": {"version": pilot.SOURCE_CONTRACT["codex_version"],
                  "sha256": sha(codex.read_bytes()),
                  "source_revision": pilot.SOURCE_CONTRACT["revision"]},
        "sshai": {"sha256": sha(sshai.read_bytes()),
                  "help_sha256": sha(b"synthetic help\n"), "revision": "synthetic-checkout"},
        "model": {"id": "gpt-5.6-sol", "version": "synthetic-catalog-pin",
                  "reasoning_effort": "high"},
        "assessment": {"model": "synthetic-independent-assessor",
                       "version": "synthetic-assessor-pin", "reasoning_effort": "high",
                       "max_contexts": 24,
                       "batching": "same-task-both-arms-all-repetitions",
                       "instructions_sha256": "1" * 64, "rubric_sha256": "2" * 64},
        "codex_exec": {
            "history_mode": "paginated",
            "config_overrides": list(pilot.REQUIRED_CODEX_OVERRIDES),
            "advertised_tools": [{
                "name": "local_command",
                "name_provenance": "expected-from-pinned-config",
                "schema_sha256": None,
                "schema_provenance": "unavailable",
            }],
            "allowed_audit_signatures": [
                {"source": "cli", "record_type": "command_execution",
                 "tool_name": "command_execution", "evidence_kind": "call"},
                {"source": "rollout.turn_item", "record_type": "CommandExecution",
                 "tool_name": "CommandExecution", "evidence_kind": "call"},
                {"source": "rollout.response_item", "record_type": "function_call",
                 "tool_name": "future_tool_name", "evidence_kind": "reported_call"},
                {"source": "rollout.event_msg", "record_type": "exec_command_begin",
                 "tool_name": "exec_command_begin", "evidence_kind": "reported_call_lifecycle"},
            ],
            "disabled_capabilities": sorted(pilot.REQUIRED_DISABLED_CAPABILITIES),
        },
        "environment": {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
                        "SHELL": "/bin/bash", "USER": "synthetic-pilot",
                        "LOGNAME": "synthetic-pilot"},
        "limits": {"timeout_seconds": 600},
        "evidence_handling": {
            "private": True, "raw_publication": "prohibited",
            "retention_days_after_report_or_termination": 90,
            "automatic_deletion": False,
        },
    }


class PilotFixture(unittest.TestCase):
    def setUp(self):
        self.cleanup = tempfile.TemporaryDirectory()
        self.base = Path(self.cleanup.name).resolve()
        self.bundle = synthetic_bundle(self.base / "bundle")
        self.private = self.base / "private-parent"
        self.codex = self.private / "bin" / "fake-codex"
        self.sshai = self.private / "bin" / "fake-sshai"
        write(self.codex, b"synthetic native codex\n", 0o700)
        write(self.sshai, b"synthetic checkout sshai\n", 0o700)
        self.auth = self.private / "auth-home" / "auth.json"
        write(self.auth, b'{"synthetic":"credential-placeholder"}\n', 0o600)
        self.assessment_instructions = self.private / "staging" / "assessment-instructions.md"
        self.assessment_rubric = self.private / "staging" / "assessment-rubric.md"
        write(self.assessment_instructions, b"Synthetic independent assessment instructions.\n")
        write(self.assessment_rubric, b"Synthetic frozen 0-2 rubric.\n")
        self.config = self.private / "staging" / "config.json"
        write(self.config, (json.dumps(self.config_value(), sort_keys=True) + "\n").encode())
        model = {"slug": "gpt-5.6-sol", "display_name": "Synthetic", "description": "Synthetic",
                 "default_reasoning_level": "low",
                 "supported_reasoning_levels": [{"effort": "high", "description": "Synthetic"}],
                 "shell_type": "unified_exec", "model_messages": {}, "context_window": 1000,
                 "truncation_policy": {"mode": "tokens", "limit": 500},
                 "input_modalities": ["text"], "apply_patch_tool_type": None,
                 "experimental_supported_tools": [], "supports_search_tool": False,
                 "tool_mode": "direct", "multi_agent_version": "v2"}
        self.catalog = self.private / "staging" / "model-catalog.json"
        write(self.catalog, (json.dumps({"models": [model]}, sort_keys=True) + "\n").encode())
        self.tool_overrides = self.private / "staging" / "tool-overrides.json"
        write(self.tool_overrides,
              (json.dumps(pilot.REQUIRED_CODEX_OVERRIDES, indent=2) + "\n").encode())
        self.root = self.private / "local-pilot"
        self.probes = []

    def tearDown(self):
        self.cleanup.cleanup()

    def config_value(self):
        config = make_config(self.codex, self.sshai)
        config["assessment"]["instructions_sha256"] = sha(
            self.assessment_instructions.read_bytes())
        config["assessment"]["rubric_sha256"] = sha(self.assessment_rubric.read_bytes())
        return config

    def fake_probe(self, codex, sshai, config):
        self.probes.append((codex, sshai))
        self.assertEqual(config["model"]["id"], "gpt-5.6-sol")
        return {"codex_version": config["codex"]["version"],
                "codex_version_output_sha256": "4" * 64,
                "sshai_help_sha256": config["sshai"]["help_sha256"]}

    def prepare(self):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_BINARY_PROBE", self.fake_probe):
            return pilot.prepare(
                self.root, self.bundle, self.codex, self.sshai, self.config, self.catalog,
                self.tool_overrides, self.auth, self.assessment_instructions,
                self.assessment_rubric,
            )

    def load(self):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            return pilot.load_manifest(self.root)

    def approve(self, manifest):
        approval = {
            "schema": pilot.APPROVAL_SCHEMA, "manifest_digest": manifest["digest"],
            "phase": pilot.PHASE, "session_count": 4,
            "original_diagnostic_session_ceiling": 120,
            "config_sha256": manifest["config_sha256"], "approved": True,
            "approved_at_utc": "2030-01-01T00:00:00Z",
            "authorization_note": "Synthetic test only; no model is launched.",
        }
        path = self.base / "approval.json"
        write(path, (json.dumps(approval) + "\n").encode(), 0o600)
        return path

    @staticmethod
    def fake_access(codex, sshai, scratch, fixture, protected, environment, fixture_files):
        overrides = pilot.access_overrides(scratch, fixture, protected, sshai)
        receipt = {
            "schema": pilot.ACCESS_SCHEMA, "effective_config_overrides": overrides,
            "checks": {"synthetic_fixture_read": True, "synthetic_fixture_write_denied": True,
                       "synthetic_private_read_denied": True, "synthetic_scratch_write": True},
            "fixture_access": "read-only", "scratch_access": "write",
            "network_access": "denied", "limitations": ["synthetic test receipt"],
        }
        receipt["digest"] = pilot._digest_object(receipt)
        return receipt


class PreparationTests(PilotFixture):
    def test_prepare_is_nonlaunching_balanced_private_and_copies_only_selected_inputs(self):
        with patch.object(pilot, "_collect_local_attempt", side_effect=AssertionError("must not launch")):
            manifest = self.prepare()
        self.assertEqual(len(self.probes), 1)
        self.assertEqual([slot["arm"] for slot in manifest["slots"]],
                         ["sshai", "baseline", "baseline", "sshai"])
        first_by_pair = [manifest["slots"][0]["arm"], manifest["slots"][2]["arm"]]
        self.assertEqual(set(first_by_pair), set(pilot.ARMS))
        self.assertEqual(manifest["session_count"], 4)
        self.assertEqual(manifest["original_diagnostic_session_ceiling"], 120)
        self.assertEqual(manifest["assessor_context_ceiling"], 24)
        self.assertFalse(manifest["experimental_savings_claim_eligible"])
        self.assertEqual({p.name for p in (self.root / "prepared" / "inputs").iterdir()}, set(pilot.CASES))
        self.assertFalse((self.root / "prepared" / "evaluator").exists())
        self.assertEqual(
            (self.root / "prepared" / "assessment" / "instructions.md").read_bytes(),
            self.assessment_instructions.read_bytes(),
        )
        self.assertEqual(
            (self.root / "prepared" / "assessment" / "rubric.md").read_bytes(),
            self.assessment_rubric.read_bytes(),
        )
        self.assertEqual(list((self.root / "slots").iterdir()), [])
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.root / "manifest.json").stat().st_mode & 0o777, 0o600)
        self.load()

    def test_malformed_config_and_missing_pins_fail_before_root_creation(self):
        for mutate in (
            lambda config: config["limits"].update(timeout_seconds=599),
            lambda config: config["evidence_handling"].update(
                retention_days_after_report_or_termination=89),
            lambda config: config["codex_exec"]["advertised_tools"][0].update(
                schema_sha256="3" * 64),
        ):
            with self.subTest(mutate=mutate):
                config = self.config_value()
                mutate(config)
                write(self.config, json.dumps(config).encode())
                with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
                    with self.assertRaises(pilot.PilotInputError):
                        pilot.prepare(
                            self.root, self.bundle, self.codex, self.sshai, self.config,
                            self.catalog, self.tool_overrides, self.auth,
                            self.assessment_instructions, self.assessment_rubric,
                        )
                self.assertFalse(self.root.exists())

    def test_catalog_and_tool_controls_fail_closed_on_reduced_or_changed_inputs(self):
        catalog = json.loads(self.catalog.read_text())
        catalog["models"][0]["supports_search_tool"] = True
        self.catalog.write_text(json.dumps(catalog) + "\n")
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_BINARY_PROBE", self.fake_probe):
            with self.assertRaises(pilot.PilotInputError):
                pilot.prepare(
                    self.root, self.bundle, self.codex, self.sshai, self.config,
                    self.catalog, self.tool_overrides, self.auth,
                    self.assessment_instructions, self.assessment_rubric,
                )
        self.assertFalse(self.root.exists())
        catalog["models"][0]["supports_search_tool"] = False
        self.catalog.write_text(json.dumps(catalog) + "\n")
        self.tool_overrides.write_text(json.dumps(pilot.REQUIRED_CODEX_OVERRIDES[:-1]) + "\n")
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_BINARY_PROBE", self.fake_probe):
            with self.assertRaises(pilot.PilotInputError):
                pilot.prepare(
                    self.root, self.bundle, self.codex, self.sshai, self.config,
                    self.catalog, self.tool_overrides, self.auth,
                    self.assessment_instructions, self.assessment_rubric,
                )
        self.assertFalse(self.root.exists())

    def test_tampered_prepared_fixture_config_binary_and_manifest_are_rejected(self):
        scenarios = (
            lambda: (self.root / "prepared" / "inputs" / "M01" / "context.txt").chmod(0o600)
                    or (self.root / "prepared" / "inputs" / "M01" / "context.txt").write_text("tamper\n"),
            lambda: (self.root / "config.json").write_text("{}\n"),
            lambda: (self.root / "prepared" / "assessment" / "rubric.md").chmod(0o600)
                    or (self.root / "prepared" / "assessment" / "rubric.md").write_text("tamper\n"),
            lambda: self.sshai.write_bytes(b"changed\n"),
            lambda: self._tamper_manifest(),
        )
        for mutate in scenarios:
            with self.subTest(mutate=mutate):
                if self.root.exists():
                    import shutil
                    for path in self.root.rglob("*"):
                        if path.is_dir():
                            path.chmod(0o700)
                    shutil.rmtree(self.root)
                self.sshai.write_bytes(b"synthetic checkout sshai\n")
                self.sshai.chmod(0o700)
                self.config.write_text(json.dumps(self.config_value()) + "\n")
                self.prepare()
                mutate()
                with self.assertRaises((pilot.PilotInputError, ValueError)):
                    self.load()

    def _tamper_manifest(self):
        path = self.root / "manifest.json"
        value = json.loads(path.read_text())
        value["slots"].reverse()
        path.write_text(json.dumps(value) + "\n")

    def test_preflight_runs_no_model_canaries_without_consuming_a_slot(self):
        self.prepare()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_ACCESS_QUALIFIER", self.fake_access), \
             patch.object(pilot, "_ATTEMPT_COLLECTOR", side_effect=AssertionError("must not launch")):
            summary = pilot.preflight(self.root)
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["model_launches"], 0)
        self.assertEqual(summary["scheduled_slots_consumed"], 0)
        self.assertEqual({row["case_id"] for row in summary["cases"]}, set(pilot.CASES))
        self.assertEqual(list((self.root / "slots").iterdir()), [])
        retained = json.loads((self.root / "readiness" / "result.json").read_text())
        self.assertEqual(retained["digest"], pilot._digest_object(retained))
        self.assertEqual(retained["advertised_tools"][0]["schema_provenance"], "unavailable")
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                pilot.preflight(self.root)

    def test_access_profile_is_granular_read_fixture_write_scratch_and_denies_private_sources(self):
        root = self.base / "profile"
        scratch, fixture = root / "scratch", root / "fixture"
        protected = [root / "prepared", root / "evaluator", root / "evidence", self.auth]
        for path in (scratch, fixture, *protected[:-1]):
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
        overrides = pilot.access_overrides(scratch, fixture, protected, self.sshai)
        text = "\n".join(overrides)
        self.assertIn(f'{json.dumps(str(fixture))}="read"', text)
        self.assertIn(f'{json.dumps(str(self.sshai))}="read"', text)
        self.assertIn('\":workspace_roots\"={\".\"=\"write\"}', text)
        for path in protected:
            self.assertIn(f'{json.dumps(str(path))}="deny"', text)
        self.assertNotIn(f'{json.dumps(str(protected[1]))}="read"', text)


class CollectionTests(PilotFixture):
    def setUp(self):
        super().setUp()
        self.manifest = self.prepare()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_ACCESS_QUALIFIER", self.fake_access):
            pilot.preflight(self.root)
        self.approval = self.approve(self.manifest)

    def run_slot(self, number, attempt_collector):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_ACCESS_QUALIFIER", self.fake_access), \
             patch.object(pilot, "_ATTEMPT_COLLECTOR", attempt_collector):
            return pilot.run_slot(self.root, number, self.approval, allow_model_run=True)

    @staticmethod
    def successful_collector(attempt_dir, argv, *, cli_data=None, rollout_data=None,
                             capture_rollout=True, invalid_discovery=False,
                             capture_answer=True, **kwargs):
        cli, rollout = completion_streams()
        cli_data = jsonl(cli) if cli_data is None else cli_data
        rollout_data = jsonl(rollout) if rollout_data is None else rollout_data
        code = r'''
import base64, os, pathlib, sys
cli, rollout, answer = (base64.b64decode(value) for value in sys.argv[1:4])
home=pathlib.Path(os.environ["CODEX_HOME"]); target=home/"sessions"/"synthetic.jsonl"
if sys.argv[5] == "True":
 target.parent.mkdir(parents=True); target.write_bytes(rollout)
if sys.argv[6] == "True":
 (home/"unexpected-link").symlink_to(home/"nonexistent")
if sys.argv[7] == "True":
 pathlib.Path(sys.argv[4]).write_bytes(answer)
sys.stdout.buffer.write(cli)
'''
        command = [sys.executable, "-c", code,
                   base64.b64encode(cli_data).decode(), base64.b64encode(rollout_data).decode(),
                   base64.b64encode(ANSWER.encode()).decode(), str(kwargs["answer_path"]),
                   str(capture_rollout), str(invalid_discovery), str(capture_answer)]
        return pilot._collect_local_attempt(attempt_dir, command, **kwargs)

    @staticmethod
    def start_failure_collector(attempt_dir, argv, **kwargs):
        return pilot._collect_local_attempt(attempt_dir, ["/definitely/missing/synthetic-command"], **kwargs)

    @classmethod
    def unsupported_record_collector(cls, attempt_dir, argv, **kwargs):
        result = cls.successful_collector(attempt_dir, argv, **kwargs)
        events_path = result["attempt_dir"] / "events.jsonl"
        events = events_path.read_bytes() + jsonl([{
            "type": "item.completed",
            "item": {"id": "future-1", "type": "future_call", "status": "completed"},
        }])
        events_path.write_bytes(events)
        return result

    def test_successful_synthetic_slot_captures_usage_completion_and_unknown_grades(self):
        summary = self.run_slot(1, self.successful_collector)
        self.assertEqual(summary["launch"], "attempted")
        self.assertEqual(summary["execution"], "completed")
        self.assertTrue(summary["usage"]["complete"])
        self.assertEqual(summary["completion_evidence"],
                         {"status": "matched", "finality": "unknown", "version_bounded": True})
        self.assertEqual(summary["tool_audit"]["unique_tool_call_count"], None)
        self.assertFalse(summary["tool_audit"]["os_execution_attestation"])
        self.assertEqual(summary["auditor_grade"], "unknown")
        self.assertEqual(summary["independent_model_assessment"], "unknown")
        self.assertFalse(summary["experimental_savings_claim_eligible"])
        self.assertEqual(summary["continuation"], {"allowed": True, "blockers": []})
        slot_root = self.root / "slots" / "001"
        self.assertTrue((slot_root / "codex-home" / "auth.json").is_symlink())
        self.assertEqual((slot_root / "codex-home" / "auth.json").resolve(), self.auth)
        self.assertEqual((slot_root / "codex-home" / "model-catalog.json").read_bytes(),
                         self.catalog.read_bytes())
        self.assertNotEqual(slot_root / "fixture", slot_root / "scratch")
        self.assertNotEqual(slot_root / "evidence", slot_root / "scratch")
        self.assertTrue(all((slot_root / "fixture" / name).stat().st_mode & 0o777 == 0o600
                            for name in ("context.txt", "observations.tsv")))
        attempt = slot_root / "evidence" / "attempt"
        self.assertTrue((attempt / "attempt.json").is_file())
        receipt = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(receipt["schema"], pilot.collector.ASSOCIATED_ATTEMPT_SCHEMA)
        self.assertEqual(receipt["rollout_discovery"]["initial_inventory"],
                         ["auth.json", "model-catalog.json"])
        capture_report = json.loads((slot_root / "evidence" / "capture-report.json").read_text())
        self.assertEqual(capture_report["answer"]["state"], "captured")
        self.assertEqual(capture_report["answer"]["text"], ANSWER)
        self.assertEqual(capture_report["answer"]["source"], "explicit")
        self.assertEqual(json.loads((attempt / "delivery.json").read_text())
                         ["rollout_discovery"]["candidate_count"], 1)
        retained = b"".join(path.read_bytes() for path in (slot_root / "evidence").rglob("*")
                            if path.is_file())
        self.assertNotIn(b"credential-placeholder", retained)
        raw_audit = json.loads((slot_root / "evidence" / "tool-audit.json").read_text())
        self.assertFalse(raw_audit["cross_source_joining"])
        self.assertFalse(raw_audit["shell_substring_classification"])
        self.assertEqual(raw_audit["semantic_routing"]["status"], "unknown")

    def native_runtime(self, codex_home):
        directory = codex_home / "tmp" / "arg0" / "codex-arg0Synthetic"
        directory.mkdir(parents=True)
        write(directory / ".lock", b"")
        for name in ("apply_patch", "applypatch", "codex-execve-wrapper"):
            (directory / name).symlink_to(self.codex)
        return directory

    def test_qualification_native_runtime_survives_collection_and_discovery(self):
        # Installed sandbox startup leaves these aliases before collection.
        # Rejecting all tmp entries, or all discovery symlinks, breaks this path.
        def access(codex, sshai, scratch, fixture, protected, environment, files):
            self.native_runtime(Path(environment["CODEX_HOME"]))
            return PilotFixture.fake_access(codex, sshai, scratch, fixture, protected,
                                            environment, files)
        with patch.object(self, "fake_access", side_effect=access):
            summary = self.run_slot(1, self.successful_collector)
        self.assertEqual(summary["continuation"], {"allowed": True, "blockers": []})
        attempt = self.root / "slots/001/evidence/attempt"
        request = json.loads((attempt / "attempt.json").read_text())
        self.assertEqual(request["rollout_discovery"]["initial_inventory"],
                         ["auth.json", "model-catalog.json", "tmp"])
        discovery = json.loads((attempt / "delivery.json").read_text())["rollout_discovery"]
        self.assertEqual(discovery["candidate_count"], 1)
        self.assertEqual(len(discovery["native_runtime"]["entries"]), 6)
        self.assertEqual(discovery["native_runtime"]["classification"], "pinned-arg0-layout")
        self.assertTrue((self.root / "slots/001/codex-home/tmp/arg0/codex-arg0Synthetic/apply_patch").is_symlink())

    def test_native_runtime_does_not_admit_unknown_files_or_foreign_aliases(self):
        for variant in ("foreign-alias", "regular-alias", "extra-jsonl", "linked-tmp",
                        "nested-directory", "nonempty-lock", "linked-lock", "entry-overflow"):
            with self.subTest(variant=variant):
                home = self.private / ("runtime-" + variant)
                home.mkdir()
                runtime = self.native_runtime(home)
                if variant == "foreign-alias":
                    (runtime / "apply_patch").unlink()
                    (runtime / "apply_patch").symlink_to(self.sshai)
                elif variant == "regular-alias":
                    (runtime / "apply_patch").unlink()
                    write(runtime / "apply_patch", b"not-an-alias")
                elif variant == "extra-jsonl":
                    write(runtime / "hidden.jsonl", b"{}\n")
                elif variant == "linked-tmp":
                    (home / "tmp").rename(home / "elsewhere")
                    (home / "tmp").symlink_to(home / "elsewhere")
                elif variant == "nested-directory":
                    (runtime / "nested").mkdir()
                elif variant == "nonempty-lock":
                    write(runtime / ".lock", b"unexpected")
                elif variant == "linked-lock":
                    (runtime / ".lock").unlink()
                    (runtime / ".lock").symlink_to(self.auth)
                else:
                    for index in range(pilot.MAX_DISCOVERY_ENTRIES):
                        (home / "tmp/arg0" / f"codex-arg0{index}").mkdir()
                with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
                     self.assertRaises(pilot.PilotInputError):
                    pilot._discover_rollouts(home)

    def test_process_created_native_aliases_are_not_lost_rollout_evidence(self):
        original = pilot.legacy._bounded_process
        def process(command, prompt, environment, cwd, timeout):
            captured = original(command, prompt, environment, cwd, timeout)
            self.native_runtime(Path(environment["CODEX_HOME"]))
            return captured
        with patch.object(pilot.legacy, "_bounded_process", side_effect=process):
            summary = self.run_slot(1, self.successful_collector)
        self.assertTrue(summary["continuation"]["allowed"])
        self.assertEqual(summary["completion_evidence"]["status"], "matched")

    def test_process_created_foreign_alias_preserves_capture_but_stops_next_slot(self):
        original = pilot.legacy._bounded_process
        def process(command, prompt, environment, cwd, timeout):
            captured = original(command, prompt, environment, cwd, timeout)
            directory = self.native_runtime(Path(environment["CODEX_HOME"]))
            (directory / "applypatch").unlink()
            (directory / "applypatch").symlink_to(self.auth)
            return captured
        with patch.object(pilot.legacy, "_bounded_process", side_effect=process):
            summary = self.run_slot(1, self.successful_collector)
        self.assert_capture_stops_later_slots(summary, "rollout_discovery_failed")

    def test_incomplete_initial_runtime_scan_refuses_before_process_receipt(self):
        scandir = os.scandir
        blocked = []
        def fail(path):
            if blocked and Path(path) == blocked[0]:
                raise PermissionError("synthetic unreadable runtime subtree")
            return scandir(path)
        def collect(attempt_dir, argv, **kwargs):
            blocked.append(self.native_runtime(kwargs["codex_home"]))
            return self.successful_collector(attempt_dir, argv, **kwargs)
        with patch.object(os, "scandir", side_effect=fail):
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(1, collect)
        self.assertFalse((self.root / "slots/001/evidence/attempt").exists())
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, self.successful_collector)
        self.assertFalse((self.root / "slots/002").exists())

    def assert_post_process_scan_failure_stops(self, native_runtime):
        original, scandir = pilot.legacy._bounded_process, os.scandir
        blocked = []
        def process(command, prompt, environment, cwd, timeout):
            captured = original(command, prompt, environment, cwd, timeout)
            home = Path(environment["CODEX_HOME"])
            if native_runtime:
                blocked.append(self.native_runtime(home))
            else:
                hidden = home / "sessions/hidden"
                hidden.mkdir()
                blocked.append(hidden)
            return captured
        def fail(path):
            if blocked and Path(path) == blocked[0]:
                raise PermissionError("synthetic incomplete discovery")
            return scandir(path)
        with patch.object(pilot.legacy, "_bounded_process", side_effect=process), \
             patch.object(os, "scandir", side_effect=fail):
            summary = self.run_slot(1, self.successful_collector)
        self.assert_capture_stops_later_slots(summary, "rollout_discovery_failed")
        attempt = self.root / "slots/001/evidence/attempt"
        self.assertEqual((attempt / "answer.txt").read_bytes(), ANSWER.encode())
        self.assertTrue((attempt / "events.jsonl").stat().st_size)

    def test_incomplete_post_process_runtime_scan_stops_continuation(self):
        self.assert_post_process_scan_failure_stops(native_runtime=True)

    def test_incomplete_post_process_rollout_scan_stops_continuation(self):
        self.assert_post_process_scan_failure_stops(native_runtime=False)

    def test_unknown_initial_content_still_refuses_before_process_receipt(self):
        def collect(attempt_dir, argv, **kwargs):
            self.native_runtime(kwargs["codex_home"])
            write(kwargs["codex_home"] / "prior.jsonl", b"{}\n")
            return self.successful_collector(attempt_dir, argv, **kwargs)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, collect)
        self.assertFalse((self.root / "slots/001/evidence/attempt").exists())
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, self.successful_collector)
        self.assertFalse((self.root / "slots/002").exists())

    def test_unsupported_tool_record_stops_later_slots_without_replacement(self):
        summary = self.run_slot(1, self.unsupported_record_collector)
        self.assertFalse(summary["continuation"]["allowed"])
        self.assertIn("unsupported_or_unallowed_tool_record", summary["continuation"]["blockers"])
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, self.successful_collector)
        self.assertFalse((self.root / "slots" / "002").exists())

    def assert_capture_stops_later_slots(self, summary, blocker):
        self.assertFalse(summary["continuation"]["allowed"])
        self.assertIn(blocker, summary["continuation"]["blockers"])
        first = self.root / "slots" / "001"
        retained = {str(path.relative_to(first)): path.read_bytes()
                    for path in (first / "evidence").rglob("*") if path.is_file()}
        retained["result.json"] = (first / "result.json").read_bytes()
        retained["reservation.json"] = (first / "reservation.json").read_bytes()
        for number, reason in ((1, "already reserved"), (2, "prior-slot blocker")):
            with self.assertRaisesRegex(pilot.PilotInputError, reason):
                self.run_slot(number, self.successful_collector)
        self.assertFalse((self.root / "slots" / "002").exists())
        for name, data in retained.items():
            self.assertEqual((first / name).read_bytes(), data)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            body = json.dumps(pilot.summarize(self.root))
        self.assertNotIn(str(self.base), body)
        self.assertNotIn(ANSWER, body)

    def test_missing_rollout_blocks_next_slot_before_reservation(self):
        def collect(*args, **kwargs):
            return self.successful_collector(*args, capture_rollout=False, **kwargs)
        summary = self.run_slot(1, collect)
        attempt = self.root / "slots" / "001" / "evidence" / "attempt"
        delivery = json.loads((attempt / "delivery.json").read_text())
        self.assertEqual(delivery["rollout"]["state"], "lost")
        self.assertEqual(delivery["rollout"]["reason"], "no_candidate_matched_cli_thread_identity")
        self.assertEqual((attempt / "answer.txt").read_bytes(), ANSWER.encode())
        self.assertFalse(summary["usage"]["complete"])
        self.assert_capture_stops_later_slots(summary, "rollout_capture_lost")

    def test_failed_rollout_discovery_blocks_next_slot_before_reservation(self):
        def collect(*args, **kwargs):
            return self.successful_collector(*args, invalid_discovery=True, **kwargs)
        summary = self.run_slot(1, collect)
        attempt = self.root / "slots" / "001" / "evidence" / "attempt"
        delivery = json.loads((attempt / "delivery.json").read_text())
        self.assertEqual(delivery["rollout"]["reason"], "isolated_rollout_discovery_failed")
        self.assertEqual(delivery["rollout_discovery"]["state"], "invalid")
        self.assertIn("unexpected symlink", delivery["rollout_discovery"]["reason"])
        self.assert_capture_stops_later_slots(summary, "rollout_discovery_failed")

    def test_missing_answer_blocks_next_slot_before_reservation(self):
        def collect(*args, **kwargs):
            return self.successful_collector(*args, capture_answer=False, **kwargs)
        summary = self.run_slot(1, collect)
        self.assertTrue(summary["usage"]["complete"])
        self.assert_capture_stops_later_slots(summary, "answer_capture_lost")

    def test_missing_command_identity_blocks_next_slot_before_reservation(self):
        cli, _ = completion_streams()
        for event in cli:
            if event.get("item", {}).get("type") == "command_execution":
                event["item"].pop("id")
        def collect(*args, **kwargs):
            return self.successful_collector(*args, cli_data=jsonl(cli), **kwargs)
        summary = self.run_slot(1, collect)
        self.assert_capture_stops_later_slots(summary, "capture_issue:cli:malformed_item")
        audit = json.loads((self.root / "slots" / "001" / "evidence" / "tool-audit.json").read_text())
        self.assertEqual(audit["status"], "unqualified")
        self.assertIn("malformed_item", {issue["code"] for issue in audit["unsupported_records"]})

    def test_malformed_json_record_blocks_next_slot_before_reservation(self):
        cli, _ = completion_streams()
        def collect(*args, **kwargs):
            return self.successful_collector(*args, cli_data=jsonl(cli) + b'{broken\n', **kwargs)
        summary = self.run_slot(1, collect)
        self.assert_capture_stops_later_slots(summary, "capture_issue:cli:malformed_json")

    def test_malformed_rollout_item_blocks_next_slot_before_reservation(self):
        _, rollout = completion_streams()
        rollout.insert(-1, {"type": "response_item", "payload": {"arguments": "opaque"}})
        def collect(*args, **kwargs):
            return self.successful_collector(*args, rollout_data=jsonl(rollout), **kwargs)
        summary = self.run_slot(1, collect)
        delivery = json.loads((self.root / "slots" / "001" / "evidence" / "attempt" / "delivery.json").read_text())
        self.assertEqual(delivery["rollout"]["state"], "captured")
        self.assert_capture_stops_later_slots(summary, "capture_issue:rollout:malformed_response_item")

    def assert_missing_function_identity_stops(self, *, remove_response_id):
        _, rollout = completion_streams()
        request = rollout[3]["payload"]
        request.pop("call_id")
        if remove_response_id:
            request.pop("id")
        def collect(*args, **kwargs):
            return self.successful_collector(*args, rollout_data=jsonl(rollout), **kwargs)
        summary = self.run_slot(1, collect)
        self.assert_capture_stops_later_slots(summary, "unsupported_or_unallowed_tool_record")
        audit = json.loads((self.root / "slots" / "001" / "evidence" / "tool-audit.json").read_text())
        self.assertIn("missing_tool_identity", {issue["code"] for issue in audit["unsupported_records"]})

    def test_function_request_without_identity_blocks_next_slot(self):
        self.assert_missing_function_identity_stops(remove_response_id=True)

    def test_response_id_cannot_replace_function_call_identity(self):
        self.assert_missing_function_identity_stops(remove_response_id=False)

    def assert_unallowed_grouped_lifecycle_stops(self, *, reverse):
        _, rollout = completion_streams()
        events = [{"type": "event_msg", "payload": {"type": kind, "call_id": "one-call"}}
                  for kind in ("exec_command_begin", "exec_command_end")]
        rollout[3:4] = events[::-1] if reverse else events
        def collect(*args, **kwargs):
            return self.successful_collector(*args, rollout_data=jsonl(rollout), **kwargs)
        summary = self.run_slot(1, collect)
        self.assert_capture_stops_later_slots(summary, "unsupported_or_unallowed_tool_record")
        audit = json.loads((self.root / "slots" / "001" / "evidence" / "tool-audit.json").read_text())
        self.assertEqual(audit["status"], "unqualified")
        self.assertIsNone(audit["unique_tool_call_count"])

    def test_grouped_begin_cannot_hide_unallowed_end(self):
        self.assert_unallowed_grouped_lifecycle_stops(reverse=False)

    def test_grouped_end_cannot_hide_unallowed_end_in_reverse_order(self):
        self.assert_unallowed_grouped_lifecycle_stops(reverse=True)

    def test_intentional_unknown_qualification_does_not_block_next_slot(self):
        summary = self.run_slot(1, self.successful_collector)
        self.assertEqual(summary["completion_evidence"]["finality"], "unknown")
        self.assertEqual(summary["tool_audit"]["semantic_routing"]["status"], "unknown")
        report = json.loads((self.root / "slots" / "001" / "evidence" / "capture-report.json").read_text())
        self.assertEqual(report["instrumentation"]["status"], "unknown")
        second = self.run_slot(2, self.successful_collector)
        self.assertEqual(second["execution"], "completed")
        self.assertTrue((self.root / "slots" / "002" / "reservation.json").is_file())

    def test_compaction_uncertainty_alone_does_not_block_next_slot(self):
        cli, rollout = completion_streams()
        cli.insert(-1, {"type": "compacted"})
        rollout.insert(-1, {"type": "compacted", "payload": {}})
        def collect(*args, **kwargs):
            return self.successful_collector(*args, cli_data=jsonl(cli),
                                             rollout_data=jsonl(rollout), **kwargs)
        summary = self.run_slot(1, collect)
        self.assertFalse(summary["usage"]["complete"])
        report = json.loads((self.root / "slots" / "001" / "evidence" / "capture-report.json").read_text())
        self.assertEqual(report["instrumentation"]["status"], "unknown")
        self.assertEqual({issue["code"] for issue in report["issues"]},
                         {"compaction_usage_uncertain"})
        self.assertEqual(summary["continuation"], {"allowed": True, "blockers": []})
        self.assertEqual(self.run_slot(2, self.successful_collector)["execution"], "completed")

    def test_unknown_rollout_record_blocks_next_slot(self):
        _, rollout = completion_streams()
        rollout.insert(-1, {"type": "future_record", "payload": {}})
        def collect(*args, **kwargs):
            return self.successful_collector(*args, rollout_data=jsonl(rollout), **kwargs)
        summary = self.run_slot(1, collect)
        self.assert_capture_stops_later_slots(summary, "unsupported_or_unallowed_tool_record")

    def assert_reserved_incomplete(self):
        first = self.root / "slots" / "001"
        before = {str(path.relative_to(first)): path.read_bytes()
                  for path in first.rglob("*") if path.is_file()}
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            rows = pilot.summarize(self.root)["slots"]
        self.assertEqual(rows[0]["state"], "reserved-incomplete")
        self.assertEqual(rows[0]["launch"], "attempted-or-unknown")
        self.assertEqual(rows[0]["execution"], "unknown")
        self.assertFalse(rows[0]["retryable"])
        self.assertEqual(rows[0]["continuation"], {
            "allowed": False, "blockers": ["reserved_slot_missing_result"],
        })
        self.assertEqual([row["state"] for row in rows[1:]], ["unattempted"] * 3)
        for number, reason in ((1, "already reserved"), (2, "retained result")):
            with self.assertRaisesRegex(pilot.PilotInputError, reason):
                self.run_slot(number, self.successful_collector)
        self.assertFalse((self.root / "slots" / "002").exists())
        after = {str(path.relative_to(first)): path.read_bytes()
                 for path in first.rglob("*") if path.is_file()}
        self.assertEqual(after, before)
        self.assertFalse((first / "result.json").exists())

    def test_reservation_only_summary_is_incomplete_not_unattempted(self):
        first = self.root / "slots" / "001"
        write(first / "reservation.json", json.dumps({
            "manifest_digest": self.manifest["digest"], "slot": self.manifest["slots"][0],
            "approval_sha256": sha(self.approval.read_bytes()), "one_shot": True,
        }).encode())
        self.assert_reserved_incomplete()

    def test_attempt_evidence_without_result_is_incomplete_not_unattempted(self):
        self.run_slot(1, self.successful_collector)
        first = self.root / "slots" / "001"
        self.assertTrue((first / "evidence" / "attempt" / "process.json").is_file())
        (first / "result.json").unlink()
        self.assert_reserved_incomplete()

    def test_interruption_before_reservation_receipt_is_not_unattempted(self):
        (self.root / "slots" / "001").mkdir()
        self.assert_reserved_incomplete()

    def test_start_failure_is_retained_and_same_slot_cannot_retry(self):
        summary = self.run_slot(1, self.start_failure_collector)
        self.assertEqual(summary["execution"], "failed")
        process = json.loads((self.root / "slots" / "001" / "evidence" / "attempt" / "process.json").read_text())
        self.assertIsNotNone(process["start_error"])
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, self.successful_collector)

    def test_run_requires_successful_retained_preflight(self):
        (self.root / "readiness" / "result.json").write_text("{}\n")
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, self.successful_collector)
        self.assertFalse((self.root / "slots" / "001").exists())

    def test_schedule_cannot_skip_prior_slot_and_requires_approval_before_reservation(self):
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, self.successful_collector)
        self.assertFalse((self.root / "slots" / "002").exists())
        denied = json.loads(self.approval.read_text())
        denied["approved"] = False
        self.approval.write_text(json.dumps(denied) + "\n")
        self.approval.chmod(0o600)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 1, self.approval, allow_model_run=True)
        self.assertFalse((self.root / "slots" / "001").exists())

    def test_prompt_bound_rejects_before_attempt_and_reserves_no_retry(self):
        def oversized(attempt_dir, argv, **kwargs):
            kwargs["prompt"] = b"x" * (capture.MAX_CAPTURE_BYTES + 1)
            return pilot._collect_local_attempt(attempt_dir, argv, **kwargs)
        with self.assertRaises(Exception):
            self.run_slot(1, oversized)
        self.assertTrue((self.root / "slots" / "001" / "result.json").is_file())
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, self.successful_collector)

    def test_sanitized_summary_has_no_paths_raw_output_or_savings_claim(self):
        self.run_slot(1, self.start_failure_collector)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            summary = pilot.summarize(self.root)
        body = json.dumps(summary)
        self.assertNotIn(str(self.base), body)
        self.assertFalse(summary["raw_outputs_included"])
        self.assertFalse(summary["private_paths_included"])
        self.assertIsNone(summary["comparative_savings_claim"])
        self.assertFalse(summary["experimental_savings_claim_eligible"])
        self.assertEqual(summary["assessor_context_ceiling"], 24)


class AuditTests(unittest.TestCase):
    def config(self):
        # Only the audit subsection is consumed by _audit.
        return {"codex_exec": {"allowed_audit_signatures": [
            {"source": "cli", "record_type": "command_execution",
             "tool_name": "command_execution", "evidence_kind": "call"},
        ]}}

    def test_allowed_lifecycle_is_recorded_without_execution_or_unique_count_claim(self):
        helper = CallEvidenceTests()
        report = helper.report(cli_items=[("item.completed", helper.command())])
        audit = pilot._audit(report, self.config())
        self.assertEqual(audit["status"], "bounded-recorded")
        self.assertEqual(audit["entry_count"], 1)
        self.assertFalse(audit["entries"][0]["execution_attested"])
        self.assertIsNone(audit["unique_tool_call_count"])
        self.assertFalse(audit["cross_source_joining"])
        self.assertIn("raw_observations", audit["entries"][0])

    def test_unknown_or_nonallowed_tool_record_makes_outcome_unqualified(self):
        helper = CallEvidenceTests()
        report = helper.report(cli_items=[("item.completed", {
            "id": "future-1", "type": "future_call", "status": "completed",
            "arguments": {"opaque": True},
        })])
        audit = pilot._audit(report, self.config())
        self.assertEqual(audit["status"], "unqualified")
        self.assertEqual(audit["unallowed_entries"], [1])
        self.assertTrue(audit["unsupported_records"])

    def test_each_grouped_signature_is_checked_without_cartesian_product(self):
        helper = CallEvidenceTests()
        events = [{"type": "dynamic_tool_call_request", "call_id": "one-call", "name": "first"},
                  {"type": "dynamic_tool_call_response", "call_id": "one-call", "name": "second"}]
        config = {"codex_exec": {"allowed_audit_signatures": [
            {"source": "rollout.event_msg", "record_type": "dynamic_tool_call_request",
             "tool_name": "first", "evidence_kind": "reported_request"},
            {"source": "rollout.event_msg", "record_type": "dynamic_tool_call_response",
             "tool_name": "second", "evidence_kind": "reported_call_lifecycle"},
        ]}}
        for ordered in (events, events[::-1]):
            with self.subTest(ordered=ordered):
                audit = pilot._audit(helper.report(events=ordered), config)
                self.assertEqual(audit["status"], "bounded-recorded")
                self.assertEqual(audit["entry_count"], 1)
                self.assertEqual(audit["observation_count"], 2)
                self.assertIsNone(audit["unique_tool_call_count"])

    def test_grouped_function_requests_check_every_tool_name(self):
        helper = CallEvidenceTests()
        responses = [{"type": "function_call", "call_id": "one-call", "name": name,
                      "arguments": "{}"} for name in ("allowed", "not-allowed")]
        config = {"codex_exec": {"allowed_audit_signatures": [
            {"source": "rollout.response_item", "record_type": "function_call",
             "tool_name": "allowed", "evidence_kind": "reported_call"},
        ]}}
        for ordered in (responses, responses[::-1]):
            with self.subTest(ordered=ordered):
                audit = pilot._audit(helper.report(responses=ordered), config)
                self.assertEqual(audit["status"], "unqualified")
                self.assertEqual(audit["unallowed_entries"], [1])

    def test_request_identity_is_explicit_not_a_record_position(self):
        helper = CallEvidenceTests()
        for kind in ("function_call", "custom_tool_call", "local_shell_call"):
            config = {"codex_exec": {"allowed_audit_signatures": [
                {"source": "rollout.response_item", "record_type": kind,
                 "tool_name": "allowed", "evidence_kind": "reported_call"},
            ]}}
            for identity in (None, "", 7):
                with self.subTest(kind=kind, identity=identity):
                    raw = {"type": kind, "id": "response-id", "call_id": identity,
                           "name": "allowed", "arguments": "{}"}
                    audit = pilot._audit(helper.report(responses=[raw]), config)
                    self.assertEqual(audit["status"], "unqualified")
                    self.assertIn("missing_tool_identity",
                                  {issue["code"] for issue in audit["unsupported_records"]})

    def test_request_and_lifecycle_with_same_id_are_not_double_joined(self):
        helper = CallEvidenceTests()
        report = helper.report(
            cli_items=[("item.completed", helper.command())],
            responses=[{"type": "function_call", "call_id": "cmd-1",
                        "name": "exec_command", "arguments": "{}"}],
        )
        audit = pilot._audit(report, self.config())
        self.assertEqual(audit["entry_count"], 2)
        self.assertEqual(audit["observation_count"], 2)
        self.assertIsNone(audit["unique_tool_call_count"])
        self.assertEqual(audit["status"], "unqualified")


class InstalledRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("SSHAI_TEST_CODEX_RUNTIME_SSHAI"),
                         "opt-in installed Codex no-model runtime check")
    def test_real_access_helper_then_synthetic_collection(self):
        # Reproduce the actual qualification -> collector boundary without a
        # model, real auth material, retained study slots, or live host access.
        codex = pilot.EXPECTED_CODEX_PATH
        sshai = Path(os.environ["SSHAI_TEST_CODEX_RUNTIME_SSHAI"]).resolve(strict=True)
        # Codex refuses arg0 helpers beneath the OS temporary directory.
        with tempfile.TemporaryDirectory(prefix="issue10-runtime-test-", dir="/Users/Shared") as tmp:
            base = Path(tmp)
            base.chmod(0o700)
            scratch, fixture = base / "scratch", base / "fixture"
            home, codex_home = base / "home", base / "codex-home"
            for directory in (scratch, fixture, home, codex_home, base / "protected"):
                directory.mkdir(mode=0o700)
            write(fixture / "context.txt", b"synthetic fixture\n")
            write(codex_home / "auth.json", b"{}\n")
            write(codex_home / "model-catalog.json", b"{}\n")
            environment = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                           "HOME": str(home), "CODEX_HOME": str(codex_home),
                           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
            receipt = pilot._qualify_access(
                codex, sshai, scratch, fixture, [base / "protected", Path.home()],
                environment, {"context.txt": {"sha256": sha(b"synthetic fixture\n")}})
            self.assertTrue(all(receipt["checks"].values()))
            self.assertTrue((codex_home / "tmp/arg0").is_dir())
            result = CollectionTests.successful_collector(
                base / "attempt", [], prompt=b"synthetic only", env=environment,
                cwd=scratch, timeout_seconds=10, codex_home=codex_home,
                answer_path=base / "answer.txt", association={"synthetic": True})
            self.assertEqual(result["process"]["execution"], "completed")
            self.assertEqual(result["delivery"]["rollout"]["state"], "captured")
            self.assertEqual(result["delivery"]["answer"]["state"], "captured")
            self.assertTrue(result["delivery"]["rollout_discovery"]["native_runtime"]["present"])


if __name__ == "__main__":
    unittest.main()
