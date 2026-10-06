#!/usr/bin/env python3
"""Synthetic FIFO broker regressions; the installed Codex canary is opt-in."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    import benchmark_issue10_fifo_broker as fifo
except ModuleNotFoundError:
    fifo = None


def wait_read(fd: int, timeout: float = 3) -> bytes:
    if not select.select([fd], [], [], timeout)[0]:
        raise AssertionError("synthetic controller did not signal in time")
    return os.read(fd, 4096)


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(fifo, "the controller-owned FIFO broker is not implemented")
        self.tmp = tempfile.TemporaryDirectory(prefix="issue10-fifo-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.scratch = self.root / "scratch"
        self.scratch.mkdir()
        self.home = self.root / "home"
        self.home.mkdir()
        self.audit = self.root / "private" / "audit"
        self.audit.parent.mkdir()
        self.env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin:/opt/homebrew/bin",
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}

    def start(self, callback=None, **limits):
        def echo(request):
            return fifo.TransportResult(b"out:\x00\xff" + request.command + request.stdin,
                                        b"err:\x00\xfe", 7)
        broker = fifo.FIFOBroker(self.scratch, "issue10-synthetic", callback or echo,
                                 audit_dir=self.audit,
                                 limits=fifo.Limits(io_timeout=0.5, command_timeout=1,
                                                    slot_timeout=10, **limits)).start()
        self.addCleanup(broker.stop)
        shim = self.scratch / "ssh"
        shim.write_text(fifo.ssh_shim_source(broker.endpoint, Path(sys.executable).resolve()))
        shim.chmod(0o700)
        self.broker, self.shim = broker, shim
        return broker

    def call(self, *args: str, stdin: bytes = b""):
        return subprocess.run([str(self.shim), *args], input=stdin, env=self.env,
                              capture_output=True, timeout=5)

    def direct(self, payload: bytes, declared: int | None = None):
        fd = os.open(self.broker.endpoint.request_path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, struct.pack("!I", len(payload) if declared is None else declared) + payload)
        finally:
            os.close(fd)

    def request(self, transaction: str = "a" * 32, argv=None):
        return json.dumps({"schema": fifo.REQUEST_SCHEMA, "id": transaction,
                           "argv": [base64.b64encode(x).decode() for x in
                                    (argv or [b"issue10-synthetic", b"synthetic command"])],
                           "stdin": ""}, separators=(",", ":")).encode()

    def test_bash_descendant_preserves_bytes_exit_and_original_request(self):
        broker = self.start()
        p = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c",
                            'exec "$@"', "probe", str(self.shim),
                            "issue10-synthetic", "printf synthetic"], input=b"\x00\xff\n",
                           env=self.env, capture_output=True, timeout=5, close_fds=True)
        self.assertEqual((p.returncode, p.stdout, p.stderr),
                         (7, b"out:\x00\xffprintf synthetic\x00\xff\n", b"err:\x00\xfe"))
        status = broker.stop()
        self.assertEqual(status["calls"], 1)
        request_path = next(self.audit.glob("*.request.json"))
        request = json.loads(request_path.read_bytes())
        self.assertEqual(base64.b64decode(request["stdin"]), b"\x00\xff\n")
        response = json.loads(next(self.audit.glob("*.response.json")).read_bytes())
        self.assertEqual(response["request_sha256"], hashlib.sha256(request_path.read_bytes()).hexdigest())
        self.assertEqual(response["response"]["id"], request["id"])
        self.assertEqual(response["response"]["status"], "completed")
        self.assertFalse(broker.endpoint.directory.exists())
        self.assertIsNone(broker.pid)

    def test_sshai_fixed_options_are_accepted_but_never_forwarded_to_callback(self):
        def transport(request):
            self.assertFalse(hasattr(request, "argv"))
            self.assertFalse(hasattr(request, "host"))
            return fifo.TransportResult(request.command, request.stdin, 0)
        self.start(transport)
        path = str(self.scratch / "sshai-root" / "cm" / "%C")
        args = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR",
                "-o", "ControlMaster=auto", "-o", "ControlPath=" + path,
                "-o", "ControlPersist=15m", "--", "issue10-synthetic", "command"]
        p = self.call(*args, stdin=b"body")
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, b"command", b"body"))

    def test_open_ssh_baseline_joins_remote_command_arguments(self):
        self.start()
        p = self.call("issue10-synthetic", "printf", "synthetic")
        self.assertEqual(p.stdout, b"out:\x00\xffprintf synthetic")
        self.assertEqual(p.returncode, 7)

    def test_non_utf8_command_bytes_are_preserved(self):
        self.start()
        command = b"synthetic-\xff"
        p = self.call("issue10-synthetic", os.fsdecode(command))
        self.assertEqual((p.returncode, p.stdout), (7, b"out:\x00\xff" + command))

    def test_main_adapter_body_limits_accept_exact_boundaries(self):
        with fifo.FifoBroker(self.scratch, lambda c, s: (c, s, 7), 3,
                             audit_dir=self.audit, io_timeout=2) as broker:
            command, body = b"x" * 65536, b"\x00\xff" * (1 << 19)
            p = subprocess.run([*broker.client_argv, "issue10-target", command.decode()],
                               input=body, env=self.env, capture_output=True, timeout=8)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (7, command, body))
        receipt, = broker.receipts
        self.assertEqual((receipt["command_bytes"], receipt["stdin_bytes"]), (65536, 1 << 20))

    def test_foreign_hosts_and_transport_options_never_invoke_transport(self):
        cases = [["foreign", "cmd"], ["user@issue10-synthetic", "cmd"],
                 ["-p", "22", "issue10-synthetic", "cmd"],
                 ["-i", "/synthetic-key", "issue10-synthetic", "cmd"],
                 ["-F", "/synthetic-config", "issue10-synthetic", "cmd"],
                 ["-o", "ProxyCommand=synthetic", "issue10-synthetic", "cmd"],
                 ["-o", "ProxyJump=none", "issue10-synthetic", "cmd"],
                 ["-o", "ControlPath=/foreign/%C", "issue10-synthetic", "cmd"],
                 ["-o", "BatchMode=no", "issue10-synthetic", "cmd"],
                 ["-o", "BatchMode=yes", "-o", "BatchMode=yes", "issue10-synthetic", "cmd"],
                 ["scp", "synthetic", "issue10-synthetic:/tmp/file"]]
        for index, args in enumerate(cases):
            with self.subTest(args=args):
                if index:
                    self.audit = self.audit.parent / ("audit" + str(index))
                broker = self.start()
                p = self.call(*args)
                self.assertEqual(p.returncode, 255)
                self.assertEqual(p.stdout, b"")
                self.assertEqual(broker.stop()["calls"], 0)

    def test_direct_protocol_cannot_bypass_fixed_host(self):
        broker = self.start()
        self.direct(self.request(argv=[b"foreign", b"cmd"]))
        self.assertEqual(broker.wait(3)["reason"], "rejected-request")
        self.assertEqual(broker.stop()["calls"], 0)

    def test_malformed_and_oversize_frames_fail_closed(self):
        cases = [(b"not-json", None), (b"", 10_000_000),
                 (b'{"schema":1,"schema":2}', None),
                 (self.request()[:-1], None)]
        for index, (payload, declared) in enumerate(cases):
            with self.subTest(index=index):
                if index:
                    self.audit = self.audit.parent / ("invalid" + str(index))
                broker = self.start()
                self.direct(payload, declared)
                self.assertEqual(broker.wait(3)["reason"], "protocol-error")
                self.assertEqual(broker.stop()["calls"], 0)

    def test_incomplete_frame_times_out_without_transport(self):
        broker = self.start()
        self.direct(b"partial", 100)
        self.assertEqual(broker.wait(3)["reason"], "frame-timeout")
        self.assertEqual(broker.stop()["calls"], 0)

    def test_inode_replacement_and_symlink_are_refused_and_not_unlinked(self):
        for index, symlink in enumerate([False, True]):
            with self.subTest(symlink=symlink):
                if index:
                    self.audit = self.audit.parent / "replacement2"
                broker = self.start()
                path = broker.endpoint.request_path
                path.unlink()
                if symlink:
                    target = self.root / "synthetic-target"
                    target.write_bytes(b"keep")
                    path.symlink_to(target)
                else:
                    os.mkfifo(path, 0o600)
                p = self.call("issue10-synthetic", "cmd")
                self.assertEqual(p.returncode, 255)
                self.assertEqual(broker.stop()["calls"], 0)
                self.assertTrue(path.exists() or path.is_symlink())
                if symlink:
                    self.assertEqual(target.read_bytes(), b"keep")

    def test_parallel_shim_rejects_busy_without_mixed_response(self):
        ready_r, ready_w = os.pipe()
        gate_r, gate_w = os.pipe()
        self.addCleanup(lambda: [os.close(f) for f in (ready_r, ready_w, gate_r, gate_w)])
        def blocked(request):
            os.write(ready_w, b"ready")
            os.read(gate_r, 1)
            return fifo.TransportResult(b"first", b"first-err", 7)
        broker = self.start(blocked)
        first = subprocess.Popen([str(self.shim), "issue10-synthetic", "first"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=self.env)
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        self.assertEqual(wait_read(ready_r), b"ready")
        second = self.call("issue10-synthetic", "second")
        self.assertEqual((second.returncode, second.stdout), (255, b""))
        self.assertIn(b"busy", second.stderr)
        os.write(gate_w, b"x")
        out, err = first.communicate(timeout=3)
        self.assertEqual((first.returncode, out, err), (7, b"first", b"first-err"))
        self.assertEqual(broker.stop()["calls"], 1)

    def test_direct_concurrent_frame_cancels_slot_without_second_execution(self):
        ready_r, ready_w = os.pipe()
        self.addCleanup(lambda: [os.close(f) for f in (ready_r, ready_w)])
        def blocked(request):
            os.write(ready_w, b"ready")
            time.sleep(30)
            return fifo.TransportResult(b"unreachable", b"", 0)
        broker = self.start(blocked)
        first = subprocess.Popen([str(self.shim), "issue10-synthetic", "first"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, env=self.env)
        self.addCleanup(lambda: first.kill() if first.poll() is None else None)
        wait_read(ready_r)
        self.direct(self.request("b" * 32))
        first.communicate(timeout=4)
        self.assertEqual(first.returncode, 255)
        self.assertEqual(broker.wait(3)["reason"], "concurrent-request")
        self.assertEqual(broker.stop()["calls"], 1)

    def test_command_deadline_and_stop_kill_transport_worker(self):
        for index, cancel in enumerate([False, True]):
            with self.subTest(cancel=cancel):
                if index:
                    self.audit = self.audit.parent / "cancel-audit"
                ready_r, ready_w = os.pipe()
                def blocked(request):
                    os.write(ready_w, str(os.getpid()).encode())
                    time.sleep(30)
                    return fifo.TransportResult(b"unreachable", b"", 0)
                broker = self.start(blocked)
                p = subprocess.Popen([str(self.shim), "issue10-synthetic", "cmd"],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env=self.env)
                worker_pid = int(wait_read(ready_r))
                started = time.monotonic()
                if cancel:
                    self.assertEqual(broker.stop()["reason"], "cancelled")
                out, err = p.communicate(timeout=4)
                self.assertEqual((p.returncode, out), (255, b""))
                self.assertLess(time.monotonic() - started, 3)
                with self.assertRaises(ProcessLookupError):
                    os.kill(worker_pid, 0)
                broker.stop()
                os.close(ready_r)
                os.close(ready_w)

    def test_output_cap_is_not_full_success(self):
        broker = self.start(lambda r: fifo.TransportResult(b"x" * 100, b"y" * 100, 0),
                            max_output_bytes=32)
        p = self.call("issue10-synthetic", "cmd")
        self.assertEqual(p.returncode, 255)
        self.assertEqual(p.stdout, b"x" * 32)
        self.assertIn(b"output limited", p.stderr)
        broker.stop()
        reply = json.loads(next(self.audit.glob("*.response.json")).read_bytes())["response"]
        self.assertEqual((reply["status"], reply["transport_exit_code"]), ("output-limited", 0))

    def test_input_caps_and_call_ceiling(self):
        broker = self.start(max_stdin_bytes=8, max_command_bytes=8, max_calls=1)
        for command, body in [("123456789", b""), ("cmd", b"123456789")]:
            p = self.call("issue10-synthetic", command, stdin=body)
            self.assertEqual(p.returncode, 255)
        self.assertEqual(self.call("issue10-synthetic", "cmd").returncode, 7)
        self.assertEqual(self.call("issue10-synthetic", "cmd").returncode, 255)
        self.assertEqual(broker.wait(3)["reason"], "call-limit")
        self.assertEqual(broker.stop()["calls"], 1)

    def test_slot_deadline_and_empty_stop_leave_no_process_or_fifo(self):
        broker = self.start()
        self.assertEqual(broker.stop()["reason"], "cancelled")
        self.assertFalse(broker.endpoint.directory.exists())
        self.assertEqual(broker.stop()["reason"], "cancelled")
        self.audit = self.audit.parent / "slot-timeout"
        broker = fifo.FIFOBroker(self.scratch, "issue10-synthetic",
                                 lambda r: fifo.TransportResult(b"", b"", 0),
                                 audit_dir=self.audit,
                                 limits=fifo.Limits(slot_timeout=0.2)).start()
        self.addCleanup(broker.stop)
        self.assertEqual(broker.wait(3)["reason"], "slot-timeout")
        broker.stop()
        self.assertFalse(broker.endpoint.directory.exists())

    def test_start_failure_closes_descriptors_and_removes_owned_endpoints(self):
        before = len(list(Path("/dev/fd").iterdir()))
        broker = fifo.FIFOBroker(self.scratch, "issue10-synthetic", lambda r: None,
                                 audit_dir=self.audit)
        with patch.object(fifo.os, "fork", side_effect=OSError("synthetic fork failure")):
            with self.assertRaises(OSError):
                broker.start()
        self.assertEqual(list(self.scratch.iterdir()), [])
        self.assertEqual(len(list(Path("/dev/fd").iterdir())), before)
        self.assertIsNone(broker.pid)

    def test_stdout_backpressure_has_a_finite_client_deadline(self):
        broker = fifo.FifoBroker(self.scratch, lambda c, s: (b"x" * 200000, b"", 0),
                                 2, audit_dir=self.audit, io_timeout=0.2).start()
        self.addCleanup(broker.stop)
        p = subprocess.Popen([*broker.client_argv, "issue10-target", "cmd"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.env)
        def cleanup():
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=2)
        self.addCleanup(cleanup)
        # Do not drain stdout until the client exits: its pipe must fill.
        self.assertEqual(p.wait(timeout=4), 255)
        p.communicate(timeout=1)

    def test_transaction_replay_never_invokes_a_second_transport(self):
        broker = self.start()
        fd = os.open(broker.endpoint.response_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            payload = self.request()
            self.direct(payload)
            data = bytearray(wait_read(fd))
            while len(data) < 4 or len(data) < 4 + struct.unpack("!I", data[:4])[0]:
                data.extend(wait_read(fd))
            self.assertEqual(json.loads(data[4:])["id"], "a" * 32)
            self.direct(payload)
            self.assertEqual(broker.wait(3)["reason"], "protocol-error")
            self.assertEqual(broker.stop()["calls"], 1)
        finally:
            os.close(fd)

    def test_direct_protocol_cannot_bypass_stdin_cap(self):
        broker = self.start(max_stdin_bytes=8)
        value = json.loads(self.request())
        value["stdin"] = base64.b64encode(b"123456789").decode()
        self.direct(json.dumps(value).encode())
        self.assertEqual(broker.wait(3)["reason"], "rejected-request")
        self.assertEqual(broker.stop()["calls"], 0)

    def test_main_adapter_api_returns_hashed_private_receipts(self):
        def fixed_handler(command, stdin):
            return command, stdin, 7
        with fifo.FifoBroker(self.scratch, fixed_handler, 1, audit_dir=self.audit,
                             fixed_alias="issue10-target", io_timeout=0.5) as broker:
            p = subprocess.run([*broker.client_argv, "issue10-target", "synthetic"],
                               input=b"body\x00\xff", env=self.env, capture_output=True, timeout=4)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (7, b"synthetic", b"body\x00\xff"))
            source = fifo.shim_source(broker.endpoint, Path(sys.executable).resolve())
            self.assertTrue(source.startswith("#!"))
            with self.assertRaises(RuntimeError):
                _ = broker.receipts
        receipt, = broker.receipts
        self.assertEqual(receipt["command_sha256"], hashlib.sha256(b"synthetic").hexdigest())
        self.assertEqual(receipt["stdin_sha256"], hashlib.sha256(b"body\x00\xff").hexdigest())
        self.assertEqual(receipt["stdout_bytes"], 9)
        self.assertEqual(receipt["status"], "completed")
        self.assertEqual(receipt["exit_code"], 7)
        self.assertEqual(Path(receipt["request_path"]).parent, self.audit)
        self.assertEqual(len(receipt["response_sha256"]), 64)

    def test_live_checkpoint_separates_smoke_ids_without_stopping_broker(self):
        with fifo.FifoBroker(self.scratch, lambda c, s: (c, s, 7), 1,
                             audit_dir=self.audit, io_timeout=0.5) as broker:
            def call(command):
                return subprocess.run([*broker.client_argv, "issue10-target", command],
                                      input=b"", env=self.env, capture_output=True, timeout=4)
            self.assertEqual(broker.completed_transaction_ids(), ())
            self.assertEqual(call("smoke").returncode, 7)
            smoke_ids = broker.completed_transaction_ids()
            self.assertEqual(len(smoke_ids), 1)
            self.assertIsNotNone(broker.pid)
            with self.assertRaises(RuntimeError):
                _ = broker.receipts
            self.assertEqual(call("model-synthetic").returncode, 7)
            all_ids = broker.completed_transaction_ids()
            self.assertEqual(len(all_ids), 2)
            self.assertEqual(all_ids[:1], smoke_ids)
            self.assertEqual(len(set(all_ids) - set(smoke_ids)), 1)
        self.assertEqual(tuple(r["transaction_id"] for r in broker.receipts), all_ids)
        self.assertEqual([r["status"] for r in broker.receipts], ["completed", "completed"])
        with self.assertRaises(RuntimeError):
            broker.completed_transaction_ids()

    def test_live_checkpoint_active_timeout_does_not_cancel_broker(self):
        ready_r, ready_w = os.pipe()
        gate_r, gate_w = os.pipe()
        self.addCleanup(lambda: [os.close(f) for f in (ready_r, ready_w, gate_r, gate_w)])
        def transport(request):
            if request.command == b"slow":
                os.write(ready_w, b"ready")
                os.read(gate_r, 1)
            return fifo.TransportResult(request.command, b"", 7)
        broker = self.start(transport)
        p = subprocess.Popen([str(self.shim), "issue10-synthetic", "slow"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.env)
        def cleanup():
            if p.poll() is None:
                p.kill()
            p.communicate(timeout=2)
        self.addCleanup(cleanup)
        wait_read(ready_r)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            broker.completed_transaction_ids(timeout=0.05)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertIsNotNone(broker.pid)
        os.write(gate_w, b"x")
        self.assertEqual(p.communicate(timeout=3), (b"slow", b""))
        self.assertEqual(len(broker.completed_transaction_ids()), 1)
        self.assertEqual(self.call("issue10-synthetic", "next").returncode, 7)
        self.assertEqual(len(broker.completed_transaction_ids()), 2)

    def test_live_checkpoint_partial_frame_is_not_quiescent(self):
        broker = self.start()
        self.direct(b"partial", 100)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            broker.completed_transaction_ids(timeout=0.05)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertEqual(broker.wait(3)["reason"], "frame-timeout")
        with self.assertRaises(RuntimeError):
            broker.completed_transaction_ids()

    def test_partial_frame_cancellation_is_not_idle_stop(self):
        broker = self.start()
        self.direct(b"partial", 100)
        end = time.monotonic() + 0.2
        while not (self.audit / "busy").exists() and time.monotonic() < end:
            time.sleep(0.005)
        self.assertTrue((self.audit / "busy").exists())
        started = time.monotonic()
        status = broker.stop()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(status, {"reason": "cancelled-active-request", "calls": 0})
        self.assertEqual(list(self.audit.glob("*.request.json")), [])
        self.assertFalse(broker.endpoint.directory.exists())
        self.assertIsNone(broker.pid)

    def test_live_checkpoint_bounds_wait_and_preserves_lock(self):
        import fcntl
        broker = self.start()
        for value in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                broker.completed_transaction_ids(value)
        fd = os.open(broker.endpoint.lock_path, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                broker.completed_transaction_ids(timeout=100)
            self.assertLess(time.monotonic() - started, 0.8)
            fcntl.flock(fd, fcntl.LOCK_UN)
            self.assertEqual(broker.completed_transaction_ids(), ())
            self.assertEqual(self.call("issue10-synthetic", "next").returncode, 7)
        finally:
            os.close(fd)

    def test_live_checkpoint_does_not_consume_pending_response(self):
        broker = self.start()
        self.direct(self.request())
        end = time.monotonic() + 0.3
        while not list(self.audit.glob("*.completed")) and time.monotonic() < end:
            time.sleep(0.005)
        self.assertEqual(len(list(self.audit.glob("*.completed"))), 1)
        with self.assertRaises(TimeoutError):
            broker.completed_transaction_ids(timeout=0.05)
        fd = os.open(broker.endpoint.response_path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            data = bytearray(wait_read(fd))
            while len(data) < 4 or len(data) < 4 + struct.unpack("!I", data[:4])[0]:
                data.extend(wait_read(fd))
            self.assertEqual(json.loads(data[4:])["id"], "a" * 32)
        finally:
            os.close(fd)
        self.assertEqual(broker.completed_transaction_ids(), ("a" * 32,))
        marker, = self.audit.glob("*.completed")
        marker.write_bytes(b"synthetic-corruption")
        with self.assertRaises(RuntimeError):
            broker.completed_transaction_ids()
        marker.write_bytes(b"")
        self.assertEqual(self.call("issue10-synthetic", "next").returncode, 7)

    def test_live_checkpoint_waits_for_closed_audit_and_marker(self):
        # Delay the final audit write to reproduce the client-return/audit race.
        finish_r, finish_w = os.pipe()
        self.addCleanup(lambda: [os.close(f) for f in (finish_r, finish_w)])
        write_private = fifo._write_private
        def delayed(path, data):
            if str(path).endswith(".response.json"):
                write_private(path, data[:1])
                os.read(finish_r, 1)
                with open(path, "ab") as stream:
                    stream.write(data[1:])
            else:
                write_private(path, data)
        with patch.object(fifo, "_write_private", delayed):
            broker = self.start()
        self.assertEqual(self.call("issue10-synthetic", "smoke").returncode, 7)
        with self.assertRaises(TimeoutError):
            broker.completed_transaction_ids(timeout=0.05)
        os.write(finish_w, b"x")
        self.assertEqual(len(broker.completed_transaction_ids()), 1)

    def test_live_checkpoint_rejects_failed_classification(self):
        def transport(request):
            return fifo.TransportResult(b"x" * 100 if request.command == b"large" else b"ok", b"", 0)
        broker = self.start(transport, max_output_bytes=32)
        self.assertEqual(self.call("issue10-synthetic", "large").returncode, 255)
        with self.assertRaises(RuntimeError):
            broker.completed_transaction_ids()
        self.assertIsNotNone(broker.pid)
        self.assertEqual(self.call("issue10-synthetic", "small").returncode, 0)
        with self.assertRaises(RuntimeError):
            broker.completed_transaction_ids()
        broker.stop()
        replies = [json.loads(p.read_bytes())["response"] for p in sorted(self.audit.glob("*.response.json"))]
        self.assertEqual([r["status"] for r in replies], ["output-limited", "completed"])

    def test_audit_directory_must_be_outside_scratch_and_paths_physical(self):
        with self.assertRaises(ValueError):
            fifo.FIFOBroker(self.scratch, "issue10-synthetic", lambda r: None,
                            audit_dir=self.scratch / "audit").start()
        linked = self.root / "linked"
        linked.symlink_to(self.scratch, target_is_directory=True)
        with self.assertRaises(ValueError):
            fifo.FIFOBroker(linked, "issue10-synthetic", lambda r: None,
                            audit_dir=self.audit).start()


@unittest.skipUnless(os.environ.get("SSHAI_ISSUE10_CODEX_CANARY") == "1" and sys.platform == "darwin",
                     "optional pinned Darwin no-model sandbox canary")
class DarwinCanary(unittest.TestCase):
    def test_fifo_with_readonly_fixture_and_denied_data_network(self):
        import benchmark_issue10_sandbox as sandbox
        import benchmark_issue10_local_pilot as pilot
        binary = Path(os.environ.get("SSHAI_ISSUE10_CODEX_BINARY", str(pilot.EXPECTED_CODEX_PATH)))
        self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(),
                         "98491713ffb196061003ee148636e743997cc31d76144ba7c53462269896891d")
        import socket
        with tempfile.TemporaryDirectory(prefix="issue10-fifo-canary-", dir="/Users/Shared") as t:
            root = Path(t)
            scratch, fixture, private, home, codex_home = [root / x for x in
                                                        ("scratch", "fixture", "private", "home", "codex-home")]
            for p in (scratch, fixture, private, home, codex_home):
                p.mkdir()
            (fixture / "canary").write_bytes(b"synthetic-fixture")
            denied = {}
            for name in ("ssh-key", "auth", "home", "repository", "oracle", "evidence"):
                path = private / name
                path.write_bytes(b"synthetic-only")
                denied[name] = str(path)
            (scratch / "linked").symlink_to(private / "oracle")
            denied["symlink"] = str(scratch / "linked")
            denied["traversal"] = str(scratch / ".." / "private" / "auth")
            with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as agent:
                tcp.bind(("127.0.0.1", 0)); tcp.listen(1)
                agent.bind(str(private / "agent.sock")); agent.listen(1)
                with fifo.FIFOBroker(scratch, "issue10-synthetic",
                                     lambda r: fifo.TransportResult(r.command, r.stdin, 7),
                                     audit_dir=private / "audit") as broker:
                    shim = scratch / "ssh"
                    shim.write_text(fifo.ssh_shim_source(broker.endpoint, Path(sys.executable).resolve()))
                    shim.chmod(0o700)
                    spec = {"shim": str(shim), "denied": denied, "fixture": str(fixture / "canary"),
                            "scratch": str(scratch), "port": tcp.getsockname()[1],
                            "agent": str(private / "agent.sock"), "audit": str(private / "audit")}
                    probe = r'''
import errno,json,os,pathlib,socket,subprocess,sys
s=json.loads(sys.argv[1]);out={}
p=subprocess.run(['/bin/bash','--noprofile','--norc','-c','exec "$@"','probe',s['shim'],'issue10-synthetic','synthetic-command'],input=b'synthetic-stdin\x00\xff',capture_output=True,timeout=8,close_fds=True)
out['broker']=(p.returncode,p.stdout,p.stderr)==(7,b'synthetic-command',b'synthetic-stdin\x00\xff')
for k,v in s['denied'].items():
 try:pathlib.Path(v).read_bytes();out[k]=False
 except OSError as e:out[k]=e.errno in (errno.EPERM,errno.EACCES)
try:list(pathlib.Path(s['audit']).iterdir());out['control_records_denied']=False
except OSError as e:out['control_records_denied']=e.errno in (errno.EPERM,errno.EACCES)
out['fixture_read']=pathlib.Path(s['fixture']).read_bytes()==b'synthetic-fixture'
try:pathlib.Path(s['fixture']).write_bytes(b'bad');out['fixture_write_denied']=False
except OSError as e:out['fixture_write_denied']=e.errno in (errno.EPERM,errno.EACCES)
pathlib.Path(s['scratch'],'allowed').write_bytes(b'synthetic');out['scratch_write']=True
for k,addr,kind in [('loopback',('127.0.0.1',s['port']),socket.AF_INET),('outbound',('192.0.2.1',443),socket.AF_INET),('agent',s['agent'],socket.AF_UNIX)]:
 try:
  x=socket.socket(kind,socket.SOCK_STREAM);x.settimeout(1);x.connect(addr);out[k]=False;x.close()
 except OSError as e:out[k]=e.errno in (errno.EPERM,errno.EACCES)
print(json.dumps(out,sort_keys=True))
'''
                    cmd = [str(binary), "sandbox", "-P", sandbox.PROFILE, "--include-managed-config", "-C", str(scratch)]
                    for setting in pilot.access_overrides(scratch, fixture, [private, home, codex_home], Path("/usr/bin/true")):
                        cmd += ["-c", setting]
                    cmd += ["--", str(Path(sys.executable).resolve()), "-I", "-c", probe, json.dumps(spec)]
                    env = {"HOME": str(home), "CODEX_HOME": str(codex_home),
                           "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                           "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
                    result = subprocess.run(cmd, cwd=scratch, env=env, capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                    checks = json.loads(result.stdout)
                    self.assertEqual(len(checks), 16)
                    self.assertTrue(all(v is True for v in checks.values()), checks)
                    checkpoint_ids = broker.completed_transaction_ids()
                    self.assertEqual(len(checkpoint_ids), 1)
                # Low-level canary broker has no receipts property; the main
                # adapter's terminal receipts contract is covered above.
                self.assertEqual(broker.stop()["calls"], len(checkpoint_ids))


if __name__ == "__main__":
    unittest.main()
