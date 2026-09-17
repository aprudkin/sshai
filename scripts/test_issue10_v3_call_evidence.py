#!/usr/bin/env python3
"""Synthetic call-evidence regressions; no model, SSH or tool execution."""
from __future__ import annotations

import unittest

import benchmark_issue10_v3_capture as adapter
from test_issue10_v3_capture import cli_records, rollout_records, process


class CallEvidenceTests(unittest.TestCase):
    def report(self, *, cli_items=(), responses=(), events=()):
        cli = cli_records()
        cli[2:4] = [{"type": phase, "item": item} for phase, item in cli_items]
        rollout = rollout_records()
        rollout[3:4] = [
            *({"type": "response_item", "payload": item} for item in responses),
            *({"type": "event_msg", "payload": item} for item in events),
        ]
        return adapter.capture(cli, rollout, process())

    def command(self, **changes):
        return {"id": "cmd-1", "type": "command_execution", "command": "printf synthetic",
                "aggregated_output": "synthetic", "status": "completed", "exit_code": 0,
                **changes}

    def test_response_requests_hosted_reports_and_suffixes_never_confirm_execution(self):
        for kind in ("function_call", "custom_tool_call", "local_shell_call", "tool_search_call",
                     "web_search_call", "image_generation_call", "vendor_future_call"):
            with self.subTest(kind=kind):
                raw = {"id": "response-1", "call_id": "call-1", "type": kind,
                       "name": "synthetic", "status": "completed", "arguments": "{}"}
                report = self.report(responses=[raw])
                entry, = report["calls"]["inventory"]
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertEqual(entry["raw_observations"][0]["item"], raw)
                self.assertEqual(entry["evidence_kind"], "unknown_response_item"
                                 if kind == "vendor_future_call" else "reported_call")
                self.assertEqual(report["calls"]["confirmed_call_entry_count"], 0)

    def test_cli_file_change_mcp_dynamic_and_unknown_payloads_survive(self):
        for kind in ("file_change", "mcp_tool_call", "dynamic_tool_call", "collab_tool_call",
                     "web_search", "future_call", "future_item"):
            with self.subTest(kind=kind):
                raw = {"id": "item-1", "type": kind, "status": "failed",
                       "changes": [{"path": "synthetic.txt", "kind": "update"}],
                       "server": "synthetic", "tool": "fixture_read", "arguments": {},
                       "error": {"message": "synthetic failure"}, "opaque": [1, 2]}
                report = self.report(cli_items=[("item.completed", raw)])
                entry, = report["calls"]["inventory"]
                self.assertEqual(entry["raw_observations"],
                                 [{"record": 3, "event": "item.completed", "item": raw}])
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertIn("unsupported_cli_item", {i["code"] for i in report["issues"]})
                self.assertEqual(report["instrumentation"]["status"], "unknown")

    def test_requests_and_legacy_lifecycle_are_not_execution_attestation(self):
        for kind in ("apply_patch_approval_request", "dynamic_tool_call_request",
                     "request_user_input", "exec_command_begin", "exec_command_end",
                     "mcp_tool_call_end", "dynamic_tool_call_response"):
            with self.subTest(kind=kind):
                raw = {"type": kind, "call_id": "call-1", "status": "declined",
                       "arguments": {"synthetic": True}}
                report = self.report(events=[raw])
                entry, = report["calls"]["inventory"]
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertEqual(entry["raw_observations"][0]["item"], raw)
                self.assertEqual(entry["evidence_kind"], "reported_request"
                                 if kind.endswith("_request") or kind == "request_user_input"
                                 else "reported_call_lifecycle")

    def test_cli_declined_and_malformed_commands_are_not_confirmed(self):
        for changes in ({"status": "declined", "exit_code": None}, {"command": None},
                        {"aggregated_output": None}, {"exit_code": True},
                        {"status": "in_progress"}, {"status": "future"}):
            with self.subTest(changes=changes):
                raw = self.command(**changes)
                report = self.report(cli_items=[("item.completed", raw)])
                entry, = report["calls"]["inventory"]
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertEqual(report["calls"]["completed_command_calls"], 0)
                self.assertEqual(report["calls"]["failed_command_calls"], 0)
                malformed = "malformed_command" in {i["code"] for i in report["issues"]}
                self.assertEqual(malformed, changes.get("status") != "declined")

    def test_nullable_exit_is_source_supported_not_proof_of_success(self):
        for status in ("completed", "failed"):
            report = self.report(cli_items=[("item.completed", self.command(
                status=status, exit_code=None))])
            entry, = report["calls"]["inventory"]
            self.assertTrue(entry["actual_call_confirmed"])
            self.assertEqual(report["instrumentation"]["status"], "unknown")
            self.assertEqual(report["calls"]["failed_command_calls"], int(status == "failed"))

    def test_decline_or_malformed_observation_cannot_be_hidden_by_grouping(self):
        for first, last in (
            (self.command(status="in_progress", exit_code=None), self.command(status="declined")),
            (self.command(command=None, status="in_progress"), self.command()),
            (self.command(status="in_progress"), {"id": "cmd-1", "type": "future_call"}),
        ):
            with self.subTest(first=first, last=last):
                report = self.report(cli_items=[("item.started", first), ("item.completed", last)])
                entry, = report["calls"]["inventory"]
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertEqual(entry["observations"], 2)
                self.assertEqual([o["item"] for o in entry["raw_observations"]], [first, last])
                self.assertEqual(report["calls"]["confirmed_call_entry_count"], 0)
                self.assertEqual(report["calls"]["completed_command_calls"], 0)

    def test_legacy_response_event_turn_item_and_cli_ids_are_not_joined(self):
        response = {"type": "function_call", "call_id": "cmd-1", "name": "exec_command",
                    "arguments": "{}"}
        command = {"type": "CommandExecution", "id": "cmd-1", "command": ["echo", "synthetic"],
                   "cwd": "file:///synthetic", "status": "completed", "exit_code": 0}
        report = self.report(
            cli_items=[("item.completed", self.command())], responses=[response],
            events=[{"type": "exec_command_begin", "call_id": "cmd-1"},
                    {"type": "exec_command_end", "call_id": "cmd-1"},
                    {"type": "raw_response_item", "item": response},
                    {"type": "item_completed", "item": command}],
        )
        self.assertEqual(report["calls"]["inventory_entry_count"], 5)
        self.assertEqual(report["calls"]["observation_count"], 6)
        self.assertEqual(report["calls"]["confirmed_call_entry_count"], 2)
        self.assertIsNone(report["calls"]["unique_session_call_count"])
        event = next(c for c in report["calls"]["inventory"] if c["source"] == "rollout.event_msg")
        self.assertEqual(event["observations"], 2)
        self.assertEqual(event["record_types"], ["exec_command_begin", "exec_command_end"])
        self.assertFalse(event["actual_call_confirmed"])

    def test_mixed_evidence_kinds_survive_source_local_grouping(self):
        events = [{"type": "dynamic_tool_call_request", "call_id": "same"},
                  {"type": "dynamic_tool_call_response", "call_id": "same"}]
        entry, = self.report(events=events)["calls"]["inventory"]
        self.assertEqual(entry["evidence_kinds"], ["reported_request", "reported_call_lifecycle"])
        self.assertEqual([o["item"] for o in entry["raw_observations"]], events)
        self.assertFalse(entry["actual_call_confirmed"])

    def test_no_unknown_entries_does_not_qualify_coverage_or_artifact_reads(self):
        report = self.report(cli_items=[("item.completed", self.command(
            command="cat synthetic-artifact.txt"))])
        self.assertEqual(report["calls"]["unknown_item_entry_count"], 0)
        self.assertEqual(report["calls"]["coverage"], "unqualified")
        self.assertIsNone(report["calls"]["unique_session_call_count"])
        self.assertFalse(report["calls"]["compliance_inferred"])
        self.assertEqual(report["calls"]["inventory"][0]["classification"], "unclassified")
        self.assertEqual(report["boundary"]["status"], "unknown")
        self.assertFalse(report["experimental_claim_eligible"])


if __name__ == "__main__":
    unittest.main()
