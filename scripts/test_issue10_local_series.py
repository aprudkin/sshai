#!/usr/bin/env python3
"""Synthetic local measurement preparation and launch-boundary checks; no real models."""
from __future__ import annotations

from collections import Counter
from contextlib import redirect_stdout
import importlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import test_issue10_local_pilot as fixtures


class SharedSeamTests(fixtures.PilotFixture):
    def test_explicit_case_selection_is_bounded_local_and_default_unchanged(self):
        _, default = pilot._fixture_inventory(self.bundle)
        self.assertEqual(set(default), {"M01", "M02"})
        for cases in ([], ["../M01"], ["M07"], ["M01", "M01"], ["L01"], list(range(7)), "M01"):
            with self.assertRaises(pilot.PilotInputError):
                pilot._fixture_inventory(self.bundle, cases=cases)
        _, selected = pilot._fixture_inventory(self.bundle, cases=("M02",))
        self.assertEqual(set(selected), {"M02"})

    def test_reserved_collection_requires_exact_retained_binding_and_is_one_shot(self):
        manifest = self.prepare()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            approval = pilot._approval(manifest, self.approve(manifest), True)
        base = self.root / "slots/001"
        base.mkdir()
        reservation = {"manifest_digest": manifest["digest"], "slot": manifest["slots"][0],
                       "approval_sha256": approval["sha256"], "one_shot": True}
        fixtures.write(base / "reservation.json", pilot._pretty(reservation))
        wrong = dict(reservation, approval_sha256="0" * 64)
        fixtures.write(base / "reservation.json", pilot._pretty(wrong))
        with self.assertRaises(pilot.PilotInputError):
            pilot.collect_reserved_slot(self.root, manifest, manifest["slots"][0], approval,
                                        qualifier=self.fake_access,
                                        attempt_collector=fixtures.CollectionTests.successful_collector)
        self.assertFalse((base / "evidence").exists())
        fixtures.write(base / "reservation.json", pilot._pretty(reservation))
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            result = pilot.collect_reserved_slot(self.root, manifest, manifest["slots"][0], approval,
                                                 qualifier=self.fake_access,
                                                 attempt_collector=fixtures.CollectionTests.successful_collector)
        self.assertEqual(result["execution"], "completed")
        with self.assertRaises(pilot.PilotInputError):
            pilot.collect_reserved_slot(self.root, manifest, manifest["slots"][0], approval,
                                        qualifier=self.fake_access,
                                        attempt_collector=fixtures.CollectionTests.successful_collector)


class SeriesFixture(fixtures.PilotFixture):
    def setUp(self):
        super().setUp()
        self.series = importlib.import_module("benchmark_issue10_local_series")
        # A synthetic immutable source-pin fixture avoids races with authorized
        # concurrent documentation edits; no source code is imported from here.
        source_root = self.base / "synthetic-current-sources"
        for name in self.series.SOURCE_PATHS:
            fixtures.write(source_root / name, (pilot.REPO / name).read_bytes())
        source_patch = patch.object(pilot, "REPO", source_root)
        source_patch.start()
        self.addCleanup(source_patch.stop)
        manifest = json.loads((self.bundle / "manifest.json").read_bytes())
        for number in range(3, 7):
            case = f"M{number:02}"
            data = f"case={case}\nsynthetic=true\n".encode()
            prompt = b"Investigate only {fixture_root}. Cite exact lines and do not repair.\n"
            files = []
            for relative, body in ((f"inputs/{case}/context.txt", data), (f"prompts/{case}.md", prompt)):
                fixtures.write(self.bundle / relative, body)
                manifest["files"].append({"path": relative, "bytes": len(body), "sha256": fixtures.sha(body)})
                if relative.startswith("inputs/"):
                    files.append({"file": "context.txt", "bytes": len(body), "lines": 2, "sha256": fixtures.sha(body)})
            manifest["cases"].append({"case_id": case, "files": files})
        fixtures.write(self.bundle / "manifest.json", pilot._pretty(manifest))
        self.root = self.private / "local-measured-series"

    def prepare(self):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(self.series, "_BINARY_PROBE", self.fake_probe):
            return self.series.prepare(self.root, self.bundle, self.codex, self.sshai, self.config,
                                       self.catalog, self.tool_overrides, self.auth,
                                       self.assessment_instructions, self.assessment_rubric)

    def approve(self, manifest):
        value = self.series.approval_template(manifest)
        value.update(approved=True, approved_at_utc="2030-01-01T00:00:00Z",
                     authorization_note="Synthetic task authorization; never a real model launch.",
                     local_pilot_review={"status": "passed", "provenance": "Synthetic reviewed prerequisite only."})
        path = self.base / "series-approval.json"
        fixtures.write(path, pilot._pretty(value))
        return path

    def current(self, collector=None):
        return patch.multiple(self.series, _ACCESS_QUALIFIER=self.fake_access,
                              _ATTEMPT_COLLECTOR=collector or fixtures.CollectionTests.successful_collector)

    def ready(self):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), self.current():
            return self.series.preflight(self.root)

    def run_slot(self, number, approval, collector=None, allow=True):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), self.current(collector):
            return self.series.run_slot(self.root, number, approval, allow_model_run=allow)


class SeriesTests(SeriesFixture):
    def test_prepare_has_exact_36_slot_counterbalanced_inventory_and_original_budget(self):
        manifest = self.prepare()
        slots = manifest["slots"]
        self.assertEqual(len(slots), 36)
        self.assertEqual([slot["slot"] for slot in slots], list(range(1, 37)))
        self.assertEqual(Counter((s["case_id"], s["replicate"], s["arm"]) for s in slots),
                         Counter((f"M{case:02}", rep, arm) for case in range(1, 7)
                                 for rep in (1, 2, 3) for arm in ("baseline", "sshai")))
        self.assertEqual(Counter(s["arm"] for s in slots[::2]), {"baseline": 9, "sshai": 9})
        for index in range(0, 36, 2):
            self.assertEqual(slots[index]["pair_id"], slots[index + 1]["pair_id"])
            self.assertNotEqual(slots[index]["arm"], slots[index + 1]["arm"])
        self.assertEqual(manifest["original_diagnostic_session_ceiling"], 120)
        self.assertEqual(manifest["budget"], {"local_pilot_consumed": 4, "local_measurement_allocated": 36,
                                            "remote_pilot_allocated": 8, "remote_measurement_allocated": 72})
        self.assertEqual(sum(manifest["budget"].values()), 120)
        self.assertFalse(manifest["experimental_savings_claim_eligible"])
        self.assertFalse(json.loads((self.root / "approval-template.json").read_bytes())["approved"])
        self.assertEqual(set(p.name for p in (self.root / "prepared/inputs").iterdir()),
                         {"M01", "M02", "M03", "M04", "M05", "M06"})
        self.assertFalse((self.root / "prepared/evaluator").exists())
        self.assertEqual(len(self.probes), 1)
        self.assertFalse(any((self.root / "slots").iterdir()))

    def test_schedule_is_exact_local_projection_of_existing_frozen_v3_recipe(self):
        v3 = importlib.import_module("benchmark_issue10_v3")
        original = [s for s in v3.schedule("measurement", 1010) if s["series"] == "M"]
        actual = self.series.schedule()
        self.assertEqual([s["protocol_slot"] for s in actual], [s["slot"] for s in original])
        for a, b in zip(actual, original, strict=True):
            self.assertEqual({k: v for k, v in a.items() if k not in {"slot", "protocol_slot"}},
                             {k: v for k, v in b.items() if k != "slot"})

    def test_all_36_slots_are_ordered_one_shot_and_never_inherit_pilot_failures(self):
        manifest = self.prepare()
        self.ready()
        approval = self.approve(manifest)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, approval)
        for number in range(1, 37):
            result = self.run_slot(number, approval)
            self.assertEqual(result["execution"], "completed")
            self.assertEqual(result["independent_model_assessment"], "unknown")
            self.assertEqual(result["tool_audit"]["semantic_routing"]["status"], "unknown")
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(number, approval)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(37, approval)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            summary = self.series.summarize(self.root)
        self.assertEqual(summary["scheduled_sessions"], 36)
        self.assertEqual(len(summary["slots"]), 36)
        self.assertIsNone(summary["comparative_savings_claim"])
        self.assertFalse(summary["experimental_savings_claim_eligible"])
        self.assertNotIn(str(self.private), json.dumps(summary))

    def test_launch_requires_fresh_readiness_approval_and_actual_pilot_review_provenance(self):
        manifest = self.prepare()
        approval = self.approve(manifest)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, approval)
        self.ready()
        for change in ({"manifest_digest": "0" * 64}, {"approved": False},
                       {"local_pilot_review": {"status": "pending", "provenance": ""}}):
            original = approval.read_bytes()
            value = dict(json.loads(original), **change)
            fixtures.write(approval, pilot._pretty(value))
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(1, approval)
            fixtures.write(approval, original)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, approval, allow=False)
        self.assertFalse((self.root / "slots/001").exists())

    def test_preflight_and_per_slot_access_use_task_only_paths_and_600_second_limit(self):
        self.prepare()
        seen = []
        def access(codex, sshai, scratch, fixture, protected, environment, material):
            seen.append(fixture)
            self.assertEqual(environment["SSHAI_ROOT"], str(scratch / "sshai-root"))
            self.assertEqual(environment["CODEX_HOME"], str(scratch.parent / "codex-home"))
            self.assertIn(self.private, protected)
            self.assertNotIn(self.auth.read_text(), json.dumps(material))
            return self.fake_access(codex, sshai, scratch, fixture, protected, environment, material)
        def collect(attempt_dir, argv, **kwargs):
            self.assertEqual(kwargs["timeout_seconds"], 600)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--ignore-rules", argv)
            self.assertIn("gpt-5.6-sol", argv)
            return fixtures.CollectionTests.successful_collector(attempt_dir, argv, **kwargs)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(self.series, "_ACCESS_QUALIFIER", access), \
             patch.object(self.series, "_ATTEMPT_COLLECTOR", collect):
            self.series.preflight(self.root)
            self.assertEqual(len(seen), 6)
            manifest = self.series.load_manifest(self.root)
            self.series.run_slot(self.root, 1, self.approve(manifest), allow_model_run=True)
        self.assertEqual(len(seen), 7)
        self.assertTrue(all("readiness" in str(path) for path in seen[:6]))
        self.assertEqual(seen[-1], self.root / "slots/001/fixture")

    def test_capture_loss_and_unsafe_or_unsupported_evidence_stop_without_replacement(self):
        def lost(*args, **kwargs):
            return fixtures.CollectionTests.successful_collector(*args, capture_rollout=False, **kwargs)
        for collector in (lost, fixtures.CollectionTests.unsupported_record_collector):
            root = self.root
            manifest = self.prepare()
            self.ready()
            approval = self.approve(manifest)
            result = self.run_slot(1, approval, collector)
            self.assertFalse(result["continuation"]["allowed"])
            result_bytes = (root / "slots/001/result.json").read_bytes()
            for number in (1, 2):
                with self.assertRaises(pilot.PilotInputError):
                    self.run_slot(number, approval)
            self.assertFalse((root / "slots/002").exists())
            self.assertEqual((root / "slots/001/result.json").read_bytes(), result_bytes)
            self.root = self.private / "second-capture-test"

    def test_fixture_access_failure_retains_consumed_slot_and_stops_later_slots(self):
        manifest = self.prepare()
        self.ready()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(self.series, "_ACCESS_QUALIFIER", side_effect=pilot.PilotInputError("synthetic unsafe access")):
            with self.assertRaises(pilot.PilotInputError):
                self.series.run_slot(self.root, 1, self.approve(manifest), allow_model_run=True)
        result = json.loads((self.root / "slots/001/result.json").read_bytes())
        self.assertEqual(result["launch"], "not-started")
        self.assertFalse(result["continuation"]["allowed"])
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, self.approve(manifest))

    def test_tampered_inputs_sources_schedule_and_budget_reject_before_reservation(self):
        manifest = self.prepare()
        self.ready()
        approval = self.approve(manifest)
        for path in (self.root / "config.json", self.root / "model-catalog.json",
                     self.root / "prepared/inputs/M06/context.txt", self.sshai,
                     self.root / "prepared/assessment/rubric.md"):
            original, mode = path.read_bytes(), path.stat().st_mode & 0o777
            path.chmod(0o700)
            fixtures.write(path, original + b"tampered", 0o700)
            try:
                with self.assertRaises((pilot.PilotInputError, ValueError)):
                    self.run_slot(1, approval)
            finally:
                fixtures.write(path, original, mode)
        path = self.root / "manifest.json"
        original = path.read_bytes()
        for change in ({"budget": {"local_measurement_allocated": 37}}, {"session_count": 37},
                       {"slots": list(reversed(manifest["slots"]))}, {"sources": {}}, {"schedule_seed": 1111}):
            value = dict(manifest, **change)
            value["digest"] = pilot._digest_object(value)
            fixtures.write(path, pilot._pretty(value))
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(1, approval)
        fixtures.write(path, original)
        self.assertFalse((self.root / "slots/001").exists())

    def test_cli_preparation_and_readiness_do_not_manufacture_launch_authority(self):
        arguments = ["prepare", str(self.root)]
        for flag, path in (("fixtures", self.bundle), ("codex", self.codex), ("sshai", self.sshai),
                           ("config", self.config), ("model-catalog", self.catalog),
                           ("tool-overrides", self.tool_overrides), ("auth-file", self.auth),
                           ("assessment-instructions", self.assessment_instructions),
                           ("assessment-rubric", self.assessment_rubric)):
            arguments.extend(["--" + flag, str(path)])
        out = io.StringIO()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), self.current(), \
             patch.object(self.series, "_BINARY_PROBE", self.fake_probe), redirect_stdout(out):
            self.assertEqual(self.series._main(arguments), 0)
            prepared = json.loads(out.getvalue())
            self.assertEqual(prepared["model_launches"], 0)
            self.assertTrue(prepared["pilot_review_required"])
            self.assertNotIn(str(self.private), out.getvalue())
            out.seek(0); out.truncate()
            self.assertEqual(self.series._main(["preflight", str(self.root)]), 0)
            self.assertEqual(json.loads(out.getvalue())["scheduled_slots_consumed"], 0)
            manifest = self.series.load_manifest(self.root)
            approval = self.approve(manifest)
            with self.assertRaises(pilot.PilotInputError):
                self.series._main(["run-slot", str(self.root), "--slot", "1", "--approval", str(approval)])
        self.assertFalse(any((self.root / "slots").iterdir()))

    def test_native_canary_helpers_survive_collection_without_becoming_tool_calls(self):
        manifest = self.prepare()
        self.ready()
        def native_access(*args):
            receipt = self.fake_access(*args)
            helper = args[2].parent / "codex-home/tmp/arg0/codex-arg0ABC123"
            helper.mkdir(parents=True)
            fixtures.write(helper / ".lock", b"")
            for name in ("apply_patch", "applypatch", "codex-execve-wrapper"):
                (helper / name).symlink_to(self.codex)
            return receipt
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), self.current(), \
             patch.object(self.series, "_ACCESS_QUALIFIER", native_access):
            result = self.series.run_slot(self.root, 1, self.approve(manifest), allow_model_run=True)
        self.assertEqual(result["execution"], "completed")
        request = json.loads((self.root / "slots/001/evidence/attempt/attempt.json").read_bytes())
        self.assertTrue(request["rollout_discovery"]["native_runtime"]["present"])
        self.assertFalse(result["tool_audit"]["os_execution_attestation"])

    def test_cli_error_item_blocks_observable_model_rerouting_without_relabelling_process(self):
        manifest = self.prepare()
        self.ready()
        cli, rollout = fixtures.completion_streams()
        cli.insert(-1, {"type": "item.completed", "item": {
            "id": "synthetic-error", "type": "error", "message": "Synthetic model routing warning"}})
        def rerouted(*args, **kwargs):
            return fixtures.CollectionTests.successful_collector(*args, cli_data=fixtures.jsonl(cli),
                                                                  rollout_data=fixtures.jsonl(rollout), **kwargs)
        approval = self.approve(manifest)
        result = self.run_slot(1, approval, rerouted)
        self.assertEqual(result["execution"], "completed")
        self.assertIn("cli_error_item", result["continuation"]["blockers"])
        self.assertEqual(result["independent_model_assessment"], "unknown")
        self.assertTrue((self.root / "slots/001/evidence/attempt/answer.txt").exists())
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, approval)
        self.assertFalse((self.root / "slots/002").exists())

    def test_observed_model_effort_mismatch_or_missing_context_blocks_later_slots(self):
        for variant in ("model", "effort", "missing"):
            manifest = self.prepare()
            self.ready()
            cli, rollout = fixtures.completion_streams()
            if variant == "missing":
                rollout = [r for r in rollout if r.get("type") != "turn_context"]
            else:
                for record in rollout:
                    if record.get("type") == "turn_context":
                        record["payload"][variant] = "synthetic-foreign"
            def changed_context(*args, **kwargs):
                return fixtures.CollectionTests.successful_collector(*args, cli_data=fixtures.jsonl(cli),
                                                                      rollout_data=fixtures.jsonl(rollout), **kwargs)
            approval = self.approve(manifest)
            result = self.run_slot(1, approval, changed_context)
            self.assertIn("observed_model_context_missing_or_mismatched", result["continuation"]["blockers"])
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(2, approval)
            self.assertFalse((self.root / "slots/002").exists())
            self.root = self.private / ("context-variant-" + variant)

    def test_capture_overflow_consumes_slot_and_stops_later_reservations(self):
        manifest = self.prepare()
        self.ready()
        def overflow(attempt_dir, argv, **kwargs):
            command = [sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*1_010_000)"]
            return pilot._collect_local_attempt(attempt_dir, command, **kwargs)
        approval = self.approve(manifest)
        result = self.run_slot(1, approval, overflow)
        self.assertIn("capture_overflow", result["continuation"]["blockers"])
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(2, approval)
        self.assertFalse((self.root / "slots/002").exists())

    def test_source_fixture_and_current_code_pins_reject_before_launch(self):
        manifest = self.prepare()
        self.ready()
        approval = self.approve(manifest)
        path = self.bundle / "inputs/M05/context.txt"
        original = path.read_bytes()
        fixtures.write(path, b"changed synthetic fixture")
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(1, approval)
        fixtures.write(path, original)
        sources = self.base / "synthetic-source-snapshot"
        for name in self.series.SOURCE_PATHS:
            fixtures.write(sources / name, (pilot.REPO / name).read_bytes())
        fixtures.write(sources / "scripts/benchmark_issue10_local_series.py", b"changed synthetic source")
        with patch.object(pilot, "REPO", sources):
            with self.assertRaises(pilot.PilotInputError):
                self.run_slot(1, approval)
        self.assertFalse((self.root / "slots/001").exists())

    def test_foreign_slot_and_symlinked_prepared_material_refuse(self):
        self.prepare()
        foreign = self.root / "slots/037"
        foreign.mkdir()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                self.series.load_manifest(self.root)
        foreign.rmdir()
        source = self.root / "prepared/inputs/M06"
        source.parent.chmod(0o700)
        source.rename(source.parent / "real-M06")
        source.symlink_to(source.parent / "real-M06", target_is_directory=True)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises((pilot.PilotInputError, ValueError)):
                self.series.load_manifest(self.root)

    def test_reserved_incomplete_summary_retains_full_denominator_and_never_repairs(self):
        self.prepare()
        base = self.root / "slots/001"
        base.mkdir()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            summary = self.series.summarize(self.root)
        self.assertEqual(summary["slots"][0]["state"], "reserved-incomplete")
        self.assertEqual(summary["slots"][0]["launch"], "attempted-or-unknown")
        self.assertEqual(summary["slots"][1]["state"], "unattempted")
        self.assertEqual(len(summary["slots"]), 36)
        self.assertFalse((base / "result.json").exists())

    def test_failed_preflight_is_retained_one_shot_and_never_consumes_diagnostics(self):
        self.prepare()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(self.series, "_ACCESS_QUALIFIER", side_effect=pilot.PilotInputError("synthetic access failure")):
            with self.assertRaises(pilot.PilotInputError):
                self.series.preflight(self.root)
        self.assertEqual(json.loads((self.root / "readiness/result.json").read_bytes())["status"], "blocked")
        self.assertFalse(any((self.root / "slots").iterdir()))
        with self.assertRaises(pilot.PilotInputError):
            self.ready()


if __name__ == "__main__":
    unittest.main()
