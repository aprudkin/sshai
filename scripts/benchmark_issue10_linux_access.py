#!/usr/bin/env python3
"""Concrete controller-side Linux/SSH access adapter for Issue 10.

Only the outside controller can invoke this module. Model tools receive a FIFO
shim, never this transport, host configuration or credentials. Named canaries
qualify bounded accesses; they do not establish exhaustive OS observation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time

import benchmark_issue10_linux_boundary as boundary
import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_sandbox as sandbox
from benchmark_issue10_fifo_broker import FifoBroker, shim_source

legacy = pilot.legacy
ACCESS_SCHEMA = 'sshai-benchmark/issue10-linux-access-1'
MAX_OUTPUT = 8 * 1024 * 1024


def boundary_plan(manifest: dict, slot: dict, base: Path) -> dict:
    remote = manifest['remote_plan']
    nonce = hashlib.sha256((manifest['digest'] + '\n' + str(base) + '\n' + str(slot['slot'])).encode()).hexdigest()[:32]
    files = manifest['slot_material'][str(slot['slot'])]['fixture_files']
    return {**{key: remote[key] for key in ('uid', 'gid', 'bash', 'setpriv', 'python', 'tools', 'runtime_files')},
            'schema': boundary.SCHEMA, 'nonce': nonce, 'root': remote['parent'] + '/issue10-linux-' + nonce,
            'fixture_source': remote['parent'] + '/fixtures/' + slot['case_id'],
            'fixture_files': [{'path': name, 'size': pin['bytes'], 'sha256': pin['sha256']}
                              for name, pin in sorted(files.items())]}


def plan_digest(plan: dict) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def inspection_passed(value: dict, plan: dict) -> bool:
    expected = {row['path']: True for row in plan['fixture_files']}
    return (isinstance(value, dict) and value.get('schema') == plan['schema']
            and value.get('plan_sha256') == plan_digest(plan)
            and value.get('fixture_integrity') == expected and value.get('fixture_inventory_exact') is True)


def ssh_argv(host: str, control_dir: str, command: str) -> list[str]:
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', host):
        raise ValueError('require a fixed explicit SSH alias')
    if (not isinstance(control_dir, str) or not control_dir.startswith('/')
            or '\x00' in control_dir or not isinstance(command, str) or '\x00' in command):
        raise ValueError('invalid fixed SSH transport values')
    if len((control_dir + '/' + 'x' * 40).encode()) >= 104:
        raise ValueError('control socket path exceeds the macOS OpenSSH bound')
    return ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'LogLevel=ERROR',
            '-o', 'ControlMaster=auto', '-o', 'ControlPath=' + control_dir + '/%C',
            '-o', 'ControlPersist=15m', host, command]


def bounded_process(argv: list[str], stdin: bytes, env: dict, cwd: Path,
                    seconds: float, output_limit: int) -> dict:
    """Bound streams/deadline while retaining the callback's inherited process group."""
    if (not isinstance(stdin, bytes) or type(seconds) not in (int, float) or not 0 < seconds <= 600
            or type(output_limit) is not int or not 1 <= output_limit <= MAX_OUTPUT):
        raise ValueError('invalid process bounds')
    result = {'stdout': bytearray(), 'stderr': bytearray(), 'exit_code': None,
              'timed_out': False, 'overflow': False, 'start_error': False}
    proc = None
    started = time.monotonic()
    try:
        with tempfile.TemporaryFile() as source, selectors.DefaultSelector() as selector:
            source.write(stdin)
            source.seek(0)
            proc = subprocess.Popen(argv, stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=env, cwd=cwd, close_fds=True, start_new_session=False)
            for name, stream in (('stdout', proc.stdout), ('stderr', proc.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            deadline = started + seconds
            while selector.get_map() or proc.poll() is None:
                left = deadline - time.monotonic()
                if left <= 0:
                    result['timed_out'] = True
                    break
                if not selector.get_map():
                    try:
                        proc.wait(timeout=left)
                    except subprocess.TimeoutExpired:
                        result['timed_out'] = True
                    break
                for key, _ in selector.select(min(left, 0.1)):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    room = output_limit - len(result['stdout']) - len(result['stderr'])
                    result[key.data].extend(data[:max(room, 0)])
                    if len(data) > room:
                        result['overflow'] = True
                        break
                if result['overflow']:
                    break
    except (OSError, subprocess.SubprocessError):
        result['start_error'] = True
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            result['exit_code'] = proc.returncode
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
    result['duration_seconds'] = time.monotonic() - started
    result['stdout'], result['stderr'] = bytes(result['stdout']), bytes(result['stderr'])
    return result


class LinuxAccess:
    def __init__(self, manifest: dict, root: Path):
        self.root = legacy._physical(Path(root))
        if manifest.get('digest') != pilot._digest_object(manifest):
            raise ValueError('access requires the unchanged phase manifest')
        self.manifest = manifest
        self.remote = manifest['remote_plan']

    def session(self, slot: dict, base: Path, environment: dict):
        return _Session(self, slot, base, environment)


class _Session:
    def __init__(self, owner: LinuxAccess, slot: dict, base: Path, environment: dict):
        self.owner, self.slot = owner, slot
        self.base = legacy._physical(Path(base))
        if not self.base.is_relative_to(owner.root):
            raise ValueError('session control must stay in its private phase')
        self.scratch, self.evidence = self.base / 'scratch', self.base / 'evidence'
        self.manifest, self.remote = owner.manifest, owner.remote
        self.plan = boundary.validate_plan(boundary_plan(self.manifest, slot, self.base))
        self.environment = {**environment, 'PATH': str(self.scratch / 'bin') + ':' + environment['PATH']}
        self.config_overrides = []
        self.broker = None
        self.local_receipt = self.remote_receipt = self.access_receipt = None
        self.no_model_transaction_ids = []
        self.finalized = None
        self.prepared = False
        # The pinned Darwin minimal-runtime policy permits reads under
        # /private/tmp even with a named deny. Keep controller sockets outside
        # that exception and qualify their actual location before model launch.
        self.transport_root = Path(tempfile.mkdtemp(prefix='i10-', dir='/Users/Shared'))
        self.control_dir = self.transport_root / 'cm'
        self.control_dir.mkdir(mode=0o700)
        self.control_evidence = self.evidence / 'remote-control'
        legacy._new_dir(self.control_evidence)
        self.transport_environment = {key: os.environ[key] for key in ('HOME', 'PATH', 'SSH_AUTH_SOCK') if key in os.environ}
        self.transport_environment.update(LANG='C', LC_ALL='C')
        self._closed = False

    def _remote(self, command: str, body: bytes, *, seconds: float = 60) -> dict:
        argv = ssh_argv(self.remote['host_alias'], str(self.control_dir), command)
        result = bounded_process(argv, body, self.transport_environment, self.owner.root, seconds, MAX_OUTPUT)
        nonce = os.urandom(16).hex()
        # Actual transport evidence remains outside model-readable scratch.
        for suffix, data in (('stdout', result['stdout']), ('stderr', result['stderr'])):
            legacy._write_new(self.control_evidence / (nonce + '.' + suffix), data)
        receipt = {key: value for key, value in result.items() if key not in ('stdout', 'stderr')}
        receipt.update(argv=argv, stdin_bytes=len(body), stdin_sha256=pilot._sha(body),
                       stdout_bytes=len(result['stdout']), stdout_sha256=pilot._sha(result['stdout']),
                       stderr_bytes=len(result['stderr']), stderr_sha256=pilot._sha(result['stderr']))
        legacy._write_new(self.control_evidence / (nonce + '.json'), pilot._pretty(receipt))
        return result

    def _control(self, body: str, *, seconds: int = 60) -> dict:
        result = self._remote('bash -s', body.encode(), seconds=seconds)
        if (result['exit_code'] != 0 or result['timed_out'] or result['overflow'] or result['start_error']
                or result['stderr']):
            raise ValueError('remote boundary control failed; private transport evidence retained')
        value = json.loads(result['stdout'])
        if not isinstance(value, dict) or value.get('plan_sha256') != plan_digest(self.plan):
            raise ValueError('remote control receipt does not match frozen boundary')
        return value

    def _diagnostic(self, command: bytes, stdin: bytes):
        result = self._remote(boundary.entry_command(self.plan, command), stdin, seconds=29)
        if result['timed_out'] or result['start_error'] or result['exit_code'] == 255:
            return b'', b'issue10 SSH transport failed or timed out\n', 255
        if result['overflow']:
            return b'', b'issue10 transport output limit exceeded\n', 125
        if self.plan['root'].encode() in result['stderr']:
            return b'', b'issue10 boundary failure\n', 125
        rc = result['exit_code']
        return result['stdout'], result['stderr'], rc if isinstance(rc, int) and 0 <= rc <= 255 else 125

    def __enter__(self):
        try:
            state = self._control(boundary.preparation_body(self.plan), seconds=120)
            if state.get('status') != 'prepared' or state.get('entry_hashes') != {
                    name: row['sha256'] for name, row in boundary.entry_files(self.plan).items()}:
                raise ValueError('prepared remote entry differs from frozen source')
            self.prepared = True
            legacy._mkdir_private(self.scratch / 'bin')
            audit_dir = self.evidence / 'broker-audit'
            self.broker = FifoBroker(self.scratch, self._diagnostic, deadline_seconds=30,
                                    audit_dir=audit_dir, max_command_bytes=64 * 1024,
                                    max_output_bytes=4 * 1024 * 1024, max_calls=128, slot_timeout=720)
            self.broker.start()
            python = Path(sys.executable).resolve(strict=True)
            legacy._write_new(self.scratch / 'bin' / 'ssh', shim_source(self.broker.endpoint, python).encode(), mode=0o500)
            auth = Path(self.manifest['auth']['path'])
            protected = [self.owner.root, Path.home(), auth, auth.parent.parent,
                         audit_dir, self.transport_root, Path('/Users/Shared')]
            executable = self.manifest['sshai']
            self.local_receipt = sandbox.qualify(
                Path(self.manifest['codex']['path']), self.scratch, protected,
                environment=self.environment,
                read_executables=[{'path': executable['path'], 'sha256': executable['sha256'],
                                   'help_sha256': self.manifest['config']['sshai']['help_sha256']}])
            self.config_overrides = list(self.local_receipt['config_overrides'])
            return self
        except BaseException:
            # Failed preparations remain private/retained. Do not infer terminality
            # or delete a remote root because entering a session failed.
            self.close(cleanup=False)
            raise

    def _smoke(self) -> dict:
        python = Path(sys.executable).resolve(strict=True)
        script = r'''
import json,pathlib,subprocess,sys
ssh,sshai,alias=sys.argv[1:]
body_a=b"printf 'issue10-shim-smoke\\n'; printf 'issue10-shim-error\\n' >&2; exit 7\n"
body_b=b"printf 'issue10-sshai-smoke\\n'; printf 'issue10-sshai-error\\n' >&2; exit 7\n"
a=subprocess.run([ssh,alias,'bash -s'],input=body_a,capture_output=True,timeout=35)
b=subprocess.run([sshai,'run','--timeout','25','--result-format=json','--body-file','-',alias],input=body_b,capture_output=True,timeout=35)
try:
 x=json.loads(b.stdout); r=x['runs'][0]; saved=pathlib.Path(r['artifact_path']).read_bytes()
 out,err=b'issue10-sshai-smoke\n',b'issue10-sshai-error\n'
 good=(b.returncode==7 and x['summary']['failed']==1 and r['exit']==7 and not r.get('transport_error') and not r.get('truncated') and saved in (out+err,err+out))
except (ValueError,KeyError,IndexError,TypeError,OSError): good=False
print(json.dumps({'shim_stdout':a.stdout==b'issue10-shim-smoke\n','shim_stderr':a.stderr==b'issue10-shim-error\n','shim_exit':a.returncode==7,'sshai_body_and_saved_artifact':good},sort_keys=True))
'''
        argv = [self.manifest['codex']['path'], 'sandbox', '-P', sandbox.PROFILE,
                '--include-managed-config', '-C', str(self.scratch)]
        for override in self.config_overrides:
            argv += ['-c', override]
        argv += ['--', str(python), '-I', '-c', script, str(self.scratch / 'bin/ssh'),
                 self.manifest['sshai']['path'], 'issue10-target']
        result = bounded_process(argv, b'', self.environment, self.scratch, 80, 1024 * 1024)
        self.no_model_transaction_ids = list(self.broker.completed_transaction_ids(timeout=5))
        legacy._write_new(self.evidence / 'client-smoke-stdout.json', result['stdout'])
        legacy._write_new(self.evidence / 'client-smoke-stderr.txt', result['stderr'])
        if result['exit_code'] or result['timed_out'] or result['overflow'] or result['start_error']:
            raise ValueError('same-profile shim/sshai smoke lifecycle failed')
        checks = json.loads(result['stdout'])
        if set(checks) != {'shim_stdout', 'shim_stderr', 'shim_exit', 'sshai_body_and_saved_artifact'} or not all(v is True for v in checks.values()):
            raise ValueError('same-profile shim/sshai smoke did not pass')
        # Remove only these disposable canary artifacts, before the model starts.
        # Their command/output receipts remain protected in control/broker evidence.
        state = self.scratch / 'sshai-root'
        if state.is_symlink() or not state.is_dir():
            raise ValueError('smoke state was replaced')
        shutil.rmtree(state)
        state.mkdir(mode=0o700)
        return checks

    def qualify(self) -> dict:
        if self.access_receipt is not None:
            raise ValueError('session access qualification is one-shot')
        self.remote_receipt = self._control(boundary.qualification_body(self.plan, self.remote['canaries']), seconds=45)
        checks = self.remote_receipt.get('checks')
        if (self.remote_receipt.get('schema') != 'sshai-issue10-linux-access-1'
                or not isinstance(checks, dict) or not checks or not all(v is True for v in checks.values())):
            raise ValueError('remote named-canary evidence is incomplete')
        smoke = self._smoke()
        combined = {**{'local:' + k: v for k, v in self.local_receipt['checks'].items()},
                    **{'remote:' + k: v for k, v in checks.items()},
                    **{'local:broker:' + k: v for k, v in smoke.items()}}
        receipt = {'schema': ACCESS_SCHEMA, 'status': 'passed', 'manifest_digest': self.manifest['digest'],
                   'slot': self.slot, 'base': str(self.base), 'effective_config_overrides': self.config_overrides,
                   'environment_sha256': pilot._sha(pilot._encoded(self.environment)),
                   'remote_fixture_pins': self.manifest['slot_material'][str(self.slot['slot'])]['fixture_files'],
                   'runtime_pins': self.remote['runtime_files'], 'checks': combined,
                   'boundary_plan_sha256': plan_digest(self.plan),
                   'local_receipt': self.local_receipt, 'remote_receipt': self.remote_receipt,
                   'no_model_transaction_ids': self.no_model_transaction_ids,
                   'limitations': ['Named accesses, not exhaustive confinement, routing, usage or finality attestation.',
                                   'Private FIFO audit includes no-model smoke requests separately from model activity.']}
        receipt['digest'] = pilot._digest_object(receipt)
        self.access_receipt = receipt
        return receipt

    def finalize(self) -> dict:
        if self.finalized is not None:
            return self.finalized
        lifecycle = self.broker.stop() if self.broker is not None else {'status': 'not-started'}
        receipts = list(self.broker.receipts) if self.broker is not None else []
        inspection, cleanup = {}, {'status': 'retained-unknown'}
        if self.prepared:
            try:
                inspection = self._control(boundary.inspection_body(self.plan), seconds=60)
                if not inspection_passed(inspection, self.plan):
                    raise ValueError('fixture integrity did not pass')
                cleanup = self._control(boundary.cleanup_body(self.plan), seconds=60)
            except (ValueError, OSError, json.JSONDecodeError):
                # The owner lock refuses active/unknown work. No unsafe cleanup retry.
                cleanup = {'status': 'retained-unknown'}
        good = inspection_passed(inspection, self.plan)
        evidence = {'schema': 'sshai-issue10-linux-broker-evidence-1', 'lifecycle': lifecycle,
                    'receipts': receipts, 'inspection': inspection, 'remote_cleanup': cleanup,
                    'no_model_transaction_ids': self.no_model_transaction_ids,
                    'model_transaction_ids': [row['transaction_id'] for row in receipts
                                              if row['transaction_id'] not in self.no_model_transaction_ids]}
        path = self.evidence / 'broker-evidence.json'
        data = pilot._pretty(evidence)
        if len(data) > MAX_OUTPUT:
            raise ValueError('broker evidence exceeds declared capacity')
        legacy._write_new(path, data)
        expected = self.manifest['slot_material'][str(self.slot['slot'])]['fixture_files']
        self.finalized = {'fixture_integrity': {name: good for name in expected},
                          'fixture_inventory_exact': good,
                          'broker_evidence': {'path': str(path), 'sha256': pilot._sha(data)},
                          'remote_cleanup': cleanup,
                          'access_status': 'passed' if (good and self.access_receipt is not None
                                                        and self.access_receipt.get('status') == 'passed'
                                                        and cleanup.get('status') == 'removed') else 'unknown'}
        return self.finalized

    def close(self, *, cleanup: bool = True):
        if self._closed:
            return
        try:
            if cleanup and self.prepared and self.finalized is None:
                self.finalize()
            elif self.broker is not None:
                self.broker.stop()
        finally:
            self._closed = True
            # Terminate only the master created in this session's private socket
            # directory; never reset a configured/shared SSH master.
            argv = ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o',
                    'ControlPath=' + str(self.control_dir) + '/%C', '-O', 'exit', self.remote['host_alias']]
            result = bounded_process(argv, b'', self.transport_environment, self.owner.root, 5, 4096)
            closed = (result['exit_code'] == 0 and not result['timed_out'] and not result['start_error'])
            empty = self.control_dir.is_dir() and not any(self.control_dir.iterdir())
            legacy._write_new(self.evidence / 'transport-close.json', pilot._pretty({
                'status': 'closed' if closed or empty else 'retained-unknown',
                'exit_code': result['exit_code'], 'timed_out': result['timed_out'],
                'stdout_sha256': pilot._sha(result['stdout']), 'stderr_sha256': pilot._sha(result['stderr'])}))
            if closed or empty:
                shutil.rmtree(self.transport_root)

    def __exit__(self, *_):
        self.close()
