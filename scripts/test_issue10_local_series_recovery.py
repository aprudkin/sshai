#!/usr/bin/env python3
"""One exact historical prefix, built entirely from synthetic current helpers."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_local_series as series
import test_issue10_local_pilot as fixtures
from test_issue10_local_series import SeriesFixture
from test_issue10_observer_capacity import large_streams

COMMIT = "4d283d77f4289d91dff1462a45629a32b8aeb1e1"


class RecoveryTests(SeriesFixture):
    def prefix(self, *, cleanup_runtime=False, thread_history=False):
        import benchmark_issue10_local_series_recovery as recovery
        self.recovery = recovery
        manifest = self.prepare()
        self.ready()
        self.snapshot = self.base / "historical-public-source"
        for name, expected in recovery.ORIGINAL_SOURCES.items():
            data = subprocess.check_output(["git", "show", COMMIT + ":" + name], cwd=Path(__file__).resolve().parents[1])
            self.assertEqual(fixtures.sha(data), expected)
            fixtures.write(self.snapshot / name, data)
        manifest.pop("capture_capacity")
        manifest["sources"] = dict(recovery.ORIGINAL_SOURCES)
        manifest["digest"] = pilot._digest_object(manifest)
        fixtures.write(self.root / "manifest.json", pilot._pretty(manifest))
        ready = json.loads((self.root / "readiness/result.json").read_bytes())
        ready["manifest_digest"] = manifest["digest"]
        ready["digest"] = pilot._digest_object(ready)
        fixtures.write(self.root / "readiness/result.json", pilot._pretty(ready))
        approval = {"path": self.approve(manifest)}
        approval.update(value=json.loads(approval["path"].read_bytes()), sha256=fixtures.sha(approval["path"].read_bytes()))
        def access_with_runtime(*args):
            receipt = self.fake_access(*args)
            if cleanup_runtime:
                helper = args[2].parent / "codex-home/tmp/arg0/codex-arg0SYN123"
                helper.mkdir(parents=True, mode=0o755)
                fixtures.write(helper / ".lock", b"")
                for name in ("apply_patch", "applypatch", "codex-execve-wrapper"):
                    (helper / name).symlink_to(self.codex)
            return receipt
        for number in (1, 2, 3):
            slot = manifest["slots"][number - 1]
            base = self.root / "slots" / f"{number:03}"
            base.mkdir()
            fixtures.write(base / "reservation.json", pilot._pretty({"manifest_digest": manifest["digest"], "slot": slot,
                "approval_sha256": approval["sha256"], "one_shot": True}))
            def collect(attempt, argv, **kwargs):
                cli, native = large_streams() if number == 3 else tuple(map(fixtures.jsonl, fixtures.completion_streams()))
                thread = f"synthetic-thread-{number}"
                cli = cli.replace(b"synthetic-thread", thread.encode())
                native = native.replace(b"synthetic-thread", thread.encode())
                scratch = kwargs["cwd"]
                fixtures.write(scratch / "cli-source", cli)
                fixtures.write(scratch / "native-source", native)
                cleanup = 'import shutil;shutil.rmtree(h/"tmp/arg0/codex-arg0SYN123");' if cleanup_runtime else ''
                history = ('(h/"thread_history_1.sqlite").write_bytes(b"d"*4096);'
                           '(h/"thread_history_1.sqlite-wal").write_bytes(b"x"*1854032);'
                           '(h/"thread_history_1.sqlite-shm").write_bytes(b"x"*32768);') if thread_history and number == 3 else ''
                code = ('import os,pathlib,sys;p=pathlib.Path;h=p(os.environ["CODEX_HOME"]);' + cleanup + history +
                    '(h/"sessions").mkdir();(h/"sessions/synthetic.jsonl").write_bytes(p("native-source").read_bytes());'
                    'p(sys.argv[1]).write_text(sys.argv[2]);sys.stdout.buffer.write(p("cli-source").read_bytes())')
                result = pilot._collect_local_attempt(attempt, [sys.executable, "-c", code, str(kwargs["answer_path"]), fixtures.ANSWER], **kwargs)
                # Synthetic native invocation receipt, never executed historical code/model.
                request = result["attempt"]
                request["argv_sha256"] = kwargs["association"]["argv_sha256"]
                fixtures.write(attempt / "attempt.json", pilot.legacy._canon(request))
                return result
            with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
                pilot.collect_reserved_slot(self.root, manifest, slot, approval, qualifier=access_with_runtime, attempt_collector=collect)
        return manifest

    def recover(self, name="capture-recovery"):
        new = self.private / name
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), self.current(), \
             patch.object(series, "_BINARY_PROBE", self.fake_probe):
            manifest = series.prepare_capture_recovery(new, self.root, self.snapshot,
                         reason="synthetic observer capacity defect", authorization_note="synthetic preparation only")
        return new, manifest

    def test_exact_size_only_prefix_is_retained_with_new_evidence_and_original_4_to_36(self):
        original = self.prefix()
        before = {str(p.relative_to(self.root)): fixtures.sha(p.read_bytes()) for p in (self.root / "slots").rglob("*") if p.is_file() and not p.is_symlink()}
        new, manifest = self.recover()
        self.assertEqual(manifest["slots"], original["slots"])
        self.assertEqual(manifest["capture_recovery"]["executable_slots"], list(range(4, 37)))
        self.assertEqual(manifest["budget"], original["budget"])
        self.assertEqual((self.root / "slots/003/evidence/attempt/rollout.jsonl").read_bytes(), b"")
        self.assertTrue((new / "recovery/slot-003/native-rollout.jsonl").is_file())
        self.assertEqual(before, {str(p.relative_to(self.root)): fixtures.sha(p.read_bytes()) for p in (self.root / "slots").rglob("*") if p.is_file() and not p.is_symlink()})
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            summary = series.summarize(new)
        self.assertEqual(len(summary["slots"]), 36)
        self.assertFalse(summary["slots"][2]["continuation"]["allowed"])
        self.assertEqual(summary["slots"][2]["completion_evidence"]["finality"], "unknown")
        self.assertIn("late", summary["slots"][2]["supplementary_capture"]["acquisition_limit"])
        with self.assertRaises(pilot.PilotInputError):
            self.recover("second-claim")
        self.assertFalse((self.private / "second-claim").exists())
        self.root = new
        approval = self.approve(manifest)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(4, approval)
        self.assertFalse((new / "slots/004").exists())
        self.ready()
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(4, approval, allow=False)
        with self.assertRaises(pilot.PilotInputError):
            self.run_slot(3, approval)
        result = self.run_slot(4, approval)
        self.assertEqual(result["execution"], "completed")
        self.assertFalse((new / "slots/003").exists())

    def test_cleaned_initial_alias_layout_keeps_declared_runtime_directory_ancestors(self):
        self.prefix(cleanup_runtime=True)
        request = json.loads((self.root / "slots/001/evidence/attempt/attempt.json").read_bytes())
        self.assertTrue(request["rollout_discovery"]["native_runtime"]["present"])
        self.assertEqual(len(request["rollout_discovery"]["native_runtime"]["entries"]), 6)
        self.assertEqual(list((self.root / "slots/001/codex-home/tmp/arg0").iterdir()), [])
        new, manifest = self.recover()
        census = manifest["capture_recovery"]["prefix_evidence"]["slots"][0]["inventory"]
        self.assertEqual(census["codex-home/tmp"], {"directory": True})
        self.assertEqual(census["codex-home/tmp/arg0"], {"directory": True})
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            self.assertEqual(series.load_manifest(new), manifest)
        (self.root / "slots/001/codex-home/tmp/foreign-directory").mkdir()
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                series.load_manifest(new)

    def test_completed_measured_thread_history_companions_are_metadata_only_and_exact_names(self):
        original = self.prefix(thread_history=True)
        home = self.root / "slots/003/codex-home"
        sizes = {"thread_history_1.sqlite-wal": 1854032, "thread_history_1.sqlite-shm": 32768}
        for name, size in sizes.items():
            self.assertNotIn(name, pilot.AUTH_NATIVE_METADATA)
            self.assertEqual((home / name).stat().st_size, size)
        new, manifest = self.recover()
        census = manifest["capture_recovery"]["prefix_evidence"]["slots"][2]["inventory"]
        for name, size in sizes.items():
            self.assertEqual(census["codex-home/" + name]["native_metadata"]["bytes"], size)
            self.assertEqual(census["codex-home/" + name]["native_metadata"]["mode"], 0o644)
            self.assertNotIn("sha256", census["codex-home/" + name])
            self.assertIn("contents not interpreted", census["codex-home/" + name]["native_metadata"]["provenance"])
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            self.assertEqual(series.load_manifest(new), manifest)
        fixtures.write(home / "thread_history_2.sqlite-wal", b"unknown", 0o644)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
            with self.assertRaises(pilot.PilotInputError):
                candidates, discovery = pilot._discover_rollouts(home)
                self.recovery._census(home.parent, original, 3, discovery, candidates[0])

    def test_measured_companions_reject_missing_database_unsafe_types_modes_and_oversize(self):
        original = self.prefix()
        home = self.root / "slots/003/codex-home"
        base = home / "thread_history_1.sqlite"
        companion = home / "thread_history_1.sqlite-wal"
        fixtures.write(companion, b"synthetic", 0o644)
        def refuses():
            with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex):
                with self.assertRaises(pilot.PilotInputError):
                    candidates, discovery = pilot._discover_rollouts(home)
                    self.recovery._census(home.parent, original, 3, discovery, candidates[0])
        refuses()
        fixtures.write(base, b"synthetic database", 0o644)
        companion.chmod(0o666)
        refuses()
        fixtures.write(companion, b"x" * (series.CAPTURE_CAPACITY["capture_limit"] + 1), 0o644)
        refuses()
        companion.unlink()
        companion.symlink_to(base)
        refuses()
        companion.unlink()
        companion.mkdir()
        refuses()
        self.assertFalse((self.root / "capture-recovery-owner.json").exists())

    def test_incomplete_original_slot_census_still_refuses_claim(self):
        manifest = self.prefix(cleanup_runtime=True)
        real_walk = self.recovery.os.walk
        def incomplete(top, *args, **kwargs):
            if Path(top) == self.root / "slots/001":
                kwargs["onerror"](PermissionError("synthetic incomplete census"))
                return iter(())
            return real_walk(top, *args, **kwargs)
        with patch.object(pilot, "EXPECTED_CODEX_PATH", self.codex), \
             patch.object(self.recovery.os, "walk", incomplete):
            with self.assertRaisesRegex(pilot.PilotInputError, "incomplete CODEX_HOME traversal"):
                self.recovery.prefix(self.root, manifest)
        self.assertFalse((self.root / "capture-recovery-owner.json").exists())

    def test_new_binding_owner_and_supplementary_tampering_refuse_before_reservation(self):
        self.prefix()
        old_root = self.root
        new, manifest = self.recover()
        self.root = new
        self.ready()
        approval = self.approve(manifest)
        for path in (old_root / "capture-recovery-owner.json", old_root / "slots/001/result.json",
                     new / "recovery/slot-003/native-rollout.jsonl", old_root / "slots/003/codex-home/sessions/synthetic.jsonl"):
            data, mode = path.read_bytes(), path.stat().st_mode & 0o777
            path.chmod(0o600)
            fixtures.write(path, data + b"tamper")
            try:
                with self.assertRaises((pilot.PilotInputError, ValueError)):
                    self.run_slot(4, approval)
            finally:
                fixtures.write(path, data, mode)
        self.assertFalse((new / "slots/004").exists())

    def test_other_prefix_population_and_native_candidate_ambiguity_refuse(self):
        self.prefix()
        extra = self.root / "slots/004"
        extra.mkdir()
        with self.assertRaises(pilot.PilotInputError):
            self.recover()
        extra.rmdir()
        candidate = self.root / "slots/003/codex-home/sessions/synthetic.jsonl"
        duplicate = candidate.parent / "other.jsonl"
        fixtures.write(duplicate, candidate.read_bytes())
        with self.assertRaises(pilot.PilotInputError):
            self.recover()
        duplicate.unlink()
        unsafe = self.root / "slots/003/scratch/unsafe-link"
        unsafe.symlink_to(candidate)
        with self.assertRaises(pilot.PilotInputError):
            self.recover()
        unsafe.unlink()
        cli = self.root / "slots/003/evidence/attempt/events.jsonl"
        data = cli.read_bytes()
        cli.write_bytes(data + fixtures.jsonl([{"type": "item.completed", "item": {"type": "error", "message": "Synthetic model rerouting"}}]))
        process_path = cli.parent / "process.json"
        process = json.loads(process_path.read_bytes())
        process["stdout_bytes"] = len(cli.read_bytes())
        fixtures.write(process_path, pilot._pretty(process))
        with self.assertRaises(pilot.PilotInputError):
            self.recover()
        self.assertFalse((self.root / "capture-recovery-owner.json").exists())

    def test_non_size_source_model_process_access_and_unknown_failures_refuse(self):
        self.prefix()
        paths = [self.snapshot / "scripts/benchmark_issue10_local_series.py",
                 self.root / "slots/003/evidence/attempt/process.json",
                 self.root / "slots/003/codex-home/sessions/synthetic.jsonl",
                 self.root / "slots/003/result.json"]
        for index, path in enumerate(paths):
            original = path.read_bytes()
            data = original + b"changed" if index == 0 else original
            if index == 1:
                value = json.loads(original); value["capture_overflow"] = True; data = pilot._pretty(value)
            if index == 2:
                data = original.replace(b'gpt-5.6-sol', b'foreign-model')
            if index == 3:
                value = json.loads(original); value["access"]["status"] = "failed"; data = pilot._pretty(value)
            fixtures.write(path, data)
            with self.assertRaises((pilot.PilotInputError, ValueError)):
                self.recover()
            fixtures.write(path, original)
        extra = self.root / "slots/003/evidence/attempt/unrecognized"
        fixtures.write(extra, b"unknown")
        with self.assertRaises(pilot.PilotInputError):
            self.recover()
        self.assertFalse((self.root / "capture-recovery-owner.json").exists())


if __name__ == "__main__":
    unittest.main()
