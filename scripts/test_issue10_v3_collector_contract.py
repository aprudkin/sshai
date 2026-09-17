#!/usr/bin/env python3
"""Additional synthetic-only contract checks for the accepted collector API."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_v3_collector as collector
from test_issue10_v3_collector import ENV, cli_line, python_command


class CollectorContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.attempt = self.root / "attempt"

    def run_child(self, code="pass", **options):
        args = dict(prompt=b"synthetic stdin", env=ENV, cwd=self.root, timeout_seconds=2)
        args.update(options)
        return collector.collect_attempt(self.attempt, python_command(code), **args)

    def test_attempt_publication_failure_prevents_spawn(self):
        with patch.object(collector.legacy, "_write_new", side_effect=OSError("synthetic disk error")):
            with patch.object(collector.legacy, "_bounded_process", side_effect=AssertionError("no spawn")):
                with self.assertRaises(OSError):
                    self.run_child()
        self.assertFalse((self.attempt / "process.json").exists())

    def test_late_publication_failure_retains_attempt_and_prior_files(self):
        write = collector.legacy._write_new
        def fail_delivery(path, data, *args, **kwargs):
            if path.name == "delivery.json":
                raise OSError("synthetic disk error")
            return write(path, data, *args, **kwargs)
        with patch.object(collector.legacy, "_write_new", side_effect=fail_delivery):
            with self.assertRaises(OSError):
                self.run_child()
        self.assertTrue((self.attempt / "attempt.json").exists())
        self.assertTrue((self.attempt / "process.json").exists())
        self.assertFalse((self.attempt / "delivery.json").exists())
        with patch.object(collector.legacy, "_bounded_process", side_effect=AssertionError("no retry")):
            with self.assertRaises(collector.CollectorInputError):
                self.run_child()

    def test_oversized_files_retain_delivery_errors_not_silent_loss(self):
        answer = self.root / "answer"
        rollout = self.root / "rollout"
        rollout.write_bytes(b"x" * (collector.capture_adapter.MAX_CAPTURE_BYTES + 1))
        code = ("from pathlib import Path; "
                f"Path({str(answer)!r}).write_bytes(b'x' * "
                f"{collector.capture_adapter.MAX_ANSWER_BYTES + 1}); "
                f"print({cli_line()!r})")
        result = self.run_child(code, answer_path=answer,
                                rollout_candidates=[rollout])
        self.assertEqual(result["delivery"]["answer"]["reason"], "answer_input_error")
        self.assertEqual(result["delivery"]["rollout"]["candidates"][0]["status"], "input_error")
        self.assertTrue((self.attempt / "delivery.json").exists())
        # This API retains error receipts, not prefixes of oversized source files.
        self.assertFalse((self.attempt / "answer.txt").exists())
        self.assertFalse((self.attempt / "rollout-candidates" / "001.jsonl").exists())
        self.assertGreater(answer.stat().st_size, collector.capture_adapter.MAX_ANSWER_BYTES)

    def test_pipe_eof_does_not_disable_process_timeout(self):
        result = self.run_child("import os,time; os.close(1); os.close(2); time.sleep(60)",
                                timeout_seconds=0.15)
        self.assertEqual(result["process"]["execution"], "timeout")
        self.assertTrue(result["process"]["timed_out"])

    def test_prompt_delivery_and_nonzero_exit_leave_answer_independent(self):
        answer = self.root / "answer"
        code = ("import sys; from pathlib import Path; "
                "assert sys.stdin.buffer.read() == b'synthetic stdin'; "
                f"Path({str(answer)!r}).write_bytes(b'partial or final: unverified'); sys.exit(7)")
        result = self.run_child(code, answer_path=answer)
        self.assertEqual(result["process"]["exit_code"], 7)
        self.assertEqual(result["process"]["execution"], "failed")
        self.assertEqual(result["delivery"]["answer"]["state"], "captured")
        receipt = json.loads((self.attempt / "attempt.json").read_bytes())
        self.assertEqual(receipt["prompt_bytes"], len(b"synthetic stdin"))
        self.assertNotIn("prompt", receipt)
        self.assertNotIn("env", receipt)

    def test_invalid_bounds_prevent_attempt(self):
        for options in ({"timeout_seconds": float("inf")}, {"timeout_seconds": 0},
                        {"timeout_seconds": True}, {"prompt": b'x' * (collector.MAX_PROMPT_BYTES + 1)}):
            with self.subTest(options=options):
                with self.assertRaises(collector.CollectorInputError):
                    self.run_child(**options)
                self.assertFalse(self.attempt.exists())


if __name__ == "__main__":
    unittest.main()
