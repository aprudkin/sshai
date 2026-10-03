#!/usr/bin/env python3
"""Bounded prospective native record capacity, using a real synthetic process."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_v3_capture as capture
import test_issue10_local_pilot as fixtures

LIMITS = {"stream_limit": 8 * 1024 * 1024, "capture_limit": 8 * 1024 * 1024,
          "line_limit": 4 * 1024 * 1024}


def large_streams(size=914252):
    cli, rollout = fixtures.completion_streams()
    text = "x" * size
    cli[2]["item"].update(aggregated_output=text)
    rollout.insert(-2, {"type": "event_msg", "payload": {"type": "item_completed",
        "thread_id": "synthetic-thread", "turn_id": "synthetic-turn", "item": {
        "type": "CommandExecution", "id": "large-command", "command": ["cat", "synthetic.txt"],
        "cwd": "file:///synthetic", "status": "completed", "exit_code": 0,
        "stdout": text, "aggregated_output": text, "formatted_output": "x" * 40112,
        "stderr": "", "duration": {"secs": 0, "nanos": 1}}}})
    # A model-facing truncation notice is legitimate, unlike observer overflow.
    rollout.insert(-2, {"type": "response_item", "payload": {"type": "function_call_output",
        "call_id": "large-command", "output": "Warning: model-facing output truncated; synthetic tail"}})
    return fixtures.jsonl(cli), fixtures.jsonl(rollout)


class ObserverTests(fixtures.PilotFixture):
    def collect(self, cli, rollout, *, stderr=b"", **limits):
        scratch = self.base / "synthetic-scratch"
        home = self.base / "synthetic-native-home"
        scratch.mkdir(exist_ok=True)
        home.mkdir(exist_ok=True)
        fixtures.write(home / "auth.json", b"synthetic")
        fixtures.write(home / "model-catalog.json", b"{}")
        fixtures.write(scratch / "cli-source", cli)
        fixtures.write(scratch / "rollout-source", rollout)
        fixtures.write(scratch / "stderr-source", stderr)
        code = ('import os,pathlib,sys; p=pathlib.Path; h=p(os.environ["CODEX_HOME"]); '
                '(h/"sessions").mkdir(); (h/"sessions/synthetic.jsonl").write_bytes(p("rollout-source").read_bytes()); '
                'p("answer-source").write_text(sys.argv[1]);sys.stderr.buffer.write(p("stderr-source").read_bytes());'
                'sys.stdout.buffer.write(p("cli-source").read_bytes())')
        return pilot._collect_local_attempt(self.base / "attempt", [sys.executable, "-c", code, fixtures.ANSWER],
            prompt=b"synthetic", env={"PATH": os.environ["PATH"], "CODEX_HOME": str(home)}, cwd=scratch,
            timeout_seconds=10, codex_home=home, answer_path=scratch / "answer-source", association={}, **limits)

    def test_large_native_duplicate_record_roundtrips_actual_process_and_retained_sources(self):
        cli, rollout = large_streams()
        self.assertGreater(len(rollout), capture.MAX_CAPTURE_BYTES)
        self.assertGreater(max(map(len, rollout.splitlines())), capture.MAX_LINE_BYTES)
        self.assertFalse(capture.parse_jsonl(cli, "old-default")["complete"])
        result = self.collect(cli, rollout, **LIMITS)
        self.assertFalse(result["process"]["capture_overflow"])
        self.assertEqual(result["delivery"]["rollout"]["state"], "captured")
        attempt = result["attempt_dir"]
        self.assertEqual((attempt / "events.jsonl").read_bytes(), cli)
        self.assertEqual((attempt / "rollout.jsonl").read_bytes(), rollout)
        parse = {key: value for key, value in LIMITS.items() if key != "stream_limit"}
        report = capture.capture_bytes(cli, rollout, (attempt / "process.json").read_bytes(), fixtures.ANSWER.encode(), **parse)
        self.assertEqual(report["issues"], [])
        self.assertEqual(capture.completion_evidence_bytes(cli, rollout, fixtures.ANSWER.encode(), **parse)["status"], "matched")
        self.assertEqual(result["attempt"]["limits"]["stdout_bytes"], LIMITS["stream_limit"])

    def test_stream_overflow_is_still_retained_and_reported(self):
        result = self.collect(b"x" * (LIMITS["stream_limit"] + 1), b"", **LIMITS)
        self.assertTrue(result["process"]["capture_overflow"])
        self.assertLessEqual((result["attempt_dir"] / "events.jsonl").stat().st_size, LIMITS["stream_limit"])

    def test_stderr_overflow_is_independent_of_stdout(self):
        cli, native = tuple(map(fixtures.jsonl, fixtures.completion_streams()))
        result = self.collect(cli, native, stderr=b"x" * (LIMITS["stream_limit"] + 1), **LIMITS)
        self.assertTrue(result["process"]["capture_overflow"])
        self.assertEqual(result["process"]["stderr_bytes"], LIMITS["stream_limit"])

    def test_oversize_native_candidate_stays_intact_without_clean_delivery(self):
        cli, _ = tuple(map(fixtures.jsonl, fixtures.completion_streams()))
        result = self.collect(cli, b"x" * (LIMITS["capture_limit"] + 1), **LIMITS)
        self.assertFalse(result["process"]["capture_overflow"])
        self.assertEqual(result["delivery"]["rollout"]["state"], "lost")
        self.assertEqual(result["delivery"]["rollout"]["candidates"][0]["status"], "input_error")
        self.assertEqual((self.base / "synthetic-native-home/sessions/synthetic.jsonl").stat().st_size, LIMITS["capture_limit"] + 1)

    def test_invalid_or_unbounded_capacity_refuses_before_process_and_directory(self):
        for kwargs in ({"stream_limit": True}, {"stream_limit": 8 * 1024 * 1024 + 1},
                       {"capture_limit": 8 * 1024 * 1024 + 1}, {"line_limit": 4 * 1024 * 1024 + 1}, {"line_limit": 0}):
            with self.assertRaises(pilot.collector.CollectorInputError):
                self.collect(b"", b"", **kwargs)
            self.assertFalse((self.base / "attempt").exists())

    def test_new_bounds_do_not_relax_answer_limits_and_old_parser_defaults(self):
        parse = {key: value for key, value in LIMITS.items() if key != "stream_limit"}
        for data, code in ((b"x" * (LIMITS["capture_limit"] + 1), "capture_too_large"),
                           (fixtures.jsonl([{"text": "x" * LIMITS["line_limit"]}]), "jsonl_record_too_large")):
            self.assertIn(code, {i["code"] for i in capture.parse_jsonl(data, "new", **parse)["issues"]})
        cli, rollout = fixtures.completion_streams()
        report = capture.capture_bytes(fixtures.jsonl(cli), fixtures.jsonl(rollout), b'{"timed_out":false,"exit_code":0}',
                                       b"x" * (capture.MAX_ANSWER_BYTES + 1), **parse)
        self.assertIn("capture_too_large", {i["code"] for i in report["issues"] if i["source"] == "answer"})


if __name__ == "__main__":
    unittest.main()
