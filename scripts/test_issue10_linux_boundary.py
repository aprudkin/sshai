"""Offline generation and synthetic-controller tests; no SSH/model/namespaces."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import benchmark_issue10_linux_boundary as boundary


def pin(data=b"synthetic\n"):
    return hashlib.sha256(data).hexdigest()


def plan_at(parent="/synthetic/control"):
    return {
        "schema": boundary.SCHEMA,
        "root": parent + "/issue10-linux-" + "a" * 32,
        "nonce": "a" * 32, "uid": 65534, "gid": 65534,
        "fixture_source": parent + "/incoming",
        "fixture_files": [{"path": "logs/example.log", "size": 10, "sha256": pin()}],
        "runtime_files": [
            {"source": "/usr/bin/" + name, "path": dest, "size": 10,
             "sha256": pin(), "executable": True}
            for name, dest in [("bash", "/bin/bash"), ("setpriv", "/usr/bin/setpriv"),
                               ("python3.11", "/usr/bin/python3")]
        ],
        "tools": {name: {"path": "/usr/bin/" + name, "sha256": pin()}
                  for name in ("python", "mount", "unshare")},
        "bash": "/bin/bash", "setpriv": "/usr/bin/setpriv", "python": "/usr/bin/python3",
    }


def remote_namespace():
    namespace = {}
    exec(compile(boundary._REMOTE, "synthetic-remote-library", "exec"), namespace)
    return namespace


class GenerationTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan_at()

    def canaries(self):
        return [{"name": name, "path": "/tmp/synthetic/" + name,
                 "sha256": pin(), "size": 10} for name in ("sibling", "other_case")]

    def test_generated_actions_are_bash_syntax_and_python_syntax(self):
        bodies = [boundary.preparation_body(self.plan), boundary.run_body(self.plan, b"printf '%s\\n' diagnostic\n"),
                  boundary.qualification_body(self.plan, self.canaries()), boundary.inspection_body(self.plan),
                  boundary.cleanup_body(self.plan)]
        for body in bodies:
            with self.subTest(action=body[-100:]):
                result = subprocess.run(["bash", "-n"], input=body.encode(), capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                script = body.split("\n", 1)[1].rsplit("\n", 2)[0] + "\n"
                compile(script, "generated-remote", "exec")

    def test_diagnostic_is_encoded_not_privileged_shell_code(self):
        diagnostic = b"echo injected; $(id); ' \"\nISSUE10_ANYTHING\n"
        body = boundary.run_body(self.plan, diagnostic)
        self.assertNotIn(diagnostic.decode(), body)
        # The controller request is data, not interpolated into privileged Python.
        script = body.split("\n", 1)[1].rsplit("\n", 2)[0]
        ns = remote_namespace()
        with mock.patch.dict(ns, {"main": lambda request: request}):
            request = eval(script.rsplit("\nmain(", 1)[1][:-1], ns)
        import base64
        self.assertEqual(base64.b64decode(request["body"]), diagnostic)
        self.assertEqual(request["action"], "run")

    def test_entry_command_is_fixed_path_and_one_base64_argument(self):
        import base64
        command = b"bash -s; printf '%s\\n' \"$(anything)\""
        argv = shlex.split(boundary.entry_command(self.plan, command))
        self.assertEqual(argv[0], self.plan['root'] + '/entry.sh')
        self.assertEqual(len(argv), 2)
        self.assertEqual(base64.b64decode(argv[1], validate=True), command)
        self.assertNotIn(command.decode(), boundary.entry_command(self.plan, command))
        for value in (b'', b'bad\0command', b'x' * (boundary.MAX_BODY + 1), 'text'):
            with self.assertRaises(ValueError): boundary.entry_command(self.plan, value)

    def test_entry_files_are_pinned_and_entry_shell_does_not_consume_stdin(self):
        import base64
        entries = boundary.entry_files(self.plan)
        self.assertEqual(set(entries), {'entry.py', 'entry.sh'})
        for name, row in entries.items():
            data = base64.b64decode(row['content'], validate=True)
            self.assertEqual(hashlib.sha256(data).hexdigest(), row['sha256'])
            if name.endswith('.py'): compile(data, name, 'exec')
            else:
                self.assertEqual(subprocess.run(['bash', '-n'], input=data, capture_output=True).returncode, 0)
                self.assertNotIn(b' -s', data)
                self.assertNotIn(b'<<', data)
                self.assertIn(b'"$@"', data)

    def test_returned_plan_is_detached(self):
        checked = boundary.validate_plan(self.plan)
        checked["tools"]["python"]["path"] = "/changed"
        self.assertEqual(self.plan["tools"]["python"]["path"], "/usr/bin/python")

    def test_invalid_roots_paths_ids_and_pins_refuse(self):
        cases = [("root", "/"), ("root", "/tmp/wrong"), ("root", "/tmp/../issue10-linux-" + "a" * 32),
                 ("root", "/tmp/has space/issue10-linux-" + "a" * 32), ("uid", 0),
                 ("uid", True), ("gid", 9999), ("nonce", "z" * 32),
                 ("fixture_source", self.plan["root"])]
        for name, value in cases:
            p = copy.deepcopy(self.plan); p[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                boundary.preparation_body(p)
        p = copy.deepcopy(self.plan); p["tools"]["mount"]["sha256"] = "unknown"
        with self.assertRaises(ValueError): boundary.preparation_body(p)

    def test_file_limits_overlap_and_reserved_destinations_refuse(self):
        for destination in ("/fixture/x", "/scratch/x", "/proc/x", "/dev/x", "/tmp/x", "/usr/../x"):
            p = copy.deepcopy(self.plan); p["runtime_files"][0]["path"] = destination
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                boundary.preparation_body(p)
        for mutate in (lambda p: p["runtime_files"].append(copy.deepcopy(p["runtime_files"][0])),
                       lambda p: p["runtime_files"][0].update(size=boundary.MAX_BYTES + 1),
                       lambda p: p["runtime_files"][0].update(executable="yes"),
                       lambda p: p.update(runtime_files=[]),
                       lambda p: p["fixture_files"][0].update(path="../outside")):
            p = copy.deepcopy(self.plan); mutate(p)
            with self.assertRaises(ValueError): boundary.preparation_body(p)

    def test_runtime_manifests_never_mount_source_directories(self):
        ns = remote_namespace()
        stage = ns["_STAGE"]
        self.assertNotIn("/etc", stage)
        self.assertNotIn("ro-bind /", stage)
        self.assertNotIn("fixture_source", stage)
        self.assertIn("copy_file(row[\"source\"]", boundary._REMOTE)
        self.assertNotIn("ldd", boundary._REMOTE)

    def test_body_timeout_and_output_bounds(self):
        for body in (b"", b"\0", b"x" * (boundary.MAX_BODY + 1), "echo text"):
            with self.subTest(body=type(body)), self.assertRaises(ValueError):
                boundary.run_body(self.plan, body)
        for seconds in (0, 601, True):
            with self.assertRaises(ValueError): boundary.run_body(self.plan, b"true", seconds=seconds)
        for limit in (0, 1023, 8 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError): boundary.run_body(self.plan, b"true", output_limit=limit)

    def test_canary_categories_and_pins_required(self):
        cases = [[], self.canaries()[:1], self.canaries() + [self.canaries()[0]]]
        bad = self.canaries(); bad[0]["path"] = self.plan["root"] + "/inside"; cases.append(bad)
        bad = self.canaries(); bad[0]["size"] = 4097; cases.append(bad)
        bad = self.canaries(); bad[1]["name"] = "not_other_case"; cases.append(bad)
        for canaries in cases:
            with self.assertRaises(ValueError): boundary.qualification_body(self.plan, canaries)


class SyntheticControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.plan = plan_at(str(self.parent))
        fixture = Path(self.plan["fixture_source"]); (fixture / "logs").mkdir(parents=True)
        (fixture / "logs/example.log").write_bytes(b"synthetic\n")
        for index, row in enumerate(self.plan["runtime_files"]):
            source = self.parent / ("runtime-" + str(index))
            source.write_bytes(b"synthetic\n"); source.chmod(0o755)
            row["source"] = str(source)
        self.ns = remote_namespace()

    def prepare(self):
        # Explicit synthetic seams: never acquire root or invoke a mount/chroot.
        with mock.patch.dict(self.ns, {"secure_parent": lambda p: self.parent}), mock.patch("os.chown"):
            return self.ns["prepare"](self.plan, boundary.entry_files(self.plan))

    def test_preparation_copies_only_pinned_population_and_is_one_shot(self):
        result = self.prepare(); root = Path(self.plan["root"])
        self.assertEqual(result["status"], "prepared")
        self.assertEqual((root / "rootfs/fixture/logs/example.log").read_bytes(), b"synthetic\n")
        self.assertEqual((root / "rootfs/bin/bash").read_bytes(), b"synthetic\n")
        self.assertEqual((root / "rootfs/bin/bash").stat().st_mode & 0o777, 0o555)
        with self.assertRaises(FileExistsError): self.prepare()

    def test_unlisted_fixture_and_symlink_source_refuse_before_creation(self):
        fixture = Path(self.plan["fixture_source"])
        (fixture / "extra").write_text("synthetic")
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(Path(self.plan["root"]).exists())
        (fixture / "extra").unlink(); (fixture / "extra").symlink_to(fixture / "logs/example.log")
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(Path(self.plan["root"]).exists())

    def test_hash_failure_retains_partial_root_without_ready_or_cleanup(self):
        self.plan["runtime_files"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError): self.prepare()
        root = Path(self.plan["root"])
        self.assertTrue((root / "owner.json").is_file())
        self.assertFalse((root / "ready").exists())
        with self.assertRaises(FileExistsError): self.prepare()

    def test_hardlink_and_parent_symlink_refuse(self):
        path = self.parent / "hardlinked"; path.write_bytes(b"synthetic\n")
        os.link(path, self.parent / "hardlink")
        with self.assertRaises(ValueError): self.ns["physical"](path, "file")
        link = self.parent / "alias"; link.symlink_to(Path(self.plan["fixture_source"]), target_is_directory=True)
        with self.assertRaises(ValueError): self.ns["physical"](link / "logs/example.log", "file")

    @contextlib.contextmanager
    def synthetic_uts(self, calls, *, current_inode=222, domain_result=0, hostname_error=None):
        from types import SimpleNamespace
        original_stat = os.stat
        def namespace_stat(path, *args, **kwargs):
            if path == '/proc/self/ns/uts':
                calls.append(('uts_guard', (path,), {}))
                return SimpleNamespace(st_dev=7, st_ino=current_inode)
            return original_stat(path, *args, **kwargs)
        def hostname(value):
            calls.append(('hostname', (value,), {}))
            if hostname_error: raise hostname_error
        def domainname(value, size):
            calls.append(('domainname', (value, size), {}))
            return domain_result
        libc = mock.Mock(); libc.setdomainname.side_effect = domainname
        with mock.patch('os.stat', namespace_stat), mock.patch('socket.sethostname', hostname), \
             mock.patch('ctypes.CDLL', return_value=libc):
            yield libc

    def test_stage_order_mounts_then_chroot_then_capability_drop_then_bash(self):
        calls = []
        def record(name):
            return lambda *a, **k: calls.append((name, a, k))
        stage_plan = {**self.plan, "command_b64": "YmFzaCAtcw==", "status_fd": 12, "parent_uts": [7, 111]}
        with self.synthetic_uts(calls), mock.patch("sys.argv", ["stage", json.dumps(stage_plan)]), \
             mock.patch('os.dup', return_value=13), mock.patch('os.open', return_value=14), \
             mock.patch('os.dup2'), mock.patch('os.close'), mock.patch('os.set_inheritable'), \
             mock.patch("subprocess.run", record("mount")), mock.patch("os.mknod", record("mknod")), \
             mock.patch("os.chmod", record("chmod")), mock.patch("os.chroot", record("chroot")), \
             mock.patch("os.chdir", record("chdir")), mock.patch("os.closerange", record("closefds")), \
             mock.patch("resource.setrlimit", record("rlimit")), mock.patch("os.execve", record("exec")):
            exec(self.ns["_STAGE"], {})
        names = [c[0] for c in calls]
        self.assertLess(names.index('uts_guard'), names.index('hostname'))
        self.assertLess(names.index('hostname'), names.index('domainname'))
        self.assertLess(names.index('domainname'), names.index('mount'))
        self.assertEqual(next(args for name, args, _ in calls if name == 'hostname'), (b'issue10-target',))
        self.assertEqual(next(args for name, args, _ in calls if name == 'domainname'), (b'issue10.invalid', 15))
        self.assertLess(names.index("mount"), names.index("chroot"))
        self.assertLess(names.index("chroot"), names.index("closefds"))
        self.assertLess(names.index("closefds"), names.index("exec"))
        import resource
        expected_limits = {
            resource.RLIMIT_CORE: (0, 0),
            resource.RLIMIT_FSIZE: (16777216, 16777216),
            resource.RLIMIT_NOFILE: (256, 256),
            resource.RLIMIT_AS: (536870912, 536870912),
            resource.RLIMIT_NPROC: (128, 128),
            resource.RLIMIT_CPU: (60, 60),
        }
        self.assertEqual({args[0]: args[1] for name, args, _ in calls if name == 'rlimit'}, expected_limits)
        for index, (name, _, _) in enumerate(calls):
            if name == 'rlimit':
                self.assertGreater(index, names.index('chroot'))
                self.assertLess(index, names.index('exec'))
        argv = calls[-1][1][1]; env = calls[-1][1][2]
        self.assertIn("--clear-groups", argv); self.assertIn("--bounding-set=-all", argv)
        self.assertIn("--no-new-privs", argv); self.assertIn("--ambient-caps=-all", argv)
        self.assertEqual(argv[-9:-5], ["/usr/bin/python3", "-I", "-B", "-c"])
        self.assertEqual(argv[-4:], ["YmFzaCAtcw==", "/bin/bash", "12", "13"])
        decoder = argv[-5]
        import base64
        with mock.patch('sys.argv', ['decoder', base64.b64encode(b'printf diagnostic').decode(), '/bin/bash', '12', '13']), \
             mock.patch('os.execve') as execute, mock.patch('os.dup2'), mock.patch('os.close'), \
             mock.patch('os.set_inheritable'), mock.patch('os.write') as write:
            exec(decoder, {})
        self.assertEqual(execute.call_args.args[1][-1], b'printf diagnostic')
        write.assert_called_once_with(12, b'READY\n')
        with mock.patch('sys.argv', ['decoder', base64.b64encode(b'bad\0command').decode(), '/bin/bash', '12', '13']), \
             mock.patch('os.execve') as execute, mock.patch('os.write') as write, \
             mock.patch('os._exit', side_effect=SystemExit(125)), self.assertRaises(SystemExit) as failure:
            exec(decoder, {})
        self.assertEqual(failure.exception.code, 125)
        write.assert_called_once_with(12, b'FAIL\n')
        execute.assert_not_called()
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"})
        mounts = [c[1][0] for c in calls if c[0] == "mount"]
        self.assertEqual(mounts[0][1:], ["--make-rprivate", "/"])
        self.assertTrue(any("remount,bind,ro,nosuid,nodev" in m for m in mounts))
        self.assertTrue(any(m[1:5] == ['-t', 'proc', '-o', 'nosuid,nodev,noexec,subset=pid'] for m in mounts))
        self.assertTrue(all(self.plan["root"] in " ".join(m) for m in mounts[1:]))

    def fake_unshare(self, body, lifecycle=b'READY\n'):
        import sys
        script = self.parent / "fake-unshare"
        shim = ("import json,os,sys; p=json.loads(sys.argv[1]); "
                + "os.write(p['status_fd']," + repr(lifecycle) + "); os.close(p['status_fd']); "
                + "os.execve('/bin/sh',['/bin/sh','-c'," + repr(body) + "],dict(os.environ))")
        script.write_text('#!/bin/sh\nfor last in "$@"; do :; done\nexec '
                          + shlex.quote(sys.executable) + ' -I -B -c ' + shlex.quote(shim) + ' "$last"\n')
        script.chmod(0o755)
        self.plan["tools"]["unshare"]["path"] = str(script)
        Path(self.plan["root"]).mkdir(exist_ok=True)

    def test_synthetic_transport_launch_preserves_stdin_and_capture(self):
        self.fake_unshare("exec /bin/cat")
        rc, output, counts = self.ns["launch"](self.plan, b"arbitrary bash body\n", 2, 1024, True)
        self.assertEqual(rc, 0); self.assertEqual(output["stdout"], b"arbitrary bash body\n")
        self.assertEqual(counts, {"stdout": 20, "stderr": 0})

    def test_entry_launch_forwards_stdin_bytes_without_outer_shell_parsing(self):
        self.fake_unshare('exec /bin/cat')
        payload = b'$(not-executed); exit 99\n\x00opaque bytes\n'
        with tempfile.TemporaryFile() as source:
            source.write(payload); source.seek(0)
            with mock.patch('sys.stdin', mock.Mock(buffer=source)):
                rc, output, _ = self.ns['launch'](self.plan, None, 2, 1024, True, 'YmFzaCAtcw==')
        self.assertEqual(rc, 0)
        self.assertEqual(output['stdout'], payload)

    def test_entry_rejects_malformed_argv_before_launch(self):
        for argv in (['entry'], ['entry', 'eA==', 'extra'], ['entry', 'x'], ['entry', ';id;'], ['entry', 'A' * 87388]):
            checker = mock.Mock()
            with mock.patch('sys.argv', argv), mock.patch.dict(self.ns, {'check_tools': checker}), self.assertRaises(ValueError):
                self.ns['entry_main'](self.plan)
            checker.assert_not_called()

    def test_mount_failure_never_reaches_chroot_or_setpriv(self):
        stage_plan = {**self.plan, 'command_b64': 'YmFzaCAtcw==', 'status_fd': 12, 'parent_uts': [7, 111]}
        with self.synthetic_uts([]), mock.patch('sys.argv', ['stage', json.dumps(stage_plan)]), \
             mock.patch('os.dup', return_value=13), mock.patch('os.open', return_value=14), \
             mock.patch('os.dup2'), mock.patch('os.close'), mock.patch('os.set_inheritable'), \
             mock.patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'mount')), \
             mock.patch('os.chroot') as chroot, mock.patch('os.execve') as execute, \
             mock.patch('os.write') as write, mock.patch('os._exit', side_effect=SystemExit(125)), self.assertRaises(SystemExit) as failure:
            exec(self.ns['_STAGE'], {})
        self.assertEqual(failure.exception.code, 125)
        write.assert_called_once_with(12, b'FAIL\n')
        chroot.assert_not_called(); execute.assert_not_called()

    def test_uts_guard_and_failed_setters_refuse_before_mount_or_target_exec(self):
        cases = [
            (None, {}, False, False),
            ([7, 111], {'current_inode': 111}, False, False),
            ([7, 111], {'hostname_error': PermissionError('synthetic setter denied')}, True, False),
            ([7, 111], {'domain_result': -1}, True, True),
        ]
        for parent, options, hostname_called, domain_called in cases:
            calls = []; stage_plan = {**self.plan, 'command_b64': 'YmFzaCAtcw==', 'status_fd': 12, 'parent_uts': parent}
            with self.subTest(parent=parent, options=options), self.synthetic_uts(calls, **options) as libc, \
                 mock.patch('sys.argv', ['stage', json.dumps(stage_plan)]), \
                 mock.patch('os.dup', return_value=13), mock.patch('os.open', return_value=14), \
                 mock.patch('os.dup2'), mock.patch('os.close'), mock.patch('os.set_inheritable'), \
                 mock.patch('subprocess.run') as mount, mock.patch('os.chroot') as chroot, \
                 mock.patch('os.execve') as execute, mock.patch('os.write') as write, \
                 mock.patch('os._exit', side_effect=SystemExit(125)), self.assertRaises(SystemExit) as failure:
                exec(self.ns['_STAGE'], {})
            self.assertEqual(failure.exception.code, 125)
            write.assert_called_once_with(12, b'FAIL\n')
            mount.assert_not_called(); chroot.assert_not_called(); execute.assert_not_called()
            names = [name for name, _, _ in calls]
            self.assertEqual('hostname' in names, hostname_called)
            self.assertEqual('domainname' in names, domain_called)
            if domain_called:
                import ctypes
                self.assertEqual(libc.setdomainname.argtypes, (ctypes.c_char_p, ctypes.c_size_t))
                self.assertEqual(libc.setdomainname.restype, ctypes.c_int)
                libc.setdomainname.assert_called_once_with(b'issue10.invalid', 15)

    def test_uts_qualification_checks_require_both_synthetic_identifiers(self):
        import ast, ctypes, socket
        from types import SimpleNamespace
        probe = ast.parse(self.ns['_PROBE'])
        domain_function = next(node for node in probe.body if isinstance(node, ast.FunctionDef)
                               and node.name == 'checked_domainname')
        checks = [node for node in probe.body if isinstance(node, ast.Assign)
                  and isinstance(node.targets[0], ast.Subscript)
                  and isinstance(node.targets[0].slice, ast.Constant)
                  and node.targets[0].slice.value in {'synthetic_hostname', 'synthetic_domainname'}]
        self.assertEqual(len(checks), 2)
        code = compile(ast.Module(body=[domain_function, *checks], type_ignores=[]), 'synthetic-UTS-checks', 'exec')
        for hostname, domain, expected in [
            ('issue10-target', b'issue10.invalid', (True, True)),
            ('synthetic-wrong-host', b'issue10.invalid', (False, True)),
            ('issue10-target', b'synthetic-wrong-domain', (True, False)),
        ]:
            ns = {'out': {}, 'socket': socket, 'os': os, 'ctypes': ctypes}
            libc = mock.Mock()
            def get_domain(buffer, size):
                self.assertEqual(size, 65); self.assertEqual(len(buffer), 65)
                buffer.value = domain
                return 0
            libc.getdomainname.side_effect = get_domain
            with mock.patch('socket.gethostname', return_value=hostname), \
                 mock.patch('os.uname', return_value=SimpleNamespace(nodename=hostname)), \
                 mock.patch('ctypes.CDLL', return_value=libc), mock.patch('pathlib.Path.read_text') as read:
                exec(code, ns)
            self.assertEqual((ns['out']['synthetic_hostname'], ns['out']['synthetic_domainname']), expected)
            self.assertEqual(libc.getdomainname.argtypes, (ctypes.POINTER(ctypes.c_char), ctypes.c_size_t))
            self.assertEqual(libc.getdomainname.restype, ctypes.c_int)
            read.assert_not_called()

    def test_domain_getter_error_is_checked_without_proc_sys_read(self):
        import ast, ctypes, errno
        function = next(node for node in ast.parse(self.ns['_PROBE']).body
                        if isinstance(node, ast.FunctionDef) and node.name == 'checked_domainname')
        ns = {'ctypes': ctypes}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'domain-getter', 'exec'), ns)
        libc = mock.Mock(); libc.getdomainname.return_value = -1
        with mock.patch('ctypes.CDLL', return_value=libc), mock.patch('ctypes.get_errno', return_value=errno.EIO), \
             mock.patch('pathlib.Path.read_text') as read, self.assertRaises(OSError) as failure:
            ns['checked_domainname']()
        self.assertEqual(failure.exception.errno, errno.EIO)
        self.assertEqual(libc.getdomainname.call_args.args[1], 65)
        read.assert_not_called()

    def test_global_proc_negative_checks_open_only_and_close_unexpected_access(self):
        import ast, errno
        tree = ast.parse(self.ns['_PROBE'])
        denied = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'denied')
        loop = next(node for node in tree.body if isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name) and node.target.id == 'name')
        code = compile(ast.Module(body=[denied, loop], type_ignores=[]), 'global-proc-checks', 'exec')
        expected_paths = ['/proc/' + name for name in ('meminfo', 'stat', 'diskstats', 'version', 'sys')]
        for accessible in (False, True):
            ns = {'out': {}, 'os': os, 'errno': errno}
            with mock.patch('os.open', return_value=99,
                            side_effect=None if accessible else FileNotFoundError(errno.ENOENT, 'hidden')) as opened, \
                 mock.patch('os.close') as closed, mock.patch('os.read') as read:
                exec(code, ns)
            self.assertEqual(opened.call_args_list, [mock.call(path, os.O_RDONLY, 0o600) for path in expected_paths])
            self.assertEqual(ns['out'], {'global_proc:' + path.rsplit('/', 1)[-1]: not accessible for path in expected_paths})
            self.assertEqual(closed.call_count, 5 if accessible else 0)
            read.assert_not_called()
        self.assertNotIn('/proc/sys/kernel/domainname', self.ns['_PROBE'])
        self.assertIn('/proc/self/status', self.ns['_PROBE'])

    def test_qualification_opens_global_proc_without_bytes_and_requires_new_checks(self):
        canaries = [{"name": name, "path": "/synthetic/" + name, "size": 10, "sha256": pin()}
                    for name in ('sibling', 'other_case')]
        global_names = ('meminfo', 'stat', 'diskstats', 'version', 'sys')
        keys = {'fixture:logs/example.log', 'outside:sibling', 'outside:other_case'}
        keys |= {'global_proc:' + name for name in global_names}
        keys |= {'identity', 'privileges', 'resource_limits', 'synthetic_hostname', 'synthetic_domainname',
                 'traversal', 'symlink', 'scratch', 'fixture_write_denied', 'fixture_create_denied',
                 'outside_write_denied', 'descendant', 'network'}
        process = mock.Mock(return_value=(0, {'stdout': json.dumps(dict.fromkeys(keys, True)).encode(), 'stderr': b''},
                                         {'stdout': 1000, 'stderr': 0}))
        listener = mock.MagicMock(); listener.__enter__.return_value = listener
        listener.getsockname.return_value = ('127.0.0.1', 1234)
        with mock.patch.dict(self.ns, {'digest': lambda *a: pin(), 'launch': process}), \
             mock.patch('socket.socket', return_value=listener), mock.patch('os.open', return_value=99) as opened, \
             mock.patch('os.close') as closed, mock.patch('os.read') as read:
            receipt = self.ns['qualify'](self.plan, canaries, 30, 1024)
        self.assertEqual(receipt['checks'], dict.fromkeys(keys, True))
        self.assertEqual(opened.call_args_list,
                         [mock.call('/proc/' + name, os.O_RDONLY | os.O_NOFOLLOW) for name in global_names])
        self.assertEqual(closed.call_args_list, [mock.call(99)] * 5)
        read.assert_not_called(); process.assert_called_once()
        with mock.patch.dict(self.ns, {'digest': lambda *a: pin(), 'launch': process}), \
             mock.patch('os.open', side_effect=FileNotFoundError('synthetic unavailable target')), self.assertRaises(FileNotFoundError):
            process.reset_mock()
            self.ns['qualify'](self.plan, canaries, 30, 1024)
        process.assert_not_called()

    def test_unsupported_proc_subset_mount_fails_without_retry_or_target_exec(self):
        stage_plan = {**self.plan, 'command_b64': 'YmFzaCAtcw==', 'status_fd': 12, 'parent_uts': [7, 111]}
        commands = []
        def mount(argv, **kwargs):
            commands.append(argv)
            if argv[1:3] == ['-t', 'proc']:
                raise subprocess.CalledProcessError(1, argv)
        with self.synthetic_uts([]), mock.patch('sys.argv', ['stage', json.dumps(stage_plan)]), \
             mock.patch('os.dup', return_value=13), mock.patch('os.open', return_value=14), \
             mock.patch('os.dup2'), mock.patch('os.close'), mock.patch('os.set_inheritable'), \
             mock.patch('subprocess.run', side_effect=mount), mock.patch('os.chroot') as chroot, \
             mock.patch('os.execve') as execute, mock.patch('os.write') as write, \
             mock.patch('os._exit', side_effect=SystemExit(125)), self.assertRaises(SystemExit) as failure:
            exec(self.ns['_STAGE'], {})
        self.assertEqual(failure.exception.code, 125)
        proc_mounts = [argv for argv in commands if argv[1:3] == ['-t', 'proc']]
        self.assertEqual(len(proc_mounts), 1)
        self.assertEqual(proc_mounts[0][4], 'nosuid,nodev,noexec,subset=pid')
        chroot.assert_not_called(); execute.assert_not_called()
        write.assert_called_once_with(12, b'FAIL\n')

    def test_synthetic_output_overflow_refuses(self):
        self.fake_unshare("exec /bin/cat")
        with self.assertRaisesRegex(ValueError, "overflow"):
            self.ns["launch"](self.plan, b"x" * 2048, 2, 1024, True)

    def test_synthetic_timeout_is_bounded(self):
        self.fake_unshare("sleep 10")
        start = time.monotonic()
        with self.assertRaisesRegex(ValueError, "timeout"):
            self.ns["launch"](self.plan, b"true\n", 1, 1024, True)
        self.assertLess(time.monotonic() - start, 5)

    def test_namespace_argv_uses_all_isolations_and_no_inherited_environment(self):
        self.fake_unshare("exec /bin/cat")
        original = subprocess.Popen
        calls = []
        def capture(*args, **kwargs):
            calls.append((args, kwargs)); return original(*args, **kwargs)
        with mock.patch("subprocess.Popen", capture):
            self.ns["launch"](self.plan, b"true", 2, 1024, True)
        argv = calls[0][0][0]; kwargs = calls[0][1]
        for flag in ("--mount", "--pid", "--net", "--ipc", "--uts", "--fork", "--kill-child=KILL"):
            self.assertIn(flag, argv)
        self.assertTrue(kwargs["close_fds"]); self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(set(kwargs["env"]), {"PATH", "LANG", "LC_ALL"})
        stage_plan = json.loads(argv[-1])
        self.assertNotIn("fixture_files", stage_plan)
        self.assertNotIn("runtime_files", stage_plan)

    def test_cleanup_requires_no_mount_and_never_follows_scratch_symlinks(self):
        self.prepare(); root = Path(self.plan["root"])
        outside = self.parent / "outside"; outside.mkdir(); (outside / "keep").write_text("synthetic")
        (root / "rootfs/scratch/escape").symlink_to(outside, target_is_directory=True)
        with mock.patch("pathlib.Path.read_text", return_value="1 0 0:1 / " + str(root) + " rw - tmpfs tmpfs rw\n"):
            with self.assertRaisesRegex(ValueError, "mounted"):
                self.ns["cleanup"](self.plan, root)
        self.assertTrue(root.exists())
        with mock.patch("pathlib.Path.read_text", return_value=""):
            result = self.ns["cleanup"](self.plan, root)
        self.assertEqual(result["status"], "removed")
        self.assertFalse(root.exists()); self.assertEqual((outside / "keep").read_text(), "synthetic")

    def test_parent_death_signal_is_required_and_race_refuses(self):
        libc = mock.Mock(); libc.prctl.return_value = 0
        with mock.patch('ctypes.CDLL', return_value=libc), mock.patch('os.getppid', return_value=123), \
             mock.patch('os._exit') as exit_process:
            self.ns['parent_death'](123)
        exit_process.assert_not_called()
        libc.prctl.assert_called_once_with(1, int(self.ns['signal'].SIGKILL), 0, 0, 0)
        for rc, parent in [(1, 123), (0, 456)]:
            libc.prctl.return_value = rc
            with mock.patch('ctypes.CDLL', return_value=libc), mock.patch('os.getppid', return_value=parent), \
                 mock.patch('os._exit') as exit_process:
                self.ns['parent_death'](123)
            exit_process.assert_called_once_with(125)

    def owned_stat_seam(self):
        original = Path.stat
        root = Path(self.plan['root'])
        def synthetic_root_stat(path, *args, **kwargs):
            result = original(path, *args, **kwargs)
            if path == self.parent or path.is_relative_to(root):
                values = list(result); values[4] = 0
                if path == root / 'rootfs/scratch':
                    values[4] = self.plan['uid']; values[5] = self.plan['gid']
                return os.stat_result(values)
            return result
        return mock.patch('pathlib.Path.stat', synthetic_root_stat)

    def test_inspection_runs_verified_current_fixture_without_scratch_reads(self):
        self.prepare(); root = Path(self.plan['root']); image = root / 'rootfs'
        (image / 'scratch/.qualification-link').symlink_to('/unreadable-synthetic-target')
        output = io.StringIO()
        with self.owned_stat_seam(), mock.patch.dict(self.ns, {'check_tools': lambda p: None}), \
             mock.patch('sys.stdout', output):
            self.ns['main']({'plan': self.plan, 'action': 'inspect'})
        result = json.loads(output.getvalue())
        self.assertEqual(result['fixture_integrity'], {'logs/example.log': True})
        self.assertTrue(result['fixture_inventory_exact'])
        self.assertEqual(result['plan_sha256'], self.ns['identity'](self.plan)['plan_sha256'])
        self.assertTrue((image / 'scratch/.qualification-link').is_symlink())

    def test_inspection_extra_files_and_symlinks_are_not_followed_or_hashed(self):
        self.prepare(); image = Path(self.plan['root']) / 'rootfs'; fixture = image / 'fixture'
        outside = self.parent / 'outside-unknown'; outside.mkdir(); (outside / 'never-read').write_text('synthetic')
        (fixture / 'extra-file').write_text('synthetic unexpected bytes')
        (fixture / 'extra-link').symlink_to(outside / 'never-read')
        (fixture / 'extra-dir-link').symlink_to(outside, target_is_directory=True)
        original = self.ns['digest']; paths = []
        def guarded_digest(path, size=None):
            paths.append(Path(path))
            self.assertEqual(Path(path), fixture / 'logs/example.log')
            return original(path, size)
        with mock.patch.dict(self.ns, {'digest': guarded_digest}):
            result = self.ns['inspect_image'](self.plan, image)
        self.assertFalse(result['fixture_inventory_exact'])
        self.assertEqual(result['fixture_integrity'], {'logs/example.log': True})
        self.assertEqual(paths, [fixture / 'logs/example.log'])

    def test_inspection_changed_fixture_pin_refuses(self):
        self.prepare(); image = Path(self.plan['root']) / 'rootfs'
        file = image / 'fixture/logs/example.log'; file.chmod(0o600); file.write_bytes(b'changed!!\n'); file.chmod(0o444)
        with self.assertRaises(ValueError): self.ns['inspect_image'](self.plan, image)

    def test_inspection_refuses_active_owner_lock(self):
        self.prepare(); verify = mock.Mock()
        with self.owned_stat_seam(), mock.patch.dict(self.ns, {'check_tools': lambda p: None, 'verify_image': verify}):
            with self.ns['owned'](self.plan):
                with self.assertRaises(BlockingIOError):
                    self.ns['main']({'plan': self.plan, 'action': 'inspect'})
        verify.assert_not_called()

    def execute_generated_entry(self, payload, argument='YmFzaCAtcw==', check=None):
        # Inject synthetic privilege/verification seams immediately before the
        # UNCHANGED generated try/except. launch and its actual subprocess remain real.
        import ast, base64, contextlib
        script = base64.b64decode(boundary.entry_files(self.plan)['entry.py']['content'])
        tree = ast.parse(script)
        self.assertIsInstance(tree.body[-1], ast.Try)
        tree.body[-1:-1] = ast.parse('check_tools=_test_check\nowned=_test_owned\nverify_image=_test_verify\n').body
        ns = {'_test_check': check or (lambda p: None),
              '_test_owned': lambda p: contextlib.nullcontext(Path(p['root'])),
              '_test_verify': lambda p, root: root / 'rootfs'}
        stdout = io.BytesIO(); stderr = io.BytesIO()
        out_stream = io.TextIOWrapper(stdout, write_through=True)
        err_stream = io.TextIOWrapper(stderr, write_through=True)
        try:
            with tempfile.TemporaryFile() as source:
                source.write(payload); source.seek(0)
                with mock.patch('sys.argv', ['entry.py', argument]), mock.patch('sys.stdin', mock.Mock(buffer=source)), \
                     mock.patch('sys.stdout', out_stream), mock.patch('sys.stderr', err_stream), self.assertRaises(SystemExit) as result:
                    exec(compile(tree, 'generated-entry', 'exec'), ns)
            return result.exception.code, stdout.getvalue(), stderr.getvalue()
        finally:
            out_stream.close(); err_stream.close()

    def test_generated_entry_invalid_argument_and_parent_errors_are_fixed(self):
        rc, out, err = self.execute_generated_entry(b'', argument='not valid')
        self.assertEqual((rc, out, err), (125, b'', b'issue10 boundary failure\n'))
        def bad_pin(plan): raise ValueError('private root: ' + plan['root'])
        rc, out, err = self.execute_generated_entry(b'', check=bad_pin)
        self.assertEqual((rc, out, err), (125, b'', b'issue10 boundary failure\n'))
        self.assertNotIn(self.plan['root'].encode(), err)

    def test_actual_generated_entry_subprocess_invalid_arg_has_no_traceback(self):
        import base64, sys
        script = self.parent / 'generated-entry.py'
        script.write_bytes(base64.b64decode(boundary.entry_files(self.plan)['entry.py']['content']))
        result = subprocess.run([sys.executable, '-I', '-B', str(script), 'invalid argument'],
                                capture_output=True, timeout=3)
        self.assertEqual((result.returncode, result.stdout, result.stderr),
                         (125, b'', b'issue10 boundary failure\n'))
        self.assertNotIn(str(self.parent).encode(), result.stderr)

    def test_decoder_failed_bash_exec_emits_private_ready_then_fail_only(self):
        import ast, base64
        tree = ast.parse(self.ns['_STAGE'])
        guarded = next(node for node in tree.body if isinstance(node, ast.Try))
        setup = ast.parse(ast.literal_eval(guarded.body[0].value.args[0]))
        decoder = ast.literal_eval(next(node.value for node in setup.body
                                       if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                                       and node.targets[0].id == 'decoder'))
        output = io.StringIO()
        with mock.patch('sys.argv', ['decoder', base64.b64encode(b'true').decode(), '/bin/bash', '12', '13']), \
             mock.patch('os.dup2'), mock.patch('os.close'), mock.patch('os.set_inheritable'), \
             mock.patch('os.write') as write, mock.patch('os.execve', side_effect=OSError('private host path')), \
             mock.patch('os._exit', side_effect=SystemExit(125)), mock.patch('sys.stderr', output), \
             self.assertRaises(SystemExit) as failure:
            exec(decoder, {})
        self.assertEqual(failure.exception.code, 125)
        self.assertEqual(write.call_args_list, [mock.call(12, b'READY\n'), mock.call(12, b'FAIL\n')])
        self.assertEqual(output.getvalue(), '')

    def test_generated_entry_preexec_failures_do_not_forward_raw_output(self):
        for lifecycle in (b'', b'FAIL\n', b'READY\nFAIL\n'):
            self.fake_unshare('printf private-host-path >&2; printf privileged-output; exit 1', lifecycle)
            rc, out, err = self.execute_generated_entry(b'')
            self.assertEqual((rc, out, err), (125, b'', b'issue10 boundary failure\n'))

    def test_generated_entry_real_echo_stderr_and_exit125_are_unchanged(self):
        self.fake_unshare('exec /bin/bash --noprofile --norc -s')
        rc, out, err = self.execute_generated_entry(
            b"echo ordinary-evidence; printf 'issue10 boundary failure\\nordinary stderr\\n' >&2; exit 125\n")
        self.assertEqual(rc, 125)
        self.assertEqual(out, b'ordinary-evidence\n')
        self.assertEqual(err, b'issue10 boundary failure\nordinary stderr\n')

    def test_unknown_action_cannot_invoke_arbitrary_operation(self):
        with mock.patch.dict(self.ns, {"check_tools": lambda p: None, "owned": mock.MagicMock()}):
            with self.assertRaises(Exception):
                self.ns["main"]({"plan": self.plan, "action": "shell"})


if __name__ == "__main__":
    unittest.main()
