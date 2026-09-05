#!/usr/bin/env python3
"""No-model qualification of Codex 0.151.0 restrictive reads on macOS.

This is a compatibility canary, not a proof of complete sandbox confinement.
Only Codex's supported named permission profiles are used; never silently fall
back to workspace-write or an independently invented Seatbelt profile.
"""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import tempfile
from typing import Any

PROFILE = "issue10"
ENVIRONMENT_KEYS = {
    "HOME", "CODEX_HOME", "PATH", "SSHAI_ROOT", "TMPDIR", "LANG", "LC_ALL",
    "SHELL", "USER", "LOGNAME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
}
CODEX_VERSION = "codex-cli 0.151.0"
SOURCE = (
    "https://github.com/openai/codex/blob/rust-v0.151.0/"
    "codex-rs/config/src/permissions_toml.rs"
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _physical(path: Path) -> Path:
    """Require a physical existing path; do not normalize away input symlinks."""
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("sandbox qualification paths must not contain symlinks")
    if not path.exists():
        raise ValueError("sandbox qualification path does not exist")
    return path


def config_overrides(
    workspace: Path,
    protected_roots: list[Path],
    read_executables: list[Path] | None = None,
) -> list[str]:
    """Build whole TOML tables: dotted -c keys cannot encode these map keys."""
    workspace = _physical(workspace)
    if not workspace.is_dir():
        raise ValueError("sandbox workspace must be a directory")
    protected = sorted({_physical(path) for path in protected_roots})
    for path in protected:
        if path == workspace or path.is_relative_to(workspace):
            raise ValueError("protected paths must be outside the task workspace")
    entries = ['":root"="deny"', '":minimal"="read"']
    # Homebrew contains the pinned interpreter/tool runtime, not task answers.
    # Explicit denials below override this allowance for protected descendants.
    if Path("/opt/homebrew").is_dir():
        entries.append('"/opt/homebrew"="read"')
    entries += [f'{json.dumps(str(path))}="deny"' for path in protected]
    for executable in read_executables or []:
        executable = _physical(Path(executable))
        if (not stat.S_ISREG(executable.stat().st_mode)
                or not os.access(executable, os.X_OK) or executable in protected):
            raise ValueError("read exception requires an unambiguous regular executable")
        entries.append(f'{json.dumps(str(executable))}="read"')
    entries.append('":workspace_roots"={"."="write"}')
    return [
        f'default_permissions="{PROFILE}"',
        f'permissions.{PROFILE}.workspace_roots={{{json.dumps(str(workspace))}=true}}',
        f'permissions.{PROFILE}.filesystem={{{",".join(entries)}}}',
        f'permissions.{PROFILE}.network={{enabled=false}}',
    ]


# Every denied target exists and is readable by the unsandboxed parent. Never
# read user secrets to test a denial: probe files contain only synthetic bytes.
PROBE = r'''
import errno, hashlib, json, os, pathlib, resource, socket, subprocess, sys, tempfile
spec = json.loads(sys.argv[1])
results = {}
for name, path in spec["denied"].items():
    try:
        with open(path, "rb") as stream:
            stream.read(1)
    except OSError as exc:
        results[name] = exc.errno in (errno.EPERM, errno.EACCES)
    else:
        results[name] = False
for name, path in spec["denied_open"].items():
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        results[name] = exc.errno in (errno.EPERM, errno.EACCES)
    else:
        os.close(fd)  # Never consume bytes from a real protected file.
        results[name] = False
for name, path in spec["denied_writes"].items():
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        results[name] = exc.errno in (errno.EPERM, errno.EACCES)
    else:
        os.close(fd)
        results[name] = False
try:
    results["allowed_read"] = pathlib.Path(spec["allowed"]).read_text() == "synthetic-canary\n"
    pathlib.Path(spec["allowed_write"]).write_text("synthetic-write\n")
    results["allowed_write"] = True
except OSError:
    results["allowed_read"] = results["allowed_write"] = False
for index, pin in enumerate(spec["read_executables"]):
    # Bound captured help output at the kernel layer, without command bodies.
    def limit_capture():
        resource.setrlimit(resource.RLIMIT_FSIZE, (1000000, 1000000))
    with tempfile.TemporaryFile(dir=pathlib.Path(spec["allowed"]).parent) as capture:
        run = subprocess.run([pin["path"], "help"], stdout=capture,
                             stderr=subprocess.DEVNULL, timeout=5,
                             preexec_fn=limit_capture)
        capture.seek(0)
        output = capture.read(1000001)
        results[f"pinned_executable_{index}"] = (
            run.returncode == 0 and len(output) <= 1000000
            and hashlib.sha256(output).hexdigest() == pin["help_sha256"]
        )
child = 'import errno,sys\ntry:\n open(sys.argv[1],"rb")\nexcept OSError as e:\n sys.exit(0 if e.errno in (errno.EPERM,errno.EACCES) else 2)\nelse:\n sys.exit(3)'
run = subprocess.run(["/bin/sh", "-c", 'exec "$1" -I -c "$2" "$3"',
                      "probe", sys.executable, child, spec["child_denied"]],
                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
results["descendant_read_denied"] = run.returncode == 0
sock = socket.socket()
sock.settimeout(2)
try:
    sock.connect(("127.0.0.1", spec["port"]))
except OSError as exc:
    results["tool_network_denied"] = exc.errno in (errno.EPERM, errno.EACCES)
else:
    results["tool_network_denied"] = False
finally:
    sock.close()
print(json.dumps(results, sort_keys=True))
'''


def verify_no_managed_mcp(
    codex_path: Path,
    workspace: Path,
    *,
    environment: dict[str, str],
) -> dict[str, Any]:
    """Explicit launch barrier; do not inspect credentials or contact a service.

    Codex 0.151.0 can load cloud config_toml after refreshing ChatGPT auth.
    A local or network MCP inventory is not a stable absence proof. The study
    retains ChatGPT login by user choice; no supported all-clear exists yet.
    See rust-v0.151.0 codex-rs/cloud-config/src/service.rs and
    codex-rs/config/src/cloud_config_bundle.rs. Never replace this barrier with
    an operator bypass or a fabricated qualification receipt.
    """
    raise ValueError(
        "paid launch blocked: ChatGPT managed/cloud configuration cannot yet be "
        "qualified without possible parent-side MCP activation"
    )


def qualify(
    codex_path: Path,
    workspace: Path,
    protected_roots: list[Path],
    *,
    environment: dict[str, str] | None = None,
    read_executables: list[dict[str, str]] | None = None,
) -> dict:
    """Require fresh successful probes before a paid run; never call a model.

    The caller must use the returned config_overrides unchanged for codex exec,
    with the same environment and workspace. Receipt excludes credentials and
    probe contents. POSIX file permissions are not an agent read sandbox.
    """
    if platform.system() != "Darwin":
        raise ValueError("issue10 launch qualification currently supports macOS only")
    codex_path, workspace = _physical(Path(codex_path)), _physical(Path(workspace))
    if not stat.S_ISREG(codex_path.stat().st_mode) or not os.access(codex_path, os.X_OK):
        raise ValueError("qualification requires an executable native Codex file")
    pins = []
    for pin in read_executables or []:
        if set(pin) != {"path", "sha256", "help_sha256"}:
            raise ValueError("malformed pinned executable")
        executable = _physical(Path(pin["path"]))
        if (not stat.S_ISREG(executable.stat().st_mode)
                or not os.access(executable, os.X_OK)
                or _digest(executable.read_bytes()) != pin["sha256"]):
            raise ValueError("pinned executable changed or is unsafe")
        pins.append(dict(pin, path=str(executable)))
    python = Path(sys.executable).resolve(strict=True)
    if not str(python).startswith(("/opt/homebrew/", "/usr/bin/")):
        raise ValueError("qualification Python runtime is outside the reviewed readable roots")
    with ExitStack() as cleanup:
        tmp = cleanup.enter_context(tempfile.TemporaryDirectory(
            prefix="issue10-qualification-", dir=workspace.parent,
        ))
        control = Path(tmp)
        home, codex_home = control / "home", control / "codex-home"
        home.mkdir(mode=0o700)
        codex_home.mkdir(mode=0o700)
        env = dict(environment) if environment is not None else {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(home), "CODEX_HOME": str(codex_home),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        }
        if set(env) - ENVIRONMENT_KEYS or not {"HOME", "CODEX_HOME", "PATH"} <= set(env):
            raise ValueError("qualification requires an explicit non-secret environment allowlist")
        protected = [Path(path) for path in protected_roots] + [control]
        overrides = config_overrides(workspace, protected, [Path(pin["path"]) for pin in pins])
        version = subprocess.run(
            [str(codex_path), "--version"], env=env, cwd=workspace,
            capture_output=True, text=True, timeout=10, check=False,
        )
        if version.returncode != 0 or version.stdout.strip() != CODEX_VERSION:
            raise ValueError("Codex version is not the reviewed 0.151.0")
        denied = {}
        denied_open = {}
        for index, path in enumerate(protected_roots):
            path = _physical(Path(path))
            name = f"protected_{index}"
            if path.is_dir():
                directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(
                    prefix=".issue10-canary-", dir=path,
                )))
                sentinel = directory / "canary"
                sentinel.write_text("synthetic-only\n")
                sentinel.chmod(0o600)
                denied[name] = str(sentinel)
            elif stat.S_ISREG(path.stat().st_mode):
                denied_open[name] = str(path)
            else:
                raise ValueError("protected root must be a directory or regular file")
        for name in ("sibling", "oracle", "evidence", "auth", "home", "repository"):
            target = control / name
            target.mkdir(mode=0o700, exist_ok=True)
            sentinel = target / "canary"
            sentinel.write_text("synthetic-only\n")
            sentinel.chmod(0o600)
            denied[name] = str(sentinel)
        with tempfile.TemporaryDirectory(prefix="qualification-visible-", dir=workspace) as visible:
            visible = Path(visible)
            allowed = visible / "allowed"
            allowed.write_text("synthetic-canary\n")
            allowed.chmod(0o600)
            link = visible / "linked-canary"
            link.symlink_to(denied["oracle"])
            denied["symlink"] = str(link)
            denied["parent_traversal"] = str(
                visible / ".." / ".." / control.name / "oracle" / "canary"
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                spec = {
                    "denied": denied,
                    "denied_open": denied_open,
                    "read_executables": pins,
                    "denied_writes": {"sibling_write": str(control / "forbidden-write")},
                    "allowed": str(allowed),
                    "allowed_write": str(visible / "write"),
                    "child_denied": denied["auth"],
                    "port": listener.getsockname()[1],
                }
                command = [
                    str(codex_path), "sandbox", "-P", PROFILE,
                    "--include-managed-config", "-C", str(workspace),
                ]
                for override in overrides:
                    command.extend(["-c", override])
                command.extend(["--", str(python), "-I", "-c", PROBE, json.dumps(spec)])
                result = subprocess.run(
                    command, env=env, cwd=workspace, capture_output=True,
                    text=True, timeout=20, check=False,
                )
            try:
                checks = json.loads(result.stdout)
            except (ValueError, TypeError):
                raise ValueError("sandbox probe did not produce its expected result") from None
            expected = set(denied) | set(denied_open) | {
                f"pinned_executable_{index}" for index in range(len(pins))
            } | {
                "sibling_write", "allowed_read", "allowed_write",
                "descendant_read_denied", "tool_network_denied",
            }
            if result.returncode != 0 or not isinstance(checks, dict) or set(checks) != expected:
                raise ValueError("sandbox probe lifecycle or case inventory failed")
            if not all(value is True for value in checks.values()):
                failures = ", ".join(sorted(key for key, value in checks.items() if value is not True))
                raise ValueError(f"sandbox qualification failed: {failures}")
        for pin in pins:
            executable = _physical(Path(pin["path"]))
            if _digest(executable.read_bytes()) != pin["sha256"]:
                raise ValueError("pinned executable changed during qualification")
        # The measured profile must be identical to the probed profile: keeping
        # this now-absent path denied is harmless and preserves exact settings.
        receipt = {
            "schema": "sshai-issue10-sandbox/v1",
            "codex_version": CODEX_VERSION,
            "codex_sha256": _digest(codex_path.read_bytes()),
            "macos_version": platform.mac_ver()[0],
            "kernel": platform.release(),
            "kernel_build": platform.version(),
            "environment_sha256": _digest(json.dumps(env, sort_keys=True).encode()),
            "machine": platform.machine(),
            "workspace": str(workspace),
            "protected_roots": sorted(str(_physical(path)) for path in protected_roots),
            "config_overrides": overrides,
            "read_executables": pins,
            "python_sha256": _digest(python.read_bytes()),
            "probe_sha256": _digest(PROBE.encode()),
            "source": SOURCE,
            "checks": checks,
            "limitations": [
                "Protected directories use synthetic canaries; protected files are opened without reading bytes.",
                "No finite probe proves complete confinement; live Codex parity needs pilot validation.",
                "Trusted Homebrew runtime and Codex minimal runtime roots remain readable.",
            ],
        }
        receipt["digest"] = _digest(json.dumps(receipt, sort_keys=True).encode())
        return receipt
