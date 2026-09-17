#!/usr/bin/env python3
"""Synthetic subprocess tests for the Issue 10 v3 bounded collector."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

import benchmark_issue10_v3_capture as capture_adapter
import benchmark_issue10_v3_collector as collector


THREAD = "synthetic-thread"
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}


def cli_line(thread: str = THREAD) -> str:
    return json.dumps({"type": "thread.started", "thread_id": thread}, separators=(",", ":"))


def rollout_bytes(thread: str = THREAD) -> bytes:
    return (json.dumps({"type": "session_meta", "payload": {"id": thread}},
                       separators=(",", ":")) + "\n").encode()


def python_command(code: str, *arguments: Path | str) -> list[str]:
    return [sys.executable, "-c", code, *(str(item) for item in arguments)]


class CollectorTests(unittest.TestCase):
    def collect(self, root: Path, name: str, command: list[str], **kwargs):
        return collector.collect_attempt(
            root / name, command, prompt=b"synthetic prompt", env=ENV,
            cwd=root, timeout_seconds=kwargs.pop("timeout_seconds", 2), **kwargs,
        )

    def test_invalid_association_refused_without_attempt_or_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / 'spawned'
            command = python_command(
                f'from pathlib import Path; Path({str(marker)!r}).write_text("unexpected")')
            for index, value in enumerate(([], {'bad': object()}, {'bad': float('nan')},
                                           {'large': 'x' * 16384})):
                with self.subTest(index=index), self.assertRaises(collector.CollectorInputError):
                    self.collect(root, f'attempt-{index}', command, association=value)
                self.assertFalse((root / f'attempt-{index}').exists())
                self.assertFalse(marker.exists())

    def test_answer_finality_is_unknown_independently_of_delivery_and_exit(self) -> None:
        cases = [
            ("success", "pass", b"Partial or final", "completed", "captured"),
            ("nonzero", "sys.exit(7)", b"Partial or final", "failed", "captured"),
            ("timeout", "time.sleep(5)", b"Partial or final", "timeout", "captured"),
            ("empty", "pass", b"", "completed", "lost"),
            ("missing", "pass", None, "completed", "lost"),
        ]
        for name, ending, data, execution, state in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                answer = root / "answer-source"
                code = "import sys, time; from pathlib import Path; "
                if data is not None:
                    code += f"Path(sys.argv[1]).write_bytes({data!r}); "
                result = self.collect(
                    root, "attempt", python_command(code + ending, answer),
                    answer_path=answer, timeout_seconds=0.5,
                )
                delivery = result["delivery"]["answer"]
                self.assertEqual(result["process"]["execution"], execution)
                self.assertEqual(delivery["state"], state)
                self.assertEqual(delivery["finality"], "unknown")
                self.assertEqual(delivery["finality_reason"],
                                 "no_qualified_final_answer_evidence")
                saved = json.loads((root / "attempt" / "delivery.json").read_bytes())
                self.assertEqual(saved["answer"], delivery)
                if state == "captured":
                    self.assertEqual((root / "attempt" / "answer.txt").read_bytes(), data)

    def test_success_receipt_precedes_spawn_and_outputs_are_private(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            attempt = root / "attempt"
            answer = root / "answer-source.txt"
            rollout = root / "rollout-source.jsonl"
            rollout.write_bytes(rollout_bytes())
            code = (
                "import json,os,stat,sys; from pathlib import Path; "
                "receipt=Path(sys.argv[1])/'attempt.json'; "
                "assert receipt.is_file(); assert stat.S_IMODE(receipt.stat().st_mode)==0o600; "
                "Path(sys.argv[2]).write_text('Synthetic final answer'); "
                f"print({cli_line()!r},flush=True)"
            )
            result = self.collect(
                root, "attempt", python_command(code, attempt, answer),
                rollout_candidates=[rollout], answer_path=answer,
            )
            self.assertEqual(result["process"]["execution"], "completed")
            self.assertEqual(result["delivery"]["rollout"]["state"], "captured")
            self.assertEqual(result["delivery"]["rollout"]["cli_thread_id"], THREAD)
            self.assertEqual(result["delivery"]["answer"]["state"], "captured")
            self.assertEqual((attempt / "rollout.jsonl").read_bytes(), rollout_bytes())
            self.assertEqual((attempt / "answer.txt").read_text(), "Synthetic final answer")
            self.assertEqual(stat.S_IMODE(attempt.stat().st_mode), 0o700)
            for path in attempt.rglob("*"):
                expected = 0o700 if path.is_dir() else 0o600
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, str(path))

    def test_existing_attempt_refuses_overwrite_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            marker = root / "second-spawned"
            first = python_command(f"print({cli_line()!r})")
            self.collect(root, "attempt", first)
            second = python_command(
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')",
                marker,
            )
            with self.assertRaisesRegex(collector.CollectorInputError, "refusing existing path"):
                self.collect(root, "attempt", second)
            self.assertFalse(marker.exists())
            self.assertTrue((root / "attempt" / "attempt.json").is_file())

    def test_timeout_is_bounded_and_process_group_is_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self.collect(
                root, "timeout",
                python_command("import time; time.sleep(60)"),
                timeout_seconds=0.1,
            )
            process = result["process"]
            self.assertEqual(process["execution"], "timeout")
            self.assertTrue(process["timed_out"])
            self.assertIsNotNone(process["pid"])
            with self.assertRaises(ProcessLookupError):
                os.kill(process["pid"], 0)

    def test_process_group_descendant_is_cleaned_after_leader_exit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            code = (
                "import subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "print(p.pid,flush=True)"
            )
            result = self.collect(root, "descendant", python_command(code))
            child_pid = int((root / "descendant" / "events.jsonl").read_text().strip())
            self.assertEqual(result["process"]["execution"], "completed")
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_stdout_and_stderr_overflow_are_bounded_and_retained(self) -> None:
        for stream, descriptor in (("stdout", 1), ("stderr", 2)):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                code = (
                    f"import os,time; os.write({descriptor},b'x'*"
                    f"{collector.MAX_STREAM_BYTES + 4096}); time.sleep(60)"
                )
                result = self.collect(root, stream, python_command(code))
                process = result["process"]
                self.assertEqual(process["execution"], "failed")
                self.assertTrue(process["capture_overflow"])
                self.assertTrue(process[f"{stream}_limit_reached"])
                retained = root / stream / ("events.jsonl" if stream == "stdout" else "stderr.txt")
                self.assertEqual(retained.stat().st_size, collector.MAX_STREAM_BYTES)
                self.assertEqual(stat.S_IMODE(retained.stat().st_mode), 0o600)

    def test_start_failure_is_retained_after_pre_spawn_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result = self.collect(root, "start-error", [str(root / "does-not-exist")])
            self.assertEqual(result["process"]["execution"], "failed")
            self.assertIsNone(result["process"]["pid"])
            self.assertIn("No such file", result["process"]["start_error"])
            self.assertTrue((root / "start-error" / "attempt.json").is_file())
            self.assertTrue((root / "start-error" / "process.json").is_file())
            self.assertEqual(result["delivery"]["answer"]["reason"], "process_not_started")
            self.assertEqual(result["delivery"]["rollout"]["reason"], "process_not_started")

    def test_malformed_ambiguous_and_mismatched_rollouts_are_not_selected(self) -> None:
        cases: list[tuple[str, list[bytes], str, str]] = [
            ("malformed", [b"{bad json}\n"],
             "unresolved_candidate_identity", "malformed"),
            ("ambiguous-identity", [rollout_bytes() + rollout_bytes("other-thread")],
             "unresolved_candidate_identity", "ambiguous_identity"),
            ("multiple-matches", [rollout_bytes(), rollout_bytes()],
             "ambiguous_matching_candidates", "usable"),
            ("mismatched", [rollout_bytes("other-thread")],
             "no_candidate_matched_cli_thread_identity", "mismatched_identity"),
        ]
        for name, bodies, expected_reason, candidate_status in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                paths = []
                for index, body in enumerate(bodies, 1):
                    path = root / f"candidate-{index}.jsonl"
                    path.write_bytes(body)
                    paths.append(path)
                result = self.collect(
                    root, "attempt", python_command(f"print({cli_line()!r})"),
                    rollout_candidates=paths,
                )
                delivery = result["delivery"]["rollout"]
                self.assertEqual(delivery["state"], "lost")
                self.assertEqual(delivery["reason"], expected_reason)
                self.assertEqual(delivery["candidates"][0]["status"], candidate_status)
                self.assertEqual((root / "attempt" / "rollout.jsonl").read_bytes(), b"")
                # Candidate evidence and process receipts survive even though
                # the capture adapter cannot project a matching identity.
                report = capture_adapter.capture_bytes(
                    (root / "attempt" / "events.jsonl").read_bytes(),
                    (root / "attempt" / "rollout.jsonl").read_bytes(),
                    (root / "attempt" / "process.json").read_bytes(),
                    answer_state="lost",
                )
                with self.assertRaises(capture_adapter.CaptureInputError):
                    capture_adapter.coordinator_record(report)
                self.assertTrue((root / "attempt" / "process.json").is_file())
                self.assertTrue((root / "attempt" / "delivery.json").is_file())

    def test_unresolved_competitor_blocks_single_matching_rollout(self) -> None:
        cases = {
            "malformed": b"{bad json}\n",
            "missing_identity": b'{}\n',
            "ambiguous_identity": rollout_bytes() + rollout_bytes(),
            "invalid_identity": rollout_bytes() + b'{"type":"session_meta","payload":{"id":""}}\n',
            "invalid_payload": rollout_bytes() + b'{"type":"session_meta","payload":null}\n',
            "input_error": None,
            "oversized": b"x" * (capture_adapter.MAX_CAPTURE_BYTES + 1),
        }
        for name, body in cases.items():
            for reverse in (False, True):
                with self.subTest(name=name, reverse=reverse), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory).resolve()
                    good, competitor = root / "good", root / "competitor"
                    good.write_bytes(rollout_bytes())
                    if body is not None:
                        competitor.write_bytes(body)
                    paths = [good, competitor]
                    if reverse:
                        paths.reverse()
                    result = self.collect(
                        root, "attempt", python_command(f"print({cli_line()!r})"),
                        rollout_candidates=paths,
                    )
                    delivery = result["delivery"]["rollout"]
                    self.assertEqual(delivery["state"], "lost")
                    self.assertEqual(delivery["reason"], "unresolved_candidate_identity")
                    self.assertIsNone(delivery["selected_candidate"])
                    self.assertEqual((root / "attempt" / "rollout.jsonl").read_bytes(), b"")
                    observation = delivery["candidates"][paths.index(competitor)]
                    expected = "input_error" if name == "oversized" else name
                    if name == "invalid_payload":
                        expected = "invalid_identity"
                    self.assertEqual(observation["status"], expected)
                    if body is not None and name != "oversized":
                        self.assertEqual((root / "attempt" / observation["retained"]).read_bytes(), body)
                    self.assertTrue((root / "attempt" / "delivery.json").is_file())

    def test_valid_nonmatching_competitor_does_not_block_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            good, other = root / "good", root / "other"
            good.write_bytes(rollout_bytes())
            other.write_bytes(rollout_bytes("other-thread"))
            result = self.collect(
                root, "attempt", python_command(f"print({cli_line()!r})"),
                rollout_candidates=[other, good],
            )
            delivery = result["delivery"]["rollout"]
            self.assertEqual(delivery["state"], "captured")
            self.assertEqual(delivery["selected_candidate"], 2)
            self.assertEqual(delivery["candidates"][0]["status"], "mismatched_identity")

    def test_malformed_or_invalid_cli_identity_cannot_select_rollout(self) -> None:
        cases = {
            "malformed": cli_line() + "\n{bad json}\n",
            "invalid-identity": cli_line() + "\n" + json.dumps({
                "type": "thread.started", "thread_id": "",
            }) + "\n",
        }
        expected = {
            "malformed": "malformed_cli_jsonl",
            "invalid-identity": "invalid_cli_thread_identity",
        }
        for name, output in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                rollout = root / "candidate.jsonl"
                rollout.write_bytes(rollout_bytes())
                result = self.collect(
                    root, "attempt",
                    python_command("import sys; sys.stdout.write(sys.argv[1])", output),
                    rollout_candidates=[rollout],
                )
                delivery = result["delivery"]["rollout"]
                self.assertEqual(delivery["state"], "lost")
                self.assertEqual(delivery["reason"], expected[name])
                self.assertIsNone(delivery["selected_candidate"])
                self.assertEqual((root / "attempt" / "events.jsonl").read_text(), output)
                self.assertEqual((root / "attempt" / "rollout.jsonl").read_bytes(), b"")

    def test_oversized_candidate_and_answer_are_bounded_delivery_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            candidate = root / "oversized-rollout.jsonl"
            candidate.write_bytes(b"r" * (capture_adapter.MAX_CAPTURE_BYTES + 1))
            answer = root / "answer-source"
            code = (
                "from pathlib import Path; import sys; "
                f"Path(sys.argv[1]).write_bytes(b'a'*{capture_adapter.MAX_ANSWER_BYTES + 1}); "
                f"print({cli_line()!r})"
            )
            result = self.collect(
                root, "attempt", python_command(code, answer),
                rollout_candidates=[candidate], answer_path=answer,
            )
            rollout_delivery = result["delivery"]["rollout"]
            answer_delivery = result["delivery"]["answer"]
            self.assertEqual(rollout_delivery["state"], "lost")
            self.assertEqual(rollout_delivery["candidates"][0]["status"], "input_error")
            self.assertIn("bounded file exceeds", rollout_delivery["candidates"][0]["error"])
            self.assertEqual(answer_delivery["state"], "lost")
            self.assertEqual(answer_delivery["reason"], "answer_input_error")
            self.assertIn("bounded file exceeds", answer_delivery["error"])
            self.assertFalse((root / "attempt" / "answer.txt").exists())
            self.assertEqual((root / "attempt" / "rollout.jsonl").read_bytes(), b"")

    def test_preexisting_answer_is_rejected_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            answer = root / "answer-source"
            answer.write_text("stale answer")
            marker = root / "spawned"
            command = python_command(
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')",
                marker,
            )
            with self.assertRaisesRegex(
                collector.CollectorInputError, "must not exist before process spawn",
            ):
                self.collect(root, "attempt", command, answer_path=answer)
            self.assertFalse(marker.exists())
            self.assertFalse((root / "attempt").exists())
            self.assertEqual(answer.read_text(), "stale answer")

    def test_missing_and_empty_answer_are_delivery_loss_not_absence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            missing = root / "missing-answer"
            empty = root / "empty-answer"
            command = python_command(f"print({cli_line()!r})")
            empty_command = python_command(
                "from pathlib import Path; import sys; "
                f"Path(sys.argv[1]).write_bytes(b''); print({cli_line()!r})",
                empty,
            )
            missing_result = self.collect(
                root, "missing", command, answer_path=missing,
            )
            empty_result = self.collect(
                root, "empty", empty_command, answer_path=empty,
            )
            self.assertEqual(missing_result["delivery"]["answer"]["state"], "lost")
            self.assertEqual(missing_result["delivery"]["answer"]["reason"], "answer_input_error")
            self.assertEqual(empty_result["delivery"]["answer"]["state"], "lost")
            self.assertEqual(empty_result["delivery"]["answer"]["reason"], "empty_answer_file")
            self.assertNotEqual(missing_result["delivery"]["answer"]["state"], "absent")
            self.assertNotEqual(empty_result["delivery"]["answer"]["state"], "absent")
            self.assertFalse((root / "missing" / "answer.txt").exists())
            self.assertFalse((root / "empty" / "answer.txt").exists())

    def test_answer_is_retained_independently_of_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            answer = root / "answer-source"
            code = (
                "from pathlib import Path; import sys,time; "
                "Path(sys.argv[1]).write_text('Answer before hang'); time.sleep(60)"
            )
            result = self.collect(
                root, "attempt", python_command(code, answer),
                answer_path=answer, timeout_seconds=0.1,
            )
            self.assertEqual(result["process"]["execution"], "timeout")
            self.assertEqual(result["delivery"]["answer"]["state"], "captured")
            self.assertEqual((root / "attempt" / "answer.txt").read_text(), "Answer before hang")

    def test_source_symlink_is_rejected_before_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            target = root / "rollout"
            target.write_bytes(rollout_bytes())
            link = root / "rollout-link"
            link.symlink_to(target)
            with self.assertRaisesRegex(collector.CollectorInputError, "symlink"):
                self.collect(
                    root, "attempt", python_command(f"print({cli_line()!r})"),
                    rollout_candidates=[link],
                )
            self.assertFalse((root / "attempt").exists())


if __name__ == "__main__":
    unittest.main()
