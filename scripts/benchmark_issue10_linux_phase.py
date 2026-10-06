#!/usr/bin/env python3
"""One-shot prospective Linux/SSH pilot and measurement, separate from local studies.

Preparation is offline. Preflight uses no model; run-slot requires fresh named local
and remote canaries, immutable readiness and manifest-bound actual task approval.
The concrete LinuxAccess adapter owns trusted SSH, confinement and FIFO broker IO.
Missing production capabilities fail closed, never via an operator readiness flag.
"""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import random
import re
import stat
import sys
import tomllib
from typing import Any

import benchmark_issue10_local_pilot as pilot
import benchmark_issue10_v3 as coordinator

legacy, capture, collector = pilot.legacy, pilot.capture, pilot.collector
REPO = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA = 'sshai-benchmark/issue10-linux-manifest-1'
REMOTE_PLAN_SCHEMA = 'sshai-benchmark/issue10-linux-remote-plan-1'
APPROVAL_SCHEMA = 'sshai-benchmark/issue10-linux-approval-1'
ACCESS_SCHEMA = 'sshai-benchmark/issue10-linux-access-1'
READINESS_SCHEMA = 'sshai-benchmark/issue10-linux-readiness-1'
RESULT_SCHEMA = 'sshai-benchmark/issue10-linux-result-1'
SUMMARY_SCHEMA = 'sshai-benchmark/issue10-linux-summary-1'
PILOT_REVIEW_SCHEMA = 'sshai-benchmark/issue10-linux-pilot-review-1'
PHASE = 'linux-ssh-prospective-1'
CASES = tuple(f'L{n:02}' for n in range(1, 7))
CAPTURE_CAPACITY = {'stream_limit': 8 * 1024 * 1024, 'capture_limit': 8 * 1024 * 1024,
                    'line_limit': 4 * 1024 * 1024}
BUDGET = {'diagnostic_consumed_before_linux': 40, 'assessment_consumed_before_linux': 7,
          'linux_pilot_allocated': 4, 'linux_measurement_allocated': 36,
          'diagnostic_ceiling': 120, 'assessment_ceiling': 24,
          'subscription_only': True, 'retries': 0, 'substitution': False}
SOURCE_PATHS = pilot.SOURCE_PATHS | {
    'scripts/benchmark_issue10_linux_phase.py', 'scripts/benchmark_issue10_linux_access.py',
    'scripts/benchmark_issue10_linux_boundary.py', 'scripts/benchmark_issue10_fifo_broker.py',
    'scripts/benchmark_issue10_v3.py', 'scripts/benchmark_issue10_v3_cases.py',
    'scripts/benchmark_issue10_intercepted_patch.py',
}


class PhaseInputError(pilot.PilotInputError):
    """A prospective pin, capability or one-shot gate is absent or inconsistent."""

SEED = 1010
TARGET_ALIAS = 'issue10-target'
REMOTE_FIXTURE = '/fixture'


def schedule(population: str) -> list[dict]:
    """Fixed original Linux measurement projection or two balanced pilot pairs."""
    if population == 'measurement':
        selected = [slot for slot in coordinator.schedule('measurement', SEED) if slot['series'] == 'L']
        return [{**slot, 'protocol_slot': slot['slot'], 'slot': number}
                for number, slot in enumerate(selected, 1)]
    if population != 'pilot':
        raise ValueError('Linux population must be pilot or measurement')
    rng = random.Random(SEED)
    cases, first_arms = ['L01', 'L02'], ['baseline', 'sshai']
    rng.shuffle(cases)
    rng.shuffle(first_arms)
    pairs = list(zip(cases, first_arms))
    rng.shuffle(pairs)
    slots = []
    for pair_order, (case_id, first) in enumerate(pairs, 1):
        for arm in (first, 'sshai' if first == 'baseline' else 'baseline'):
            slots.append({'slot': len(slots) + 1, 'pair_id': f'{case_id}-r1',
                          'pair_order': pair_order, 'case_id': case_id, 'replicate': 1,
                          'series': 'L', 'arm': arm})
    return slots


def render_prompt(source: bytes, scratch: str, arm: str, sshai_path: str) -> bytes:
    """Retain the original symptom goal; only environment/branch access differs."""
    try:
        text = source.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('task prompt must be UTF-8') from exc
    if text.count('{fixture_root}') != 1 or arm not in ('baseline', 'sshai'):
        raise ValueError('task requires exactly one fixture_root and a known arm')
    if any(not isinstance(p, str) or not p.startswith('/') or any(c in p for c in '\x00\r\n')
           for p in (scratch, sshai_path)):
        raise ValueError('scratch and executable must be explicit absolute paths')
    text = text.replace('{fixture_root}', REMOTE_FIXTURE)
    text += f'\nAuthorized writable LOCAL scratch directory: {scratch}\n'
    text += (
        f'Investigate the supplied remote Linux snapshots through SSH alias {TARGET_ALIAS}. '
        'The supplied SSH client forwards commands through system OpenSSH to the selected host, '
        'inside a per-case confined environment. There is no local source-fixture copy. '
        'Remote source fixtures are read-only; remote writable scratch is /scratch. '
        'Do not inspect live host data, change fixtures or access other cases. '
        'Do not execute the proposed remedy, use unrelated network tools, transfer fixture trees, '
        'spawn subagents, change SSH settings or install tools. '
        'Local notes and command-body files may be created only in authorized local scratch.\n'
    )
    if arm == 'baseline':
        text += (
            f'Use the supplied ssh client with {TARGET_ALIAS} for remote diagnostics, without sshai. '
            'Choose Bash commands, filters and output limits freely. '
            'You may save and process already captured remote outputs in local scratch.\n'
        )
    else:
        text += (
            f'Route every new remote fixture diagnostic through exactly {sshai_path} run '
            f'with {TARGET_ALIAS}; do not use direct ssh for new diagnostics. '
            'Use --body-file for multiline Bash bodies; options precede the host. '
            'Keep SSHAI_ROOT at its supplied isolated local path. '
            'Use sshai help, q, diff or --delta where useful. Ordinary local tools may process '
            'already captured sshai artifacts without first proving q inadequate; this is not '
            'permission to read new source data. Query tools receive the artifact path as their '
            'last argv argument. Truncated output is incomplete and discarded bytes cannot be '
            'recovered by querying. Cite original fixture paths and source lines in the answer.\n'
        )
    return text.encode('utf-8')



def _read(path: Path, label: str, limit: int = capture.MAX_CAPTURE_BYTES) -> bytes:
    try:
        return legacy._read_bounded(pilot._private_regular(path, label), limit)
    except (OSError, ValueError) as exc:
        raise PhaseInputError(f'{label} is absent, changed or unsafe') from exc


def _json(path: Path, label: str) -> dict:
    try:
        return pilot._json_file(path, label)
    except (OSError, ValueError) as exc:
        raise PhaseInputError(f'invalid {label}') from exc


def _sealed(value: dict) -> dict:
    return {**value, 'digest': pilot._digest_object(value)}


def _digest(value: dict, label: str) -> None:
    if value.get('digest') != pilot._digest_object(value):
        raise PhaseInputError(f'{label} digest changed')


def _absolute(value: Any) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', value)
            or any(p in ('', '.', '..') for p in value[1:].split('/'))):
        raise PhaseInputError('require an explicit absolute lexical path without traversal')
    return value


def _remote_plan(value: dict) -> dict:
    required = {'schema', 'host_alias', 'parent', 'uid', 'gid', 'bash', 'setpriv',
                'python', 'tools', 'runtime_files', 'canaries'}
    if (set(value) - (required | {'runtime_version'}) or not required <= set(value)
            or value['schema'] != REMOTE_PLAN_SCHEMA
            or not isinstance(value['host_alias'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value['host_alias'])
            or not re.fullmatch(r'/tmp/sshai-issue10-[0-9a-f]{32}', str(value['parent']))):
        raise PhaseInputError('invalid private remote plan')
    import benchmark_issue10_linux_boundary as boundary
    # Structural validation only. Actual remote hashes/access remain mandatory canaries.
    plan = {key: value[key] for key in ('uid', 'gid', 'bash', 'setpriv', 'python', 'tools', 'runtime_files')}
    plan.update(schema=boundary.SCHEMA, root=value['parent'] + '/issue10-linux-' + '0' * 32,
                nonce='0' * 32, fixture_source=value['parent'] + '/fixtures/L01',
                fixture_files=[{'path': 'synthetic-validation-only', 'size': 0, 'sha256': pilot._sha(b'')}])
    try:
        boundary.validate_plan(plan)
    except ValueError as exc:
        raise PhaseInputError('invalid remote runtime declaration') from exc
    canaries = value['canaries']
    if not isinstance(canaries, list) or not 1 <= len(canaries) <= 32:
        raise PhaseInputError('require bounded explicit remote synthetic canaries')
    names = set()
    for row in canaries:
        pilot._exact_object(row, {'name', 'path', 'size', 'sha256'}, 'remote canary')
        if (not isinstance(row['name'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', row['name'])
                or row['name'] in names or type(row['size']) is not int or not 0 <= row['size'] <= 1024):
            raise PhaseInputError('invalid remote canary')
        names.add(row['name'])
        _absolute(row['path'])
        pilot._hex_digest(row['sha256'], 'canary hash')
    return value


def _fixture_inventory(bundle: Path) -> tuple[dict, dict]:
    manifest = _json(bundle / 'manifest.json', 'original draft-2 fixture inventory')
    if manifest.get('schema_version') != 'issue10-synthetic-v3-draft-2':
        raise PhaseInputError('Linux requires the unchanged draft-2 fixture bundle')
    entries, cases = manifest.get('files'), manifest.get('cases')
    if not isinstance(entries, list) or not isinstance(cases, list):
        raise PhaseInputError('invalid fixture inventory')
    listed = {}
    for row in entries:
        pilot._exact_object(row, {'path', 'bytes', 'sha256'}, 'fixture file')
        name = pilot._safe_relative(row['path'], 'fixture path')
        if name in listed or type(row['bytes']) is not int or not 0 <= row['bytes'] <= capture.MAX_CAPTURE_BYTES:
            raise PhaseInputError('duplicate or oversized fixture file')
        pilot._hex_digest(row['sha256'], 'fixture hash')
        listed[name] = row
    selected = {}
    for case in CASES:
        rows = [row for row in cases if isinstance(row, dict) and row.get('case_id') == case]
        if len(rows) != 1 or not isinstance(rows[0].get('files'), list) or not rows[0]['files']:
            raise PhaseInputError('require six unique original Linux cases')
        names = [pilot._safe_relative(row.get('file'), 'Linux fixture') for row in rows[0]['files']]
        if len(set(names)) != len(names) or len(names) > 512:
            raise PhaseInputError('duplicate or excessive Linux inputs')
        selected[case] = {}
        for relative, target in [(name, f'inputs/{case}/{name}') for name in names] + [('__prompt__', f'prompts/{case}.md')]:
            row = listed.get(target)
            data = _read(bundle / target, 'source fixture or exact prompt')
            if row is None or len(data) != row['bytes'] or pilot._sha(data) != row['sha256']:
                raise PhaseInputError('original fixture or prompt hash mismatch')
            selected[case][relative] = data
    return manifest, selected


def _preflight_slots() -> list:
    measured = schedule('measurement')
    return [{**next(s for s in measured if s['case_id'] == case), 'slot': 1000 + index}
            for index, case in enumerate(CASES, 1)]


def _all_material(root: Path, slots: list, selected: dict, sshai: str) -> dict:
    return _material(root, slots + _preflight_slots(), selected, sshai)


def _material(root: Path, slots: list, selected: dict, sshai: str) -> dict:
    return {str(slot['slot']): {
        'fixture_files': {name: {'bytes': len(data), 'sha256': pilot._sha(data)}
                          for name, data in sorted(selected[slot['case_id']].items()) if name != '__prompt__'},
        'source_prompt_sha256': pilot._sha(selected[slot['case_id']]['__prompt__']),
        'rendered_prompt_sha256': pilot._sha(render_prompt(selected[slot['case_id']]['__prompt__'],
            str(root / 'slots' / f"{slot['slot']:03}" / 'scratch'), slot['arm'], sshai)),
    } for slot in slots}


def _evidence_pins(paths: list[Path]) -> dict:
    return {str(path): pilot._sha(_read(path, 'prerequisite evidence', CAPTURE_CAPACITY['capture_limit']))
            for path in paths}


def _verify_pins(pins: dict) -> None:
    if not isinstance(pins, dict) or not pins:
        raise PhaseInputError('missing concrete prerequisite evidence pins')
    for name, digest in pins.items():
        _absolute(name)
        pilot._hex_digest(digest, 'evidence pin')
        if pilot._sha(_read(Path(name), 'pinned evidence', CAPTURE_CAPACITY['capture_limit'])) != digest:
            raise PhaseInputError('bound prerequisite evidence changed')


def _pilot_binding(root: Path | None, review_path: Path | None) -> dict:
    if root is None or review_path is None:
        raise PhaseInputError('measurement requires actual Linux pilot and caller qualification evidence')
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    if manifest['population'] != 'pilot':
        raise PhaseInputError('measurement prerequisite is not the Linux pilot')
    load_readiness(root, manifest)
    paths = [root / 'manifest.json', root / 'readiness/result.json']
    paths += [root / 'readiness' / f'access-{case}.json' for case in CASES]
    for slot in manifest['slots']:
        path = root / 'slots' / f"{slot['slot']:03}" / 'result.json'
        result = _load_result(path, manifest, slot)
        if result['continuation']['allowed'] is not True:
            raise PhaseInputError('Linux pilot retained a blocking outcome')
        paths.append(path)
    review_path = pilot._private_regular(review_path, 'caller pilot qualification')
    review = _json(review_path, 'caller pilot qualification')
    pilot._exact_object(review, {'schema', 'manifest_digest', 'status', 'provenance', 'evidence'}, 'pilot review')
    if (review['schema'] != PILOT_REVIEW_SCHEMA or review['manifest_digest'] != manifest['digest']
            or review['status'] != 'passed' or not isinstance(review['provenance'], str) or not review['provenance'].strip()):
        raise PhaseInputError('pilot qualification provenance is missing or mismatched')
    _verify_pins(review['evidence'])
    paths += [review_path, *map(Path, review['evidence'])]
    return {'root': str(root), 'manifest_digest': manifest['digest'], 'review_path': str(review_path),
            'evidence': _evidence_pins(paths), 'qualification': 'caller-provenance-not-controller-semantic-proof'}


def approval_template(manifest: dict) -> dict:
    return {'schema': APPROVAL_SCHEMA, 'phase': PHASE, 'population': manifest['population'],
            'manifest_digest': manifest['digest'], 'config_sha256': manifest['config_sha256'],
            'session_count': manifest['session_count'], 'original_diagnostic_session_ceiling': 120,
            'approved': False, 'approved_at_utc': '', 'authorization_note': ''}


def prepare(root: Path, fixture_bundle: Path, codex_path: Path, sshai_path: Path,
            config_path: Path, model_catalog_path: Path, tool_overrides_path: Path,
            auth_path: Path, assessment_instructions_path: Path, assessment_rubric_path: Path,
            remote_plan_path: Path, *, population: str = 'pilot', pilot_root: Path | None = None,
            pilot_review_path: Path | None = None, intercepted_patch_profile: dict | None = None,
            _binary_probe=None) -> dict:
    """Freeze controller-only original six Linux inputs. Never provision SSH or launch a model."""
    slots = schedule(population)
    if intercepted_patch_profile is not None:
        import benchmark_issue10_intercepted_patch as patch
        if intercepted_patch_profile != patch.ADD_PROFILE:
            raise PhaseInputError('only the explicit prospective helper-add version-2 profile is supported')
    root = legacy._physical(Path(root), must_exist=False)
    bundle = legacy._physical(Path(fixture_bundle))
    codex = pilot._private_regular(codex_path, 'Codex', executable=True)
    sshai = pilot._private_regular(sshai_path, 'old-study sshai', executable=True)
    auth = pilot._private_regular(auth_path, 'dedicated authentication file')
    if (codex != pilot.EXPECTED_CODEX_PATH or stat.S_IMODE(auth.stat().st_mode) != 0o600
            or root.parent != auth.parent.parent):
        raise PhaseInputError('require pinned native Codex and new dedicated-private-parent child')
    config = pilot._validate_config(_json(config_path, 'Linux config'))
    if not re.fullmatch(r'ad1532b[0-9a-f]{0,33}', config['sshai']['revision']):
        raise PhaseInputError('Linux requires the ad1532b old-study sshai build')
    catalog = _read(model_catalog_path, 'catalog')
    pilot._validate_model_catalog(_json(model_catalog_path, 'catalog'), config['model']['id'])
    controls = _read(tool_overrides_path, 'controls')
    try:
        if json.loads(controls, object_pairs_hook=pilot._no_duplicate_keys) != pilot.REQUIRED_CODEX_OVERRIDES:
            raise ValueError('controls mismatch')
    except ValueError as exc:
        raise PhaseInputError('controls differ from pinned local helper controls') from exc
    assessment = {}
    for label, path in [('instructions', assessment_instructions_path), ('rubric', assessment_rubric_path)]:
        data = _read(path, f'assessment {label}')
        if pilot._sha(data) != config['assessment'][f'{label}_sha256'] or not data.decode('utf-8').strip():
            raise PhaseInputError('assessment input differs from config')
        assessment[label] = data
    remote_path = pilot._private_regular(remote_plan_path, 'remote plan')
    remote_data = _read(remote_path, 'remote plan')
    remote = _remote_plan(_json(remote_path, 'remote plan'))
    fixture_manifest, selected = _fixture_inventory(bundle)
    prerequisite = _pilot_binding(pilot_root, pilot_review_path) if population == 'measurement' else None
    if population == 'pilot' and (pilot_root is not None or pilot_review_path is not None):
        raise PhaseInputError('pilot cannot inherit another population')
    probes = (_binary_probe or pilot._probe_binaries)(codex, sshai, config)
    manifest = {'schema': MANIFEST_SCHEMA, 'phase': PHASE, 'population': population,
                'approval_schema': APPROVAL_SCHEMA, 'root': str(root), 'schedule_seed': SEED,
                'slots': slots, 'preflight_slots': _preflight_slots(), 'cases': list(CASES), 'session_count': len(slots),
                'capture_capacity': dict(CAPTURE_CAPACITY), 'budget': dict(BUDGET),
                'original_diagnostic_session_ceiling': 120, 'assessor_context_ceiling': 24,
                'no_retry_no_resume': True, 'experimental_savings_claim_eligible': False,
                'fixture_bundle': {'path': str(bundle), 'manifest_sha256': legacy._file_digest(bundle / 'manifest.json'),
                                   'schema_version': fixture_manifest['schema_version']},
                'slot_material': _all_material(root, slots, selected, str(sshai)),
                'codex': {'path': str(codex), **config['codex'],
                          'version_output_sha256': probes['codex_version_output_sha256']},
                'sshai': {'path': str(sshai), **config['sshai']},
                'system_ssh': {'path': '/usr/bin/ssh', 'sha256': legacy._file_digest(Path('/usr/bin/ssh'))},
                'auth': {'path': str(auth), 'required_mode': '0600', 'content_read_or_retained': False},
                'config': config, 'config_sha256': pilot._sha(pilot._encoded(config)),
                'model_catalog': {'sha256': pilot._sha(catalog), 'bytes': len(catalog)},
                'tool_overrides': {'sha256': pilot._sha(controls), 'bytes': len(controls)},
                'assessment_inputs': {k: {'sha256': pilot._sha(v), 'bytes': len(v)} for k, v in assessment.items()},
                'remote_plan': remote, 'remote_plan_source': {'path': str(remote_path), 'sha256': pilot._sha(remote_data)},
                'sources': {name: pilot._sha(_read(REPO / name, 'controller source')) for name in sorted(SOURCE_PATHS)},
                'pilot_prerequisite': prerequisite, 'intercepted_patch_profile': intercepted_patch_profile}
    manifest = _sealed(manifest)
    if len(pilot._pretty(manifest)) > capture.MAX_CAPTURE_BYTES:
        raise PhaseInputError('manifest exceeds bounded private input contract')
    legacy._new_dir(root)
    legacy._mkdir_private(root / 'slots')
    for case, files in selected.items():
        for name, data in files.items():
            relative = f'prompts/{case}.md' if name == '__prompt__' else f'inputs/{case}/{name}'
            legacy._write_new(root / 'prepared' / relative, data, mode=0o400)
    for label, data in assessment.items():
        legacy._write_new(root / f'prepared/assessment/{label}.md', data, mode=0o400)
    for path in sorted((p for p in (root / 'prepared').rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        path.chmod(0o500)
    (root / 'prepared').chmod(0o500)
    for name, data in [('model-catalog.json', catalog), ('tool-overrides.json', controls),
                       ('remote-plan.json', remote_data), ('config.json', pilot._pretty(config)),
                       ('manifest.json', pilot._pretty(manifest)),
                       ('approval-template.json', pilot._pretty(approval_template(manifest)))]:
        legacy._write_new(root / name, data, mode=0o600)
    return manifest


def load_manifest(root: Path) -> dict:
    root = legacy._physical(Path(root))
    manifest = _json(root / 'manifest.json', 'Linux manifest')
    _digest(manifest, 'manifest')
    if (manifest.get('schema') != MANIFEST_SCHEMA or manifest.get('phase') != PHASE or manifest.get('root') != str(root)
            or manifest.get('schedule_seed') != SEED or manifest.get('cases') != list(CASES)
            or manifest.get('slots') != schedule(manifest.get('population'))
            or manifest.get('preflight_slots') != _preflight_slots()
            or manifest.get('session_count') != len(manifest['slots'])
            or manifest.get('budget') != BUDGET or manifest.get('capture_capacity') != CAPTURE_CAPACITY
            or manifest.get('no_retry_no_resume') is not True or manifest.get('experimental_savings_claim_eligible') is not False
            or manifest.get('original_diagnostic_session_ceiling') != 120 or manifest.get('assessor_context_ceiling') != 24
            or set(manifest.get('sources', {})) != SOURCE_PATHS):
        raise PhaseInputError('manifest differs from prospective fixed contract')
    profile = manifest.get('intercepted_patch_profile')
    if profile is not None:
        import benchmark_issue10_intercepted_patch as patch
        if profile != patch.ADD_PROFILE:
            raise PhaseInputError('intercepted helper patch profile changed or unsupported')
    config = pilot._validate_config(manifest['config'])
    if _json(root / 'config.json', 'config') != config or pilot._sha(pilot._encoded(config)) != manifest['config_sha256']:
        raise PhaseInputError('config changed')
    for key in ('codex', 'sshai'):
        row = manifest[key]
        path = pilot._private_regular(Path(row['path']), key, executable=True)
        if legacy._file_digest(path) != row['sha256'] or any(row[k] != v for k, v in config[key].items()):
            raise PhaseInputError('pinned binary changed')
    if Path(manifest['codex']['path']) != pilot.EXPECTED_CODEX_PATH or not manifest['sshai']['revision'].startswith('ad1532b'):
        raise PhaseInputError('binary selection changed')
    auth = pilot._private_regular(Path(manifest['auth']['path']), 'auth source')
    if stat.S_IMODE(auth.stat().st_mode) != 0o600 or auth.parent.parent != root.parent:
        raise PhaseInputError('auth source mode or dedicated parent changed')
    for name, sha in manifest['sources'].items():
        if pilot._sha(_read(REPO / name, 'source')) != sha:
            raise PhaseInputError('controller source changed; prepare a new prospective root')
    _verify_pins({manifest['system_ssh']['path']: manifest['system_ssh']['sha256']})
    for name, key in [('model-catalog.json', 'model_catalog'), ('tool-overrides.json', 'tool_overrides')]:
        data = _read(root / name, name)
        if len(data) != manifest[key]['bytes'] or pilot._sha(data) != manifest[key]['sha256']:
            raise PhaseInputError('catalog or controls changed')
    pilot._validate_model_catalog(_json(root / 'model-catalog.json', 'catalog'), config['model']['id'])
    if json.loads(_read(root / 'tool-overrides.json', 'controls')) != pilot.REQUIRED_CODEX_OVERRIDES:
        raise PhaseInputError('controls changed')
    remote_data = _read(root / 'remote-plan.json', 'remote plan')
    remote = _remote_plan(_json(root / 'remote-plan.json', 'remote plan'))
    if (remote != manifest['remote_plan'] or pilot._sha(remote_data) != manifest['remote_plan_source']['sha256']
            or _read(Path(manifest['remote_plan_source']['path']), 'source remote plan') != remote_data):
        raise PhaseInputError('private remote plan changed')
    bundle = Path(manifest['fixture_bundle']['path'])
    if legacy._file_digest(pilot._private_regular(bundle / 'manifest.json', 'original fixture inventory')) != manifest['fixture_bundle']['manifest_sha256']:
        raise PhaseInputError('original bundle inventory changed')
    _, selected = _fixture_inventory(bundle)
    expected_paths = set()
    for case, files in selected.items():
        for name, data in files.items():
            relative = f'prompts/{case}.md' if name == '__prompt__' else f'inputs/{case}/{name}'
            expected_paths.add(relative)
            if _read(root / 'prepared' / relative, 'prepared fixture') != data:
                raise PhaseInputError('prepared fixture or exact prompt changed')
    for label, info in manifest['assessment_inputs'].items():
        relative = f'assessment/{label}.md'
        expected_paths.add(relative)
        data = _read(root / 'prepared' / relative, 'assessment input')
        if len(data) != info['bytes'] or pilot._sha(data) != info['sha256']:
            raise PhaseInputError('assessment input changed')
    if stat.S_IMODE((root / 'prepared').stat().st_mode) != 0o500:
        raise PhaseInputError('immutable prepared root mode changed')
    actual_paths = set()
    for path in (root / 'prepared').rglob('*'):
        if (path.is_symlink() or not (path.is_file() or path.is_dir())
                or stat.S_IMODE(path.stat().st_mode) != (0o400 if path.is_file() else 0o500)):
            raise PhaseInputError('unexpected prepared entry or immutable mode changed')
        if path.is_file():
            actual_paths.add(str(path.relative_to(root / 'prepared')))
    if actual_paths != expected_paths or manifest['slot_material'] != _all_material(root, manifest['slots'], selected, manifest['sshai']['path']):
        raise PhaseInputError('prepared population or rendered prompts changed')
    prerequisite = manifest['pilot_prerequisite']
    if manifest['population'] == 'measurement':
        if not isinstance(prerequisite, dict) or Path(prerequisite['root']) == root:
            raise PhaseInputError('measurement pilot binding absent')
        _verify_pins(prerequisite['evidence'])
        if _pilot_binding(Path(prerequisite['root']), Path(prerequisite['review_path'])) != prerequisite:
            raise PhaseInputError('pilot prerequisite changed')
    elif prerequisite is not None:
        raise PhaseInputError('pilot cannot inherit outcomes')
    allowed_slots = {f"{s['slot']:03}" for s in manifest['slots']}
    for path in (root / 'slots').iterdir():
        if path.name not in allowed_slots or path.is_symlink() or not path.is_dir():
            raise PhaseInputError('unexpected slot directory')
    return manifest


def _adapter(manifest: dict, root: Path, factory):
    if factory is not None:
        return factory(manifest, root)
    try:
        module = importlib.import_module('benchmark_issue10_linux_access')
        return module.LinuxAccess(manifest, root)
    except (ImportError, AttributeError) as exc:
        raise PhaseInputError('missing production LinuxAccess capability; no model permitted') from exc


def _provision(root: Path, manifest: dict, base: Path) -> dict:
    for relative in ('scratch', 'evidence', 'home', 'codex-home', 'scratch/sshai-root', 'scratch/tmp',
                     'home/.config', 'home/.cache', 'home/.local-share'):
        legacy._mkdir_private(base / relative)
    (base / 'codex-home/auth.json').symlink_to(Path(manifest['auth']['path']))
    legacy._write_new(base / 'codex-home/model-catalog.json', _read(root / 'model-catalog.json', 'catalog'), mode=0o400)
    return pilot._environment(base, base / 'scratch', manifest['config'])


def _session_settings(session, environment: dict, base: Path) -> tuple[dict, list]:
    actual, overrides = session.environment, session.config_overrides
    expected = {**environment, 'PATH': str(base / 'scratch/bin') + ':' + environment['PATH']}
    if actual != expected:
        raise PhaseInputError('adapter must preserve sanitized env except exact scratch/bin PATH prefix')
    if not isinstance(overrides, list) or not overrides or any(not isinstance(v, str) or not v or '\x00' in v for v in overrides):
        raise PhaseInputError('adapter must provide concrete named permission overrides')
    try:
        permission = tomllib.loads('\n'.join(overrides))
        profile = permission['permissions'][pilot.sandbox.PROFILE]
        if (set(permission) != {'default_permissions', 'permissions'}
                or permission['default_permissions'] != pilot.sandbox.PROFILE
                or set(permission['permissions']) != {pilot.sandbox.PROFILE}
                or set(profile) != {'workspace_roots', 'filesystem', 'network'}
                or profile['workspace_roots'] != {str(base / 'scratch'): True}
                or profile['network'] != {'enabled': False}
                or profile['filesystem'].get(':root') != 'deny'
                or profile['filesystem'].get(':workspace_roots') != {'.': 'write'}):
            raise ValueError('named profile mismatch')
    except (KeyError, TypeError, ValueError) as exc:
        raise PhaseInputError('adapter must use the exact scratch-bound network-denied named profile') from exc
    return dict(actual), list(overrides)


def _access(receipt: dict, manifest: dict, slot: dict, base: Path, environment: dict, overrides: list) -> dict:
    if not isinstance(receipt, dict):
        raise PhaseInputError('missing real local and remote canary receipt')
    _digest(receipt, 'access receipt')
    checks = receipt.get('checks')
    if (receipt.get('schema') != ACCESS_SCHEMA or receipt.get('status') != 'passed'
            or receipt.get('manifest_digest') != manifest['digest'] or receipt.get('slot') != slot
            or receipt.get('base') != str(base) or receipt.get('effective_config_overrides') != overrides
            or receipt.get('environment_sha256') != pilot._sha(pilot._encoded(environment))
            or receipt.get('remote_fixture_pins') != manifest['slot_material'][str(slot['slot'])]['fixture_files']
            or receipt.get('runtime_pins') != manifest['remote_plan']['runtime_files']
            or not isinstance(checks, dict) or not checks or not all(value is True for value in checks.values())
            or not any(name.startswith('local:') for name in checks) or not any(name.startswith('remote:') for name in checks)):
        raise PhaseInputError('actual path/config-bound access receipt is changed, incomplete or failed')
    return receipt


def _broker_blockers(evidence: dict) -> list[str]:
    if (not isinstance(evidence, dict) or evidence.get('schema') != 'sshai-issue10-linux-broker-evidence-1'
            or not isinstance(evidence.get('lifecycle'), dict)
            or not isinstance(evidence['lifecycle'].get('reason'), str)
            or type(evidence['lifecycle'].get('calls')) is not int
            or not 0 <= evidence['lifecycle']['calls'] <= 128
            or not isinstance(evidence.get('receipts'), list) or len(evidence['receipts']) > 129
            or any(not isinstance(row, dict) or not isinstance(row.get('status'), str)
                   or not isinstance(row.get('publication'), str) for row in evidence['receipts'])):
        raise PhaseInputError('broker lifecycle evidence is malformed or missing')
    blockers = []
    # stop() normally cancels an idle supervisor. Cancellation of an active
    # request, lost publication and other supervisor exits are independent
    # continuation failures, not evidence of fixture mutation or semantic routing.
    if evidence['lifecycle']['reason'] != 'cancelled':
        blockers.append('broker_supervisor_abnormal')
    if any(row['status'] != 'completed' or row['publication'] != 'fifo-published'
           for row in evidence['receipts']):
        blockers.append('broker_transaction_incomplete_or_failed')
    return blockers


def _finalized(value: dict, manifest: dict, slot: dict, base: Path) -> dict:
    fields = {'fixture_integrity', 'fixture_inventory_exact', 'broker_evidence', 'remote_cleanup', 'access_status'}
    if 'broker_blockers' in value:
        fields.add('broker_blockers')
    pilot._exact_object(value, fields, 'finalized boundary')
    expected = manifest['slot_material'][str(slot['slot'])]['fixture_files']
    if (not isinstance(value['fixture_integrity'], dict) or set(value['fixture_integrity']) != set(expected)
            or any(type(v) is not bool for v in value['fixture_integrity'].values())
            or type(value['fixture_inventory_exact']) is not bool or value['access_status'] not in ('passed', 'failed', 'unknown')
            or not isinstance(value['remote_cleanup'], dict) or not isinstance(value['remote_cleanup'].get('status'), str)):
        raise PhaseInputError('boundary finalization integrity is malformed')
    broker = pilot._exact_object(value['broker_evidence'], {'path', 'sha256'}, 'broker evidence binding')
    path = pilot._private_regular(Path(broker['path']), 'private broker evidence')
    data = _read(path, 'broker evidence', CAPTURE_CAPACITY['capture_limit'])
    if not path.is_relative_to(base / 'evidence') or pilot._sha(data) != broker['sha256']:
        raise PhaseInputError('broker evidence hash/path binding failed')
    blockers = _broker_blockers(json.loads(data))
    if 'broker_blockers' in value and value['broker_blockers'] != blockers:
        raise PhaseInputError('derived broker blockers changed')
    if value['access_status'] == 'passed' and (not all(value['fixture_integrity'].values()) or value['fixture_inventory_exact'] is not True):
        raise PhaseInputError('boundary cannot declare failed integrity passed')
    return {**value, 'broker_blockers': blockers}


def preflight(root: Path, *, _adapter_factory=None) -> dict:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    if any((root / 'slots').iterdir()):
        raise PhaseInputError('preflight cannot follow any reserved slot')
    readiness = root / 'readiness'
    if readiness.exists() or readiness.is_symlink():
        raise PhaseInputError('readiness is one-shot; failed or successful preflight is retained')
    legacy._new_dir(readiness)
    rows, case = [], None
    try:
        adapter = _adapter(manifest, root, _adapter_factory)
        for case in CASES:
            # Disjoint 1001..1006 IDs qualify all six cases without allocating diagnostic slots.
            slot = next(s for s in manifest['preflight_slots'] if s['case_id'] == case)
            base = readiness / 'cases' / case
            environment = _provision(root, manifest, base)
            with adapter.session(slot, base, environment) as session:
                actual, overrides = _session_settings(session, environment, base)
                try:
                    receipt = _access(session.qualify(), manifest, slot, base, actual, overrides)
                finally:
                    final = _finalized(session.finalize(), manifest, slot, base)
                    legacy._write_new(base / 'evidence/boundary-final.json', pilot._pretty(_sealed(final)))
                if final['access_status'] != 'passed' or final['broker_blockers']:
                    raise PhaseInputError('preflight access or broker lifecycle is not passed')
                legacy._write_new(readiness / f'access-{case}.json', pilot._pretty(receipt))
                rows.append({'case_id': case, 'slot': slot, 'base': str(base),
                             'receipt_sha256': legacy._file_digest(readiness / f'access-{case}.json'),
                             'receipt_digest': receipt['digest'], 'environment': actual,
                             'config_overrides': overrides,
                             'final_sha256': legacy._file_digest(base / 'evidence/boundary-final.json')})
    except BaseException as exc:
        result = _sealed({'schema': READINESS_SCHEMA, 'phase': PHASE, 'manifest_digest': manifest['digest'],
                          'status': 'blocked', 'case_id': case, 'reason_type': type(exc).__name__,
                          'model_launches': 0, 'scheduled_slots_consumed': 0, 'cases': rows})
        legacy._write_new(readiness / 'result.json', pilot._pretty(result))
        raise
    result = _sealed({'schema': READINESS_SCHEMA, 'phase': PHASE, 'manifest_digest': manifest['digest'],
                      'status': 'passed', 'model_launches': 0, 'scheduled_slots_consumed': 0, 'cases': rows})
    legacy._write_new(readiness / 'result.json', pilot._pretty(result))
    return {'schema': READINESS_SCHEMA, 'status': 'passed', 'model_launches': 0, 'scheduled_slots_consumed': 0,
            'cases': [{'case_id': case, 'status': 'passed'} for case in CASES],
            'limitations': ['Named canaries and private broker records are not exhaustive OS attestation.',
                           'Model availability, usage, finality, semantic routing and quality remain unqualified.']}


def load_readiness(root: Path, manifest: dict) -> dict:
    result = _json(root / 'readiness/result.json', 'readiness')
    _digest(result, 'readiness')
    if (result.get('schema') != READINESS_SCHEMA or result.get('phase') != PHASE or result.get('status') != 'passed'
            or result.get('manifest_digest') != manifest['digest'] or type(result.get('model_launches')) is not int
            or result['model_launches'] != 0 or type(result.get('scheduled_slots_consumed')) is not int
            or result['scheduled_slots_consumed'] != 0 or [row.get('case_id') for row in result.get('cases', [])] != list(CASES)):
        raise PhaseInputError('successful unchanged no-model readiness is required')
    for row in result['cases']:
        case, slot, base = row['case_id'], row['slot'], Path(row['base'])
        if slot != next(s for s in manifest['preflight_slots'] if s['case_id'] == case):
            raise PhaseInputError('readiness representative differs from the frozen six-case inventory')
        path = root / 'readiness' / f'access-{case}.json'
        if (base != root / 'readiness/cases' / case or legacy._file_digest(pilot._private_regular(path, 'access receipt')) != row['receipt_sha256']
                or legacy._file_digest(pilot._private_regular(base / 'evidence/boundary-final.json', 'boundary final')) != row['final_sha256']):
            raise PhaseInputError('readiness evidence changed')
        receipt = _access(_json(path, 'readiness access'), manifest, slot, base, row['environment'], row['config_overrides'])
        if receipt['digest'] != row['receipt_digest']:
            raise PhaseInputError('readiness receipt changed')
        final = _json(base / 'evidence/boundary-final.json', 'retained finalization')
        _digest(final, 'retained finalization')
        _finalized({k: v for k, v in final.items() if k != 'digest'}, manifest, slot, base)
    return result


def _approval(root: Path, manifest: dict, path: Path, allow: bool) -> dict:
    path = pilot._private_regular(path, 'actual task approval')
    value = _json(path, 'approval')
    template = approval_template(manifest)
    pilot._exact_object(value, set(template), 'approval')
    if (not allow or stat.S_IMODE(path.stat().st_mode) != 0o600 or value['approved'] is not True
            or any(value[k] != v for k, v in template.items() if k not in ('approved', 'approved_at_utc', 'authorization_note'))
            or any(not isinstance(value[k], str) or not value[k].strip() for k in ('approved_at_utc', 'authorization_note'))):
        raise PhaseInputError('require manifest-bound actual task approval and --allow-model-run')
    return {'sha256': legacy._file_digest(path), 'path': str(path)}


def _load_result(path: Path, manifest: dict, slot: dict) -> dict:
    value = _json(path, 'retained slot result')
    _digest(value, 'slot result')
    if value.get('schema') != RESULT_SCHEMA or value.get('manifest_digest') != manifest['digest'] or value.get('slot') != slot:
        raise PhaseInputError('retained result differs from original slot')
    return value


def _parse_attempt(base: Path, attempt: dict, manifest: dict) -> tuple[dict, dict, dict, dict, list]:
    evidence, config = base / 'evidence', manifest['config']
    attempt_dir = legacy._physical(Path(attempt['attempt_dir']))
    if attempt_dir != evidence / 'attempt':
        raise PhaseInputError('collector evidence escaped private attempt root')
    limits = {k: v for k, v in CAPTURE_CAPACITY.items() if k != 'stream_limit'}
    delivery = _json(attempt_dir / 'delivery.json', 'attempt delivery')
    events = _read(attempt_dir / 'events.jsonl', 'events', CAPTURE_CAPACITY['capture_limit'])
    rollout = _read(attempt_dir / 'rollout.jsonl', 'native rollout', CAPTURE_CAPACITY['capture_limit'])
    process = _read(attempt_dir / 'process.json', 'process receipt')
    answer = _read(attempt_dir / 'answer.txt', 'answer', capture.MAX_ANSWER_BYTES) if (attempt_dir / 'answer.txt').exists() else None
    report = capture.capture_bytes(events, rollout, process, answer,
                                   answer_state='captured' if answer is not None else 'lost', **limits)
    completion = capture.completion_evidence_bytes(events, rollout, answer, **limits)
    profile = manifest['intercepted_patch_profile']
    if profile is None:
        audit = pilot._audit(report, config)
    else:
        import benchmark_issue10_intercepted_patch as patch
        audit = patch.audit(report, config, base / 'scratch', profile=profile)
    for name, value in [('capture-report.json', report), ('completion-evidence.json', completion), ('tool-audit.json', audit)]:
        legacy._write_new(evidence / name, pilot._pretty(value))
    blockers = []
    if report['execution']['execution'] != 'completed':
        blockers.append('model_process_not_completed')
    rows = capture.parse_jsonl(events, 'linux-fixed-model-cli', **limits)['records']
    if any(isinstance(row, dict) and isinstance(row.get('item'), dict) and row['item'].get('type') == 'error' for row in rows):
        blockers.append('cli_error_item')
    contexts = [row.get('payload') for row in capture.parse_jsonl(rollout, 'linux-fixed-model-rollout', **limits)['records']
                if isinstance(row, dict) and row.get('type') == 'turn_context']
    if not contexts or any(not isinstance(row, dict) or row.get('model') != config['model']['id']
                           or row.get('effort') != config['model']['reasoning_effort'] for row in contexts):
        blockers.append('observed_model_context_missing_or_mismatched')
    if delivery.get('rollout_discovery', {}).get('state') == 'invalid':
        blockers.append('rollout_discovery_failed')
    for name in ('rollout', 'answer'):
        if delivery.get(name, {}).get('state') != 'captured':
            blockers.append(f'{name}_capture_lost')
    blockers += sorted({f"capture_issue:{row['source']}:{row['code']}" for row in report['issues'] if row.get('effect') == 'invalid'})
    if report['execution']['capture_overflow']:
        blockers.append('capture_overflow')
    if audit['status'] != 'bounded-recorded':
        blockers.append('unsupported_or_unallowed_tool_record')
    return report, completion, audit, delivery, blockers


def run_slot(root: Path, number: int, approval_path: Path, *, allow_model_run: bool = False,
             _adapter_factory=None, _attempt_collector=None) -> dict:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    load_readiness(root, manifest)
    approval = _approval(root, manifest, approval_path, allow_model_run)
    if type(number) is not int or not 1 <= number <= manifest['session_count']:
        raise PhaseInputError('slot must be an integer in the fixed Linux schedule')
    slot = manifest['slots'][number - 1]
    for prior in manifest['slots'][:number - 1]:
        value = _load_result(root / 'slots' / f"{prior['slot']:03}" / 'result.json', manifest, prior)
        if value['continuation']['allowed'] is not True:
            raise PhaseInputError('retained prior slot blocks later reservations')
    base = root / 'slots' / f'{number:03}'
    if base.exists() or base.is_symlink():
        raise PhaseInputError('slot consumed; no retry, replacement, overwrite or resume')
    legacy._new_dir(base)
    legacy._write_new(base / 'reservation.json', pilot._pretty({'manifest_digest': manifest['digest'],
                         'slot': slot, 'approval_sha256': approval['sha256'], 'one_shot': True}))
    stage = 'reserved-before-fresh-access'
    result = None
    try:
        environment = _provision(root, manifest, base)
        adapter = _adapter(manifest, root, _adapter_factory)
        with adapter.session(slot, base, environment) as session:
            actual, overrides = _session_settings(session, environment, base)
            stage = 'fresh-access-qualification'
            try:
                receipt = _access(session.qualify(), manifest, slot, base, actual, overrides)
                legacy._write_new(base / 'evidence/access-receipt.json', pilot._pretty(receipt))
                source = _read(root / 'prepared/prompts' / f"{slot['case_id']}.md", 'source prompt', collector.MAX_PROMPT_BYTES)
                prompt = render_prompt(source, str(base / 'scratch'), slot['arm'], manifest['sshai']['path'])
                if pilot._sha(prompt) != manifest['slot_material'][str(number)]['rendered_prompt_sha256']:
                    raise PhaseInputError('rendered prompt changed')
                legacy._write_new(base / 'evidence/prompt.txt', prompt)
                argv = pilot._model_argv(manifest, base, {'effective_config_overrides': overrides})
                association = {'manifest_digest': manifest['digest'], 'slot': slot,
                               'approval_sha256': approval['sha256'], 'access_receipt_digest': receipt['digest'],
                               'model': manifest['config']['model'], 'history_mode': 'paginated',
                               'config_sha256': manifest['config_sha256'], 'argv_sha256': pilot._sha(pilot._encoded(argv))}
                stage = 'model-attempt-requested'
                attempt = (_attempt_collector or pilot._collect_local_attempt)(base / 'evidence/attempt', argv,
                    prompt=prompt, env=actual, cwd=base / 'scratch', timeout_seconds=600,
                    codex_home=base / 'codex-home', answer_path=base / 'evidence/last-message.txt',
                    association=association, **CAPTURE_CAPACITY)
                report, completion, audit, delivery, blockers = _parse_attempt(base, attempt, manifest)
            finally:
                final = _finalized(session.finalize(), manifest, slot, base)
                legacy._write_new(base / 'evidence/boundary-final.json', pilot._pretty(_sealed(final)))
        if final['access_status'] != 'passed':
            blockers.append('fixture_access_or_integrity_failure')
        blockers.extend(final['broker_blockers'])
        result = {'schema': RESULT_SCHEMA, 'manifest_digest': manifest['digest'], 'slot': slot,
                  'launch': 'attempted', 'execution': report['execution']['execution'],
                  'usage': {'totals': report['usage']['totals'], 'complete': report['usage']['complete'],
                            'counting_method': report['usage']['counting_method']},
                  'completion_evidence': {'status': completion['status'], 'finality': 'unknown', 'version_bounded': True},
                  'tool_audit': {'status': audit['status'], 'entry_count': audit['entry_count'],
                                 'observation_count': audit['observation_count'], 'unique_tool_call_count': None,
                                 'os_execution_attestation': False, 'semantic_routing': audit['semantic_routing']},
                  'access': {'status': final['access_status'], 'receipt_digest': receipt['digest'],
                             'fixture_integrity': final['fixture_integrity'], 'fixture_inventory_exact': final['fixture_inventory_exact']},
                  'continuation': {'allowed': not blockers, 'blockers': blockers},
                  'experimental_savings_claim_eligible': False}
        result = _sealed(result)
        legacy._write_new(base / 'result.json', pilot._pretty(result))
    except BaseException:
        if result is None and not (base / 'result.json').exists():
            result = pilot._failure_result(slot, stage)
            result.update(schema=RESULT_SCHEMA, manifest_digest=manifest['digest'])
            if stage == 'model-attempt-requested':
                result['launch'] = 'attempted-or-unknown'
            legacy._write_new(base / 'result.json', pilot._pretty(_sealed(result)))
        raise
    return pilot._result_summary(result)


def summarize(root: Path) -> dict:
    root = legacy._physical(Path(root))
    manifest = load_manifest(root)
    rows = []
    for slot in manifest['slots']:
        base = root / 'slots' / f"{slot['slot']:03}"
        path = base / 'result.json'
        if path.exists() or path.is_symlink():
            row = {**pilot._result_summary(_load_result(path, manifest, slot)), 'retryable': False}
        else:
            row = {'slot': slot['slot'], 'case_id': slot['case_id'], 'arm': slot['arm'], 'state': 'unattempted',
                   'auditor_grade': 'unknown', 'independent_model_assessment': 'unknown'}
            if base.exists() or base.is_symlink():
                row.update(state='reserved-incomplete', launch='attempted-or-unknown', execution='unknown', retryable=False,
                           continuation={'allowed': False, 'blockers': ['reserved_slot_missing_result']})
        rows.append(row)
    status = 'not-run'
    path = root / 'readiness/result.json'
    if path.exists() or path.is_symlink():
        value = _json(path, 'readiness result')
        _digest(value, 'readiness result')
        status = value.get('status', 'unknown')
        if status == 'passed':
            load_readiness(root, manifest)
    return {'schema': SUMMARY_SCHEMA, 'phase': PHASE, 'population': manifest['population'],
            'scheduled_sessions': manifest['session_count'], 'budget': manifest['budget'], 'readiness': status,
            'slots': rows, 'raw_outputs_included': False, 'private_paths_included': False,
            'comparative_savings_claim': None, 'experimental_savings_claim_eligible': False}


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    prepare_parser = subs.add_parser('prepare')
    for name in ('root', 'fixture-bundle', 'codex', 'sshai', 'config', 'model-catalog', 'tool-overrides',
                 'auth', 'assessment-instructions', 'assessment-rubric', 'remote-plan'):
        prepare_parser.add_argument('--' + name, type=Path, required=True)
    prepare_parser.add_argument('--population', choices=['pilot', 'measurement'], required=True)
    prepare_parser.add_argument('--pilot-root', type=Path)
    prepare_parser.add_argument('--pilot-review', type=Path)
    prepare_parser.add_argument('--intercepted-patch-profile', choices=['helper-add-2'])
    for command in ('preflight', 'run-slot', 'summary'):
        child = subs.add_parser(command)
        child.add_argument('--root', type=Path, required=True)
        if command == 'run-slot':
            child.add_argument('--slot', type=int, required=True)
            child.add_argument('--approval', type=Path, required=True)
            child.add_argument('--allow-model-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            profile = None
            if args.intercepted_patch_profile:
                import benchmark_issue10_intercepted_patch as patch
                profile = dict(patch.ADD_PROFILE)
            manifest = prepare(args.root, args.fixture_bundle, args.codex, args.sshai, args.config,
                args.model_catalog, args.tool_overrides, args.auth, args.assessment_instructions,
                args.assessment_rubric, args.remote_plan, population=args.population,
                pilot_root=args.pilot_root, pilot_review_path=args.pilot_review,
                intercepted_patch_profile=profile)
            result = {'schema': MANIFEST_SCHEMA, 'manifest_digest': manifest['digest'],
                      'population': manifest['population'], 'scheduled_sessions': manifest['session_count']}
        elif args.command == 'preflight':
            result = preflight(args.root)
        elif args.command == 'run-slot':
            result = run_slot(args.root, args.slot, args.approval, allow_model_run=args.allow_model_run)
        else:
            result = summarize(args.root)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        # Private raw paths/errors remain in retained controller records, not public stdout.
        print(json.dumps({'schema': SUMMARY_SCHEMA, 'status': 'blocked', 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(_main())
