#!/usr/bin/env python3
"""One-context private assessor launcher; not a study coordinator or grader.

prepare --root NEW --packet BRIDGE_PACKET --config CONFIG --catalog CATALOG
        --native NATIVE --auth-source AUTH
preflight --root ROOT                 (no model; one-shot named access canaries)
run --root ROOT --approval APPROVAL --allow-model-run

Config is exactly {schema, native, model, catalog_sha256, environment,
config_overrides, protected_paths, budget, instructions_file_sha256, rubric_sha256}.
Native: {sha256, version, source_revision}; model: {id, version, reasoning_effort}.
Use pinned Codex 0.151.0 and gpt-5.6-sol/high. Overrides must equal CONTROLS below;
shell/unified exec are disabled. Environment supplies only PATH, LANG, LC_ALL,
SHELL, USER, LOGNAME; HOME/CODEX_HOME/XDG/TMPDIR are isolated, not inherited.
Protected paths are explicit physical existing private roots/files; additionally
this root, the original bridge packet and auth source are denied to sandboxed reads.
Budget: {context_id: opaque32hex, consumed_before: integer0..23, ceiling:24,
ledger_sha256: SHA256, provenance: nonempty caller note}. This is caller-declared
serial-budget provenance, not a trusted global attestation or a permission grant.
The caller owns serial allocation across all preparations; do not copy roots or
prepare replacement contexts after failure. Empty eligible packets refuse.

Approval is caller-recorded, exactly {schema, manifest_digest, context_id,
decision:"advance-this-context-once", authorization_note: nonempty actual authority
provenance, ledger_sha256}. No command fabricates approval. Both approval and the
explicit allow flag are required. A reservation consumes this one-shot context even
on failure. No retries, overwrite, resume, model substitution, auth repair or paid
fallback. Source/config/native/catalog/input drift refuses before reservation.

Only packet/input.json is assessor-readable, alongside reviewed minimal/runtime
reads and scratch. Owner inventory, control receipts, approval, source packet and
caller-protected diagnostics remain outside that access. Native parent networking
for subscription auth/inference is not sandboxed tool networking. Disabled controls
and recorded zero calls are not proof of complete OS observation or absence of
managed/cloud configuration. Any recorded tool activity/unsupported capture blocks
assessment use. The runner never qualifies finality, validates grades or imports
results: main separately qualifies the retained final JSON and uses the bridge.

Input cap: collector 1 MiB; assessment-response eligibility: bridge 64 KiB.
New manifest-2 roots explicitly freeze native_capture_capacity as 8 MiB per native
rollout and 4 MiB per JSONL record, including both complete persisted user-prompt
copies. Manifest-1 roots refuse, never receive an implicit capacity upgrade.
Global collector/capture defaults and CLI process-stream bounds stay unchanged.
Raw answers retain their existing bounded cap, including oversized answers as
invalid evidence, never silently truncated into valid JSON.
All evidence is private, append-only (0700 directories, 0600 regular files), retained
without automatic deletion. stdout is only a bounded summary; use --help for flags.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import platform
import socket
import stat
import sys
import tempfile
from typing import Any

import benchmark_issue10 as legacy
import benchmark_issue10_local_assessment as bridge
import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_sandbox as sandbox
import benchmark_issue10_v3_capture as capture
import benchmark_issue10_v3_collector as collector
import benchmark_issue10_v3_review as review

CONFIG_SCHEMA = 'sshai-benchmark/issue10-assessor-config-1'
MANIFEST_SCHEMA = 'sshai-benchmark/issue10-assessor-manifest-2'
APPROVAL_SCHEMA = 'sshai-benchmark/issue10-assessor-approval-1'
NATIVE_CAPTURE_BYTES = 8 * 1024 * 1024
NATIVE_RECORD_BYTES = 4 * 1024 * 1024
CONTROLS = [item.replace('features.shell_tool=true', 'features.shell_tool=false')
            .replace('features.unified_exec=true', 'features.unified_exec=false')
            for item in pilot.REQUIRED_CODEX_OVERRIDES]
REPO = Path(__file__).resolve().parent.parent
SOURCES = [Path(module.__file__).resolve() for module in
           (legacy, bridge, pilot, sandbox, capture, collector, review)] + [Path(__file__).resolve(), pilot.AMENDMENT]
_ACCESS_QUALIFIER = None  # Synthetic-test seams only; no CLI bypass.


def write(path: Path, body: bytes) -> None:
    bridge.bounded(body, 'private runner output')
    legacy._write_new(path, body)


def obj(path: Path) -> dict:
    value = bridge.parse(bridge.read_file(path))
    if not isinstance(value, dict):
        raise ValueError('JSON object required')
    return value


def sha_object(value: dict) -> str:
    return bridge.digest(bridge.encode({key: val for key, val in value.items() if key != 'digest'}))


def _native_capacity(manifest: dict) -> dict:
    capacity = bridge.exact(manifest.get('native_capture_capacity'),
                            {'capture_limit', 'line_limit'}, 'native capture capacity')
    if (type(capacity['capture_limit']) is not int or capacity['capture_limit'] != NATIVE_CAPTURE_BYTES
            or type(capacity['line_limit']) is not int or capacity['line_limit'] != NATIVE_RECORD_BYTES):
        raise ValueError('native capture capacity must freeze 8 MiB files / 4 MiB records')
    return capacity


def auth_identity(path: Path) -> dict:
    """Metadata only: never read, hash, copy or report credential contents."""
    path = bridge.physical_path(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError('auth source must be a physical private regular file')
    return {'path': str(path), 'device': info.st_dev, 'inode': info.st_ino,
            'mode': 0o600, 'contents': 'unread and unpinned; native auth may refresh this source'}


def validate_config(config: dict) -> None:
    bridge.exact(config, {'schema', 'native', 'model', 'catalog_sha256', 'environment',
                        'config_overrides', 'protected_paths', 'budget',
                        'instructions_file_sha256', 'rubric_sha256'}, 'assessor config')
    if config['schema'] != CONFIG_SCHEMA or config['config_overrides'] != CONTROLS:
        raise ValueError('unsupported config or no-tools controls')
    native = bridge.exact(config['native'], {'sha256', 'version', 'source_revision'}, 'native')
    if (native['version'] != sandbox.CODEX_VERSION
            or native['source_revision'] != pilot.SOURCE_CONTRACT['revision']):
        raise ValueError('native source/version not pinned adapter contract')
    model = bridge.exact(config['model'], {'id', 'version', 'reasoning_effort'}, 'model')
    if model['id'] != 'gpt-5.6-sol' or model['reasoning_effort'] != 'high':
        raise ValueError('fixed gpt-5.6-sol/high required')
    bridge.text(model['version'], 'model version', maximum=256)
    for value in (native['sha256'], config['catalog_sha256'], config['instructions_file_sha256'], config['rubric_sha256']):
        pilot._hex_digest(value, 'config pin')
    env = config['environment']
    bridge.exact(env, {'PATH', 'LANG', 'LC_ALL', 'SHELL', 'USER', 'LOGNAME'}, 'environment')
    if any(not isinstance(v, str) or not v or '\x00' in v for v in env.values()):
        raise ValueError('explicit non-secret environment required')
    budget = bridge.exact(config['budget'], {'context_id', 'consumed_before', 'ceiling', 'ledger_sha256', 'provenance'}, 'budget')
    if (not isinstance(budget['context_id'], str) or bridge._ID.fullmatch(budget['context_id']) is None
            or type(budget['consumed_before']) is not int or not 0 <= budget['consumed_before'] < 24
            or type(budget['ceiling']) is not int or budget['ceiling'] != 24):
        raise ValueError('caller serial budget must have remaining context within 24 ceiling')
    pilot._hex_digest(budget['ledger_sha256'], 'budget ledger pin')
    bridge.text(budget['provenance'], 'caller budget provenance', maximum=2048)
    if not isinstance(config['protected_paths'], list) or not 1 <= len(config['protected_paths']) <= 32:
        raise ValueError('explicit bounded protected path inventory required')
    for path in config['protected_paths']:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError('protected paths must be absolute')
        physical = sandbox._physical(Path(path))
        if not physical.is_dir() and not stat.S_ISREG(physical.stat().st_mode):
            raise ValueError('protected paths must be directories or regular files')


def _packet_pins(packet_root: Path, config: dict) -> tuple[bytes, dict]:
    supplied, owner = bridge.load_packet(packet_root)
    bridge.exact(supplied, {'schema', 'instructions', 'packet'}, 'assessment input')
    packet = supplied['packet']
    bridge.exact(packet, {'schema', 'packet_id', 'task_id', 'privacy', 'blinding', 'line_convention',
                        'source_sha256', 'prompt', 'semantic_key', 'fixtures', 'rubric', 'answers'}, 'packet')
    if packet['schema'] != bridge.PACKET_SCHEMA or not packet['answers']:
        raise ValueError('one task with nonempty eligible answers required')
    if packet['rubric'] != review.RUBRIC:
        raise ValueError('rubric mismatch')
    bridge.checked_blob(packet['prompt'], 'original prompt')
    bridge.checked_blob(packet['semantic_key'], 'semantic key')
    files = {}
    for fixture in packet['fixtures']:
        bridge.exact(fixture, {'file', 'text', 'sha256', 'line_count'}, 'original fixture')
        name = bridge.relative_file(fixture['file'])
        bridge.checked_blob({key: fixture[key] for key in ('text', 'sha256')}, 'fixture', empty=True)
        if name in files or type(fixture['line_count']) is not int or fixture['line_count'] != bridge.physical_lines(fixture['text']):
            raise ValueError('original fixture inventory/line mismatch')
        files[name] = fixture['sha256']
    source = {'prompt_sha256': packet['prompt']['sha256'], 'semantic_key_sha256': packet['semantic_key']['sha256'],
              'fixture_sha256': files}
    if bridge.digest(bridge.encode(source)) != packet['source_sha256'] or packet['line_convention'] != bridge.LINE_CONVENTION:
        raise ValueError('source/physical-line binding mismatch')
    ids = set()
    for answer in packet['answers']:
        bridge.exact(answer, {'answer_id', 'text', 'sha256'}, 'opaque answer')
        if not isinstance(answer['answer_id'], str) or bridge._ID.fullmatch(answer['answer_id']) is None or answer['answer_id'] in ids:
            raise ValueError('invalid/duplicate opaque answer ID')
        ids.add(answer['answer_id'])
        bridge.checked_blob({key: answer[key] for key in ('text', 'sha256')}, 'answer')
    if (owner['pins']['instructions_file_sha256'] != config['instructions_file_sha256']
            or owner['pins']['rubric_sha256'] != config['rubric_sha256']
            or bridge.digest(supplied['instructions'].encode()) != owner['pins']['assessor_section_sha256']):
        raise ValueError('instruction/rubric pins differ from qualified packet')
    raw = bridge.read_file(packet_root / 'assessor/input.json')
    pins = {'input_sha256': bridge.digest(raw), 'owner_sha256': bridge.digest(bridge.read_file(packet_root / 'owner/inventory.json')),
            'complete_sha256': bridge.digest(bridge.read_file(packet_root / 'complete.json')),
            'packet_id': packet['packet_id'], 'task_id': packet['task_id'],
            'assessor_section_sha256': owner['pins']['assessor_section_sha256'],
            'source_sha256': packet['source_sha256'], 'eligible_answer_count': len(packet['answers'])}
    return raw, pins


def prepare(root: Path, packet_root: Path, config_path: Path, catalog_path: Path,
            native: Path, auth_source: Path) -> dict:
    root, packet_root = bridge.physical_path(root), sandbox._physical(packet_root)
    native = sandbox._physical(native)
    if native != pilot.EXPECTED_CODEX_PATH:
        raise ValueError('only the reviewed native Darwin path is supported')
    legacy._native_codex(native)
    config_raw, catalog_raw = bridge.read_file(config_path), bridge.read_file(catalog_path)
    config = bridge.parse(config_raw)
    validate_config(config)
    pilot._validate_model_catalog(bridge.parse(catalog_raw), config['model']['id'])
    if legacy._file_digest(native) != config['native']['sha256'] or bridge.digest(catalog_raw) != config['catalog_sha256']:
        raise ValueError('native/catalog pin mismatch')
    raw, packet_pins = _packet_pins(packet_root, config)
    auth = auth_identity(auth_source)
    if root == packet_root or root.is_relative_to(packet_root) or packet_root.is_relative_to(root):
        raise ValueError('runner and original packet must be separate nonnested roots')
    legacy._new_dir(root)
    for name in ('packet', 'runtime', 'runtime/home', 'runtime/codex-home', 'runtime/scratch',
                 'runtime/scratch/tmp', 'runtime/home/.config', 'runtime/home/.cache', 'runtime/home/.local-share'):
        legacy._mkdir_private(root / name)
    write(root / 'packet/input.json', raw)
    write(root / 'config.json', config_raw)
    write(root / 'catalog.json', catalog_raw)
    write(root / 'runtime/codex-home/model-catalog.json', catalog_raw)
    (root / 'runtime/codex-home/auth.json').symlink_to(Path(auth['path']))
    env = {**config['environment'], 'HOME': str(root / 'runtime/home'), 'CODEX_HOME': str(root / 'runtime/codex-home'),
           'TMPDIR': str(root / 'runtime/scratch/tmp'), 'XDG_CONFIG_HOME': str(root / 'runtime/home/.config'),
           'XDG_CACHE_HOME': str(root / 'runtime/home/.cache'), 'XDG_DATA_HOME': str(root / 'runtime/home/.local-share')}
    protected = sorted(set(config['protected_paths'] + [str(root), str(packet_root), auth['path']]))
    overrides = sandbox.config_overrides(root / 'runtime/scratch', [Path(p) for p in protected])
    prefix = f'permissions.{sandbox.PROFILE}.filesystem={{'
    index = next(i for i, value in enumerate(overrides) if value.startswith(prefix))
    overrides[index] = overrides[index][:-1] + f',{json.dumps(str(root / "packet/input.json"))}="read"' + '}'
    argv = [str(native), 'exec', '--model', config['model']['id'], '-c', 'model_reasoning_effort="high"',
            '-c', f'model_catalog_json={json.dumps(str(root / "runtime/codex-home/model-catalog.json"))}']
    for value in [*CONTROLS, *overrides]:
        argv.extend(['-c', value])
    argv += ['--ignore-user-config', '--ignore-rules', '--json', '--color', 'never', '--skip-git-repo-check',
             '--output-last-message', str(root / 'context/last-message.txt'), '-C', str(root / 'runtime/scratch'), '-']
    manifest = {'schema': MANIFEST_SCHEMA, 'root': str(root), 'packet_root': str(packet_root), 'packet': packet_pins,
                'config': config, 'config_path': str(sandbox._physical(config_path)), 'config_sha256': bridge.digest(config_raw),
                'catalog_path': str(sandbox._physical(catalog_path)), 'catalog_sha256': bridge.digest(catalog_raw),
                'native_path': str(native), 'auth_source': auth, 'argv': argv, 'environment': env,
                'cwd': str(root / 'runtime/scratch'), 'permission_overrides': overrides, 'protected_paths': protected,
                'timeout_seconds': 600, 'response_bytes': bridge.MAX_RESPONSE_BYTES,
                'native_capture_capacity': {'capture_limit': NATIVE_CAPTURE_BYTES, 'line_limit': NATIVE_RECORD_BYTES},
                'tool_surface': {'expected_tools': [], 'schema_sha256': None,
                                 'basis': 'pinned no-tools configuration; advertised schemas not independently observed'},
                'budget_basis': 'caller-declared serial ledger; global allocation not verified by runner',
                'sources': {str(path): legacy._file_digest(path) for path in SOURCES}}
    manifest['digest'] = sha_object(manifest)
    write(root / 'manifest.json', bridge.encode(manifest))
    return manifest


def load(root: Path) -> dict:
    root = sandbox._physical(root)
    for directory in (root, root / 'packet', root / 'runtime', root / 'runtime/home', root / 'runtime/codex-home', root / 'runtime/scratch'):
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError('prepared directories must remain private')
    manifest = obj(root / 'manifest.json')
    if manifest.get('schema') != MANIFEST_SCHEMA or manifest.get('root') != str(root) or manifest.get('digest') != sha_object(manifest):
        raise ValueError('manifest identity/digest mismatch')
    _native_capacity(manifest)
    validate_config(manifest['config'])
    for name, expected in manifest['sources'].items():
        if legacy._file_digest(sandbox._physical(Path(name))) != expected:
            raise ValueError('runner/helper/source drift')
    for path, expected in ((root / 'packet/input.json', manifest['packet']['input_sha256']),
                           (root / 'config.json', manifest['config_sha256']), (Path(manifest['config_path']), manifest['config_sha256']),
                           (root / 'catalog.json', manifest['catalog_sha256']), (Path(manifest['catalog_path']), manifest['catalog_sha256']),
                           (root / 'runtime/codex-home/model-catalog.json', manifest['catalog_sha256'])):
        if bridge.digest(bridge.read_file(path)) != expected:
            raise ValueError('prepared config/catalog/packet drift')
        if path.is_relative_to(root) and stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError('prepared regular files must remain private')
    if set((root / 'packet').iterdir()) != {root / 'packet/input.json'}:
        raise ValueError('assessor visible inventory includes foreign metadata')
    if legacy._file_digest(sandbox._physical(Path(manifest['native_path']))) != manifest['config']['native']['sha256']:
        raise ValueError('native drift')
    if auth_identity(Path(manifest['auth_source']['path'])) != manifest['auth_source']:
        raise ValueError('auth source identity changed')
    link = root / 'runtime/codex-home/auth.json'
    if not link.is_symlink() or link.readlink() != Path(manifest['auth_source']['path']):
        raise ValueError('auth link changed')
    _, pins = _packet_pins(Path(manifest['packet_root']), manifest['config'])
    if pins != manifest['packet']:
        raise ValueError('original packet/owner receipt drift')
    return manifest


_PACKET_CHECKS = r'''
try:
 data=pathlib.Path(spec["packet"]).read_bytes()
 results["packet_read"]=hashlib.sha256(data).hexdigest()==spec["packet_sha256"]
except OSError: results["packet_read"]=False
for name,path,flags in [("packet_write_denied",spec["packet"],os.O_WRONLY),
                         ("packet_create_denied",spec["packet_create"],os.O_WRONLY|os.O_CREAT|os.O_EXCL)]:
 try: fd=os.open(path,flags,0o600)
 except OSError as e: results[name]=e.errno in (errno.EPERM,errno.EACCES,errno.EROFS)
 else: os.close(fd); results[name]=False
'''
PROBE = sandbox.PROBE.replace('print(json.dumps(results, sort_keys=True))', _PACKET_CHECKS + '\nprint(json.dumps(results, sort_keys=True))')


def qualify(manifest: dict) -> dict:
    """Existing named canaries plus exact read-only packet checks; no model."""
    if platform.system() != 'Darwin':
        raise ValueError('named native qualification is Darwin-only')
    root, scratch = Path(manifest['root']), Path(manifest['cwd'])
    native, env = Path(manifest['native_path']), manifest['environment']
    legacy._offline_probe(native, ['--version'], sandbox.CODEX_VERSION)
    with ExitStack() as cleanup:
        denied, denied_open = {}, {}
        for index, name in enumerate(manifest['protected_paths']):
            path = sandbox._physical(Path(name))
            if path.is_dir():
                directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='.assessor-canary-', dir=path)))
                directory.chmod(0o700)
                write(directory / 'canary', b'synthetic-only\n')
                denied[f'protected_{index}'] = str(directory / 'canary')
            else:
                denied_open[f'protected_{index}'] = str(path)
        visible = Path(cleanup.enter_context(tempfile.TemporaryDirectory(prefix='canaries-', dir=scratch)))
        visible.chmod(0o700)
        write(visible / 'allowed', b'synthetic-canary\n')
        child_denied = next(iter(denied.values()), next(iter(denied_open.values()), None))
        spec = {'denied': denied, 'denied_open': denied_open, 'denied_writes': {'control_write_denied': str(root / '.forbidden-write')},
                'allowed': str(visible / 'allowed'), 'allowed_write': str(visible / 'write'), 'read_executables': [],
                'child_denied': child_denied, 'packet': str(root / 'packet/input.json'),
                'packet_sha256': manifest['packet']['input_sha256'], 'packet_create': str(root / 'packet/.forbidden-create')}
        argv = [str(native), 'sandbox', '-P', sandbox.PROFILE, '--include-managed-config', '-C', str(scratch)]
        for value in [*CONTROLS, *manifest['permission_overrides']]:
            argv.extend(['-c', value])
        python = Path(sys.executable).resolve(strict=True)
        if not str(python).startswith(('/opt/homebrew/', '/usr/bin/')):
            raise ValueError('qualification runtime outside reviewed minimal roots')
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen(1)
            spec['port'] = listener.getsockname()[1]
            argv += ['--', str(python), '-I', '-c', PROBE, json.dumps(spec)]
            outcome = legacy._bounded_process(argv, b'', env, scratch, 30)
        checks = bridge.parse(outcome['stdout'])
        expected = set(denied) | set(denied_open) | {'control_write_denied', 'allowed_read', 'allowed_write',
                   'descendant_read_denied', 'tool_network_denied', 'packet_read', 'packet_write_denied', 'packet_create_denied'}
        if (outcome['exit_code'] != 0 or any(outcome[key] for key in ('timed_out', 'capture_overflow', 'interrupted', 'start_error'))
                or not isinstance(checks, dict) or set(checks) != expected or not all(value is True for value in checks.values())):
            raise ValueError('exact named packet/access canaries failed')
    return {'checks': checks, 'permission_overrides': manifest['permission_overrides'],
            'environment_sha256': bridge.digest(bridge.encode(env)), 'probe_sha256': bridge.digest(PROBE.encode()),
            'limitations': 'named accesses only; not exhaustive OS observation or managed/cloud configuration attestation'}


def _initial_runtime(manifest: dict) -> None:
    """Reject foreign local config/rules before even the no-model helper starts."""
    home, codex_home = Path(manifest['environment']['HOME']), Path(manifest['environment']['CODEX_HOME'])
    expected_home = {home / name for name in ('.config', '.cache', '.local-share')}
    if set(home.iterdir()) != expected_home or any(path.is_symlink() or not path.is_dir() or list(path.iterdir()) for path in expected_home):
        raise ValueError('fresh HOME contains foreign config/rules/state')
    native = pilot._native_runtime_inventory(codex_home)
    expected = {codex_home / 'auth.json', codex_home / 'model-catalog.json'}
    if native['present']:
        expected.add(codex_home / 'tmp')
    if set(codex_home.iterdir()) != expected:
        raise ValueError('fresh CODEX_HOME contains foreign config/rules/state')


def _access(manifest: dict) -> dict:
    _initial_runtime(manifest)
    receipt = (_ACCESS_QUALIFIER or qualify)(manifest)
    _initial_runtime(manifest)
    if (not isinstance(receipt, dict) or receipt.get('permission_overrides') != manifest['permission_overrides']
            or not isinstance(receipt.get('checks'), dict) or not receipt['checks']
            or not all(value is True for value in receipt['checks'].values())
            or not {'packet_read', 'packet_write_denied', 'packet_create_denied', 'tool_network_denied'} <= set(receipt['checks'])):
        raise ValueError('access qualification failed')
    return receipt


def preflight(root: Path) -> dict:
    manifest = load(root)
    directory = Path(root) / 'preflight'
    legacy._new_dir(directory)
    result = {'manifest_digest': manifest['digest'], 'passed': False}
    try:
        result['access'] = _access(manifest)
        load(root)
        result['passed'] = True
    except Exception as exc:
        result['error'] = str(exc)
    result['digest'] = sha_object(result)
    write(directory / 'result.json', bridge.encode(result))
    return result


def run(root: Path, approval_path: Path, *, allow_model_run: bool = False) -> dict:
    manifest = load(root)
    parse_limits = _native_capacity(manifest)
    ready = obj(Path(root) / 'preflight/result.json')
    if (ready.get('manifest_digest') != manifest['digest'] or ready.get('passed') is not True
            or ready.get('digest') != sha_object(ready)
            or ready.get('access', {}).get('permission_overrides') != manifest['permission_overrides']):
        raise ValueError('successful unchanged no-model preflight required')
    approval_raw = bridge.read_file(approval_path)
    approval = bridge.parse(approval_raw)
    bridge.exact(approval, {'schema', 'manifest_digest', 'context_id', 'decision', 'authorization_note', 'ledger_sha256'}, 'advance approval')
    budget = manifest['config']['budget']
    if (allow_model_run is not True or approval['schema'] != APPROVAL_SCHEMA or approval['manifest_digest'] != manifest['digest']
            or approval['context_id'] != budget['context_id'] or approval['ledger_sha256'] != budget['ledger_sha256']
            or approval['decision'] != 'advance-this-context-once'):
        raise ValueError('explicit flag and manifest-bound advance approval required')
    bridge.text(approval['authorization_note'], 'actual authorization provenance', maximum=2048)
    approval_path = sandbox._physical(approval_path)
    if (approval_path.is_relative_to(Path(manifest['cwd'])) or approval_path.is_relative_to(Path(root) / 'packet')
            or not any(approval_path == Path(p) or approval_path.is_relative_to(Path(p)) for p in manifest['protected_paths'])):
        raise ValueError('approval must be inside an explicit denial, outside assessor readable/writable paths')
    context = Path(root) / 'context'
    legacy._new_dir(context)
    write(context / 'reservation.json', bridge.encode({'manifest_digest': manifest['digest'], 'context_id': budget['context_id'],
         'approval_sha256': bridge.digest(approval_raw), 'budget': budget, 'one_shot': True,
         'preflight_sha256': bridge.digest(bridge.read_file(Path(root) / 'preflight/result.json')),
         'budget_basis': manifest['budget_basis']}))
    result = {'manifest_digest': manifest['digest'], 'context_id': budget['context_id'], 'launch': 'not-started',
              'status': 'blocked', 'finality': 'unknown', 'quality': 'unknown', 'retry_allowed': False, 'blockers': []}
    try:
        write(context / 'access.json', bridge.encode(_access(manifest)))
        load(root)
        result['launch'] = 'attempted-or-unknown'
        attempt = pilot._collect_local_attempt(context / 'attempt', manifest['argv'],
            prompt=bridge.read_file(Path(root) / 'packet/input.json'), env=manifest['environment'], cwd=Path(manifest['cwd']),
            timeout_seconds=600, codex_home=Path(root) / 'runtime/codex-home', answer_path=context / 'last-message.txt',
            association={'manifest_digest': manifest['digest'], 'context_id': budget['context_id'],
                         'budget_ledger_sha256': budget['ledger_sha256'], 'approval_sha256': bridge.digest(approval_raw)},
            **parse_limits)
        evidence = attempt['attempt_dir']
        events, process = [bridge.read_file(evidence / name) for name in ('events.jsonl', 'process.json')]
        rollout = collector._read_explicit_source(evidence / 'rollout.jsonl', parse_limits['capture_limit'])
        answer = bridge.read_file(evidence / 'answer.txt') if (evidence / 'answer.txt').exists() else None
        report = capture.capture_bytes(events, rollout, process, answer,
                                       answer_state='captured' if answer is not None else 'lost', **parse_limits)
        write(context / 'capture.json', bridge.encode(report))
        write(context / 'completion.json', bridge.encode(capture.completion_evidence_bytes(events, rollout, answer, **parse_limits)))
        blockers = result['blockers']
        # Native ModelRerouted is an ItemCompleted/ErrorItem, not a tool or
        # necessarily a failed terminal. Every such notice is a qualification gap.
        notices = capture.parse_jsonl(events, 'assessor_cli_notice', **parse_limits)['records']
        if any(isinstance(r, dict) and r.get('type') in ('item.started', 'item.completed')
               and isinstance(r.get('item'), dict) and r['item'].get('type') in ('error', 'warning') for r in notices):
            blockers.append('cli_error_or_warning_item')
        if report['calls']['inventory']:
            blockers.append('recorded_tool_activity_forbidden')
        # A no-tools assessment needs supported capture throughout. Uncertain
        # records are audit gaps, not proof of malicious activity, but still block use.
        blockers.extend(sorted({f'capture:{issue["source"]}:{issue["code"]}' for issue in report['issues']}))
        if report['execution']['execution'] != 'completed' or report['execution']['capture_overflow']:
            blockers.append('process_not_cleanly_completed')
        if attempt['delivery']['rollout']['state'] != 'captured':
            blockers.append('rollout_capture_lost')
        if len(attempt['delivery']['rollout'].get('candidates', [])) != 1:
            blockers.append('unexpected_rollout_population')
        if answer is None or len(answer) > bridge.MAX_RESPONSE_BYTES:
            blockers.append('response_missing_or_over_64KiB')
        contexts = [r.get('payload') for r in capture.parse_jsonl(rollout, 'assessor_model', **parse_limits)['records']
                    if isinstance(r, dict) and r.get('type') == 'turn_context']
        if not contexts or any(not isinstance(c, dict) or c.get('model') != 'gpt-5.6-sol' or c.get('effort') != 'high' for c in contexts):
            blockers.append('observed_model_context_missing_or_mismatched')
        load(root)
        result.update(launch='attempted', execution=report['execution']['execution'],
                      status='blocked' if blockers else 'captured-unqualified',
                      usage=report['usage'], recorded_call_entries=report['calls']['inventory_entry_count'])
    except BaseException as exc:
        result['blockers'].append(f'runner_failure:{type(exc).__name__}:{exc}')
        write(context / 'result.json', bridge.encode(result))
        raise
    write(context / 'result.json', bridge.encode(result))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    for flag in ('root', 'packet', 'config', 'catalog', 'native', 'auth-source'):
        prep.add_argument('--' + flag, type=Path, required=True)
    flight = commands.add_parser('preflight'); flight.add_argument('--root', type=Path, required=True)
    advance = commands.add_parser('run'); advance.add_argument('--root', type=Path, required=True)
    advance.add_argument('--approval', type=Path, required=True)
    advance.add_argument('--allow-model-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            value = prepare(args.root, args.packet, args.config, args.catalog, args.native, args.auth_source)
            summary = {'status': 'prepared', 'manifest_digest': value['digest'], 'context_id': value['config']['budget']['context_id']}
        elif args.command == 'preflight':
            value = preflight(args.root)
            summary = {'status': 'preflight-passed' if value['passed'] else 'preflight-blocked', 'manifest_digest': value['manifest_digest']}
        else:
            value = run(args.root, args.approval, allow_model_run=args.allow_model_run)
            summary = {key: value[key] for key in ('status', 'manifest_digest', 'context_id', 'finality', 'quality')}
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary['status'] in ('prepared', 'preflight-passed', 'captured-unqualified') else 2
    except (OSError, ValueError, KeyError) as exc:
        print(f'assessor runner refused: {type(exc).__name__}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
