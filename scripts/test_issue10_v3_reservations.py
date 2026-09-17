#!/usr/bin/env python3
"""Synthetic local tests for Issue 10 v3 coordinator reservations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import benchmark_issue10_v3 as runner
import benchmark_issue10_v3_collector as collector
from test_issue10_v3_capture import cli_records, jsonl, process, rollout_records


THREAD = "synthetic-reservation-thread"
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"}


def python_command(code: str, *arguments: Path | str) -> list[str]:
    return [sys.executable, "-c", code, *(str(item) for item in arguments)]


def offline_result(session: str) -> dict[str, object]:
    return {
        "session_id": session,
        "execution": "completed",
        "answer_state": "captured",
        "final_answer": "Synthetic final answer",
        "usage": {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 2,
        },
        "usage_complete": True,
        "instrumentation": "valid",
        "boundary": "compliant",
        "review": None,
    }


class ReservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "study"
        with patch.object(runner.legacy, "_bounded_process", side_effect=AssertionError("no prepare process")):
            self.plan = runner.prepare(self.root, "pilot")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def collect_success(
        self,
        number: int = 1,
        *,
        prompt: bytes = b"synthetic prompt passed on stdin",
        name: str = "source",
    ) -> dict[str, object]:
        reservation = self.root / "reservations" / f"{number:03}.json"
        attempt = self.root / "attempts" / f"{number:03}"
        rollout = self.base / f"{name}-rollout.jsonl"
        answer = self.base / f"{name}-answer.txt"
        expected_rollout = (
            json.dumps(
                {"type": "session_meta", "payload": {"id": THREAD}},
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        event = json.dumps(
            {"type": "thread.started", "thread_id": THREAD}, separators=(",", ":")
        )
        code = (
            "import hashlib,json,stat,sys; from pathlib import Path; "
            "reservation,attempt,rollout,answer=map(Path,sys.argv[1:5]); "
            "assert reservation.is_file(); assert (attempt/'attempt.json').is_file(); "
            "r=json.loads(reservation.read_bytes()); a=json.loads((attempt/'attempt.json').read_bytes()); "
            "assert a['association']['reservation_sha256']==hashlib.sha256(reservation.read_bytes()).hexdigest(); "
            "assert sys.stdin.buffer.read()==bytes.fromhex(sys.argv[5]); "
            "rollout.write_bytes(bytes.fromhex(sys.argv[6])); "
            "answer.write_bytes(b'Synthetic delivered answer'); "
            f"print({event!r},flush=True)"
        )
        return runner.collect_slot(
            self.root,
            number,
            python_command(
                code,
                reservation,
                attempt,
                rollout,
                answer,
                prompt.hex(),
                expected_rollout.hex(),
            ),
            prompt=prompt,
            env=ENV,
            cwd=self.base,
            timeout_seconds=2,
            rollout_candidates=[rollout],
            answer_path=answer,
        )

    def reserve_partial(self, number: int) -> None:
        with patch.object(
            runner.legacy,
            "_bounded_process",
            side_effect=RuntimeError("synthetic failure after attempt publication"),
        ):
            with self.assertRaisesRegex(RuntimeError, "after attempt publication"):
                runner.collect_slot(
                    self.root,
                    number,
                    python_command("pass"),
                    prompt=b"synthetic",
                    env=ENV,
                    cwd=self.base,
                    timeout_seconds=2,
                )

    def unbound_attempt(self, name: str, thread: str = "unbound-thread") -> Path:
        attempt = self.base / name
        event = json.dumps(
            {"type": "thread.started", "thread_id": thread}, separators=(",", ":")
        )
        collector.collect_attempt(
            attempt,
            python_command(f"print({event!r})"),
            prompt=b"unbound synthetic prompt",
            env=ENV,
            cwd=self.base,
            timeout_seconds=2,
        )
        return attempt

    def rewrite_associated_attempt(self, root: Path, number: int, receipt: dict[str, object]) -> None:
        path = root / "attempts" / f"{number:03}" / "attempt.json"
        path.write_bytes(runner.legacy._canon(receipt))

    def test_reservation_and_associated_attempt_exist_before_spawn_and_prompt_is_passed(self) -> None:
        result = self.collect_success()
        reservation_path = self.root / "reservations/001.json"
        attempt_path = self.root / "attempts/001/attempt.json"
        reservation = json.loads(reservation_path.read_bytes())
        attempt = json.loads(attempt_path.read_bytes())

        self.assertEqual(set(reservation), {
            "schema", "plan_digest", "slot", "attempt_path", "request",
        })
        self.assertEqual(reservation["schema"], runner.RESERVATION_SCHEMA)
        self.assertEqual(reservation["plan_digest"], self.plan["digest"])
        self.assertEqual(reservation["slot"], self.plan["slots"][0])
        self.assertEqual(reservation["attempt_path"], "attempts/001")
        self.assertEqual(reservation["request"]["schema"], collector.ATTEMPT_SCHEMA)
        self.assertEqual(attempt["schema"], collector.ASSOCIATED_ATTEMPT_SCHEMA)
        self.assertEqual(
            attempt["association"],
            {
                "plan_digest": self.plan["digest"],
                "slot": self.plan["slots"][0],
                "reservation_sha256": hashlib.sha256(runner.encoded(reservation)).hexdigest(),
            },
        )
        self.assertEqual(
            {key: value for key, value in attempt.items() if key not in {"schema", "association"}},
            {key: value for key, value in reservation["request"].items() if key != "schema"},
        )
        self.assertEqual(result["attempt"], attempt)
        self.assertEqual(result["process"]["execution"], "completed")
        self.assertFalse((self.root / "records/001.json").exists(), "collection must not auto-import")

        self.assertEqual(stat.S_IMODE((self.root / "reservations").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "attempts").stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(reservation_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "attempts/001").stat().st_mode), 0o700)
        for path in (self.root / "attempts/001").rglob("*"):
            expected = 0o700 if path.is_dir() else 0o600
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, path.name)

    def test_successful_reserved_import_replays_with_unknown_answer_finality(self) -> None:
        result = self.collect_success()
        runner.import_collector(self.root, 1, result["attempt_dir"])
        envelope = runner._read_envelope(self.root / "records/001.json")
        binding = envelope["collector"]["binding"]
        reservation = json.loads((self.root / "reservations/001.json").read_bytes())
        self.assertEqual(binding["stage"], "coordinator-reserved-before-collection")
        self.assertFalse(binding["pre_spawn_attestation"])
        self.assertEqual(binding["reservation"], reservation)
        record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(record["execution"], "completed")
        self.assertEqual(record["answer_state"], "lost")
        self.assertIsNone(record["final_answer"])
        report = runner.analyze_root(self.root)
        row = next(item for item in report["slots"] if item["slot"] == 1)
        self.assertTrue(row["collection_reserved"])
        self.assertTrue(row["attempted"])
        self.assertEqual(row["quality"]["reason"], "final_answer_lost")
        self.assertEqual(report["reserved_without_result_slot_count"], 0)

    def test_partial_publication_and_start_failure_leave_one_shot_reservations(self) -> None:
        self.reserve_partial(1)
        reservation = self.root / "reservations/001.json"
        attempt_receipt = self.root / "attempts/001/attempt.json"
        self.assertTrue(reservation.is_file())
        self.assertTrue(attempt_receipt.is_file())
        report = runner.analyze_root(self.root)
        row = next(item for item in report["slots"] if item["slot"] == 1)
        self.assertFalse(row["attempted"])
        self.assertTrue(row["collection_reserved"])
        self.assertEqual(row["quality"]["reason"], "reserved_without_result")
        self.assertEqual(report["reserved_without_result_slot_count"], 1)
        self.assertEqual(report["unattempted_slot_count"], 11)
        reservation_value = json.loads(reservation.read_bytes())
        self.assertEqual(report["slot_reservations"], [{
            "slot": 1,
            "reservation_sha256": runner.digest(runner.encoded(reservation_value)),
            "result_imported": False,
        }])
        self.assertNotIn(str(self.root), json.dumps(report["slot_reservations"]))
        marker = self.base / "retry-spawned"
        with self.assertRaises(ValueError):
            runner.collect_slot(
                self.root,
                1,
                python_command(
                    "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')",
                    marker,
                ),
                prompt=b"retry",
                env=ENV,
                cwd=self.base,
                timeout_seconds=2,
            )
        self.assertFalse(marker.exists())

        missing_executable = self.base / "does-not-exist"
        failed = runner.collect_slot(
            self.root,
            2,
            [str(missing_executable)],
            prompt=b"synthetic",
            env=ENV,
            cwd=self.base,
            timeout_seconds=2,
        )
        self.assertEqual(failed["process"]["execution"], "failed")
        self.assertTrue((self.root / "reservations/002.json").is_file())
        runner.import_collector(self.root, 2, failed["attempt_dir"])
        self.assertEqual(runner._read_record(self.root, 2, self.plan)["execution"], "failed")
        with self.assertRaises(ValueError):
            runner.collect_slot(
                self.root, 2, python_command("pass"), prompt=b"retry", env=ENV,
                cwd=self.base, timeout_seconds=2,
            )

    def test_failure_before_attempt_publication_keeps_reservation_visible(self) -> None:
        with patch.object(collector, 'collect_attempt', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                runner.collect_slot(self.root, 1, python_command('pass'), prompt=b'synthetic',
                                    env=ENV, cwd=self.base, timeout_seconds=2)
        self.assertFalse((self.root / 'attempts/001').exists())
        self.assertFalse((self.root / '.record-write-lock').exists())
        report = runner.analyze_root(self.root)
        self.assertEqual(report['reserved_without_result_slot_count'], 1)
        with self.assertRaises(ValueError):
            runner.collect_slot(self.root, 1, python_command('pass'), prompt=b'synthetic',
                                env=ENV, cwd=self.base, timeout_seconds=2)

    def test_publication_lock_and_failed_import_preserve_original_collection(self) -> None:
        with runner._record_write_lock(self.root):
            with self.assertRaisesRegex(ValueError, 'publication is busy'):
                runner.import_result(self.root, 1, offline_result('blocked'))
            with self.assertRaisesRegex(ValueError, 'publication is busy'):
                runner.collect_slot(self.root, 1, python_command('pass'), prompt=b'synthetic',
                                    env=ENV, cwd=self.base, timeout_seconds=2)
        self.assertFalse((self.root / 'reservations').exists())
        result = self.collect_success()
        reservation = (self.root / 'reservations/001.json').read_bytes()
        attempt = (self.root / 'attempts/001/attempt.json').read_bytes()
        original = runner.legacy._write_new

        def fail_record(path, data, *args, **kwargs):
            if path == self.root / 'records/001.json':
                raise OSError('synthetic record publication failure')
            return original(path, data, *args, **kwargs)

        with patch.object(runner.legacy, '_write_new', side_effect=fail_record):
            with self.assertRaises(OSError):
                runner.import_collector(self.root, 1, result['attempt_dir'])
        self.assertFalse((self.root / '.record-write-lock').exists())
        self.assertFalse((self.root / 'records/001.json').exists())
        self.assertEqual((self.root / 'reservations/001.json').read_bytes(), reservation)
        self.assertEqual((self.root / 'attempts/001/attempt.json').read_bytes(), attempt)
        # Retrying publication of existing bytes is not recollecting the process.
        runner.import_collector(self.root, 1, result['attempt_dir'])
        self.assertEqual(runner.analyze_root(self.root)['recorded_slot_count'], 1)

    def test_replay_rejects_rehashed_binding_or_changed_reservation(self) -> None:
        result = self.collect_success()
        runner.import_collector(self.root, 1, result['attempt_dir'])
        path = self.root / 'records/001.json'
        original = path.read_bytes()
        envelope = json.loads(original)
        envelope['collector']['binding']['reservation']['request']['prompt_sha256'] = '0' * 64
        envelope['collector_sha256'] = runner.digest(runner.encoded(envelope['collector']))
        path.write_bytes(runner.encoded(envelope))
        with self.assertRaises(ValueError):
            runner.analyze_root(self.root)
        path.write_bytes(original)
        reservation_path = self.root / 'reservations/001.json'
        reservation = json.loads(reservation_path.read_bytes())
        reservation['request']['prompt_sha256'] = '0' * 64
        reservation_path.write_bytes(runner.encoded(reservation))
        with self.assertRaises(ValueError):
            runner.analyze_root(self.root)

    def test_invalid_request_publishes_no_reservation(self) -> None:
        with self.assertRaises((ValueError, collector.CollectorInputError)):
            runner.collect_slot(
                self.root, 1, [], prompt=b"synthetic", env=ENV,
                cwd=self.base, timeout_seconds=2,
            )
        self.assertFalse((self.root / "reservations/001.json").exists())
        self.assertFalse((self.root / "attempts/001").exists())

    def test_reserved_slots_reject_other_import_paths_and_unbound_collector(self) -> None:
        for number in (1, 2, 3):
            self.reserve_partial(number)

        with self.assertRaises(ValueError):
            runner.import_result(self.root, 1, offline_result("reserved-result"))

        with self.assertRaises(ValueError):
            runner.import_capture(
                self.root,
                2,
                jsonl(cli_records()),
                jsonl(rollout_records()),
                runner.encoded(process()),
                b"Synthetic answer",
            )

        unbound = self.unbound_attempt("unbound-attempt")
        with self.assertRaises(ValueError):
            runner.import_collector(self.root, 3, unbound)
        self.assertFalse(any((self.root / "records").iterdir()))

    def test_unbound_import_remains_supported_without_a_reservation(self) -> None:
        attempt = self.unbound_attempt("legacy-unbound")
        runner.import_collector(self.root, 1, attempt)
        envelope = runner._read_envelope(self.root / "records/001.json")
        self.assertNotIn("reservation", envelope["collector"]["binding"])
        self.assertEqual(
            envelope["collector"]["binding"]["stage"],
            runner.COLLECTOR_BINDING_STAGE,
        )
        self.assertFalse((self.root / "reservations/001.json").exists())

    def test_existing_filled_slot_refuses_collection_before_spawn(self) -> None:
        runner.import_result(self.root, 1, offline_result("already-filled"))
        marker = self.base / "unexpected-spawn"
        with self.assertRaises(ValueError):
            runner.collect_slot(
                self.root,
                1,
                python_command(
                    "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('bad')",
                    marker,
                ),
                prompt=b"synthetic",
                env=ENV,
                cwd=self.base,
                timeout_seconds=2,
            )
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "reservations/001.json").exists())

    def test_cross_slot_plan_request_and_association_tampering_are_rejected(self) -> None:
        cases = ("cross-slot", "plan", "request", "association", "boolean-slot")
        for kind in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                root = base / "study"
                plan = runner.prepare(root, "pilot")
                old_base, old_root, old_plan = self.base, self.root, self.plan
                self.base, self.root, self.plan = base, root, plan
                try:
                    result = self.collect_success(name=kind)
                    reservation_path = root / "reservations/001.json"
                    attempt_path = root / "attempts/001/attempt.json"
                    reservation = json.loads(reservation_path.read_bytes())
                    attempt = json.loads(attempt_path.read_bytes())
                    import_slot = 1
                    if kind == "cross-slot":
                        import_slot = 2
                    elif kind == "plan":
                        reservation["plan_digest"] = "0" * 64
                        attempt["association"]["plan_digest"] = "0" * 64
                        attempt["association"]["reservation_sha256"] = runner.digest(
                            runner.encoded(reservation)
                        )
                        reservation_path.write_bytes(runner.encoded(reservation))
                        self.rewrite_associated_attempt(root, 1, attempt)
                    elif kind == "request":
                        reservation["request"]["prompt_sha256"] = "1" * 64
                        attempt["association"]["reservation_sha256"] = runner.digest(
                            runner.encoded(reservation)
                        )
                        reservation_path.write_bytes(runner.encoded(reservation))
                        self.rewrite_associated_attempt(root, 1, attempt)
                    elif kind == 'boolean-slot':
                        reservation['slot']['slot'] = True
                        attempt['association']['slot']['slot'] = True
                        attempt['association']['reservation_sha256'] = runner.digest(runner.encoded(reservation))
                        reservation_path.write_bytes(runner.encoded(reservation))
                        self.rewrite_associated_attempt(root, 1, attempt)
                    else:
                        attempt["association"]["reservation_sha256"] = "2" * 64
                        self.rewrite_associated_attempt(root, 1, attempt)
                    with self.assertRaises(ValueError):
                        runner.import_collector(root, import_slot, result["attempt_dir"])
                    self.assertFalse((root / "records" / f"{import_slot:03}.json").exists())
                finally:
                    self.base, self.root, self.plan = old_base, old_root, old_plan

    def test_same_slot_concurrent_collection_has_exactly_one_winner(self) -> None:
        barrier = threading.Barrier(2)
        marker = self.base / "spawn-count"
        event = json.dumps(
            {"type": "thread.started", "thread_id": "concurrent-thread"},
            separators=(",", ":"),
        )
        command = python_command(
            "from pathlib import Path; import sys,time; "
            "p=Path(sys.argv[1]); "
            "p.write_text((p.read_text() if p.exists() else '')+'spawned\\n'); "
            "time.sleep(.1); "
            f"print({event!r})",
            marker,
        )
        successes: list[dict[str, object]] = []
        failures: list[BaseException] = []

        def invoke() -> None:
            barrier.wait()
            try:
                successes.append(runner.collect_slot(
                    self.root,
                    1,
                    command,
                    prompt=b"concurrent synthetic prompt",
                    env=ENV,
                    cwd=self.base,
                    timeout_seconds=2,
                ))
            except BaseException as exc:  # retained for assertion in the parent thread
                failures.append(exc)

        threads = [threading.Thread(target=invoke) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)
        self.assertEqual(marker.read_text().splitlines(), ["spawned"])
        self.assertEqual(list((self.root / "reservations").glob("001.json")), [
            self.root / "reservations/001.json"
        ])

    def test_cli_still_has_no_model_launch_path(self) -> None:
        with patch("sys.argv", ["benchmark_issue10_v3.py", "run-one", str(self.root)]):
            with patch.object(
                runner.legacy,
                "_bounded_process",
                side_effect=AssertionError("CLI must not launch"),
            ):
                with self.assertRaises(SystemExit) as raised:
                    runner.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.root / "reservations").exists())


if __name__ == "__main__":
    unittest.main()
