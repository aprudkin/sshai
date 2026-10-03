#!/usr/bin/env python3
"""Synthetic continuation state tests; never attest authentic predecessor ordering.

The accepted historical-controller digest is patched ONLY in this synthetic fixture.
The production digest remains the exact reviewed ad1532b controller. No Git history,
installed model, credentials, or private study is needed by these tests.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import test_issue10_local_pilot as fixtures


class ContinuationTests(fixtures.PilotFixture):
    def setUp(self):
        super().setUp()
        self.old_root = self.root
        self.snapshot = self.base / "old-source-snapshot"
        names = ["benchmark_issue10_local_pilot.py", "benchmark_issue10_v3_capture.py",
                 "benchmark_issue10_v3_collector.py", "benchmark_issue10_sandbox.py",
                 "benchmark_issue10.py"]
        for name in names:
            fixtures.write(self.snapshot / "scripts" / name,
                           (pilot.REPO / "scripts" / name).read_bytes())
        fixtures.write(self.snapshot / "docs/benchmarks/issue10-methodology-amendment.md",
                       pilot.AMENDMENT.read_bytes())
        self.old_source_sha = fixtures.sha(
            (self.snapshot / "scripts/benchmark_issue10_local_pilot.py").read_bytes())
        with patch.object(pilot, "REPO", self.snapshot), \
             patch.object(pilot, "AMENDMENT", self.snapshot / "docs/benchmarks/issue10-methodology-amendment.md"):
            self.old = self.prepare()
            with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
                 patch.object(pilot, "_ACCESS_QUALIFIER", self.fake_access):
                pilot.preflight(self.old_root)
            old_approval_bytes = self.approve(self.old).read_bytes()
            self.old_approval = self.base / "predecessor-approval.json"
            fixtures.write(self.old_approval, old_approval_bytes)
            with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
                 patch.object(pilot, "_ACCESS_QUALIFIER", self.runtime_access), \
                 patch.object(pilot, "_ATTEMPT_COLLECTOR", self.inventory_failure):
                with self.assertRaisesRegex(pilot.PilotInputError, "inventory changed"):
                    pilot.run_slot(self.old_root, 1, self.old_approval, allow_model_run=True)
        self.old_bytes = {name: (self.old_root / name).read_bytes() for name in
                          ("manifest.json", "slots/001/reservation.json", "slots/001/result.json")}
        self.root = self.private / "prospective-continuation"

    def runtime_access(self, *args):
        receipt = self.fake_access(*args)
        home = args[2].parent / "codex-home"
        helper = home / "tmp/arg0/codex-arg0synthetic"
        helper.mkdir(parents=True)
        fixtures.write(helper / ".lock", b"")
        for name in ("apply_patch", "applypatch", "codex-execve-wrapper"):
            (helper / name).symlink_to(self.codex)
        return receipt

    @staticmethod
    def inventory_failure(*args, **kwargs):
        raise pilot.PilotInputError("dedicated CODEX_HOME inventory changed after controlled provisioning")

    def continue_plan(self, root=None):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(pilot, "_BINARY_PROBE", self.fake_probe), \
             patch.object(pilot, "PREPROCESS_CONTROLLER_SHA256", self.old_source_sha, create=True):
            return pilot.prepare_continuation(
                root or self.root, self.old_root, self.snapshot,
                reason="Retain original inventory failure; qualify only remaining slots.",
                authorization_note="Synthetic autonomous authorization; NOT launch approval.",
            )

    def current(self):
        return patch.multiple(pilot, EXPECTED_CODEX_PATH=self.codex,
                              PREPROCESS_CONTROLLER_SHA256=self.old_source_sha,
                              _ACCESS_QUALIFIER=self.fake_access,
                              _ATTEMPT_COLLECTOR=fixtures.CollectionTests.successful_collector)

    def test_retained_failure_and_remaining_original_schedule_execute_once(self):
        manifest = self.continue_plan()
        self.assertEqual(manifest["slots"], self.old["slots"])
        self.assertEqual(manifest["continuation"]["inherited_slots"], [1])
        self.assertEqual(manifest["continuation"]["executable_slots"], [2, 3, 4])
        self.assertEqual(len(self.probes), 2)  # predecessor prepare and fresh prepare
        self.assertFalse((self.root / "slots/001").exists())
        with self.current():
            before = pilot.summarize(self.root)
            self.assertEqual([r.get("state") for r in before["slots"]],
                             ["retained-failure", "unattempted", "unattempted", "unattempted"])
            self.assertEqual(before["slots"][0]["launch"], "attempted-or-unknown")
            self.assertFalse(before["slots"][0]["continuation"]["allowed"])
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 2, self.old_approval, allow_model_run=True)
            pilot.preflight(self.root)
            approval = self.approve(manifest)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 2, self.old_approval, allow_model_run=True)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 2, approval)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 1, approval, allow_model_run=True)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 3, approval, allow_model_run=True)
            for number in (2, 3, 4):
                self.assertEqual(pilot.run_slot(self.root, number, approval, allow_model_run=True)
                                 ["execution"], "completed")
                with self.assertRaises(pilot.PilotInputError):
                    pilot.run_slot(self.root, number, approval, allow_model_run=True)
            summary = pilot.summarize(self.root)
        self.assertEqual(summary["scheduled_sessions"], 4)
        self.assertEqual(len(summary["slots"]), 4)
        self.assertFalse(summary["experimental_savings_claim_eligible"])
        self.assertIsNone(summary["comparative_savings_claim"])
        for name, data in self.old_bytes.items():
            self.assertEqual((self.old_root / name).read_bytes(), data)
        self.assertFalse(json.loads((self.root / "approval-template.json").read_bytes())["approved"])
        self.assertNotIn(str(self.private), json.dumps(summary))

    def test_duplicate_continuation_cannot_claim_remaining_slots(self):
        self.continue_plan()
        with self.assertRaises(pilot.PilotInputError):
            self.continue_plan(self.private / "second-continuation")
        self.assertFalse((self.private / "second-continuation").exists())

    def test_authentic_predecessor_digest_is_required_without_test_patch(self):
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                pilot.prepare_continuation(self.root, self.old_root, self.snapshot,
                                           reason="Synthetic", authorization_note="Synthetic")
        self.assertFalse(self.root.exists())

    def test_source_and_binary_and_config_and_fixture_tampering_rejected(self):
        for relative, target in (
            ("source", self.snapshot / "scripts/benchmark_issue10_v3_capture.py"),
            ("binary", self.sshai),
            ("config", self.old_root / "config.json"),
            ("fixture", self.old_root / "prepared/inputs/M01/context.txt"),
            ("catalog", self.old_root / "model-catalog.json"),
        ):
            with self.subTest(relative=relative):
                original, mode = target.read_bytes(), target.stat().st_mode & 0o777
                target.chmod(0o600 if relative != "binary" else 0o700)
                fixtures.write(target, original + b"tampered\n")
                if relative == "binary":
                    target.chmod(0o700)
                try:
                    with self.assertRaises((pilot.PilotInputError, ValueError)):
                        self.continue_plan()
                    self.assertFalse(self.root.exists())
                finally:
                    fixtures.write(target, original, mode)

    def test_any_attempt_or_unknown_evidence_blocks_exception(self):
        for relative in ("evidence/attempt", "evidence/last-message.txt", "codex-home/sessions",
                         "scratch/process.json", "evidence/attempt.json"):
            with self.subTest(relative=relative):
                target = self.old_root / "slots/001" / relative
                if relative in ("evidence/attempt", "codex-home/sessions"):
                    target.mkdir()
                else:
                    fixtures.write(target, b"synthetic attempt evidence")
                try:
                    with self.assertRaises(pilot.PilotInputError):
                        self.continue_plan()
                    self.assertFalse(self.root.exists())
                finally:
                    if target.is_dir():
                        target.rmdir()
                    else:
                        target.unlink()

    def test_remaining_directory_and_missing_readiness_block_exception(self):
        extra = self.old_root / "slots/002"
        extra.mkdir()
        with self.assertRaises(pilot.PilotInputError):
            self.continue_plan()
        extra.rmdir()
        ready = self.old_root / "readiness/result.json"
        data = ready.read_bytes()
        ready.unlink()
        try:
            with self.assertRaises(pilot.PilotInputError):
                self.continue_plan()
        finally:
            fixtures.write(ready, data)

    def test_unsupported_prefix_or_result_cannot_proceed(self):
        path = self.old_root / "slots/001/result.json"
        result = json.loads(path.read_bytes())
        variants = [dict(result, launch="attempted"),
                    dict(result, access={"status": "blocked", "stage": "fresh-access-qualification"}),
                    dict(result, continuation={"allowed": True, "blockers": []}),
                    dict(result, execution="completed")]
        for value in variants:
            fixtures.write(path, pilot._pretty(value))
            with self.assertRaises(pilot.PilotInputError):
                self.continue_plan()
            self.assertFalse(self.root.exists())
        fixtures.write(path, self.old_bytes["slots/001/result.json"])
        (self.old_root / "slots/001/reservation.json").unlink()
        with self.assertRaises(pilot.PilotInputError):
            self.continue_plan()

    def test_bound_predecessor_and_ownership_tampering_blocks_launch(self):
        self.continue_plan()
        with self.current():
            pilot.preflight(self.root)
            manifest = pilot.load_manifest(self.root)
            approval = self.approve(manifest)
            for path in (self.old_root / "slots/001/result.json",
                         self.old_root / "continuation-owner.json",
                         self.snapshot / "scripts/benchmark_issue10_sandbox.py"):
                original = path.read_bytes()
                fixtures.write(path, original + b"tampered")
                try:
                    with self.assertRaises(pilot.PilotInputError):
                        pilot.run_slot(self.root, 2, approval, allow_model_run=True)
                    self.assertFalse((self.root / "slots/002").exists())
                finally:
                    fixtures.write(path, original)

    def test_changed_branch_guidance_cannot_change_frozen_task(self):
        original = pilot._branch_guidance
        with patch.object(pilot, "_branch_guidance",
                          side_effect=lambda *args: original(*args) + "Changed instructions.\n"):
            with self.assertRaises(pilot.PilotInputError):
                self.continue_plan()
        self.assertFalse(self.root.exists())
        self.assertFalse((self.old_root / "continuation-owner.json").exists())

    def test_directory_disguised_as_attempt_file_blocks_exception(self):
        target = self.old_root / "slots/001/scratch/tmp"
        target.rmdir()
        fixtures.write(target, b"synthetic process evidence")
        with self.assertRaises(pilot.PilotInputError):
            self.continue_plan()
        self.assertFalse(self.root.exists())

    def test_changed_source_fixture_blocks_launch_before_reservation(self):
        manifest = self.continue_plan()
        with self.current():
            pilot.preflight(self.root)
            fixtures.write(self.bundle / "inputs/M01/context.txt", b"tampered synthetic fixture\n")
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 2, self.approve(manifest), allow_model_run=True)
        self.assertFalse((self.root / "slots/002").exists())

    def test_remaining_failure_has_no_continuation_bypass(self):
        manifest = self.continue_plan()
        with self.current():
            pilot.preflight(self.root)
            approval = self.approve(manifest)
            with patch.object(pilot, "_ATTEMPT_COLLECTOR", fixtures.CollectionTests.start_failure_collector):
                pilot.run_slot(self.root, 2, approval, allow_model_run=True)
            for number in (2, 3, 4):
                with self.assertRaises(pilot.PilotInputError):
                    pilot.run_slot(self.root, number, approval, allow_model_run=True)
        self.assertFalse((self.root / "slots/003").exists())
        self.assertFalse((self.root / "slots/004").exists())

    def test_failed_fresh_preflight_cannot_launch(self):
        manifest = self.continue_plan()
        with self.current(), patch.object(pilot, "_ACCESS_QUALIFIER",
                                          side_effect=pilot.PilotInputError("synthetic failure")):
            with self.assertRaises(pilot.PilotInputError):
                pilot.preflight(self.root)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 2, self.approve(manifest), allow_model_run=True)
        self.assertFalse((self.root / "slots/002").exists())


if __name__ == "__main__":
    unittest.main()
