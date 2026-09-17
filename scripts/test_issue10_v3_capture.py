#!/usr/bin/env python3
"""Synthetic-only offline tests for the Issue 10 v3 capture adapter."""
from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_v3_capture as adapter


def jsonl(records: list[object]) -> bytes:
    return b"".join(
        json.dumps(item, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        for item in records
    )


def usage(input_tokens: int = 100, cached: int = 25, output: int = 10) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
    }


def cli_records(*, terminal_usage: dict[str, int] | None = None,
                final: str = "Synthetic final answer") -> list[dict[str, object]]:
    terminal_usage = usage() if terminal_usage is None else terminal_usage
    return [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.started", "item": {
            "id": "cmd-1", "type": "command_execution", "command": "printf sshai",
            "status": "in_progress", "aggregated_output": "", "exit_code": None,
        }},
        {"type": "item.completed", "item": {
            "id": "cmd-1", "type": "command_execution", "command": "printf sshai",
            "status": "completed", "aggregated_output": "sshai", "exit_code": 0,
        }},
        {"type": "item.completed", "item": {
            "id": "message-1", "type": "agent_message", "text": final,
        }},
        {"type": "turn.completed", "usage": terminal_usage},
    ]


def rollout_records(*, final_usage: dict[str, int] | None = None) -> list[dict[str, object]]:
    final_usage = usage() if final_usage is None else final_usage
    return [
        {"type": "session_meta", "payload": {
            "id": "synthetic-thread", "cli_version": "synthetic-0.151-shape", "source": "exec",
        }},
        {"type": "turn_context", "payload": {
            "model": "synthetic-model", "reasoning_effort": "synthetic",
        }},
        {"type": "event_msg", "payload": {"type": "task_started"}},
        {"type": "response_item", "payload": {
            "id": "fc-1", "type": "function_call", "call_id": "call-unknown",
            "name": "future_tool_name", "arguments": "{\"synthetic\":true}",
        }},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": usage(40, 10, 3),
        }}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": final_usage,
        }}},
        {"type": "event_msg", "payload": {"type": "task_complete"}},
    ]


def process(*, timed_out: bool = False, exit_code: int | None = 0,
            error: str | None = None) -> dict[str, object]:
    return {"timed_out": timed_out, "exit_code": exit_code, "error": error}


class PureCaptureTests(unittest.TestCase):
    def test_valid_shape_counts_final_cumulative_usage_once(self) -> None:
        report = adapter.capture(
            cli_records(), rollout_records(), process(),
            {"state": "captured", "text": "Synthetic final answer"},
        )
        self.assertEqual(report["session_id"], "synthetic-thread")
        self.assertEqual(report["usage"]["totals"], usage())
        self.assertEqual(report["usage"]["cli_snapshot_count"], 1)
        self.assertEqual(report["usage"]["rollout_snapshot_count"], 2)
        self.assertTrue(report["usage"]["complete"])
        self.assertEqual(report["usage"]["counting_method"],
                         "one final cumulative total; snapshots are never summed")
        self.assertEqual(report["answer"]["state"], "captured")
        self.assertEqual(report["answer"]["text"], "Synthetic final answer")
        self.assertEqual(report["calls"]["completed_command_calls"], 1)
        self.assertEqual(report["calls"]["failed_command_calls"], 0)
        self.assertEqual(report["calls"]["captured_command_output_bytes"], 5)
        self.assertEqual(report["execution"]["execution"], "completed")
        self.assertEqual(report["instrumentation"]["status"], "unknown")
        self.assertFalse(report["instrumentation"]["semantics_qualified"])
        self.assertEqual(report["boundary"]["status"], "unknown")
        self.assertFalse(report["experimental_claim_eligible"])

        record = adapter.coordinator_record(report)
        self.assertEqual(record, {
            "session_id": "synthetic-thread",
            "execution": "completed",
            "answer_state": "captured",
            "final_answer": "Synthetic final answer",
            "usage": usage(),
            "usage_complete": True,
            "instrumentation": "unknown",
            "boundary": "unknown",
        })

    def test_actual_call_inventory_keeps_unknown_names_without_policy_inference(self) -> None:
        cli = cli_records()
        cli.insert(-1, {"type": "item.started", "item": {
            "id": "future-cli-1", "type": "future_tool_event", "status": "in_progress",
            "name": "future_cli_item",
        }})
        cli.insert(-1, {"type": "item.completed", "item": {
            "id": "future-cli-1", "type": "future_tool_event", "status": "completed",
            "name": "future_cli_item",
        }})
        records = rollout_records()
        records.insert(-2, {"type": "response_item", "payload": {
            "id": "future-1", "type": "vendor_future_call", "call_id": "future-call",
            "name": "unlisted_tool", "input": {"path": "synthetic"},
        }})
        records.insert(-2, {"type": "event_msg", "payload": {
            "type": "mcp_tool_call_begin", "call_id": "mcp-call", "name": "unknown_mcp_tool",
        }})
        records.insert(-2, {"type": "event_msg", "payload": {
            "type": "mcp_tool_call_end", "call_id": "mcp-call", "name": "unknown_mcp_tool",
        }})
        report = adapter.capture(cli, records, process())
        inventory = report["calls"]["inventory"]
        names = {item["tool_name"] for item in inventory}
        self.assertIn("command_execution", names)
        self.assertIn("future_tool_name", names)
        self.assertIn("unlisted_tool", names)
        self.assertIn("unknown_mcp_tool", names)
        self.assertIn("future_cli_item", names)
        unknown_cli = next(item for item in inventory if item["call_id"] == "future-cli-1")
        self.assertFalse(unknown_cli["actual_call_confirmed"])
        self.assertEqual(unknown_cli["evidence_kind"], "unknown_cli_item")
        self.assertEqual(report["calls"]["unknown_item_entry_count"], 1)
        self.assertNotIn("count", report["calls"])
        self.assertGreaterEqual(report["calls"]["observation_count"],
                                report["calls"]["inventory_entry_count"])
        mcp = next(item for item in inventory if item["call_id"] == "mcp-call")
        self.assertEqual(mcp["observations"], 2)
        self.assertEqual(set(mcp["record_types"]), {"mcp_tool_call_begin", "mcp_tool_call_end"})
        self.assertTrue(all(item["classification"] == "unclassified" for item in inventory))
        self.assertFalse(report["calls"]["compliance_inferred"])
        self.assertEqual(report["boundary"]["status"], "unknown")
        # The literal "sshai" in a shell string must not upgrade compliance.
        command = next(item for item in inventory if item["source"] == "cli")
        self.assertIn("sshai", command["detail"])
        self.assertEqual(command["classification"], "unclassified")

    def test_paginated_command_preserves_source_fields_and_lifecycle(self) -> None:
        # codex rust-v0.151.0 protocol/src/items.rs CommandExecutionItem.
        started = {
            "type": "CommandExecution", "id": "cmd-1",
            "command": ["/bin/bash", "-lc", "printf sshai"],
            "cwd": "file:///synthetic/work", "parsed_cmd": [],
            "source": "agent", "status": "in_progress", "process_id": "42",
        }
        completed = {**started, "status": "completed", "exit_code": 0,
                     "aggregated_output": "sshai", "duration": {"secs": 0, "nanos": 10}}
        records = rollout_records()
        records[3:4] = [
            {"type": "event_msg", "payload": {
                "type": phase, "thread_id": "synthetic-thread", "turn_id": "turn-1",
                "item": item,
            }} for phase, item in (("item_started", started), ("item_completed", completed))
        ]
        report = adapter.capture(cli_records(), records, process())
        entries = report["calls"]["inventory"]
        self.assertEqual(len(entries), 2)  # CLI and rollout IDs are not cross-source dedup keys.
        call = next(item for item in entries if item["source"] == "rollout.turn_item")
        self.assertEqual(call["call_id"], "cmd-1")
        self.assertEqual(call["detail"], started["command"])
        self.assertEqual(call["cwd"], started["cwd"])
        self.assertEqual(call["status"], "completed")
        self.assertEqual(call["observations"], 2)
        self.assertEqual([row["item"] for row in call["raw_observations"]],
                         [started, completed])
        self.assertTrue(call["actual_call_confirmed"])
        self.assertEqual(call["classification"], "unclassified")
        self.assertEqual(report["instrumentation"]["status"], "unknown")
        self.assertFalse(report["experimental_claim_eligible"])

    def test_paginated_unsupported_items_are_explicit_audit_gaps(self) -> None:
        for kind in ("McpToolCall", "Extension", "FutureTurnItem"):
            with self.subTest(kind=kind):
                raw = {"type": kind, "id": "unknown-1", "tool": "synthetic",
                       "arguments": {"x": 1}, "status": "completed"}
                records = rollout_records()
                records[3] = {"type": "event_msg", "payload": {
                    "type": "item_completed", "item": raw}}
                report = adapter.capture(cli_records(), records, process())
                self.assertIn("unsupported_turn_item", {i["code"] for i in report["issues"]})
                entry = next(i for i in report["calls"]["inventory"]
                             if i["source"] == "rollout.turn_item")
                self.assertFalse(entry["actual_call_confirmed"])
                self.assertEqual(entry["raw_observations"][0]["item"], raw)
                self.assertEqual(report["boundary"]["status"], "unknown")

    def test_paginated_malformed_and_declined_commands_are_not_confirmed(self) -> None:
        base = {"type": "CommandExecution", "id": "command-1",
                "command": ["echo", "synthetic"], "cwd": "file:///synthetic",
                "status": "completed"}
        for changes in ({"id": None}, {"command": "echo synthetic"},
                        {"cwd": None}, {"status": "new_status"}, {"status": "declined"}):
            with self.subTest(changes=changes):
                records = rollout_records()
                records[3] = {"type": "event_msg", "payload": {
                    "type": "item_completed", "item": {**base, **changes}}}
                report = adapter.capture(cli_records(), records, process())
                entry = next(i for i in report["calls"]["inventory"]
                             if i["source"] == "rollout.turn_item")
                self.assertFalse(entry["actual_call_confirmed"])
                if changes != {"status": "declined"}:
                    self.assertIn("malformed_turn_item",
                                  {i["code"] for i in report["issues"]})

    def test_paginated_noncall_and_compaction_items(self) -> None:
        records = rollout_records()
        records[3:4] = [{"type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": kind, "id": kind},
        }} for kind in ("AgentMessage", "Reasoning", "ContextCompaction")]
        report = adapter.capture(cli_records(), records, process())
        self.assertEqual(len(report["calls"]["inventory"]), 1)
        self.assertTrue(report["compaction"]["observed"])
        self.assertFalse(report["usage"]["complete"])

    def test_timeout_failure_and_answer_are_independent(self) -> None:
        timed_out = adapter.capture(
            cli_records(), rollout_records(), process(timed_out=True, exit_code=None),
            {"state": "captured", "text": "Synthetic final answer"},
        )
        self.assertEqual(timed_out["execution"]["execution"], "timeout")
        self.assertEqual(timed_out["answer"]["state"], "captured")
        # A process hang after terminal records does not erase separately verified usage.
        self.assertTrue(timed_out["usage"]["complete"])

        failed_cli = cli_records()
        failed_cli[-1] = {"type": "turn.failed", "usage": usage()}
        failed_rollout = rollout_records()
        failed_rollout[-1] = {"type": "event_msg", "payload": {"type": "task_failed"}}
        failed = adapter.capture(
            failed_cli, failed_rollout, process(exit_code=1, error="synthetic failure"),
            {"state": "absent", "text": None},
        )
        self.assertEqual(failed["execution"]["execution"], "failed")
        self.assertEqual(failed["answer"]["state"], "absent")
        self.assertEqual(failed["usage"]["totals"], usage())
        self.assertFalse(failed["usage"]["complete"])

        overflow = adapter.capture(
            cli_records(), rollout_records(),
            {**process(), "capture_overflow": True, "interrupted": False,
             "start_error": None},
        )
        self.assertEqual(overflow["execution"]["execution"], "failed")
        self.assertFalse(overflow["usage"]["complete"])
        self.assertTrue(overflow["execution"]["capture_overflow"])

    def test_unfinished_and_lost_answer_are_retained_not_invented(self) -> None:
        cli = cli_records()[:-1]
        rollout = rollout_records()[:-1]
        report = adapter.capture(
            cli, rollout, process(timed_out=True, exit_code=None),
            {"state": "lost", "text": None},
        )
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("unfinished_turn", codes)
        self.assertEqual(report["execution"]["execution"], "timeout")
        self.assertEqual(report["answer"]["state"], "lost")
        self.assertFalse(report["usage"]["complete"])
        self.assertEqual(report["instrumentation"]["status"], "invalid")
        self.assertEqual(report["usage"]["totals"], usage())

    def test_duplicate_lifecycles_items_and_session_mismatch_are_invalid(self) -> None:
        cli = cli_records()
        cli.insert(1, copy.deepcopy(cli[0]))
        cli.insert(-1, copy.deepcopy(cli[-2]))
        rollout = rollout_records()
        rollout[0]["payload"]["id"] = "other-thread"
        report = adapter.capture(cli, rollout, process())
        codes = [item["code"] for item in report["issues"]]
        self.assertIn("duplicate_thread_start", codes)
        self.assertIn("duplicate_item_completion", codes)
        self.assertIn("session_id_mismatch", codes)
        self.assertIsNone(report["session_id"])
        self.assertEqual(report["instrumentation"]["status"], "invalid")
        with self.assertRaisesRegex(adapter.CaptureInputError, "session_id"):
            adapter.coordinator_record(report)

    def test_usage_consistency_checks_do_not_sum_or_substitute(self) -> None:
        mismatched = adapter.capture(cli_records(), rollout_records(final_usage=usage(99, 25, 10)), process())
        self.assertFalse(mismatched["usage"]["totals_match"])
        self.assertFalse(mismatched["usage"]["complete"])
        self.assertIn("usage_mismatch", {item["code"] for item in mismatched["issues"]})

        decreasing = rollout_records()
        decreasing[-2]["payload"]["info"]["total_token_usage"] = usage(30, 5, 2)
        report = adapter.capture(cli_records(), decreasing, process())
        self.assertIn("usage_decreased", {item["code"] for item in report["issues"]})
        self.assertFalse(report["usage"]["complete"])

        invalid_cached = rollout_records()
        invalid_cached[-2]["payload"]["info"]["total_token_usage"] = usage(100, 101, 10)
        report = adapter.capture(cli_records(), invalid_cached, process())
        self.assertIn("invalid_usage", {item["code"] for item in report["issues"]})
        self.assertFalse(report["usage"]["complete"])

    def test_duplicate_and_post_terminal_usage_records_are_not_summed(self) -> None:
        records = rollout_records()
        records.append({"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": usage(),
        }}})
        report = adapter.capture(cli_records(), records, process())
        self.assertEqual(report["usage"]["totals"], usage())
        self.assertEqual(report["usage"]["rollout_snapshot_count"], 3)
        self.assertIn("usage_after_terminal", {item["code"] for item in report["issues"]})
        self.assertFalse(report["usage"]["complete"])

    def test_compaction_is_observed_but_continuity_stays_unqualified(self) -> None:
        cli = cli_records()
        cli.insert(-1, {"type": "compacted"})
        records = rollout_records()
        records.insert(-2, {"type": "compacted"})
        report = adapter.capture(cli, records, process())
        self.assertTrue(report["compaction"]["observed"])
        self.assertTrue(report["compaction"]["canonical_counts_match"])
        self.assertEqual(report["compaction"]["usage_continuity"], "unqualified")
        self.assertFalse(report["usage"]["complete"])
        self.assertEqual(report["instrumentation"]["status"], "unknown")
        self.assertIn("compaction_usage_uncertain", {item["code"] for item in report["issues"]})

        mismatch = adapter.capture(cli_records(), records, process())
        self.assertFalse(mismatch["compaction"]["canonical_counts_match"])
        self.assertIn("compaction_observation_mismatch",
                      {item["code"] for item in mismatch["issues"]})
        self.assertEqual(mismatch["instrumentation"]["status"], "invalid")

    def test_intermediate_messages_are_never_promoted_to_final_answers(self) -> None:
        unfinished = cli_records(final="Intermediate diagnostic note")[:-1]
        report = adapter.capture(
            unfinished, rollout_records()[:-1],
            process(timed_out=True, exit_code=None),
        )
        self.assertEqual(report["answer"]["state"], "lost")
        self.assertIsNone(report["answer"]["text"])
        self.assertEqual(report["answer"]["cli_agent_messages"][-1]["text"],
                         "Intermediate diagnostic note")

        empty = adapter.capture(cli_records(final=""), rollout_records(), process())
        self.assertNotEqual(empty["answer"]["state"], "captured")
        self.assertIsNone(empty["answer"]["text"])

        no_message = [
            event for event in cli_records()
            if event.get("item", {}).get("type") != "agent_message"
        ]
        absent = adapter.capture(no_message, rollout_records(), process())
        self.assertEqual(absent["answer"]["state"], "absent")
        self.assertIsNone(absent["answer"]["text"])

    def test_answer_capture_is_separate_and_mismatch_is_visible(self) -> None:
        report = adapter.capture(
            cli_records(), rollout_records(), process(),
            {"state": "captured", "text": "Collector answer differs"},
        )
        self.assertEqual(report["answer"]["text"], "Collector answer differs")
        self.assertEqual(report["answer"]["cli_agent_messages"][-1]["text"],
                         "Synthetic final answer")
        self.assertIn("answer_mismatch", {item["code"] for item in report["issues"]})
        self.assertEqual(report["instrumentation"]["status"], "invalid")


class PaginatedTurnItemTests(unittest.TestCase):
    # codex rust-v0.151.0, protocol/src/items.rs (78c290807ce710180111df227df3b7a4fe845452).
    def report(self, items):
        records = rollout_records()
        records[3:4] = [
            {"type": "event_msg", "payload": {
                "type": event, "thread_id": "synthetic-thread", "turn_id": "turn-1",
                "item": item,
            }} for event, item in items
        ]
        return adapter.capture(cli_records(), records, process())

    def command(self, **changes):
        return {"type": "CommandExecution", "id": "cmd-1",
                "command": ["/bin/bash", "-lc", "printf sshai"],
                "cwd": "/synthetic/work", "parsed_cmd": [], "source": "exec",
                "status": "completed", "exit_code": 0, **changes}

    def test_paginated_command_retains_argv_identity_and_cwd(self):
        item = self.command()
        report = self.report([("item_completed", item)])
        calls = [c for c in report["calls"]["inventory"] if c["source"] == "rollout.turn_item"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["call_id"], item["id"])
        self.assertEqual(calls[0]["detail"], item["command"])
        self.assertEqual(calls[0]["cwd"], item["cwd"])
        self.assertEqual(calls[0]["raw_observations"][0]["item"], item)
        self.assertTrue(calls[0]["actual_call_confirmed"])
        # CLI remapping/overlapping IDs do not establish cross-stream identity.
        self.assertEqual(report["calls"]["inventory_entry_count"], 2)
        self.assertEqual(report["boundary"]["status"], "unknown")
        self.assertFalse(report["experimental_claim_eligible"])

    def test_started_completed_retain_both_raw_observations(self):
        started = self.command(status="in_progress", exit_code=None)
        completed = self.command(status="failed", exit_code=1)
        report = self.report([("item_started", started), ("item_completed", completed)])
        call = next(c for c in report["calls"]["inventory"] if c["source"] == "rollout.turn_item")
        self.assertEqual(call["observations"], 2)
        self.assertEqual(call["status"], "failed")
        self.assertEqual([o["item"] for o in call["raw_observations"]], [started, completed])

    def test_unknown_and_unqualified_variants_are_audit_gaps(self):
        for kind in ("FutureItem", "Extension", "McpToolCall", "FileChange", "SubAgentActivity"):
            with self.subTest(kind=kind):
                item = {"type": kind, "id": "future", "opaque": {"value": 1}}
                report = self.report([("item_completed", item)])
                self.assertIn("unsupported_turn_item", {i["code"] for i in report["issues"]})
                call = next(c for c in report["calls"]["inventory"] if c["source"] == "rollout.turn_item")
                self.assertFalse(call["actual_call_confirmed"])
                self.assertEqual(call["raw_observations"][0]["item"], item)

    def test_malformed_and_declined_commands_do_not_confirm_execution(self):
        for item in (self.command(command="printf sshai"), self.command(id=None),
                     self.command(cwd=None), self.command(status="future"),
                     self.command(status="declined", exit_code=None)):
            with self.subTest(item=item):
                report = self.report([("item_completed", item)])
                call = next(c for c in report["calls"]["inventory"] if c["source"] == "rollout.turn_item")
                self.assertFalse(call["actual_call_confirmed"])
                if item["status"] != "declined":
                    self.assertIn("malformed_turn_item", {i["code"] for i in report["issues"]})

    def test_compaction_is_not_silently_ignored_or_counted_as_call(self):
        report = self.report([("item_completed", {"type": "ContextCompaction", "id": "compact"})])
        self.assertTrue(report["compaction"]["observed"])
        self.assertFalse(report["usage"]["complete"])
        self.assertEqual(report["calls"]["inventory_entry_count"], 1)


class BoundedParsingTests(unittest.TestCase):
    def test_jsonl_malformed_duplicate_keys_blank_missing_and_truncated(self) -> None:
        parsed = adapter.parse_jsonl(
            b'{"type":"one","type":"two"}\n\n{"type":\n', "synthetic",
        )
        codes = [item["code"] for item in parsed["issues"]]
        self.assertEqual(parsed["records"], [])
        self.assertIn("malformed_json", codes)
        self.assertIn("blank_jsonl_record", codes)
        self.assertIn("missing_records", codes)
        non_json_number = adapter.parse_jsonl(b'{"value":NaN}\n', "synthetic")
        self.assertIn("malformed_json", {item["code"] for item in non_json_number["issues"]})

        truncated = adapter.parse_jsonl(b'{"type":"thread.started"}', "synthetic")
        self.assertIn("unterminated_jsonl", {item["code"] for item in truncated["issues"]})
        self.assertEqual(len(truncated["records"]), 1)

    def test_capture_bytes_preserves_malformed_sources_as_invalid_evidence(self) -> None:
        report = adapter.capture_bytes(
            jsonl(cli_records()) + b'{bad json}\n',
            jsonl(rollout_records()),
            json.dumps(process()).encode(),
            b"Synthetic final answer",
        )
        self.assertEqual(report["usage"]["totals"], usage())
        self.assertFalse(report["usage"]["complete"])
        self.assertIn("malformed_json", {item["code"] for item in report["issues"]})
        self.assertEqual(report["instrumentation"]["status"], "invalid")
        self.assertFalse(report["experimental_claim_eligible"])

    def test_oversize_is_bounded_without_parsing(self) -> None:
        parsed = adapter.parse_jsonl(b"x" * (adapter.MAX_CAPTURE_BYTES + 1), "synthetic")
        self.assertEqual(parsed["records"], [])
        self.assertEqual(parsed["issues"][0]["code"], "capture_too_large")

    def test_malformed_process_and_explicit_absent_answer(self) -> None:
        report = adapter.capture_bytes(
            jsonl(cli_records()), jsonl(rollout_records()), b'{"timed_out":false,',
            answer_state="absent",
        )
        self.assertEqual(report["execution"]["execution"], "failed")
        self.assertEqual(report["answer"]["state"], "absent")
        self.assertIn("malformed_json", {item["code"] for item in report["issues"]})
        self.assertEqual(report["instrumentation"]["status"], "invalid")


class OfflineCliTests(unittest.TestCase):
    def test_cli_reads_only_authored_files_and_emits_noneligible_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / "events.jsonl"
            rollout = root / "rollout.jsonl"
            receipt = root / "process.json"
            answer = root / "answer.txt"
            events.write_bytes(jsonl(cli_records()))
            rollout.write_bytes(jsonl(rollout_records()))
            receipt.write_text(json.dumps(process()))
            answer.write_text("Synthetic final answer")
            output = io.StringIO()
            with patch("subprocess.Popen", side_effect=AssertionError("no subprocess")):
                with patch("socket.create_connection", side_effect=AssertionError("no network")):
                    with contextlib.redirect_stdout(output):
                        code = adapter.main([
                            "--events", str(events), "--rollout", str(rollout),
                            "--process", str(receipt), "--answer", str(answer),
                        ])
            self.assertEqual(code, 0)
            document = json.loads(output.getvalue())
            self.assertFalse(document["experimental_claim_eligible"])
            self.assertEqual(document["coordinator_record"]["boundary"], "unknown")
            self.assertEqual(document["coordinator_record"]["instrumentation"], "unknown")

    def test_cli_rejects_symlink_and_oversized_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(jsonl(cli_records()))
            link = root / "events-link"
            link.symlink_to(target)
            rollout = root / "rollout"
            rollout.write_bytes(jsonl(rollout_records()))
            receipt = root / "process"
            receipt.write_text(json.dumps(process()))
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = adapter.main([
                    "--events", str(link), "--rollout", str(rollout),
                    "--process", str(receipt),
                ])
            self.assertEqual(code, 2)
            self.assertIn("regular file", error.getvalue())

            too_large = root / "too-large"
            too_large.write_bytes(b"x" * (adapter.MAX_CAPTURE_BYTES + 1))
            with contextlib.redirect_stderr(io.StringIO()):
                code = adapter.main([
                    "--events", str(too_large), "--rollout", str(rollout),
                    "--process", str(receipt),
                ])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
