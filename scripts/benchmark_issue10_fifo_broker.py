#!/usr/bin/env python3
"""Bounded, no-network Issue 10 command seam for an outside trusted controller.

The two FIFOs and an advisory transaction lock live in writable scratch. The
lock coordinates honest parallel shims; it is NOT authorization. The broker
validates every request itself, fixes the synthetic alias, discards SSH options,
and calls only a controller-supplied ``CommandRequest -> TransportResult``.
The callback must fix physical host, system OpenSSH options and confinement
prefix. It must not detach descendants from its broker-created process group.
No command is executed by this library or its shim on the controller host.

Start before launching the model. The Unix supervisor and per-command worker
are disposable processes, not daemon threads. Workers are killed/reaped on
completion, deadline or cancellation. Stop after stopping model tool processes.
``audit_dir`` must be outside scratch and explicitly denied by the qualified
Codex filesystem profile; modes 0600/0700 do not protect against the same user.
Requests are retained byte-for-byte privately, with transaction ID and SHA-256
bound into response records. Publication means FIFO publication, not receipt by
an agent. Output-limited responses are never reported as complete success.

The standalone stdlib-only shim has no imports from this repository and needs
no inherited descriptors. There is no protocol retry, reconnect or replay.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import errno
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import struct
import sys
import tempfile
import time
from typing import Callable

REQUEST_SCHEMA = "sshai-issue10-fifo-request/v1"
RESPONSE_SCHEMA = "sshai-issue10-fifo-response/v1"


class ProtocolError(ValueError):
    pass


class Cancelled(Exception):
    pass


class ConcurrentRequest(Exception):
    pass


class Deadline(Exception):
    pass


class IdleDeadline(Deadline):
    pass


@dataclass(frozen=True)
class Limits:
    max_command_bytes: int = 1 << 20
    max_stdin_bytes: int = 1 << 20
    max_output_bytes: int = 4 << 20
    max_request_bytes: int = 3 << 20
    max_response_bytes: int = 6 << 20
    max_calls: int = 64
    io_timeout: float = 5.0
    command_timeout: float = 30.0
    slot_timeout: float = 600.0

    def __post_init__(self):
        for key in ("max_command_bytes", "max_stdin_bytes", "max_output_bytes",
                    "max_request_bytes", "max_response_bytes", "max_calls"):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= (64 << 20):
                raise ValueError("invalid broker byte/call bound")
        if self.max_calls > 128 or self.max_request_bytes < 128:
            raise ValueError("invalid broker frame/call bound")
        if self.max_response_bytes < (4 * self.max_output_bytes + 2) // 3 + 1024:
            raise ValueError("response bound cannot hold capped output and metadata")
        for key in ("io_timeout", "command_timeout", "slot_timeout"):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 86400:
                raise ValueError("invalid broker deadline")


@dataclass(frozen=True)
class CommandRequest:
    transaction_id: str
    request_sha256: str
    command: bytes
    stdin: bytes
    deadline: float  # time.monotonic(), assigned by the controller, never the shim


@dataclass(frozen=True)
class TransportResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    output_limited: bool = False


@dataclass(frozen=True)
class BrokerEndpoint:
    directory: Path
    request_path: Path
    response_path: Path
    lock_path: Path
    alias: str
    control_path: str
    identities: dict[str, tuple[int, int, int, int]]
    limits: Limits


def _physical(path: Path) -> Path:
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("broker paths must be physical, not symlinked")
    if not path.is_dir():
        raise ValueError("broker parent must be an existing directory")
    return path.resolve(strict=True)


def _identity_key(s) -> tuple[int, int, int, int]:
    return (s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode), s.st_uid)


def _check_identity(identities):
    for path, expected in identities.items():
        try:
            s = os.lstat(path)
        except OSError as exc:
            raise ProtocolError("broker endpoint identity changed") from exc
        if tuple(expected) != _identity_key(s) or (not stat.S_ISDIR(s.st_mode) and s.st_nlink != 1):
            raise ProtocolError("broker endpoint identity changed")


def _parse_argv(argv, alias, control_path, command_limit):
    if not isinstance(argv, list) or not 2 <= len(argv) <= 64:
        raise ProtocolError("unsupported SSH argv")
    if any(type(x) is not bytes or b"\x00" in x for x in argv):
        raise ProtocolError("unsupported SSH argv")
    if sum(len(x) for x in argv) > command_limit + 8192:
        raise ProtocolError("SSH argv exceeds bound")
    fixed = {b"BatchMode": b"yes", b"ConnectTimeout": b"10", b"LogLevel": b"ERROR",
             b"ControlMaster": b"auto", b"ControlPersist": b"15m",
             b"ControlPath": control_path, b"StrictHostKeyChecking": b"yes",
             b"UpdateHostKeys": b"no"}
    seen = set()
    index = 0
    while index < len(argv):
        if argv[index] == b"--":
            index += 1
            break
        if not argv[index].startswith(b"-"):
            break
        if argv[index] != b"-o" or index + 1 >= len(argv):
            raise ProtocolError("unsupported SSH option")
        key, sep, value = argv[index + 1].partition(b"=")
        if not sep or key not in fixed or value != fixed[key] or key in seen:
            raise ProtocolError("unsupported SSH option")
        seen.add(key)
        index += 2
    if index >= len(argv) or argv[index] != alias or index + 1 >= len(argv):
        raise ProtocolError("unsupported SSH destination")
    # OpenSSH joins multiple command argv elements with spaces, without quoting.
    command = b" ".join(argv[index + 1:])
    if not command or len(command) > command_limit:
        raise ProtocolError("remote command exceeds bound")
    return command


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _loads(data: bytes):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ProtocolError("duplicate protocol key")
            out[key] = value
        return out
    def constant(_):
        raise ProtocolError("invalid protocol constant")
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ProtocolError("invalid protocol JSON") from exc


def _decode_bytes(value) -> bytes:
    if not isinstance(value, str):
        raise ProtocolError("invalid byte field")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError("invalid byte field") from exc
    if base64.b64encode(decoded).decode() != value:
        raise ProtocolError("noncanonical byte field")
    return decoded


def _decode_request(payload: bytes, endpoint: BrokerEndpoint, deadline: float) -> CommandRequest:
    value = _loads(payload)
    if (not isinstance(value, dict) or set(value) != {"schema", "id", "argv", "stdin"}
            or value["schema"] != REQUEST_SCHEMA
            or not isinstance(value["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", value["id"])
            or not isinstance(value["argv"], list) or len(value["argv"]) > 64):
        raise ProtocolError("invalid request envelope")
    argv = [_decode_bytes(x) for x in value["argv"]]
    command = _parse_argv(argv, endpoint.alias.encode(), os.fsencode(endpoint.control_path),
                          endpoint.limits.max_command_bytes)
    stdin = _decode_bytes(value["stdin"])
    if len(stdin) > endpoint.limits.max_stdin_bytes:
        raise ProtocolError("stdin exceeds bound")
    return CommandRequest(value["id"], hashlib.sha256(payload).hexdigest(), command, stdin, deadline)


def _reply(transaction: str, result: TransportResult, limits: Limits) -> dict:
    if (not isinstance(result, TransportResult) or type(result.stdout) is not bytes
            or type(result.stderr) is not bytes or type(result.exit_code) is not int
            or not 0 <= result.exit_code <= 255 or type(result.output_limited) is not bool):
        raise ValueError("transport callback returned an invalid result")
    stdout = result.stdout[:limits.max_output_bytes]
    stderr = result.stderr[:max(0, limits.max_output_bytes - len(stdout))]
    limited = result.output_limited or len(stdout) + len(stderr) < len(result.stdout) + len(result.stderr)
    return {"schema": RESPONSE_SCHEMA, "id": transaction,
            "stdout": base64.b64encode(stdout).decode(), "stderr": base64.b64encode(stderr).decode(),
            "status": "output-limited" if limited else "completed",
            "exit_code": 255 if limited else result.exit_code, "transport_exit_code": result.exit_code}


def _failure(transaction: str, status: str) -> dict:
    return {"schema": RESPONSE_SCHEMA, "id": transaction, "stdout": "", "stderr": "",
            "status": status, "exit_code": 255, "transport_exit_code": None}


def _kill_group(pid: int, sig: int):
    try:
        os.killpg(pid, sig)
    except OSError as exc:
        # Darwin returns EPERM for a group containing only a zombie leader.
        # Never signal a potentially reused individual PID after a reap; the
        # process group is the lifetime boundary for callback descendants.
        if exc.errno not in (errno.ESRCH, errno.EPERM):
            raise


def _reap_worker(pid: int):
    # Kill the whole callback group even after it returns, preventing SSH children
    # from outliving their call. A callback may not start detached sessions.
    _kill_group(pid, signal.SIGTERM)
    end = time.monotonic() + 0.1
    while time.monotonic() < end:
        found, _ = os.waitpid(pid, os.WNOHANG)
        if found:
            _kill_group(pid, signal.SIGKILL)
            return
        time.sleep(0.01)
    _kill_group(pid, signal.SIGKILL)
    end = time.monotonic() + 1
    while time.monotonic() < end:
        found, _ = os.waitpid(pid, os.WNOHANG)
        if found:
            return
        time.sleep(0.01)
    raise RuntimeError("transport worker could not be reaped")


def _pending(fd: int) -> bool:
    # Readiness also detects unsolicited extra frames, without consuming them.
    return bool(select.select([fd], [], [], 0)[0])


def _write_private(path: Path, data: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(data)
        while view:
            amount = os.write(fd, view)
            if amount <= 0:
                raise OSError("short audit write")
            view = view[amount:]
    finally:
        os.close(fd)


class FIFOBroker:
    """Per-slot serial broker. ``start``/``stop`` own only their new endpoints.

    ``audit_dir`` must not already exist. Audit files are retained after stop.
    Callback exceptions become a canonical failure, never raw error output.
    Use ``wait(timeout)`` for a bounded terminal wait; it does not stop a live
    slot on timeout. ``stop`` cancels active work and is idempotent. An unfinished
    frame records ``cancelled-active-request``, distinct from idle ``cancelled``.
    """
    def __init__(self, scratch: Path, alias: str,
                 transport: Callable[[CommandRequest], TransportResult], *,
                 audit_dir: Path, limits: Limits | None = None, control_path: str | None = None):
        if os.name != "posix" or not hasattr(os, "fork"):
            raise ValueError("FIFO broker requires Unix fork/process groups")
        self.scratch = _physical(scratch)
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", alias):
            raise ValueError("synthetic alias must be a simple fixed SSH alias")
        self.audit_dir = Path(audit_dir).absolute()
        _physical(self.audit_dir.parent)
        if self.audit_dir.is_relative_to(self.scratch) or self.scratch.is_relative_to(self.audit_dir):
            raise ValueError("private broker audit must be outside scratch")
        if self.audit_dir.exists() or self.audit_dir.is_symlink():
            raise ValueError("audit directory must be new")
        if not callable(transport):
            raise ValueError("a fixed controller transport callback is required")
        self.transport = transport
        self.limits = limits or Limits()
        self.alias = alias
        self.control_path = control_path or str(self.scratch / "sshai-root" / "cm" / "%C")
        control = Path(self.control_path)
        if (not control.is_absolute() or not control.is_relative_to(self.scratch)
                or any(part in ("..", ".") for part in control.parts) or control.name != "%C"
                or "\x00" in self.control_path):
            raise ValueError("ControlPath must be a fixed scratch path ending in %C")
        self.pid: int | None = None
        self.endpoint: BrokerEndpoint | None = None
        self._fds: list[int] = []
        self._startup_pipe_fds: list[int] = []
        self._owned_directory: Path | None = None
        self._owned_paths: dict[str, tuple[int, int, int, int]] = {}
        self._owned_dirs = {str(self.scratch): _identity_key(os.lstat(self.scratch))}
        self._info_fd: int | None = None
        self._cancel_fd: int | None = None
        self._info_buffer = bytearray()
        self._active: set[int] = set()
        self._ready = False
        self._wait_status: int | None = None
        self._terminal: dict | None = None
        self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.stop()

    def start(self):
        if self._started:
            raise ValueError("broker cannot be restarted or replayed")
        self._started = True
        try:
            self.audit_dir.mkdir(mode=0o700)
            directory = Path(tempfile.mkdtemp(prefix=".issue10-fifo-", dir=self.scratch))
            self._owned_directory = directory
            self._owned_dirs[str(directory)] = _identity_key(os.lstat(directory))
            request, response, lock = (directory / x for x in ("requests", "responses", "transaction.lock"))
            for path in (request, response):
                os.mkfifo(path, 0o600)
                self._owned_paths[str(path)] = _identity_key(os.lstat(path))
            lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self._fds.append(lock_fd)
            self._owned_paths[str(lock)] = _identity_key(os.fstat(lock_fd))
            request_fd = os.open(request, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
            self._fds.append(request_fd)
            response_fd = os.open(response, os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)
            self._fds.append(response_fd)
            identities = {**self._owned_dirs, **self._owned_paths}
            self.endpoint = BrokerEndpoint(directory, request, response, lock, self.alias,
                                           self.control_path, identities, self.limits)
            cancel_r, cancel_w = os.pipe()
            self._startup_pipe_fds.extend((cancel_r, cancel_w))
            info_r, info_w = os.pipe()
            self._startup_pipe_fds.extend((info_r, info_w))
            for fd in (cancel_r, cancel_w, info_r, info_w):
                os.set_blocking(fd, False)
            pid = os.fork()
            if pid == 0:
                os.close(cancel_w); os.close(info_r)
                try:
                    os.setsid()
                    self._serve(request_fd, response_fd, cancel_r, info_w)
                except BaseException:
                    os._exit(1)
                os._exit(0)
            self.pid, self._cancel_fd, self._info_fd = pid, cancel_w, info_r
            os.close(cancel_r); os.close(info_w)
            self._startup_pipe_fds = []
            end = time.monotonic() + self.limits.io_timeout + 1
            while not self._ready:
                self._refresh()
                if self.pid is None or time.monotonic() >= end:
                    raise RuntimeError("broker supervisor did not become ready")
                select.select([self._info_fd], [], [], min(0.05, end - time.monotonic()))
            return self
        except BaseException:
            self.stop()
            raise

    def _emit(self, fd: int, event: str, pid: int = 0):
        data = _json_bytes({"event": event, "pid": pid}) + b"\n"
        # At most 2*128 tiny worker records plus readiness, below a normal Unix
        # pipe capacity; no daemon thread or unbounded audit IPC is necessary.
        if os.write(fd, data) != len(data):
            raise RuntimeError("supervisor control channel failed")

    def _refresh(self):
        if self._info_fd is not None:
            while True:
                try:
                    data = os.read(self._info_fd, 4096)
                except BlockingIOError:
                    break
                if not data:
                    break
                self._info_buffer.extend(data)
                if len(self._info_buffer) > 16384:
                    raise RuntimeError("invalid supervisor status channel")
                while b"\n" in self._info_buffer:
                    line, _, tail = self._info_buffer.partition(b"\n")
                    self._info_buffer = bytearray(tail)
                    value = _loads(bytes(line))
                    if value["event"] == "ready":
                        self._ready = True
                    elif value["event"] == "active":
                        self._active.add(value["pid"])
                    elif value["event"] == "reaped":
                        self._active.discard(value["pid"])
        if self.pid is not None:
            found, status = os.waitpid(self.pid, os.WNOHANG)
            if found:
                self.pid = None
                self._wait_status = status

    def wait(self, timeout: float) -> dict:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("terminal wait needs a positive finite timeout")
        end = time.monotonic() + timeout
        while self.pid is not None:
            self._refresh()
            if self.pid is None:
                break
            if time.monotonic() >= end:
                raise TimeoutError("broker is still running")
            select.select([self._info_fd], [], [], min(0.05, end - time.monotonic()))
        self._refresh()
        return self._status()

    def completed_transaction_ids(self, timeout: float = 5) -> tuple[str, ...]:
        """Controller-only live checkpoint, after clients return and before new ones.

        Wait at most min(timeout, io_timeout) for quiescence and closed private
        audit markers. Never read live JSON, consume FIFO data, stop or restart.
        Completed IDs describe transactions, not successful remote exit codes.
        A failed classification or terminal broker fails closed. Direct protocol
        writers can invalidate quiescence; this is not a future authorization.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("checkpoint needs a positive finite timeout")
        end = time.monotonic() + min(timeout, self.limits.io_timeout)
        while True:
            self._refresh()
            if (not self._ready or self.pid is None or (self.audit_dir / "status.json").exists()
                    or (self.audit_dir / "rejected-request.bin").exists()
                    or next(self.audit_dir.glob("*.failed"), None) is not None):
                raise RuntimeError("broker checkpoint requires a live healthy slot")
            _check_identity(self.endpoint.identities)
            locked = False
            try:
                fcntl.flock(self._fds[0], fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                inventories = []
                for suffix in (".request.json", ".completed"):
                    stems = set()
                    for path in self.audit_dir.glob("*" + suffix):
                        stem = path.name[:-len(suffix)]
                        metadata = os.lstat(path)
                        if (len(stems) >= self.limits.max_calls + 1
                                or not re.fullmatch(r"[0-9]{4}-[0-9a-f]{32}", stem)
                                or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                                or (suffix == ".completed" and metadata.st_size != 0)):
                            raise RuntimeError("invalid private checkpoint inventory")
                        stems.add(stem)
                    inventories.append(stems)
                if (inventories[0] == inventories[1] and not self._active
                        and not _pending(self._fds[1]) and not _pending(self._fds[2])
                        and not (self.audit_dir / "busy").exists()):
                    self._refresh()
                    if self.pid is None or (self.audit_dir / "status.json").exists():
                        raise RuntimeError("broker became terminal during checkpoint")
                    return tuple(stem[-32:] for stem in sorted(inventories[0]))
            except BlockingIOError:
                pass
            finally:
                if locked:
                    fcntl.flock(self._fds[0], fcntl.LOCK_UN)
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("broker checkpoint did not become quiescent")
            select.select([self._info_fd], [], [], min(0.02, remaining))

    def _status(self):
        if self._terminal is None:
            path = self.audit_dir / "status.json"
            try:
                self._terminal = _loads(path.read_bytes())
            except OSError:
                self._terminal = {"reason": "supervisor-failed", "calls": 0}
        return dict(self._terminal)

    def stop(self) -> dict:
        if self.pid is not None:
            try:
                os.write(self._cancel_fd, b"x")
            except (BlockingIOError, BrokenPipeError):
                pass
            try:
                self.wait(self.limits.io_timeout + 2)
            except TimeoutError:
                self._refresh()
                for child in self._active:
                    _kill_group(child, signal.SIGKILL)
                if self.pid is not None:
                    _kill_group(self.pid, signal.SIGKILL)
                    # Unlike recorded callback PIDs, this is our unreaped direct
                    # child, including a possible pre-setsid startup failure.
                    os.kill(self.pid, signal.SIGKILL)
                self.wait(2)
        self._refresh()
        # A failed supervisor may have been killed between publishing active and
        # reaped. Kill any remaining recorded callback groups, never guessed PIDs.
        for child in self._active:
            _kill_group(child, signal.SIGKILL)
        self._active.clear()
        for fd in [*self._fds, *self._startup_pipe_fds, self._info_fd, self._cancel_fd]:
            if fd is not None:
                os.close(fd)
        self._fds = []
        self._startup_pipe_fds = []
        self._info_fd = self._cancel_fd = None
        self._cleanup_endpoints()
        return self._status()

    def _cleanup_endpoints(self):
        if self._owned_directory is None:
            return
        # Also covers partial start failures. Do not follow substituted
        # directories or unlink substituted objects; unknown files stay intact.
        for directory, expected in self._owned_dirs.items():
            try:
                if _identity_key(os.lstat(directory)) != expected:
                    return
            except OSError:
                return
        for path, expected in self._owned_paths.items():
            try:
                if _identity_key(os.lstat(path)) == expected:
                    Path(path).unlink()
            except FileNotFoundError:
                pass
        try:
            self._owned_directory.rmdir()
        except OSError as exc:
            if exc.errno not in (errno.ENOTEMPTY, errno.ENOENT):
                raise

    def _check(self, cancel_fd: int, deadline: float, *, concurrent_fd: int | None = None):
        _check_identity(self.endpoint.identities)
        if _pending(cancel_fd):
            raise Cancelled()
        if concurrent_fd is not None and _pending(concurrent_fd):
            raise ConcurrentRequest()
        if time.monotonic() >= deadline:
            raise Deadline()

    def _read_exact(self, fd: int, amount: int, deadline: float, cancel_fd: int,
                    *, concurrent_fd: int | None = None, receive_start: bool = False) -> bytes:
        data = bytearray()
        while len(data) < amount:
            self._check(cancel_fd, deadline, concurrent_fd=concurrent_fd)
            ready, _, _ = select.select([fd, cancel_fd], [], [], min(0.05, max(0, deadline - time.monotonic())))
            if fd in ready:
                if receive_start and not data:
                    _write_private(self.audit_dir / "busy", b"")
                block = os.read(fd, min(65536, amount - len(data)))
                if not block:
                    raise ProtocolError("unexpected protocol EOF")
                data.extend(block)
        return bytes(data)

    def _receive(self, fd: int, cancel_fd: int, slot_deadline: float) -> bytes:
        try:
            first = self._read_exact(fd, 1, slot_deadline, cancel_fd, receive_start=True)
        except Deadline as exc:
            raise IdleDeadline() from exc
        deadline = min(slot_deadline, time.monotonic() + self.limits.io_timeout)
        header = first + self._read_exact(fd, 3, deadline, cancel_fd)
        size = struct.unpack("!I", header)[0]
        if not 0 < size <= self.limits.max_request_bytes:
            raise ProtocolError("request frame exceeds bound")
        return self._read_exact(fd, size, deadline, cancel_fd)

    def _publish(self, fd: int, payload: bytes, deadline: float, cancel_fd: int,
                 *, concurrent_fd: int | None = None):
        data = memoryview(struct.pack("!I", len(payload)) + payload)
        while data:
            self._check(cancel_fd, deadline, concurrent_fd=concurrent_fd)
            _, ready, _ = select.select([], [fd], [], min(0.05, max(0, deadline - time.monotonic())))
            if fd in ready:
                try:
                    written = os.write(fd, data[:65536])
                except BlockingIOError:
                    continue
                if written <= 0:
                    raise ProtocolError("short protocol write")
                data = data[written:]

    def _run_transport(self, request: CommandRequest, request_fd: int, cancel_fd: int, info_fd: int):
        read_fd, write_fd = os.pipe()
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            for fd in [*self._fds, cancel_fd, info_fd]:
                os.close(fd)
            try:
                os.setpgid(0, 0)
                try:
                    reply = _reply(request.transaction_id, self.transport(request), self.limits)
                except BaseException:
                    reply = _failure(request.transaction_id, "transport-failed")
                payload = _json_bytes(reply)
                if len(payload) > self.limits.max_response_bytes:
                    raise ProtocolError("worker response exceeds bound")
                frame = memoryview(struct.pack("!I", len(payload)) + payload)
                while frame and time.monotonic() < request.deadline:
                    if select.select([], [write_fd], [], 0.05)[1]:
                        amount = os.write(write_fd, frame[:65536])
                        frame = frame[amount:]
                os._exit(0 if not frame else 1)
            except BaseException:
                os._exit(1)
        os.close(write_fd)
        try:
            # Both sides set the group, eliminating a stop-before-child-setup gap.
            try:
                os.setpgid(pid, pid)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.ESRCH):
                    raise
            self._emit(info_fd, "active", pid)
            header = self._read_exact(read_fd, 4, request.deadline, cancel_fd, concurrent_fd=request_fd)
            size = struct.unpack("!I", header)[0]
            if not 0 < size <= self.limits.max_response_bytes:
                raise ProtocolError("invalid worker response bound")
            data = self._read_exact(read_fd, size, request.deadline, cancel_fd, concurrent_fd=request_fd)
            self._check(cancel_fd, request.deadline, concurrent_fd=request_fd)
            return _loads(data)
        finally:
            os.close(read_fd)
            _reap_worker(pid)
            self._emit(info_fd, "reaped", pid)

    def _serve(self, request_fd: int, response_fd: int, cancel_fd: int, info_fd: int):
        calls, reason = 0, "supervisor-failed"
        slot_deadline = time.monotonic() + self.limits.slot_timeout
        seen: set[str] = set()
        self._emit(info_fd, "ready")
        try:
            while True:
                payload = self._receive(request_fd, cancel_fd, slot_deadline)
                if _pending(response_fd):
                    raise ProtocolError("previous response was not consumed")
                deadline = min(slot_deadline, time.monotonic() + self.limits.command_timeout)
                try:
                    request = _decode_request(payload, self.endpoint, deadline)
                except ProtocolError:
                    _write_private(self.audit_dir / "rejected-request.bin", payload)
                    value = _loads(payload)
                    if (isinstance(value, dict) and isinstance(value.get("id"), str)
                            and re.fullmatch(r"[0-9a-f]{32}", value["id"])):
                        reply = _failure(value["id"], "rejected")
                        self._publish(response_fd, _json_bytes(reply), time.monotonic() + self.limits.io_timeout,
                                      cancel_fd, concurrent_fd=request_fd)
                        reason = "rejected-request"
                        break
                    raise
                if request.transaction_id in seen:
                    raise ProtocolError("transaction replay refused")
                seen.add(request.transaction_id)
                stem = f"{len(seen):04d}-{request.transaction_id}"
                _write_private(self.audit_dir / (stem + ".request.json"), payload)
                if calls >= self.limits.max_calls:
                    reply, reason = _failure(request.transaction_id, "call-limit"), "call-limit"
                else:
                    calls += 1
                    try:
                        reply = self._run_transport(request, request_fd, cancel_fd, info_fd)
                    except ConcurrentRequest:
                        reply, reason = _failure(request.transaction_id, "concurrent-request"), "concurrent-request"
                    except Cancelled:
                        reply, reason = _failure(request.transaction_id, "cancelled"), "cancelled"
                    except Deadline:
                        reply, reason = _failure(request.transaction_id, "timeout"), "command-timeout"
                    except ProtocolError:
                        reply, reason = _failure(request.transaction_id, "transport-failed"), "transport-failed"
                response_bytes = _json_bytes(reply)
                record = {"schema": "sshai-issue10-fifo-audit-response/v1",
                          "request_sha256": request.request_sha256, "response": reply,
                          "publication": "not-published"}
                # On a terminal protocol/cancel condition, publish only the active
                # transaction's failure. Never publish a second response to mix.
                try:
                    if reason in ("cancelled", "concurrent-request"):
                        # Cancellation control remains readable; this bounded final
                        # failure publication deliberately ignores only that signal.
                        self._publish_terminal(response_fd, response_bytes)
                    else:
                        self._publish(response_fd, response_bytes,
                                      min(slot_deadline, time.monotonic() + self.limits.io_timeout),
                                      cancel_fd, concurrent_fd=request_fd)
                    record["publication"] = "fifo-published"
                finally:
                    _write_private(self.audit_dir / (stem + ".response.json"), _json_bytes(record))
                    complete = record["publication"] == "fifo-published" and reply["status"] == "completed"
                    _write_private(self.audit_dir / (stem + (".completed" if complete else ".failed")), b"")
                (self.audit_dir / "busy").unlink()
                if reason != "supervisor-failed":
                    break
        except IdleDeadline:
            reason = "slot-timeout"
        except Deadline:
            reason = "frame-timeout"
        except Cancelled:
            active = (self.audit_dir / "busy").exists() or _pending(request_fd)
            reason = "cancelled-active-request" if active else "cancelled"
        except ConcurrentRequest:
            reason = "concurrent-request"
        except ProtocolError:
            reason = "protocol-error"
        finally:
            _write_private(self.audit_dir / "status.json", _json_bytes({"reason": reason, "calls": calls}))

    def _publish_terminal(self, fd: int, payload: bytes):
        data = memoryview(struct.pack("!I", len(payload)) + payload)
        deadline = time.monotonic() + self.limits.io_timeout
        while data and time.monotonic() < deadline:
            _check_identity(self.endpoint.identities)
            if select.select([], [fd], [], 0.05)[1]:
                try:
                    data = data[os.write(fd, data):]
                except BlockingIOError:
                    pass
        if data:
            raise Deadline()


_SHIM = r'''
import base64,errno,fcntl,json,os,re,select,stat,struct,sys,time
CONFIG = json.loads(base64.b64decode(CONFIG_BASE64))
REQUEST_SCHEMA = "sshai-issue10-fifo-request/v1"
RESPONSE_SCHEMA = "sshai-issue10-fifo-response/v1"

class ProtocolError(ValueError):
    pass

SHARED_HELPERS

def output(fd,data,deadline):
    flags=fcntl.fcntl(fd,fcntl.F_GETFL)
    fcntl.fcntl(fd,fcntl.F_SETFL,flags|os.O_NONBLOCK)
    try:
        data=memoryview(data)
        while data:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise ProtocolError('client output deadline')
            if select.select([],[fd],[],min(remaining,0.05))[1]:
                try:
                    amount=os.write(fd,data[:65536])
                except BlockingIOError:
                    continue
                if amount<=0:
                    raise ProtocolError('client short output')
                data=data[amount:]
    finally:
        fcntl.fcntl(fd,fcntl.F_SETFL,flags)

def fail(message):
    try:
        output(2,b"issue10 broker: "+message+b"\n",time.monotonic()+0.25)
    except (OSError,ValueError):
        pass
    raise SystemExit(255)

def wait(fd, writing, deadline):
    _check_identity(CONFIG['identities'])
    remaining=deadline-time.monotonic()
    if remaining <= 0:
        raise ProtocolError('broker IO deadline')
    ready=select.select([] if writing else [fd], [fd] if writing else [], [], min(remaining,0.05))
    return bool(ready[1] if writing else ready[0])

def read_exact(fd,n,deadline):
    out=bytearray()
    while len(out)<n:
        if wait(fd,False,deadline):
            block=os.read(fd,min(65536,n-len(out)))
            if not block:
                raise ProtocolError('broker EOF')
            out.extend(block)
    return bytes(out)

def write_frame(fd,payload,deadline):
    data=memoryview(struct.pack('!I',len(payload))+payload)
    while data:
        if wait(fd,True,deadline):
            try:
                amount=os.write(fd,data[:65536])
            except BlockingIOError:
                continue
            if amount<=0:
                raise ProtocolError('broker short write')
            data=data[amount:]

def open_endpoint(path,flags):
    _check_identity(CONFIG['identities'])
    fd=os.open(path,flags|os.O_NOFOLLOW|os.O_NONBLOCK)
    if _identity_key(os.fstat(fd))!=tuple(CONFIG['identities'][path]):
        os.close(fd)
        raise ProtocolError('broker endpoint changed')
    _check_identity(CONFIG['identities'])
    return fd

def main():
    limits=CONFIG['limits']
    argv=[os.fsencode(x) for x in sys.argv[1:]]
    _parse_argv(argv,CONFIG['alias'].encode(),os.fsencode(CONFIG['control_path']),limits['max_command_bytes'])
    fds=[]
    try:
        lock=open_endpoint(CONFIG['lock_path'],os.O_RDWR);fds.append(lock)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            fail(b'busy; concurrent invocation rejected')
        _check_identity(CONFIG['identities'])
        deadline=time.monotonic()+limits['io_timeout']
        body=bytearray()
        # This is a non-interactive shim. A terminal is not a command body.
        if not os.isatty(0):
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0 or not select.select([0],[],[],remaining)[0]:
                    raise ProtocolError('stdin deadline')
                block=os.read(0,min(65536,limits['max_stdin_bytes']+1-len(body)))
                if not block:
                    break
                body.extend(block)
                if len(body)>limits['max_stdin_bytes']:
                    raise ProtocolError('stdin exceeds bound')
        transaction=os.urandom(16).hex()
        request={'schema':REQUEST_SCHEMA,'id':transaction,
                 'argv':[base64.b64encode(x).decode() for x in argv],
                 'stdin':base64.b64encode(body).decode()}
        payload=json.dumps(request,sort_keys=True,separators=(',',':')).encode()
        if len(payload)>limits['max_request_bytes']:
            raise ProtocolError('request exceeds bound')
        request_fd=open_endpoint(CONFIG['request_path'],os.O_WRONLY);fds.append(request_fd)
        response_fd=open_endpoint(CONFIG['response_path'],os.O_RDONLY);fds.append(response_fd)
        write_frame(request_fd,payload,time.monotonic()+limits['io_timeout'])
        deadline=time.monotonic()+limits['command_timeout']+2*limits['io_timeout']+1
        size=struct.unpack('!I',read_exact(response_fd,4,deadline))[0]
        if not 0<size<=limits['max_response_bytes']:
            raise ProtocolError('response exceeds bound')
        reply=_loads(read_exact(response_fd,size,deadline))
        if (not isinstance(reply,dict) or set(reply)!={'schema','id','stdout','stderr','status','exit_code','transport_exit_code'}
            or reply['schema']!=RESPONSE_SCHEMA or reply['id']!=transaction
            or reply['status'] not in ('completed','output-limited','rejected','call-limit','concurrent-request','cancelled','timeout','transport-failed')
            or type(reply['exit_code']) is not int or not 0<=reply['exit_code']<=255):
            raise ProtocolError('response mismatch')
        out,err=_decode_bytes(reply['stdout']),_decode_bytes(reply['stderr'])
        if len(out)+len(err)>limits['max_output_bytes']:
            raise ProtocolError('output exceeds bound')
        _check_identity(CONFIG['identities'])
        deadline=time.monotonic()+limits['io_timeout']
        output(1,out,deadline)
        output(2,err,deadline)
        if reply['status']!='completed':
            label=b'output limited' if reply['status']=='output-limited' else reply['status'].encode()
            fail(label)
        raise SystemExit(reply['exit_code'])
    finally:
        for fd in reversed(fds):
            os.close(fd)

try:
    main()
except (OSError,ValueError,RecursionError):
    fail(b'request or channel rejected; no retry')
'''


def ssh_shim_source(endpoint: BrokerEndpoint, python_executable: Path) -> str:
    """Return an executable, standalone stdlib shim, with non-secret fixed config.

    Write it exclusively in a task-visible executable path. The broker does not
    trust this source, its embedded options, nor the model's protocol client.
    """
    python = Path(python_executable).absolute()
    if (not python.is_file() or not os.access(python, os.X_OK)
            or any(char.isspace() for char in str(python))):
        raise ValueError("shim requires an existing executable interpreter without whitespace")
    config = {"alias": endpoint.alias, "control_path": endpoint.control_path,
              "request_path": str(endpoint.request_path), "response_path": str(endpoint.response_path),
              "lock_path": str(endpoint.lock_path), "identities": endpoint.identities,
              "limits": asdict(endpoint.limits)}
    encoded = base64.b64encode(_json_bytes(config)).decode()
    shared = "\n".join(inspect.getsource(f) for f in
                       (_identity_key, _check_identity, _parse_argv, _loads, _decode_bytes))
    body = _SHIM.replace("CONFIG_BASE64", repr(encoded)).replace("SHARED_HELPERS", shared)
    return f"#!{python}\n" + body


# Recommended integration spelling; endpoint carries the full identity-bound
# specification. Supplying only two paths would omit the lock and inode pins.
shim_source = ssh_shim_source


class FifoBroker(FIFOBroker):
    """Main-adapter API: fixed_handler(command: bytes, stdin: bytes).

    Return ``(stdout: bytes, stderr: bytes, rc: int)`` or ``TransportResult``.
    The enclosing supervisor enforces deadline_seconds even if the callback
    stalls; the callback must keep OpenSSH in its inherited process group.
    ``scratch`` is an existing writable directory; a fresh endpoint subdirectory
    is created beneath it. ``audit_dir`` is a new private directory outside it.
    """
    def __init__(self, scratch: Path, fixed_handler, deadline_seconds: float = 30, *,
                 audit_dir: Path, fixed_alias: str = "issue10-target",
                 max_command_bytes: int = 64 << 10, max_stdin_bytes: int = 1 << 20,
                 max_output_bytes: int = 4 << 20, max_calls: int = 64,
                 io_timeout: float = 5, slot_timeout: float = 600,
                 control_path: str | None = None):
        if not callable(fixed_handler):
            raise ValueError("a fixed controller handler is required")
        def transport(request):
            result = fixed_handler(request.command, request.stdin)
            return result if isinstance(result, TransportResult) else TransportResult(*result)
        limits = Limits(max_command_bytes=max_command_bytes, max_stdin_bytes=max_stdin_bytes,
                        max_output_bytes=max_output_bytes, max_calls=max_calls,
                        command_timeout=deadline_seconds, io_timeout=io_timeout, slot_timeout=slot_timeout,
                        max_request_bytes=max(3 << 20, 2 * (max_command_bytes + max_stdin_bytes) + 16384),
                        max_response_bytes=max(6 << 20, 2 * max_output_bytes + 2048))
        super().__init__(scratch, fixed_alias, transport, audit_dir=audit_dir,
                         limits=limits, control_path=control_path)

    @property
    def client_argv(self) -> tuple[str, ...]:
        """Direct stdlib client argv, append normal SSH args; does not read stdin here."""
        if not self._ready or self.pid is None:
            raise RuntimeError("broker is not running")
        python = Path(sys.executable).resolve(strict=True)
        return (str(python), "-I", "-c", shim_source(self.endpoint, python))

    @property
    def receipts(self) -> tuple[dict, ...]:
        """Terminal, path/hash-only private evidence; never substitute for raw audits.

        Request hash covers the original JSON payload (excluding length header).
        Response hash covers canonical wire JSON; record hash covers the private
        response/publication record. Output hashes cover only retained bytes.
        """
        if self.pid is not None or not self._started:
            raise RuntimeError("stop the broker before reading receipts")
        receipts = []
        for request_path in sorted(self.audit_dir.glob("*.request.json")):
            payload = request_path.read_bytes()
            value = _loads(payload)
            reply_path = request_path.with_name(request_path.name.replace(".request.json", ".response.json"))
            response = reply_path.read_bytes() if reply_path.exists() else None
            record = _loads(response) if response is not None else None
            request_hash = hashlib.sha256(payload).hexdigest()
            request = _decode_request(payload, self.endpoint, 0)
            receipt = {"transaction_id": request.transaction_id, "request_sha256": request_hash,
                       "request_path": str(request_path), "response_path": str(reply_path),
                       "command_bytes": len(request.command), "command_sha256": hashlib.sha256(request.command).hexdigest(),
                       "stdin_bytes": len(request.stdin), "stdin_sha256": hashlib.sha256(request.stdin).hexdigest(),
                       "status": "response-missing", "publication": "not-published",
                       "response_sha256": None, "response_record_sha256": None}
            if record is not None:
                if record["request_sha256"] != request_hash or record["response"]["id"] != value["id"]:
                    raise ProtocolError("private receipt transaction mismatch")
                reply = record["response"]
                stdout, stderr = _decode_bytes(reply["stdout"]), _decode_bytes(reply["stderr"])
                receipt.update(status=reply["status"], publication=record["publication"],
                               response_sha256=hashlib.sha256(_json_bytes(reply)).hexdigest(),
                               response_record_sha256=hashlib.sha256(response).hexdigest(),
                               stdout_bytes=len(stdout), stderr_bytes=len(stderr),
                               stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                               stderr_sha256=hashlib.sha256(stderr).hexdigest(),
                               exit_code=reply["exit_code"], transport_exit_code=reply["transport_exit_code"])
            receipts.append(receipt)
        return tuple(receipts)
