#!/usr/bin/env python3
"""Offline tests: no Codex session, credentials, or user configuration access."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import benchmark_issue10_sandbox as sandbox


def rejects(fn, message):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(message)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        workspace = root / "workspace"
        workspace.mkdir(mode=0o700)
        protected = root / "private"
        protected.mkdir(mode=0o700)
        canary_file = protected / "canary-file"
        canary_file.write_text("synthetic\n")
        executable = root / "fake-codex"
        executable.write_text("fake, never executed\n")
        executable.chmod(0o700)
        with patch.object(sandbox.subprocess, "run", side_effect=AssertionError("no preflight process allowed")):
            rejects(
                lambda: sandbox.verify_no_managed_mcp(executable, workspace, environment={}),
                "ChatGPT launch must remain blocked without credential/network access",
            )
        overrides = sandbox.config_overrides(workspace, [protected, canary_file])
        assert overrides[0] == 'default_permissions="issue10"'
        assert '":root"="deny"' in overrides[2]
        assert f'{json.dumps(str(protected))}="deny"' in overrides[2]
        assert '":workspace_roots"={"."="write"}' in overrides[2]
        pinned_overrides = sandbox.config_overrides(workspace, [root], [executable])
        assert f'{json.dumps(str(executable))}="read"' in pinned_overrides[2]
        rejects(lambda: sandbox.config_overrides(workspace, [executable], [executable]), "ambiguous executable")
        rejects(lambda: sandbox.config_overrides(workspace, [], [protected]), "directory exception")
        rejects(lambda: sandbox.config_overrides(workspace, [workspace]), "protect workspace")
        rejects(lambda: sandbox.config_overrides(workspace, [workspace / "missing"]), "missing")
        link = root / "linked-workspace"
        link.symlink_to(workspace, target_is_directory=True)
        rejects(lambda: sandbox.config_overrides(link, []), "symlink workspace")
        with patch.object(sandbox.platform, "system", return_value="Linux"):
            rejects(lambda: sandbox.qualify(executable, workspace, []), "platform must fail closed")

        seen = []
        def fake_run(command, **kwargs):
            seen.append((command, kwargs))
            if command[-1] == "--version":
                return subprocess.CompletedProcess(command, 0, sandbox.CODEX_VERSION + "\n", "")
            spec = json.loads(command[-1])
            assert spec["denied_open"], "real protected file must be tested without reading bytes"
            assert all(Path(path).exists() for path in spec["denied"].values())
            keys = set(spec["denied"]) | set(spec["denied_open"]) | set(spec["denied_writes"])
            keys |= {f"pinned_executable_{i}" for i in range(len(spec["read_executables"]))}
            keys |= {"allowed_read", "allowed_write", "descendant_read_denied", "tool_network_denied"}
            return subprocess.CompletedProcess(command, 0, json.dumps(dict.fromkeys(keys, True)), "")

        environment = {"HOME": str(root), "CODEX_HOME": str(protected), "PATH": "/usr/bin:/bin"}
        # Use an existing system interpreter for path/provenance checks only.
        interpreter = Path("/usr/bin/python3")
        if not interpreter.exists():
            raise AssertionError("offline qualification test needs /usr/bin/python3")
        pin = {
            "path": str(executable), "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
            "help_sha256": hashlib.sha256(b"synthetic help\n").hexdigest(),
        }
        with patch.object(sandbox.platform, "system", return_value="Darwin"), \
             patch.object(sandbox.sys, "executable", str(interpreter)), \
             patch.object(sandbox.subprocess, "run", side_effect=fake_run):
            receipt = sandbox.qualify(
                executable, workspace, [protected, canary_file],
                environment=environment, read_executables=[pin],
            )
            bad_pin = dict(pin, sha256="0" * 64)
            rejects(lambda: sandbox.qualify(executable, workspace, [], read_executables=[bad_pin]), "changed executable")
        assert receipt["read_executables"] == [pin]
        assert all(receipt["checks"].values()) and len(receipt["digest"]) == 64
        command, kwargs = seen[-1]
        assert kwargs["env"] == environment
        assert command[1:4] == ["sandbox", "-P", sandbox.PROFILE]
        prefix = command[:command.index("--")]
        assert [prefix[i + 1] for i, value in enumerate(prefix) if value == "-c"] == receipt["config_overrides"]
        assert not list(workspace.iterdir())
        assert list(protected.iterdir()) == [canary_file]

        def denial_missing(command, **kwargs):
            result = fake_run(command, **kwargs)
            if command[-1] != "--version":
                checks = json.loads(result.stdout)
                checks["symlink"] = False
                result.stdout = json.dumps(checks)
            return result
        with patch.object(sandbox.platform, "system", return_value="Darwin"), \
             patch.object(sandbox.sys, "executable", str(interpreter)), \
             patch.object(sandbox.subprocess, "run", side_effect=denial_missing):
            rejects(lambda: sandbox.qualify(executable, workspace, [canary_file]), "escaped read")
        assert not list(workspace.iterdir())
        with patch.object(sandbox.platform, "system", return_value="Darwin"), \
             patch.object(sandbox.sys, "executable", str(interpreter)), \
             patch.object(sandbox.subprocess, "run", side_effect=fake_run):
            unsafe_env = dict(environment, BASH_ENV="/untrusted/startup.sh")
            rejects(lambda: sandbox.qualify(executable, workspace, [], environment=unsafe_env), "unsafe environment")
        with patch.object(sandbox.platform, "system", return_value="Darwin"), \
             patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "codex-cli 0.152.0", "")):
            rejects(lambda: sandbox.qualify(executable, workspace, []), "version mismatch")
    print("benchmark_issue10 sandbox tests: ok")


if __name__ == "__main__":
    main()
