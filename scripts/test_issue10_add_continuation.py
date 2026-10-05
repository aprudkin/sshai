#!/usr/bin/env python3
"""Only exact 8c prefix-five Add qualification; synthetic processes, no models."""
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as p
import benchmark_issue10_local_series as s
import test_issue10_local_pilot as f
import test_issue10_patch_continuation as prior_tests
from test_issue10_local_series import SeriesFixture

COMMIT = "8c7cdc8d933bfe7e6496f8b131dc109e8698dd46"


class AddContinuationTests(SeriesFixture):
    prefix = prior_tests.ContinuationTests.prefix
    recover = prior_tests.ContinuationTests.recover
    retained = prior_tests.ContinuationTests.retained
    continue_plan = prior_tests.ContinuationTests.continue_plan

    def retained_fifth(self):
        self.retained()
        self.seven_root = self.root
        self.root, manifest = self.continue_plan("8c-predecessor")
        self.four_receipt = self.receipt
        self.snapshot8 = self.base / "8c-sources"
        for name, expected in self.cont.ADD_PREDECESSOR_SOURCES.items():
            data = subprocess.check_output(["git", "show", COMMIT + ":" + name], cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(f.sha(data), expected)
            f.write(self.snapshot8 / name, data)
        manifest["sources"] = dict(self.cont.ADD_PREDECESSOR_SOURCES)
        manifest["digest"] = p._digest_object(manifest)
        f.write(self.root / "manifest.json", p._pretty(manifest))
        digest_patch = patch.object(self.cont, "ADD_PREDECESSOR_DIGEST", manifest["digest"])
        digest_patch.start()
        self.addCleanup(digest_patch.stop)
        self.patch_kind = "add"
        with patch.object(p, "REPO", self.snapshot8):
            self.ready()
            approval = self.approve(manifest)
            result = self.run_slot(5, approval, collector=self.patch_collector)
        self.assertEqual(result["continuation"], {"allowed": False, "blockers": ["unsupported_or_unallowed_tool_record"]})
        base = self.root / "slots/005"
        roles = {"events": "evidence/attempt/events.jsonl", "rollout": "evidence/attempt/rollout.jsonl",
                 "answer": "evidence/attempt/answer.txt", "process": "evidence/attempt/process.json",
                 "original_result": "result.json", "original_delivery": "evidence/attempt/delivery.json",
                 "prompt": "evidence/prompt.txt", "reservation": "reservation.json"}
        bindings = {key: {"path": str(base / name), "bytes": len((base / name).read_bytes()),
                          "sha256": f.sha((base / name).read_bytes())} for key, name in roles.items()}
        self.receipt = self.private / "slot-five-qualified.json"
        f.write(self.receipt, p._pretty({"schema": "sshai-benchmark/issue10-local-live-qualification-1",
            "phase_manifest_digest": manifest["digest"], "slot": manifest["slots"][4], "source_bindings": bindings,
            "source_contract": self.cont.SOURCE_CONTRACT, "source_references": self.cont.ADD_PRODUCER_SOURCES,
            "completion": json.loads((base / "evidence/completion-evidence.json").read_bytes()),
            "usage": json.loads((base / "evidence/capture-report.json").read_bytes())["usage"],
            "status": "independently_established_final", "method": "terminal_final_event",
            "independent_review": {"role": "synthetic-evidence-review", "verdict": "accepted"}}))
        self.snapshot7 = self.snapshot8  # Shared prepare helper's explicit predecessor snapshot argument.
        return manifest

    def test_exact_prefix_five_retains_four_producing_roots_and_only_6_to_36(self):
        old = self.retained_fifth()
        previous = self.root
        before = {str(path): f.sha(path.read_bytes()) for root in (self.original, self.seven_root, previous)
                  for path in (root / "slots").rglob("*") if path.is_file() and not path.is_symlink()}
        new, manifest = self.continue_plan("add-continuation")
        self.assertEqual(manifest["patch_continuation"]["schema"], self.cont.ADD_SCHEMA)
        self.assertEqual(manifest["patch_continuation"]["inherited_slots"], [1, 2, 3, 4, 5])
        self.assertEqual(manifest["patch_continuation"]["executable_slots"], list(range(6, 37)))
        self.assertEqual(manifest["patch_continuation"]["ancestor_binding_sha256"], p._sha(p._encoded(old["patch_continuation"])))
        self.assertEqual(manifest["slots"], old["slots"])
        self.assertEqual(manifest["config"], old["config"])
        self.assertEqual(before, {name: f.sha(Path(name).read_bytes()) for name in before})
        with patch.object(p, "EXPECTED_CODEX_PATH", self.codex):
            summary = s.summarize(new)
        self.assertEqual(len(summary["slots"]), 36)
        for index in (2, 3, 4):
            self.assertFalse(summary["slots"][index]["continuation"]["allowed"])
        self.root = new
        approval = self.approve(manifest)
        with self.assertRaises(p.PilotInputError): self.run_slot(6, approval)
        self.ready()
        for number in range(1, 6):
            with self.assertRaises(p.PilotInputError): self.run_slot(number, approval)
        with self.assertRaises(p.PilotInputError): self.run_slot(7, approval)
        for number in range(6, 37):
            self.assertEqual(self.run_slot(number, approval)["execution"], "completed")
            with self.assertRaises(p.PilotInputError): self.run_slot(number, approval)
        self.root = previous
        with self.assertRaises(p.PilotInputError): self.continue_plan("duplicate-owner")

    def test_nested_source_evidence_receipt_and_owner_drift_blocks_before_reservation(self):
        self.retained_fifth()
        previous = self.root
        new, manifest = self.continue_plan("add-continuation")
        self.root = new
        self.ready()
        approval = self.approve(manifest)
        for path in (previous / "patch-continuation-owner.json", self.four_receipt, self.receipt,
                     self.snapshot8 / "scripts/benchmark_issue10_intercepted_patch.py",
                     self.seven_root / "slots/004/result.json", self.original / "capture-recovery-owner.json",
                     previous / "slots/005/evidence/attempt/rollout.jsonl", new / "continuation/slot-005/tool-audit.json"):
            data, mode = path.read_bytes(), path.stat().st_mode & 0o777
            path.chmod(0o600)
            f.write(path, data + b"tamper")
            with self.assertRaises((p.PilotInputError, ValueError)): self.run_slot(6, approval)
            f.write(path, data, mode)
        self.assertFalse((new / "slots/006").exists())

    def test_review_receipt_and_exact_prefix_required_no_arbitrary_depth(self):
        self.retained_fifth()
        data = self.receipt.read_bytes()
        receipt = json.loads(data)
        receipt["independent_review"]["verdict"] = "pending"
        f.write(self.receipt, p._pretty(receipt))
        with self.assertRaises(p.PilotInputError): self.continue_plan("not-reviewed")
        f.write(self.receipt, data)
        (self.root / "slots/006").mkdir()
        with self.assertRaises(p.PilotInputError): self.continue_plan("wrong-prefix")
        (self.root / "slots/006").rmdir()
        new, _ = self.continue_plan("add-continuation")
        self.root = new
        with self.assertRaises(p.PilotInputError): self.continue_plan("deeper-lineage")
        self.assertFalse((new / "patch-continuation-owner.json").exists())

    def test_prospective_add_collection_then_future_unknown_stops_without_retry(self):
        self.retained_fifth()
        new, manifest = self.continue_plan("add-continuation")
        self.root = new
        self.ready()
        approval = self.approve(manifest)
        result = self.run_slot(6, approval, collector=self.patch_collector)
        self.assertTrue(result["continuation"]["allowed"])
        self.assertEqual(result["tool_audit"]["semantic_routing"]["status"], "unknown")
        self.inject_unknown = True
        result = self.run_slot(7, approval, collector=self.patch_collector)
        self.assertFalse(result["continuation"]["allowed"])
        with self.assertRaises(p.PilotInputError): self.run_slot(7, approval)
        with self.assertRaises(p.PilotInputError): self.run_slot(8, approval)
        self.assertFalse((new / "slots/008").exists())


if __name__ == "__main__":
    unittest.main()
