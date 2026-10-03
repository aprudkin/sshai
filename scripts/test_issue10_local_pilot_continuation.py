#!/usr/bin/env python3
"""Synthetic continuation state tests; never attest authentic predecessor ordering.

The accepted historical-controller digest is patched ONLY in this synthetic fixture.
The production digest remains the exact reviewed ad1532b controller. No Git history,
installed model, credentials, or private study is needed by these tests.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import test_issue10_local_pilot as fixtures


class ContinuationFixture(fixtures.PilotFixture):
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


class ContinuationTests(ContinuationFixture):
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


AUTH_ERROR = "Your access token could not be refreshed because your refresh token was revoked. Please log out and sign in again."


def auth_streams():
    cli = [{"type": "thread.started", "thread_id": "synthetic-auth-thread"},
           {"type": "turn.started"}, {"type": "error", "message": AUTH_ERROR},
           {"type": "turn.failed", "error": {"message": AUTH_ERROR}}]
    payloads = [
        ("session_meta", {"id": "synthetic-auth-thread", "session_id": "synthetic-auth-thread",
                          "cli_version": "0.151.0", "history_mode": "paginated",
                          "source": "exec", "model_provider": "openai"}),
        ("event_msg", {"type": "task_started", "turn_id": "synthetic-turn", "started_at": 100,
                       "model_context_window": 1000, "collaboration_mode_kind": "default"}),
    ]
    for role in ("developer", "user"):
        payloads.append(("response_item", {
            "type": "message", "id": "synthetic-" + role, "role": role,
            "content": [{"type": "input_text", "text": "Synthetic input mentioning tools is not a call."}],
            "internal_chat_message_metadata_passthrough": {"turn_id": "synthetic-turn"},
        }))
    payloads.extend([
        ("world_state", {"full": True, "state": {"model": "gpt-5.6-sol", "permissions": {"instructions": "synthetic"}}}),
        ("turn_context", {"turn_id": "synthetic-turn", "model": "gpt-5.6-sol", "effort": "high"}),
        ("response_item", {"type": "message", "id": "synthetic-user2", "role": "user",
                           "content": [{"type": "input_text", "text": "Synthetic task."}],
                           "internal_chat_message_metadata_passthrough": {"turn_id": "synthetic-turn"}}),
        ("event_msg", {"type": "item_completed", "thread_id": "synthetic-auth-thread", "turn_id": "synthetic-turn",
                       "item": {"type": "UserMessage", "id": "synthetic-user-item",
                                "content": [{"type": "text", "text": "Synthetic task.", "text_elements": []}]},
                       "started_at_ms": 100000, "completed_at_ms": 100000}),
        ("event_msg", {"type": "task_complete", "turn_id": "synthetic-turn", "last_agent_message": None,
                       "error": {"message": AUTH_ERROR, "codex_error_info": "unauthorized"},
                       "started_at": 100, "completed_at": 101, "duration_ms": 1000}),
    ])
    rollout = [{"type": kind, "ordinal": index, "timestamp": "2030-01-01T00:00:00Z", "payload": payload}
               for index, (kind, payload) in enumerate(payloads)]
    return cli, rollout


class AuthContinuationTests(ContinuationFixture):
    def config_value(self):
        # This frozen executable is a synthetic subprocess, not an installed model.
        cli, rollout = auth_streams()
        code = (f"#!{sys.executable}\n" +
                "import os,pathlib,sys\n" +
                f"events={json.dumps(cli)!r}; rollout={json.dumps(rollout)!r}\n" +
                "import json\n" +
                "sys.stdin.buffer.read()\n" +
                "home=pathlib.Path(os.environ['CODEX_HOME'])\n" +
                "names=['config.toml','installation_id','thread_history_1.sqlite','thread-writer-locks/.coordination.lock']\n" +
                "names += [prefix+suffix for prefix in ['goals_1.sqlite','logs_2.sqlite','memories_1.sqlite','queue_1.sqlite','state_5.sqlite'] for suffix in ['', '-shm', '-wal']]\n" +
                "for name in names:\n p=home/name; p.parent.mkdir(exist_ok=True); p.write_bytes(b'synthetic native metadata')\n" +
                "target=home/'sessions'/'synthetic-auth.jsonl'\n" +
                "target.parent.mkdir(); target.write_text(''.join(json.dumps(r)+'\\n' for r in json.loads(rollout)))\n" +
                "sys.stdout.write(''.join(json.dumps(r)+'\\n' for r in json.loads(events)))\n" +
                "sys.stderr.write('synthetic startup authentication failure\\n')\n" +
                "sys.exit(1)\n")
        fixtures.write(self.codex, code.encode(), 0o700)
        return super().config_value()

    def setUp(self):
        super().setUp()
        self.first_root = self.root
        self.first_manifest = self.continue_plan()
        self.first_snapshot = self.base / "first-continuation-sources"
        for name in self.first_manifest["sources"]:
            fixtures.write(self.first_snapshot / name, (pilot.REPO / name).read_bytes())
        self.auth_source_sha = self.first_manifest["sources"]["scripts/benchmark_issue10_local_pilot.py"]
        with self.current(), patch.object(pilot, "_ATTEMPT_COLLECTOR", None):
            pilot.preflight(self.first_root)
            first_approval_bytes = self.approve(self.first_manifest).read_bytes()
            self.first_approval = self.base / "first-continuation-approval.json"
            fixtures.write(self.first_approval, first_approval_bytes)
            result = pilot.run_slot(self.first_root, 2, self.first_approval, allow_model_run=True)
            self.assertEqual(result["launch"], "attempted")
            self.assertEqual(result["execution"], "failed")
        self.failed_bytes = (self.first_root / "slots/002/result.json").read_bytes()
        self.root = self.private / "auth-repaired-continuation"

    def current(self):
        return patch.multiple(pilot, EXPECTED_CODEX_PATH=self.codex,
                              PREPROCESS_CONTROLLER_SHA256=self.old_source_sha,
                              AUTH_CONTROLLER_SHA256=getattr(self, "auth_source_sha", self.old_source_sha),
                              _ACCESS_QUALIFIER=self.fake_access,
                              _ATTEMPT_COLLECTOR=fixtures.CollectionTests.successful_collector,
                              create=True)

    def auth_plan(self, root=None):
        with self.current(), patch.object(pilot, "_BINARY_PROBE", self.fake_probe):
            return pilot.prepare_auth_continuation(
                root or self.root, self.first_root, self.first_snapshot,
                reason="Keep failures, qualify only original remaining slots.",
                authorization_note="Synthetic advance authority; not launch approval.",
                auth_repair_note="Synthetic supported browser login restored; backend not qualified.",
            )

    def test_two_inherited_failures_and_only_original_3_then_4(self):
        manifest = self.auth_plan()
        self.assertEqual(manifest["slots"], self.old["slots"])
        self.assertEqual(manifest["continuation"]["inherited_slots"], [1, 2])
        self.assertEqual(manifest["continuation"]["executable_slots"], [3, 4])
        with self.current():
            rows = pilot.summarize(self.root)["slots"]
            self.assertEqual([row.get("state") for row in rows],
                             ["retained-failure", "retained-failure", "unattempted", "unattempted"])
            self.assertEqual([row["launch"] for row in rows[:2]], ["attempted-or-unknown", "attempted"])
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 3, self.first_approval, allow_model_run=True)
            pilot.preflight(self.root)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 3, self.first_approval, allow_model_run=True)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.first_root, 3, self.first_approval, allow_model_run=True)
            approval = self.approve(manifest)
            for number in (1, 2, 4):
                with self.assertRaises(pilot.PilotInputError):
                    pilot.run_slot(self.root, number, approval, allow_model_run=True)
            with self.assertRaises(pilot.PilotInputError):
                pilot.run_slot(self.root, 3, approval)
            for number in (3, 4):
                self.assertEqual(pilot.run_slot(self.root, number, approval, allow_model_run=True)["execution"], "completed")
                with self.assertRaises(pilot.PilotInputError):
                    pilot.run_slot(self.root, number, approval, allow_model_run=True)
            summary = pilot.summarize(self.root)
        self.assertEqual(summary["scheduled_sessions"], 4)
        self.assertFalse(summary["experimental_savings_claim_eligible"])
        self.assertIsNone(summary["comparative_savings_claim"])
        self.assertNotIn(str(self.private), json.dumps(summary))
        self.assertEqual((self.first_root / "slots/002/result.json").read_bytes(), self.failed_bytes)
        for name, data in self.old_bytes.items():
            self.assertEqual((self.old_root / name).read_bytes(), data)

    def test_auth_proof_rejects_prefix_extra_calls_usage_and_changed_error(self):
        path = self.first_root / "slots/002/evidence/attempt/events.jsonl"
        original = path.read_bytes()
        cli, _ = auth_streams()
        variants = [cli[1:], [{"type": "item.completed", "item": {"type": "command_execution"}}] + cli,
                    cli + [{"type": "turn.completed", "usage": {"input_tokens": 1}}],
                    cli[:2] + [{"type": "error", "message": "other error"}] + cli[3:]]
        for records in variants:
            fixtures.write(path, fixtures.jsonl(records))
            with self.assertRaises(pilot.PilotInputError):
                self.auth_plan()
            self.assertFalse(self.root.exists())
        fixtures.write(path, original)

    def test_auth_proof_rejects_action_assistant_usage_and_unknown_rollout_kinds(self):
        path = self.first_root / "slots/002/evidence/attempt/rollout.jsonl"
        original = path.read_bytes()
        _, rollout = auth_streams()
        variants = []
        for payload in ({"type": "function_call", "call_id": "synthetic-call", "name": "exec"},
                        {"type": "message", "role": "assistant", "content": []},
                        {"type": "token_count", "info": {}}, {"type": "unknown_action"}):
            altered = json.loads(json.dumps(rollout))
            altered[6]["payload"] = payload
            variants.append(altered)
        variants.append(rollout + [rollout[-1]])
        for records in variants:
            fixtures.write(path, fixtures.jsonl(records))
            with self.assertRaises(pilot.PilotInputError):
                self.auth_plan()
        fixtures.write(path, original)

    def test_timeout_truncation_and_missing_ambiguous_delivery_refuse(self):
        base = self.first_root / "slots/002/evidence/attempt"
        for name, variants in (
            ("process.json", [{"timed_out": True}, {"stdout_limit_reached": True},
                              {"capture_overflow": True}, {"pid": None}, {"exit_code": 0}]),
            ("delivery.json", [{"rollout": {"state": "lost"}}, {"answer": {"state": "captured"}},
                               {"rollout_discovery": {"candidate_count": 2}}]),
        ):
            path = base / name
            original = path.read_bytes()
            value = json.loads(original)
            for change in variants:
                fixtures.write(path, pilot._pretty(dict(value, **change)))
                with self.assertRaises(pilot.PilotInputError):
                    self.auth_plan()
            fixtures.write(path, original)
        path = base / "events.jsonl"
        data = path.read_bytes()
        fixtures.write(path, data[:-1])
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan()

    def test_auth_exact_predecessor_pin_and_provenance_are_required(self):
        with self.current(), patch.object(pilot, "AUTH_CONTROLLER_SHA256", "0" * 64):
            with self.assertRaises(pilot.PilotInputError):
                pilot.prepare_auth_continuation(self.root, self.first_root, self.first_snapshot,
                                                reason="Synthetic", authorization_note="Synthetic", auth_repair_note="Synthetic")
        for key in ("reason", "authorization_note", "auth_repair_note"):
            notes = {"reason": "Synthetic", "authorization_note": "Synthetic", "auth_repair_note": "Synthetic"}
            notes[key] = ""
            with self.current(), patch.object(pilot, "_BINARY_PROBE", self.fake_probe):
                with self.assertRaises(pilot.PilotInputError):
                    pilot.prepare_auth_continuation(self.root, self.first_root, self.first_snapshot, **notes)
        self.assertFalse(self.root.exists())
        self.assertFalse((self.first_root / "continuation-owner.json").exists())

    def rotated_initial_runtime(self):
        base = self.first_root / "slots/002"
        helper = base / "codex-home/tmp/arg0/codex-arg0BBBBBB"
        helper.mkdir(parents=True)
        fixtures.write(helper / ".lock", b"")
        for name in ("apply_patch", "applypatch", "codex-execve-wrapper"):
            (helper / name).symlink_to(self.codex)
        recorded = "tmp/arg0/codex-arg0AAAAAA"
        rows = [{"path": "tmp/arg0", "kind": "directory"}, {"path": recorded, "kind": "directory"},
                {"path": recorded + "/.lock", "kind": "lock"}]
        rows.extend({"path": recorded + "/" + name, "kind": "native-alias"}
                    for name in ("apply_patch", "applypatch", "codex-execve-wrapper"))
        request_path = base / "evidence/attempt/attempt.json"
        request = json.loads(request_path.read_bytes())
        request["rollout_discovery"]["initial_inventory"] = ["auth.json", "model-catalog.json", "tmp"]
        request["rollout_discovery"]["native_runtime"] = {
            "classification": "pinned-arg0-layout", "present": True, "entries": rows}
        fixtures.write(request_path, pilot._pretty(request))
        with self.current():
            _, discovery = pilot._discover_rollouts(base / "codex-home")
        delivery_path = base / "evidence/attempt/delivery.json"
        delivery = json.loads(delivery_path.read_bytes())
        delivery["rollout_discovery"] = discovery
        fixtures.write(delivery_path, pilot._pretty(delivery))
        return request_path, request

    def test_initial_helper_receipt_survives_ephemeral_name_rotation(self):
        self.rotated_initial_runtime()
        manifest = self.auth_plan()
        self.assertEqual(manifest["continuation"]["executable_slots"], [3, 4])
        with self.current():
            self.assertEqual(pilot.load_manifest(self.root)["digest"], manifest["digest"])

    def test_recorded_initial_runtime_rejects_foreign_paths_kinds_and_extra_rows(self):
        path, original = self.rotated_initial_runtime()
        variants = []
        for index, field, value in ((1, "path", "tmp/arg0/codex-arg0AAAAA"),
                                    (2, "path", "tmp/arg0/codex-arg0FOREIGN/.lock"),
                                    (2, "kind", "directory"),
                                    (3, "path", "tmp/arg0/codex-arg0AAAAAA/foreign-alias")):
            changed = json.loads(json.dumps(original))
            changed["rollout_discovery"]["native_runtime"]["entries"][index][field] = value
            variants.append(changed)
        changed = json.loads(json.dumps(original))
        changed["rollout_discovery"]["native_runtime"]["entries"].append({"path": "tmp/foreign", "kind": "directory"})
        variants.append(changed)
        changed = json.loads(json.dumps(original))
        changed["rollout_discovery"]["native_runtime"]["entries"][3]["target"] = "foreign-binary"
        variants.append(changed)
        for changed in variants:
            fixtures.write(path, pilot._pretty(changed))
            with self.assertRaises(pilot.PilotInputError):
                self.auth_plan()
            self.assertFalse(self.root.exists())
        fixtures.write(path, pilot._pretty(original))

    def test_malformed_initial_provisioning_and_boolean_process_code_refuse(self):
        base = self.first_root / "slots/002/evidence/attempt"
        request_path = base / "attempt.json"
        original = request_path.read_bytes()
        value = json.loads(original)
        value["rollout_discovery"]["native_runtime"] = {"present": False, "classification": "unsupported", "entries": []}
        fixtures.write(request_path, pilot._pretty(value))
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan()
        fixtures.write(request_path, original)
        process_path = base / "process.json"
        value = json.loads(process_path.read_bytes())
        value["exit_code"] = True
        fixtures.write(process_path, pilot._pretty(value))
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan()
        self.assertFalse(self.root.exists())

    def test_native_metadata_versions_types_and_bounded_stat_binding(self):
        manifest = self.auth_plan()
        inventory = manifest["continuation"]["prefix_evidence"]["slot_inventory"]
        self.assertIn("native_metadata", inventory["codex-home/state_5.sqlite"])
        self.assertNotIn("sha256", inventory["codex-home/state_5.sqlite"])
        with self.current():
            target = self.first_root / "slots/002/codex-home/state_5.sqlite"
            fixtures.write(target, b"changed synthetic metadata length")
            with self.assertRaises(pilot.PilotInputError):
                pilot.load_manifest(self.root)

    def test_foreign_runtime_files_directories_and_symlinks_refuse(self):
        home = self.first_root / "slots/002/codex-home"
        for name in ("state_6.sqlite", "thread_history_1.sqlite-wal", "foreign-native-file"):
            target = home / name
            fixtures.write(target, b"synthetic foreign metadata")
            try:
                with self.assertRaises(pilot.PilotInputError):
                    self.auth_plan()
            finally:
                target.unlink()
        target = home / "installation_id"
        target.unlink()
        target.mkdir()
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan()
        target.rmdir()
        target.symlink_to(home / "state_5.sqlite")
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan()
        self.assertFalse(self.root.exists())

    def test_malformed_retained_report_sections_are_controlled_refusals(self):
        base = self.first_root / "slots/002"
        for relative, section in (("result.json", "usage"), ("evidence/capture-report.json", "calls"),
                                  ("evidence/capture-report.json", "usage")):
            path = base / relative
            original = path.read_bytes()
            value = json.loads(original)
            value[section] = []
            fixtures.write(path, pilot._pretty(value))
            try:
                with self.assertRaises(pilot.PilotInputError):
                    self.auth_plan()
            finally:
                fixtures.write(path, original)
        self.assertFalse(self.root.exists())

    def test_auth_duplicate_owner_and_deeper_lineage_refuse(self):
        self.auth_plan()
        with self.assertRaises(pilot.PilotInputError):
            self.auth_plan(self.private / "duplicate-auth")
        with self.current():
            with self.assertRaises(pilot.PilotInputError):
                pilot.prepare_auth_continuation(self.private / "deeper", self.root, self.first_snapshot,
                                                reason="Synthetic", authorization_note="Synthetic",
                                                auth_repair_note="Synthetic")

    def test_original_and_current_sources_and_config_and_fixture_and_evidence_are_bound(self):
        manifest = self.auth_plan()
        with self.current():
            pilot.preflight(self.root)
            approval = self.approve(manifest)
            for path in (self.first_snapshot / "scripts/benchmark_issue10_local_pilot.py",
                         self.snapshot / "scripts/benchmark_issue10_v3_collector.py",
                         self.first_root / "config.json", self.first_root / "slots/002/result.json",
                         self.first_root / "slots/002/reservation.json",
                         self.first_root / "slots/002/evidence/attempt/stderr.txt",
                         self.first_root / "continuation-owner.json", self.sshai,
                         self.root / "prepared/inputs/M01/context.txt"):
                data, mode = path.read_bytes(), path.stat().st_mode & 0o777
                path.chmod(0o700)
                fixtures.write(path, data + b"tampered", 0o700)
                try:
                    with self.assertRaises((pilot.PilotInputError, ValueError)):
                        pilot.run_slot(self.root, 3, approval, allow_model_run=True)
                    self.assertFalse((self.root / "slots/003").exists())
                finally:
                    fixtures.write(path, data, mode)

    def test_extra_attempt_artifacts_and_reserved_remainder_and_missing_inputs_refuse(self):
        for relative in ("slots/003", "slots/004", "slots/002/evidence/last-message.txt",
                         "slots/002/evidence/attempt/answer.txt"):
            path = self.first_root / relative
            if relative in ("slots/003", "slots/004"):
                path.mkdir()
            else:
                fixtures.write(path, b"synthetic extra")
            try:
                with self.assertRaises(pilot.PilotInputError):
                    self.auth_plan()
            finally:
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
        (self.first_root / "slots/002/evidence/attempt/process.json").unlink()
        with self.assertRaises((pilot.PilotInputError, ValueError)):
            self.auth_plan()


if __name__ == "__main__":
    unittest.main()
