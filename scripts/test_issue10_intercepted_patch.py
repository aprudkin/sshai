#!/usr/bin/env python3
"""Synthetic default-off intercepted scratch-helper patch qualification."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import benchmark_issue10_local_pilot as p
import test_issue10_local_pilot as f
from test_issue10_v3_capture import process


def streams(scratch, *, kind="update"):
    cli, native = deepcopy(f.completion_streams())
    target = str(scratch / "helper.sh")
    body = "echo synthetic\n"
    request = {"type": "function_call", "name": "exec_command", "call_id": "patch-request",
               "arguments": json.dumps({"cmd": "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: helper.sh\n@@\n+echo synthetic\n*** End Patch\nPATCH", "workdir": str(scratch)})}
    change = {"type": "FileChange", "id": "patch-request", "changes": {target: {
        "type": "update", "unified_diff": "@@ -0,0 +1 @@\n+echo synthetic\n", "move_path": None}},
        "status": "completed", "stdout": "Success", "stderr": ""}
    if kind == "add":
        args = json.loads(request["arguments"])
        args["cmd"] = args["cmd"].replace("Update File:", "Add File:").replace("\n@@\n", "\n")
        request["arguments"] = json.dumps(args)
        change["changes"] = {target: {"type": "add", "content": body}}
    native[-1:-1] = [{"type": "response_item", "payload": request},
                       {"type": "event_msg", "payload": {"type": "item_completed", "item": change,
                        "thread_id": native[0]["payload"]["id"], "turn_id": native[-1]["payload"]["turn_id"]}}]
    cli[-1:-1] = [{"type": event, "item": {"id": "cli-patch", "type": "file_change",
        "changes": [{"kind": kind, "path": target}], "status": status}}
        for event, status in (("item.started", "in_progress"), ("item.completed", "completed"))]
    f.write(scratch / "helper.sh", body.encode())
    return cli, native


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.scratch = Path(self.temp.name).resolve() / "scratch"
        self.scratch.mkdir()
        self.cli, self.native = streams(self.scratch)
        # Freeze ordinary allowed signatures, never add FileChange to config.
        plain = p.capture.capture_bytes(f.jsonl(f.completion_streams()[0]), f.jsonl(f.completion_streams()[1]), p._encoded(process()))
        signatures = []
        for item in plain["calls"]["inventory"]:
            for obs in item["raw_observations"]:
                for call in p._observation_calls(item["source"], obs):
                    signatures.append({k: call[k] for k in ("source", "record_type", "tool_name", "evidence_kind")})
        signatures.append({"source": "rollout.response_item", "record_type": "function_call", "tool_name": "exec_command", "evidence_kind": "reported_call"})
        self.config = {"codex_exec": {"allowed_audit_signatures": signatures}}

    def audit(self):
        import benchmark_issue10_intercepted_patch as patch
        report = p.capture.capture_bytes(f.jsonl(self.cli), f.jsonl(self.native), p._encoded(process()))
        old = deepcopy(report)
        result = patch.audit(report, self.config, self.scratch)
        self.assertEqual(report, old)
        return result

    def test_valid_literal_helper_qualifies_only_prospective_audit(self):
        original = p.capture.capture_bytes(f.jsonl(self.cli), f.jsonl(self.native), p._encoded(process()))
        self.assertEqual(p._audit(original, self.config)["status"], "unqualified")
        result = self.audit()
        self.assertEqual(result["status"], "bounded-recorded")
        self.assertEqual(result["semantic_routing"]["status"], "unknown")
        self.assertFalse(result["cross_source_joining"])
        self.assertEqual(len(result["intercepted_patch"]["qualified_requests"]), 1)
        self.assertEqual(p._audit(original, self.config)["status"], "unqualified")

    def test_missing_mismatched_identity_targets_status_and_unsafe_shapes_refuse(self):
        base_cli, base_native = deepcopy(self.cli), deepcopy(self.native)
        changes = [
            lambda c,n: n[-2]["payload"]["item"].update(id="foreign"),
            lambda c,n: n[-3]["payload"].pop("call_id"),
            lambda c,n: c[-2]["item"].update(id="foreign"),
            lambda c,n: c[-2]["item"].update(status="failed"),
            lambda c,n: n[-2]["payload"]["item"].update(status="in_progress"),
            lambda c,n: n[-3]["payload"].update(name="apply_patch"),
            lambda c,n: c[-2]["item"]["changes"][0].update(path=str(self.scratch.parent / "fixture/helper.sh")),
            lambda c,n: n[-2]["payload"]["item"].update(stdout={}),
            lambda c,n: n[-3]["payload"].update(arguments=json.dumps({"cmd": "apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: ../foreign\n@@\n+x\n*** End Patch\nPATCH", "workdir":str(self.scratch)})),
            lambda c,n: n.append({"type":"future_shape","payload":{}}),
            lambda c,n: c.pop(-3),
            lambda c,n: c[-2]["item"].pop("id"),
            lambda c,n: next(iter(n[-2]["payload"]["item"]["changes"].values())).update(move_path="foreign"),
            lambda c,n: n[-3]["payload"].update(arguments=json.dumps({"cmd":"apply_patch <<'PATCH'\n*** Begin Patch\n*** Update File: helper.sh\n@@\n+echo synthetic\n*** End Patch\nPATCH", "workdir":str(self.scratch), "shell":"foreign"})),
            lambda c,n: n[-2]["payload"]["item"].update(changes={str(self.scratch.parent / "fixture/helper.sh"): {"type":"update","move_path":None,"unified_diff":"@@ -0,0 +1 @@\n+echo synthetic\n"}}),
        ]
        for change in changes:
            with self.subTest(change=changes.index(change)):
                self.cli, self.native = deepcopy(base_cli), deepcopy(base_native)
                change(self.cli, self.native)
                self.assertEqual(self.audit()["status"], "unqualified")

    def test_duplicate_lifecycle_different_diff_extra_commands_and_symlink_refuse(self):
        baseline = deepcopy(self.native)
        self.native.insert(-1, deepcopy(self.native[-2]))
        self.assertEqual(self.audit()["status"], "unqualified")
        self.native = deepcopy(baseline)
        change = next(iter(self.native[-2]["payload"]["item"]["changes"].values()))
        change["unified_diff"] = "@@ -0,0 +1 @@\n+foreign\n"
        self.assertEqual(self.audit()["status"], "unqualified")
        self.native = deepcopy(baseline)
        args = json.loads(self.native[-3]["payload"]["arguments"])
        args["cmd"] += "\ncat ../fixture/*"
        self.native[-3]["payload"]["arguments"] = json.dumps(args)
        self.assertEqual(self.audit()["status"], "unqualified")
        self.native = baseline
        (self.scratch / "helper.sh").unlink()
        (self.scratch / "helper.sh").symlink_to(self.scratch.parent / "foreign")
        self.assertEqual(self.audit()["status"], "unqualified")


class AddPatchTests(unittest.TestCase):
    setUp = PatchTests.setUp

    def use_add(self):
        args = json.loads(self.native[-3]["payload"]["arguments"])
        args["cmd"] = args["cmd"].replace("Update File:", "Add File:").replace("\n@@\n", "\n")
        self.native[-3]["payload"]["arguments"] = json.dumps(args)
        self.native[-2]["payload"]["item"]["changes"] = {
            str(self.scratch / "helper.sh"): {"type": "add", "content": "echo synthetic\n"}}
        for row in self.cli[-3:-1]:
            row["item"]["changes"][0]["kind"] = "add"

    def audit(self):
        import benchmark_issue10_intercepted_patch as patch
        report = p.capture.capture_bytes(f.jsonl(self.cli), f.jsonl(self.native), p._encoded(process()))
        before = deepcopy(report)
        result = patch.audit(report, self.config, self.scratch, profile=patch.ADD_PROFILE)
        self.assertEqual(report, before)
        return result

    def test_add_qualifies_only_explicit_profile2_and_update_still_qualifies(self):
        import benchmark_issue10_intercepted_patch as patch
        self.assertEqual(self.audit()["status"], "bounded-recorded")
        self.use_add()
        report = p.capture.capture_bytes(f.jsonl(self.cli), f.jsonl(self.native), p._encoded(process()))
        self.assertEqual(patch.audit(report, self.config, self.scratch)["status"], "unqualified")
        result = self.audit()
        self.assertEqual(result["status"], "bounded-recorded")
        self.assertEqual(result["intercepted_patch"]["profile"], patch.ADD_PROFILE)
        self.assertEqual(result["intercepted_patch"]["qualified_requests"][0]["kind"], "add")
        self.assertEqual(result["semantic_routing"]["status"], "unknown")

    def test_wrong_add_content_kind_identity_body_target_and_unknown_shapes_reject(self):
        self.use_add()
        cli, native = deepcopy(self.cli), deepcopy(self.native)
        variants = [
            lambda c, n: next(iter(n[-2]["payload"]["item"]["changes"].values())).update(content="foreign\n"),
            lambda c, n: next(iter(n[-2]["payload"]["item"]["changes"].values())).update(type="update"),
            lambda c, n: n[-2]["payload"]["item"].update(id="foreign"),
            lambda c, n: n[-3]["payload"].update(arguments=n[-3]["payload"]["arguments"].replace("echo synthetic", "foreign")),
            lambda c, n: c[-2]["item"]["changes"][0].update(path=str(self.scratch.parent / "fixture/helper.sh")),
            lambda c, n: next(iter(n[-2]["payload"]["item"]["changes"].values())).update(extra=True),
            lambda c, n: c[-2]["item"]["changes"][0].update(kind="delete"),
            lambda c, n: n[-3]["payload"].update(name="apply_patch"),
            lambda c, n: c.insert(-1, {"type": "future.event"}),
            lambda c, n: n[-2]["payload"]["item"].update(changes={str(self.scratch / "foreign.sh"): {"type": "add", "content": "echo synthetic\n"}}),
            lambda c, n: n[-2]["payload"]["item"].update(status="failed"),
            lambda c, n: c.pop(-3),
            lambda c, n: n[-3]["payload"].update(arguments=n[-3]["payload"]["arguments"].replace("helper.sh", "../fixture/helper.sh")),
        ]
        for index, mutate in enumerate(variants):
            with self.subTest(index=index):
                self.cli, self.native = deepcopy(cli), deepcopy(native)
                mutate(self.cli, self.native)
                self.assertEqual(self.audit()["status"], "unqualified")


if __name__ == "__main__":
    unittest.main()
