#!/usr/bin/env python3
"""Offline three-series study coordinator. Experimental launch CLI remains disabled.

Preparation/import never probe Codex, sshai, hosts, or credentials. The collect_slot
library API executes an explicit caller-supplied process for collector development;
its receipts and imported records do not establish live capture qualification.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import random
from typing import Any

import benchmark_issue10 as legacy
import benchmark_issue10_v3_capture as capture_adapter
import benchmark_issue10_v3_collector as collector
from benchmark_issue10_v3_cases import build_cases
from prepare_issue10_v3_fixtures import bundle

SCHEMA = 'sshai-benchmark/issue10-v3-offline-1'
SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parent
# One atomic private envelope retains raw bytes, report, binding and projection.
# JSON/base64 and parsed-report expansion need more room than one input stream.
MAX_RECORD_BYTES = 32 * 1_048_576
CAPTURE_PROVENANCE = 'operator-supplied-offline-capture'
COLLECTOR_PROVENANCE = 'collector-supplied-offline-attempt'
COLLECTOR_BINDING_STAGE = 'coordinator-import-after-collection'
RESERVED_BINDING_STAGE = 'coordinator-reserved-before-collection'
RESERVATION_SCHEMA = 'sshai-benchmark/issue10-v3-slot-reservation-1'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + '\n').encode()


def schedule(phase: str, seed: int) -> list[dict[str, Any]]:
    if phase not in ('pilot', 'measurement') or type(seed) is not int:
        raise ValueError('invalid phase or seed')
    rng = random.Random(seed)
    pairs = []
    for series in 'MLW':
        tasks = list(range(1, 7)) if phase == 'measurement' else [1, 2]
        rng.shuffle(tasks)
        for index, task in enumerate(tasks):
            if phase == 'measurement':
                order = ['baseline', 'baseline', 'sshai'] if index < 3 else ['baseline', 'sshai', 'sshai']
                rng.shuffle(order)
            else:
                order = ['baseline' if index == 0 else 'sshai']
            for replicate, first in enumerate(order, 1):
                pair = {'pair_id': f'{series}{task:02}-r{replicate}', 'case_id': f'{series}{task:02}',
                        'series': series, 'task': task, 'replicate': replicate}
                arms = [first, 'sshai' if first == 'baseline' else 'baseline']
                pairs.append([{**pair, 'arm': arm} for arm in arms])
    rng.shuffle(pairs)
    return [{**slot, 'slot': index} for index, slot in enumerate((s for pair in pairs for s in pair), 1)]


def _instruction_block(protocol: str, label: str) -> str:
    # Capture the actual approved draft wording instead of maintaining a second
    # divergent copy. Hash both protocol and extracted prompts in the plan.
    section = protocol.split(label, 1)[1]
    return section.split('```text\n', 1)[1].split('\n```', 1)[0]


def prepare(root: Path, phase: str = 'measurement', seed: int = 1010) -> dict[str, Any]:
    root = legacy._physical(root, must_exist=False)
    slots = schedule(phase, seed)
    cases = build_cases()
    data = bundle(cases)
    protocol_path = REPO / 'docs/benchmarks/issue10-protocol.md'
    protocol = protocol_path.read_text()
    common = _instruction_block(protocol, 'Shared instruction block:')
    branch = {arm: _instruction_block(protocol, f'{arm} block:') for arm in ('Baseline', 'sshai')}
    for case_id, case in cases.items():
        for arm in ('baseline', 'sshai'):
            text = case['prompt'] + '\n' + common + '\n\n' + branch['Baseline' if arm == 'baseline' else 'sshai'] + '\n'
            data[f'planned-prompts/{case_id}-{arm}.md'] = text.encode()
    pins = {}
    for name in ('benchmark_issue10_v3.py', 'benchmark_issue10_v3_analysis.py',
                 'benchmark_issue10_v3_capture.py', 'benchmark_issue10_v3_collector.py',
                 'benchmark_issue10_v3_review.py', 'benchmark_issue10_v3_cases.py',
                 'prepare_issue10_v3_fixtures.py',
                 'benchmark_issue10.py', 'benchmark_issue10_fixtures.py'):
        pins[f'scripts/{name}'] = digest((SOURCE / name).read_bytes())
    for name in ('issue10-protocol.md', 'issue10-analyzer.md'):
        pins[f'docs/benchmarks/{name}'] = digest((REPO / 'docs/benchmarks' / name).read_bytes())
    manifest = {
        'schema': SCHEMA, 'phase': phase, 'slots': slots, 'schedule_seed': seed,
        'random_generator': 'Python random.Random / MT19937; ordered schedule itself is authoritative',
        'analysis': {'bootstrap_seed': 1010, 'bootstrap_resamples': 10000,
                     'percentile': 'linear interpolation at (n-1)*p'},
        'model_candidate': 'gpt-5.6-sol', 'reasoning_effort_candidate': 'high',
        'timeout_seconds': 600, 'launch_enabled': False, 'experimental_claim_eligible': False,
        'qualification': {'model': False, 'capture': False, 'access': False,
                          'native_formats': False, 'independent_human_calibration': False},
        'unresolved': ['actual model and CLI/binary pins', 'hosts, shells, directories and access',
                       'render deployment placeholders', 'qualified capture and tool-call audit',
                       'protocol freeze and phase-specific launch approval'],
        'accepted_smaller_cases': ['M05', 'M06', 'L05', 'L06', 'W06'],
        'sources': pins,
        'files': {name: digest(body) for name, body in sorted(data.items())},
    }
    manifest['digest'] = legacy._manifest_digest(manifest)
    legacy._new_dir(root)
    for name, body in sorted(data.items()):
        legacy._write_new(root / 'prepared' / name, body)
    legacy._write_new(root / 'plan.json', encoded(manifest))
    legacy._mkdir_private(root / 'records')
    legacy._mkdir_private(root / 'reviews')
    return manifest


def load_plan(root: Path) -> dict[str, Any]:
    root = legacy._physical(root)
    manifest = legacy._read_json(root / 'plan.json')
    if manifest.get('schema') != SCHEMA or manifest.get('digest') != legacy._manifest_digest(manifest):
        raise ValueError('plan schema or digest mismatch')
    if manifest.get('launch_enabled') is not False:
        raise ValueError('offline plan cannot enable launch')
    expected = schedule(manifest['phase'], manifest['schedule_seed'])
    if manifest['slots'] != expected:
        raise ValueError('schedule differs from pinned seed/phase')
    for name, sha in manifest['sources'].items():
        # Only coordinator-authored plans are supported; do not allow arbitrary
        # plan paths to turn validation into file disclosure.
        if not name.startswith(('scripts/', 'docs/benchmarks/')) or '..' in Path(name).parts:
            raise ValueError('invalid source pin path')
        if digest((REPO / name).read_bytes()) != sha:
            raise ValueError(f'source changed since preparation: {name}')
    for name, sha in manifest['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('invalid prepared path')
        if legacy._file_digest(root / 'prepared' / name) != sha:
            raise ValueError(f'prepared content changed: {name}')
    return manifest


def _slot(manifest: dict[str, Any], number: int) -> dict[str, Any]:
    if type(number) is not int:
        raise ValueError('slot must be an integer')
    for slot in manifest['slots']:
        if slot['slot'] == number:
            return slot
    raise ValueError('slot is not scheduled')


@contextmanager
def _record_write_lock(root: Path):
    """Fail-fast local serialization of reservation/result publication, not a process lock."""
    lock = legacy._physical(root) / '.record-write-lock'
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError('record publication is busy or an interrupted write left a lock') from None
    try:
        yield
    finally:
        lock.rmdir()


def _reservation_path(root: Path, number: int) -> Path:
    return root / 'reservations' / f'{number:03}.json'


def _load_reservation(root: Path, manifest: dict[str, Any], number: int) -> dict[str, Any] | None:
    path = _reservation_path(root, number)
    if not path.exists() and not path.is_symlink():
        return None
    value = _receipt_object(legacy._read_bounded(legacy._physical(path), capture_adapter.MAX_CAPTURE_BYTES),
                            'reservation', canonical=encoded)
    _exact_fields(value, {'schema', 'plan_digest', 'slot', 'attempt_path', 'request'}, 'reservation')
    if (value['schema'] != RESERVATION_SCHEMA or value['plan_digest'] != manifest['digest']
            or encoded(value['slot']) != encoded(_slot(manifest, number))
            or value['attempt_path'] != f'attempts/{number:03}'):
        raise ValueError('reservation plan/slot binding mismatch')
    _validate_attempt_receipt(value['request'])
    if value['request']['schema'] != collector.ATTEMPT_SCHEMA:
        raise ValueError('reservation requires an unassociated request')
    return value


def _publish_record(root: Path, number: int, data: bytes, *, reservation=None) -> None:
    # Importers cannot race each other or a reservation into claiming the same slot.
    with _record_write_lock(root):
        manifest = load_plan(root)
        current = _load_reservation(root, manifest, number)
        if current != reservation:
            raise ValueError('slot reservation requires its associated collector attempt')
        session = json.loads(data)['record']['session_id']
        for path in (root / 'records').glob('*.json'):
            if _read_envelope(path)['record']['session_id'] == session:
                raise ValueError('session_id already used by an imported slot')
        legacy._write_new(root / 'records' / f'{number:03}.json', data)


def collect_slot(root: Path, number: int, argv, *, prompt: bytes, env, cwd: Path,
                 timeout_seconds: float, rollout_candidates=(), answer_path: Path | None = None):
    """Development API: reserve once, then collect the explicit caller-supplied process.

    No model/access qualification or launch CLI is provided. A reservation survives
    any subsequent failure; import completed evidence separately, never rerun the slot.
    """
    root = legacy._physical(root)
    manifest = load_plan(root)
    _require_collector_pin(manifest)
    slot = _slot(manifest, number)
    attempt = root / 'attempts' / f'{number:03}'
    (output, command, environment, work, timeout, candidates, answer) = collector._validate_request(
        attempt, argv, prompt, env, cwd, timeout_seconds, rollout_candidates, answer_path)
    request = collector._request_receipt(command, prompt, environment, work, timeout, candidates, answer)
    reservation = {'schema': RESERVATION_SCHEMA, 'plan_digest': manifest['digest'], 'slot': slot,
                   'attempt_path': f'attempts/{number:03}', 'request': request}
    with _record_write_lock(root):
        record_path = root / 'records' / f'{number:03}.json'
        if record_path.exists() or record_path.is_symlink() or output.exists():
            raise ValueError('slot already has a result or attempt')
        legacy._write_new(_reservation_path(root, number), encoded(reservation))
    association = {'plan_digest': manifest['digest'], 'slot': slot,
                   'reservation_sha256': digest(encoded(reservation))}
    return collector.collect_attempt(output, command, prompt=prompt, env=environment, cwd=work,
                                     timeout_seconds=timeout, rollout_candidates=candidates,
                                     answer_path=answer, association=association)


def _validate_record(record: dict[str, Any]) -> None:
    if not isinstance(record, dict):
        raise ValueError('result must be an object')
    required = {'session_id', 'execution', 'answer_state', 'final_answer', 'usage',
                'usage_complete', 'instrumentation', 'boundary'}
    if not required <= set(record) or not set(record) <= required | {'slot', 'review'}:
        raise ValueError('result fields differ from the offline record contract')
    if 'slot' in record and type(record['slot']) is not int:
        raise ValueError('result slot must be an integer')
    if not isinstance(record.get('session_id'), str) or not record['session_id'].strip():
        raise ValueError('result requires a session_id')
    if record.get('execution') not in ('completed', 'timeout', 'failed'):
        raise ValueError('invalid execution outcome')
    if record.get('answer_state') not in ('captured', 'absent', 'lost'):
        raise ValueError('invalid answer state')
    final = record.get('final_answer')
    if record['answer_state'] == 'captured':
        if not isinstance(final, str) or not final.strip():
            raise ValueError('captured final answer required')
    elif final is not None:
        raise ValueError('absent/lost answer cannot contain a final answer')
    if record.get('instrumentation') not in ('valid', 'invalid', 'unknown'):
        raise ValueError('invalid instrumentation outcome')
    if record.get('boundary') not in ('compliant', 'violation', 'unknown'):
        raise ValueError('invalid boundary outcome')
    if type(record.get('usage_complete')) is not bool:
        raise ValueError('usage_complete must be boolean')
    usage = record.get('usage')
    if usage is not None:
        if not isinstance(usage, dict) or set(usage) != {'input_tokens', 'cached_input_tokens', 'output_tokens'}:
            raise ValueError('usage must contain exactly the three supported token counters')
        for field in ('input_tokens', 'cached_input_tokens', 'output_tokens'):
            value = usage.get(field)
            if type(value) is not int or value < 0:
                raise ValueError(f'invalid usage {field}')
        if usage['cached_input_tokens'] > usage['input_tokens']:
            raise ValueError('cached input exceeds total input')
    if record['usage_complete'] and usage is None:
        raise ValueError('complete usage cannot be missing')
    if record.get('review') is not None:
        raise ValueError('import review separately using record-review')


def import_result(root: Path, number: int, record: dict[str, Any]) -> None:
    manifest = load_plan(root)
    _slot(manifest, number)
    _validate_record(record)
    if 'slot' in record and record['slot'] != number:
        raise ValueError('import slot mismatch')
    payload = {**record, 'slot': number, 'review': None}
    envelope = {'plan_digest': manifest['digest'], 'provenance': 'operator-supplied-offline-record',
                'record': payload, 'record_sha256': digest(encoded(payload))}
    _publish_record(root, number, encoded(envelope))


def _read_envelope(path: Path) -> dict[str, Any]:
    legacy._physical(path)
    return json.loads(legacy._read_bounded(path, MAX_RECORD_BYTES))


def _capture_projection(report: dict[str, Any], manifest: dict[str, Any], number: int) -> dict[str, Any]:
    # Keep malformed/missing identity evidence in its attempted slot. This is a
    # local record key, explicitly not a claimed Codex session identity.
    if report['session_id'] is None:
        report = {**report, 'session_id': f"unidentified-capture:{manifest['digest']}:{number}"}
    return capture_adapter.coordinator_record(report)


def import_capture(root: Path, number: int, cli_data: bytes, rollout_data: bytes,
                   process_data: bytes, answer_data: bytes | None = None, *,
                   answer_state: str | None = None) -> None:
    """Publish supplied offline bytes and their derived record in one new file."""
    manifest = load_plan(root)
    slot = _slot(manifest, number)
    raw = {'events': cli_data, 'rollout': rollout_data, 'process': process_data,
           'answer': answer_data}
    for body in raw.values():
        if body is not None and (not isinstance(body, bytes) or len(body) > capture_adapter.MAX_CAPTURE_BYTES):
            raise ValueError('capture inputs must be bounded bytes')
    if any(raw[key] is None for key in ('events', 'rollout', 'process')):
        raise ValueError('required capture bytes missing')
    report = capture_adapter.capture_bytes(cli_data, rollout_data, process_data,
                                           answer_data, answer_state=answer_state)
    record = {**_capture_projection(report, manifest, number), 'slot': number, 'review': None}
    _validate_record(record)
    evidence = {
        'plan_digest': manifest['digest'], 'slot': slot,
        'answer_state_option': answer_state,
        'identity_origin': 'matching-streams' if report['session_id'] else 'local-slot-key',
        'raw': {key: None if body is None else {
            'base64': base64.b64encode(body).decode('ascii'),
            'bytes': len(body), 'sha256': digest(body)} for key, body in raw.items()},
        'report': report,
    }
    envelope = {'plan_digest': manifest['digest'], 'provenance': CAPTURE_PROVENANCE,
                'record': record, 'record_sha256': digest(encoded(record)),
                'capture': evidence, 'capture_sha256': digest(encoded(evidence))}
    data = encoded(envelope)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError('capture envelope exceeds storage bound')
    _publish_record(root, number, data)


def _receipt_object(data: bytes, label: str, *, canonical=legacy._canon) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f'duplicate key in collector {label}: {key}')
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ValueError(f'non-JSON constant in collector {label}: {value}')

    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=unique,
                           parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'invalid collector {label} receipt') from exc
    if not isinstance(value, dict) or data != canonical(value):
        raise ValueError(f'collector {label} receipt is not a canonical object')
    return value


def _exact_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'collector {label} fields differ from schema')
    return value


def _sha256_text(value: Any, label: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in '0123456789abcdef' for character in value)):
        raise ValueError(f'invalid collector {label} SHA-256')
    return value


def _bounded_integer(value: Any, label: str, maximum: int, *, minimum: int = 0) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'invalid collector {label}')
    return value


def _validate_attempt_receipt(value: dict[str, Any]) -> None:
    fields = {
        'schema', 'argv_sha256', 'prompt_bytes', 'prompt_sha256',
        'environment_sha256', 'cwd', 'timeout_seconds', 'limits',
        'rollout_candidates', 'answer_path',
    }
    if isinstance(value, dict) and value.get('schema') == collector.ASSOCIATED_ATTEMPT_SCHEMA:
        fields.add('association')
    _exact_fields(value, fields, 'attempt')
    if value['schema'] not in (collector.ATTEMPT_SCHEMA, collector.ASSOCIATED_ATTEMPT_SCHEMA):
        raise ValueError('unsupported collector attempt schema')
    if 'association' in value:
        association = _exact_fields(value['association'], {'plan_digest', 'slot', 'reservation_sha256'},
                                    'attempt association')
        for field in ('plan_digest', 'reservation_sha256'):
            _sha256_text(association[field], f'association {field}')
        if not isinstance(association['slot'], dict):
            raise ValueError('invalid collector association slot')
    for field in ('argv_sha256', 'prompt_sha256', 'environment_sha256'):
        _sha256_text(value[field], f'attempt {field}')
    _bounded_integer(value['prompt_bytes'], 'prompt byte count', collector.MAX_PROMPT_BYTES)
    timeout = value['timeout_seconds']
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('invalid collector timeout')
    if not isinstance(value['cwd'], str) or not Path(value['cwd']).is_absolute():
        raise ValueError('invalid collector working directory')
    expected_limits = {
        'stdout_bytes': collector.MAX_STREAM_BYTES,
        'stderr_bytes': collector.MAX_STREAM_BYTES,
        'prompt_bytes': collector.MAX_PROMPT_BYTES,
        'rollout_candidate_bytes_each': capture_adapter.MAX_CAPTURE_BYTES,
        'answer_bytes': capture_adapter.MAX_ANSWER_BYTES,
        'rollout_candidate_count': collector.MAX_ROLLOUT_CANDIDATES,
    }
    if value['limits'] != expected_limits:
        raise ValueError('collector limits differ from supported bounds')
    candidates = value['rollout_candidates']
    if (not isinstance(candidates, list) or len(candidates) > collector.MAX_ROLLOUT_CANDIDATES
            or any(not isinstance(path, str) or not path or not Path(path).is_absolute()
                   for path in candidates) or len(set(candidates)) != len(candidates)):
        raise ValueError('invalid collector rollout candidate inventory')
    answer = value['answer_path']
    if answer is not None and (not isinstance(answer, str) or not Path(answer).is_absolute()):
        raise ValueError('invalid collector answer path')
    if answer is not None and answer in candidates:
        raise ValueError('collector answer and rollout paths overlap')


def _validate_process_receipt(value: dict[str, Any]) -> None:
    fields = {
        'schema', 'exit_code', 'timed_out', 'capture_overflow', 'interrupted',
        'start_error', 'duration_seconds', 'pid', 'execution', 'stdout_bytes',
        'stderr_bytes', 'stdout_limit_reached', 'stderr_limit_reached',
    }
    _exact_fields(value, fields, 'process')
    if value['schema'] != collector.PROCESS_SCHEMA:
        raise ValueError('unsupported collector process schema')
    for field in ('timed_out', 'capture_overflow', 'interrupted',
                  'stdout_limit_reached', 'stderr_limit_reached'):
        if type(value[field]) is not bool:
            raise ValueError(f'invalid collector process {field}')
    if value['exit_code'] is not None and type(value['exit_code']) is not int:
        raise ValueError('invalid collector process exit code')
    if value['pid'] is not None and (type(value['pid']) is not int or value['pid'] <= 0):
        raise ValueError('invalid collector process pid')
    if value['pid'] is None and value['exit_code'] is not None:
        raise ValueError('unstarted collector process has an exit code')
    if value['pid'] is None and value['start_error'] is None and not value['interrupted']:
        raise ValueError('unstarted collector process lacks failure evidence')
    if value['start_error'] is not None and not isinstance(value['start_error'], str):
        raise ValueError('invalid collector process start error')
    duration = value['duration_seconds']
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration < 0):
        raise ValueError('invalid collector process duration')
    for stream in ('stdout', 'stderr'):
        count = _bounded_integer(value[f'{stream}_bytes'], f'{stream} byte count',
                                 collector.MAX_STREAM_BYTES)
        if value[f'{stream}_limit_reached'] != (count == collector.MAX_STREAM_BYTES):
            raise ValueError(f'collector {stream} limit receipt mismatch')
    if (value['capture_overflow'] and not value['stdout_limit_reached']
            and not value['stderr_limit_reached']):
        raise ValueError('collector overflow lacks a stream at its limit')
    expected = collector._execution(value)
    if value['execution'] != expected:
        raise ValueError('collector process execution is inconsistent')


def _collector_referenced_paths(delivery_data: bytes) -> set[str]:
    delivery = _receipt_object(delivery_data, 'delivery')
    _exact_fields(delivery, {'schema', 'process_execution', 'rollout', 'answer'}, 'delivery')
    rollout = delivery['rollout']
    if not isinstance(rollout, dict) or not isinstance(rollout.get('candidates'), list):
        raise ValueError('invalid collector delivery rollout')
    if len(rollout['candidates']) > collector.MAX_ROLLOUT_CANDIDATES:
        raise ValueError('too many collector delivery candidates')
    referenced: set[str] = set()
    for item in rollout['candidates']:
        if not isinstance(item, dict):
            raise ValueError('invalid collector candidate observation')
        retained = item.get('retained')
        if retained is not None:
            index = item.get('index')
            expected = (f'rollout-candidates/{index:03}.jsonl'
                        if type(index) is int and 1 <= index <= collector.MAX_ROLLOUT_CANDIDATES
                        else None)
            if retained != expected or Path(retained).is_absolute() or '..' in Path(retained).parts:
                raise ValueError('unsafe collector retained candidate path')
            referenced.add(retained)
    answer = delivery['answer']
    if not isinstance(answer, dict):
        raise ValueError('invalid collector delivery answer')
    if answer.get('retained') is not None:
        if answer['retained'] != 'answer.txt':
            raise ValueError('unsafe collector retained answer path')
        referenced.add('answer.txt')
    return referenced


def _collector_limit(relative: str) -> int:
    if relative in ('events.jsonl', 'stderr.txt'):
        return collector.MAX_STREAM_BYTES
    if relative == 'answer.txt':
        return capture_adapter.MAX_ANSWER_BYTES
    return capture_adapter.MAX_CAPTURE_BYTES


def _read_collector_attempt(attempt_dir: Path) -> dict[str, bytes]:
    attempt = legacy._physical(Path(attempt_dir))
    if not attempt.is_dir():
        raise ValueError('collector attempt path is not a directory')
    mandatory = {
        'attempt.json', 'process.json', 'delivery.json',
        'events.jsonl', 'stderr.txt', 'rollout.jsonl',
    }
    files: dict[str, bytes] = {}

    def read_relative(relative: str) -> bytes:
        # Every path is a literal collector output name or a validated retained
        # candidate name. Receipt source paths are deliberately never read.
        path = legacy._physical(attempt / relative)
        if path.parent != attempt and path.parent.parent != attempt:
            raise ValueError('collector evidence path escapes attempt directory')
        return legacy._read_bounded(path, _collector_limit(relative))

    for relative in sorted(mandatory):
        files[relative] = read_relative(relative)
    for relative in sorted(_collector_referenced_paths(files['delivery.json'])):
        if relative in files:
            raise ValueError('duplicate collector evidence path')
        files[relative] = read_relative(relative)
    return files


def _validate_collector_files(files: dict[str, bytes]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    mandatory = {
        'attempt.json', 'process.json', 'delivery.json',
        'events.jsonl', 'stderr.txt', 'rollout.jsonl',
    }
    if not isinstance(files, dict) or not mandatory <= set(files):
        raise ValueError('collector attempt is incomplete')
    attempt = _receipt_object(files['attempt.json'], 'attempt')
    process = _receipt_object(files['process.json'], 'process')
    delivery = _receipt_object(files['delivery.json'], 'delivery')
    _validate_attempt_receipt(attempt)
    _validate_process_receipt(process)
    _exact_fields(delivery, {'schema', 'process_execution', 'rollout', 'answer'}, 'delivery')
    if delivery['schema'] != collector.DELIVERY_SCHEMA:
        raise ValueError('unsupported collector delivery schema')
    if delivery['process_execution'] != process['execution']:
        raise ValueError('collector process/delivery execution mismatch')
    if len(files['events.jsonl']) != process['stdout_bytes']:
        raise ValueError('collector events byte count mismatch')
    if len(files['stderr.txt']) != process['stderr_bytes']:
        raise ValueError('collector stderr byte count mismatch')

    process_started = process['pid'] is not None
    cli_id, cli_problem = collector._cli_identity(files['events.jsonl'])
    rollout = _exact_fields(delivery['rollout'], {
        'state', 'reason', 'cli_thread_id', 'selected_candidate',
        'bytes', 'sha256', 'candidates',
    }, 'delivery rollout')
    if rollout['cli_thread_id'] != cli_id:
        raise ValueError('collector CLI identity receipt mismatch')
    observations = rollout['candidates']
    if not isinstance(observations, list) or len(observations) != len(attempt['rollout_candidates']):
        raise ValueError('collector candidate count mismatch')
    expected_files = set(mandatory)
    matching: list[int] = []
    statuses: list[str] = []
    candidate_bodies: dict[int, bytes] = {}
    for index, (source, observation) in enumerate(
            zip(attempt['rollout_candidates'], observations), 1):
        if not isinstance(observation, dict):
            raise ValueError('invalid collector candidate observation')
        if observation.get('index') != index or observation.get('source') != source:
            raise ValueError('collector candidate order/source mismatch')
        status = observation.get('status')
        if not process_started:
            _exact_fields(observation, {'index', 'source', 'status'}, 'unstarted candidate')
            if status != 'not_read_process_not_started':
                raise ValueError('unstarted collector candidate was claimed read')
            statuses.append(status)
            continue
        if status == 'input_error':
            _exact_fields(observation, {'index', 'source', 'status', 'error'},
                          'candidate input error')
            if not isinstance(observation['error'], str) or not observation['error']:
                raise ValueError('collector candidate input error lacks detail')
            statuses.append(status)
            continue
        retained = f'rollout-candidates/{index:03}.jsonl'
        expected_files.add(retained)
        body = files.get(retained)
        if body is None:
            raise ValueError('collector retained candidate is missing')
        candidate_bodies[index] = body
        identity, parsed_status, issue_codes = collector._rollout_identity(body)
        expected_status = parsed_status
        expected_match: bool | None = None
        fields = {
            'index', 'source', 'status', 'thread_id', 'bytes', 'sha256',
            'retained', 'parser_issue_codes',
        }
        if parsed_status == 'usable':
            fields.add('identity_match')
            expected_match = cli_id is not None and identity == cli_id
            if cli_id is not None and not expected_match:
                expected_status = 'mismatched_identity'
        _exact_fields(observation, fields, 'retained candidate')
        _bounded_integer(observation['bytes'], 'candidate byte count',
                         capture_adapter.MAX_CAPTURE_BYTES)
        if (observation['retained'] != retained or observation['thread_id'] != identity
                or observation['parser_issue_codes'] != issue_codes
                or observation['status'] != expected_status
                or observation['bytes'] != len(body)
                or observation['sha256'] != digest(body)):
            raise ValueError('collector retained candidate receipt mismatch')
        if (expected_match is not None
                and (type(observation['identity_match']) is not bool
                     or observation['identity_match'] != expected_match)):
            raise ValueError('collector candidate identity selection mismatch')
        if expected_match:
            matching.append(index)
        statuses.append(expected_status)

    unresolved = any(status not in {'usable', 'mismatched_identity'} for status in statuses)
    selected = matching[0] if len(matching) == 1 and not unresolved else None
    if not process_started:
        reason = 'process_not_started'
    elif cli_problem is not None:
        reason = cli_problem
    elif len(matching) > 1:
        reason = 'ambiguous_matching_candidates'
    elif unresolved:
        reason = 'unresolved_candidate_identity'
    elif selected is None:
        reason = 'no_candidate_matched_cli_thread_identity'
    else:
        reason = 'matched_cli_thread_identity'
    selected_body = candidate_bodies[selected] if selected is not None else b''
    _bounded_integer(rollout['bytes'], 'selected rollout byte count',
                     capture_adapter.MAX_CAPTURE_BYTES)
    if (rollout['selected_candidate'] != selected
            or (rollout['selected_candidate'] is not None
                and type(rollout['selected_candidate']) is not int)
            or rollout['state'] != ('captured' if selected is not None else 'lost')
            or rollout['reason'] != reason or rollout['bytes'] != len(selected_body)
            or rollout['sha256'] != digest(selected_body)
            or files['rollout.jsonl'] != selected_body):
        raise ValueError('collector rollout selection receipt mismatch')

    answer = delivery['answer']
    common = {'state', 'reason', 'source', 'finality', 'finality_reason'}
    source = attempt['answer_path']
    if not isinstance(answer, dict) or answer.get('source') != source:
        raise ValueError('collector answer source mismatch')
    if (answer.get('finality') != 'unknown'
            or answer.get('finality_reason') != 'no_qualified_final_answer_evidence'):
        raise ValueError('unsupported collector answer finality claim')
    if not process_started:
        _exact_fields(answer, common, 'unstarted answer')
        if answer['state'] != 'lost' or answer['reason'] != 'process_not_started':
            raise ValueError('invalid unstarted collector answer receipt')
    elif source is None:
        _exact_fields(answer, common, 'missing answer path')
        if answer['state'] != 'lost' or answer['reason'] != 'no_explicit_answer_path':
            raise ValueError('invalid missing collector answer path receipt')
    elif answer.get('reason') == 'answer_input_error':
        _exact_fields(answer, common | {'error'}, 'answer input error')
        if answer['state'] != 'lost' or not isinstance(answer['error'], str) or not answer['error']:
            raise ValueError('invalid collector answer input error receipt')
    elif answer.get('reason') == 'empty_answer_file':
        _exact_fields(answer, common | {'bytes', 'sha256'}, 'empty answer')
        _bounded_integer(answer['bytes'], 'empty answer byte count',
                         capture_adapter.MAX_ANSWER_BYTES)
        if (answer['state'] != 'lost' or answer['bytes'] != 0
                or answer['sha256'] != digest(b'')):
            raise ValueError('invalid empty collector answer receipt')
    elif answer.get('reason') == 'nonempty_explicit_answer_file':
        _exact_fields(answer, common | {'bytes', 'sha256', 'retained'}, 'retained answer')
        expected_files.add('answer.txt')
        body = files.get('answer.txt')
        _bounded_integer(answer['bytes'], 'retained answer byte count',
                         capture_adapter.MAX_ANSWER_BYTES, minimum=1)
        if (answer['state'] != 'captured' or answer['retained'] != 'answer.txt'
                or body is None or not body or answer['bytes'] != len(body)
                or answer['sha256'] != digest(body)):
            raise ValueError('collector retained answer receipt mismatch')
    else:
        raise ValueError('unsupported collector answer delivery reason')
    if set(files) != expected_files:
        raise ValueError('collector retained file inventory mismatch')

    report = capture_adapter.capture_bytes(
        files['events.jsonl'], files['rollout.jsonl'], files['process.json'],
        None, answer_state='lost',
    )
    # Compare retained bytes without supplying them as an explicit final answer.
    # A source-shaped match is evidence for qualification, not qualification.
    report['answer_completion'] = capture_adapter.completion_evidence_bytes(
        files['events.jsonl'], files['rollout.jsonl'], files.get('answer.txt'),
    )
    return {'attempt': attempt, 'process': process, 'delivery': delivery}, report


def _blob(data: bytes) -> dict[str, Any]:
    return {'base64': base64.b64encode(data).decode('ascii'),
            'bytes': len(data), 'sha256': digest(data)}


def _collector_files_from_evidence(value: Any) -> dict[str, bytes]:
    if not isinstance(value, dict):
        raise ValueError('collector retained files must be an object')
    files: dict[str, bytes] = {}
    for relative, item in value.items():
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or '..' in Path(relative).parts):
            raise ValueError('unsafe collector evidence filename')
        _exact_fields(item, {'base64', 'bytes', 'sha256'}, 'retained file')
        if type(item['bytes']) is not int or item['bytes'] < 0:
            raise ValueError('invalid collector retained byte count')
        _sha256_text(item['sha256'], 'retained file')
        try:
            body = base64.b64decode(item['base64'], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid collector retained base64') from exc
        if (len(body) > _collector_limit(relative) or len(body) != item['bytes']
                or digest(body) != item['sha256']):
            raise ValueError('collector retained file hash/size mismatch')
        files[relative] = body
    return files


def _require_collector_pin(manifest: dict[str, Any]) -> None:
    name = 'scripts/benchmark_issue10_v3_collector.py'
    if manifest.get('sources', {}).get(name) != digest((REPO / name).read_bytes()):
        raise ValueError('plan does not pin the supported collector source')


def _collector_binding(root: Path, manifest: dict[str, Any], number: int, attempt: dict[str, Any]):
    binding = {'plan_digest': manifest['digest'], 'slot': _slot(manifest, number),
               'stage': COLLECTOR_BINDING_STAGE, 'pre_spawn_attestation': False}
    reservation = _load_reservation(root, manifest, number)
    association = attempt.get('association')
    if reservation is None:
        if association is not None:
            raise ValueError('associated collector attempt has no slot reservation')
        return binding
    expected = {'plan_digest': manifest['digest'], 'slot': _slot(manifest, number),
                'reservation_sha256': digest(encoded(reservation))}
    request = {key: value for key, value in attempt.items() if key != 'association'}
    request['schema'] = collector.ATTEMPT_SCHEMA
    if encoded(association) != encoded(expected) or encoded(request) != encoded(reservation['request']):
        raise ValueError('collector attempt does not match reserved request/association')
    return {**binding, 'stage': RESERVED_BINDING_STAGE, 'reservation': reservation}


def import_collector(root: Path, number: int, attempt_dir: Path) -> None:
    """Import a completed collector attempt without treating it as final-answer proof."""
    manifest = load_plan(root)
    _require_collector_pin(manifest)
    _slot(manifest, number)
    files = _read_collector_attempt(attempt_dir)
    receipts, report = _validate_collector_files(files)
    binding = _collector_binding(root, manifest, number, receipts['attempt'])
    if ('reservation' in binding and legacy._physical(attempt_dir)
            != legacy._physical(root / binding['reservation']['attempt_path'])):
        raise ValueError('collector attempt is not at its reserved path')
    record = {**_capture_projection(report, manifest, number), 'slot': number, 'review': None}
    _validate_record(record)
    identity_origin = 'matching-streams' if report['session_id'] else 'local-slot-key'
    evidence = {
        'binding': binding,
        'receipt_authenticity': 'not-attested',
        'files': {name: _blob(body) for name, body in sorted(files.items())},
        'receipts': receipts,
        'report': report,
        'identity_origin': identity_origin,
        'final_answer_policy': {
            'adapter_answer_state': 'lost', 'answer_bytes_supplied': False,
            'reason': 'collector_finality_unknown',
        },
    }
    envelope = {
        'plan_digest': manifest['digest'], 'provenance': COLLECTOR_PROVENANCE,
        'record': record, 'record_sha256': digest(encoded(record)),
        'collector': evidence, 'collector_sha256': digest(encoded(evidence)),
    }
    data = encoded(envelope)
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError('collector envelope exceeds storage bound')
    _publish_record(root, number, data, reservation=binding.get('reservation'))


def _verify_collector(envelope: dict[str, Any], manifest: dict[str, Any], number: int, root: Path) -> None:
    _require_collector_pin(manifest)
    _exact_fields(envelope, {
        'plan_digest', 'provenance', 'record', 'record_sha256',
        'collector', 'collector_sha256',
    }, 'collector envelope')
    evidence = envelope['collector']
    _exact_fields(evidence, {
        'binding', 'receipt_authenticity', 'files', 'receipts', 'report',
        'identity_origin', 'final_answer_policy',
    }, 'collector evidence')
    if envelope['collector_sha256'] != digest(encoded(evidence)):
        raise ValueError('collector evidence hash mismatch')
    if evidence.get('receipt_authenticity') != 'not-attested':
        raise ValueError('collector receipt authenticity was upgraded')
    files = _collector_files_from_evidence(evidence.get('files'))
    receipts, report = _validate_collector_files(files)
    if evidence.get('binding') != _collector_binding(root, manifest, number, receipts['attempt']):
        raise ValueError('collector plan/slot binding mismatch')
    if evidence.get('receipts') != receipts or evidence.get('report') != report:
        raise ValueError('collector report/receipts differ from retained bytes')
    expected_policy = {
        'adapter_answer_state': 'lost', 'answer_bytes_supplied': False,
        'reason': 'collector_finality_unknown',
    }
    if evidence.get('final_answer_policy') != expected_policy:
        raise ValueError('collector final-answer policy mismatch')
    origin = 'matching-streams' if report['session_id'] else 'local-slot-key'
    if evidence.get('identity_origin') != origin:
        raise ValueError('collector identity origin mismatch')
    expected = {**_capture_projection(report, manifest, number),
                'slot': number, 'review': None}
    if envelope['record'] != expected:
        raise ValueError('collector projection mismatch')


def _verify_capture(envelope: dict[str, Any], manifest: dict[str, Any], number: int) -> None:
    evidence = envelope['capture']
    if envelope['capture_sha256'] != digest(encoded(evidence)):
        raise ValueError('capture hash mismatch')
    if evidence['plan_digest'] != manifest['digest'] or evidence['slot'] != _slot(manifest, number):
        raise ValueError('capture plan/slot binding mismatch')
    if set(evidence['raw']) != {'events', 'rollout', 'process', 'answer'}:
        raise ValueError('capture input inventory mismatch')
    raw = {}
    for key, item in evidence['raw'].items():
        if item is None:
            if key != 'answer':
                raise ValueError('required raw evidence missing')
            raw[key] = None
            continue
        body = base64.b64decode(item['base64'], validate=True)
        if (len(body) > capture_adapter.MAX_CAPTURE_BYTES or len(body) != item['bytes']
                or digest(body) != item['sha256']):
            raise ValueError('raw evidence hash/size mismatch')
        raw[key] = body
    report = capture_adapter.capture_bytes(raw['events'], raw['rollout'], raw['process'],
                                           raw['answer'], answer_state=evidence['answer_state_option'])
    origin = 'matching-streams' if report['session_id'] else 'local-slot-key'
    if report != evidence['report'] or evidence['identity_origin'] != origin:
        raise ValueError('capture report differs from retained bytes')
    expected = {**_capture_projection(report, manifest, number), 'slot': number, 'review': None}
    if envelope['record'] != expected:
        raise ValueError('capture projection mismatch')


def _read_record(root: Path, number: int, manifest: dict[str, Any]) -> dict[str, Any]:
    envelope = _read_envelope(root / 'records' / f'{number:03}.json')
    if (envelope.get('provenance') != COLLECTOR_PROVENANCE
            and _load_reservation(root, manifest, number) is not None):
        raise ValueError('reserved slot has an unrelated imported record')
    if envelope.get('provenance') == CAPTURE_PROVENANCE:
        _verify_capture(envelope, manifest, number)
    elif envelope.get('provenance') == COLLECTOR_PROVENANCE:
        _verify_collector(envelope, manifest, number, root)
    elif (envelope.get('provenance') != 'operator-supplied-offline-record'
          or any(field in envelope for field in (
              'capture', 'capture_sha256', 'collector', 'collector_sha256'))):
        raise ValueError('unknown record provenance')
    record = envelope['record']
    if envelope['plan_digest'] != manifest['digest'] or envelope['record_sha256'] != digest(encoded(record)):
        raise ValueError('record provenance/hash mismatch')
    if record['slot'] != number:
        raise ValueError('record slot mismatch')
    _validate_record(record)
    return record


def record_review(root: Path, number: int, review: dict[str, Any]) -> None:
    manifest = load_plan(root)
    _slot(manifest, number)
    record = _read_record(root, number, manifest)
    if record['answer_state'] != 'captured':
        raise ValueError('cannot grade an absent or lost final answer')
    if not isinstance(review, dict) or review.get('status') not in ('assessed', 'disputed'):
        raise ValueError('review needs assessed/disputed status')
    if set(review) != {'status', 'diagnosis_correct', 'evidence', 'recommendation', 'reviewer', 'reason'}:
        raise ValueError('review fields differ from the offline review contract')
    if type(review.get('diagnosis_correct')) is not bool:
        raise ValueError('diagnosis_correct must be boolean')
    for field in ('evidence', 'recommendation'):
        if type(review.get(field)) is not int or review[field] not in (0, 1, 2):
            raise ValueError('review scores must be integers 0..2')
    for field in ('reviewer', 'reason'):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise ValueError(f'review requires {field}')
    history = _review_history(root, number, manifest, record)
    data = {'plan_digest': manifest['digest'], 'record_sha256': digest(encoded(record)),
            'previous_sha256': digest(encoded(history[-1])) if history else None, 'review': review}
    envelope = _read_envelope(root / 'records' / f'{number:03}.json')
    if 'capture_sha256' in envelope:
        data['capture_sha256'] = envelope['capture_sha256']
    legacy._write_new(root / 'reviews' / f'{number:03}' / f'{len(history)+1:03}.json', encoded(data))


def _review_history(root: Path, number: int, manifest: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    directory = root / 'reviews' / f'{number:03}'
    if not directory.exists():
        return []
    legacy._physical(directory)
    history = []
    for index, path in enumerate(sorted(directory.iterdir()), 1):
        if path.name != f'{index:03}.json':
            raise ValueError('nonsequential review revision')
        receipt = legacy._read_json(path)
        if receipt['plan_digest'] != manifest['digest'] or receipt['record_sha256'] != digest(encoded(record)):
            raise ValueError('review does not bind to result')
        envelope = _read_envelope(root / 'records' / f'{number:03}.json')
        if receipt.get('capture_sha256') != envelope.get('capture_sha256'):
            raise ValueError('review does not bind to capture')
        previous = digest(encoded(history[-1])) if history else None
        if receipt.get('previous_sha256') != previous:
            raise ValueError('review revision chain mismatch')
        history.append(receipt)
    return history


def analyze_root(root: Path) -> dict[str, Any]:
    from benchmark_issue10_v3_analysis import analyze
    manifest = load_plan(root)
    allowed = {f"{slot['slot']:03}.json" for slot in manifest['slots']}
    for folder in ('records', 'reviews'):
        names = allowed if folder == 'records' else {name.removesuffix('.json') for name in allowed}
        if any(p.name not in names for p in (root / folder).iterdir()):
            raise ValueError(f'unscheduled {folder} entry')
    reservations = {}
    directory = root / 'reservations'
    if directory.exists() or directory.is_symlink():
        legacy._physical(directory)
        if any(path.name not in allowed for path in directory.iterdir()):
            raise ValueError('unscheduled reservation entry')
        for slot in manifest['slots']:
            value = _load_reservation(root, manifest, slot['slot'])
            if value is not None:
                reservations[slot['slot']] = value
    records = []
    provenance = []
    for slot in manifest['slots']:
        number = slot['slot']
        path = root / 'records' / f'{number:03}.json'
        review_path = root / 'reviews' / f'{number:03}'
        if not path.exists():
            if review_path.exists():
                raise ValueError('review has no result')
            continue
        record = _read_record(root, number, manifest)
        history = _review_history(root, number, manifest, record)
        if history:
            record = {**record, 'review': history[-1]['review']}
        records.append(record)
        envelope = _read_envelope(path)
        if envelope['provenance'] == COLLECTOR_PROVENANCE:
            provenance.append({
                'slot': number, 'kind': envelope['provenance'],
                'collector_sha256': envelope['collector_sha256'],
                'identity_origin': envelope['collector']['identity_origin'],
            })
        else:
            provenance.append({
                'slot': number, 'kind': envelope['provenance'],
                'capture_sha256': envelope.get('capture_sha256'),
                'identity_origin': envelope.get('capture', {}).get('identity_origin'),
            })
    report = analyze(manifest, records, reserved_slots=list(reservations))
    report['slot_reservations'] = [
        {'slot': number, 'reservation_sha256': digest(encoded(value)),
         'result_imported': (root / 'records' / f'{number:03}.json').exists()}
        for number, value in reservations.items()
    ]
    report['record_provenance'] = 'operator-supplied offline records; live capture not qualified'
    report['slot_provenance'] = provenance
    report['experimental_claim_eligible'] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('root', type=Path)
    p.add_argument('--phase', choices=('pilot', 'measurement'), default='measurement')
    p.add_argument('--seed', type=int, default=1010)
    for command in ('import-result', 'record-review'):
        p = sub.add_parser(command)
        p.add_argument('root', type=Path)
        p.add_argument('--slot', type=int, required=True)
        p.add_argument('--file', type=Path, required=True)
    p = sub.add_parser('import-capture', help='retain supplied offline evidence; never launches a process')
    p.add_argument('root', type=Path)
    p.add_argument('--slot', type=int, required=True)
    for name in ('events', 'rollout', 'process'):
        p.add_argument(f'--{name}', type=Path, required=True)
    p.add_argument('--answer', type=Path)
    p.add_argument('--answer-state', choices=capture_adapter.ANSWER_STATES)
    p = sub.add_parser('import-collector', help='retain a completed collector attempt; never launches a process')
    p.add_argument('root', type=Path)
    p.add_argument('--slot', type=int, required=True)
    p.add_argument('--attempt', type=Path, required=True)
    p = sub.add_parser('analyze')
    p.add_argument('root', type=Path)
    p = sub.add_parser('export-review-packet', help='export a new private randomized human-review packet')
    p.add_argument('root', type=Path)
    p.add_argument('output', type=Path)
    p = sub.add_parser('resolve-review-answer', help='verify a review packet and resolve its opaque answer ID')
    p.add_argument('root', type=Path)
    p.add_argument('--packet', required=True)
    p.add_argument('--answer', required=True)
    p = sub.add_parser('run-one', help='always refuses: live execution is not implemented or qualified')
    p.add_argument('root', type=Path)
    args = parser.parse_args()
    if args.command == 'run-one':
        parser.error('launch disabled: capture/access/model qualification and explicit phase approval are required')
    elif args.command == 'prepare':
        m = prepare(args.root, args.phase, args.seed)
        print(json.dumps({'plan_digest': m['digest'], 'slots': len(m['slots']), 'launch_enabled': False}))
    elif args.command == 'import-result':
        import_result(args.root, args.slot, legacy._read_json(args.file))
    elif args.command == 'import-capture':
        if ((args.answer is not None and args.answer_state in ('absent', 'lost'))
                or (args.answer is None and args.answer_state == 'captured')):
            parser.error('answer file and answer-state are inconsistent')
        def read_input(path: Path) -> bytes:
            return legacy._read_bounded(legacy._physical(path), capture_adapter.MAX_CAPTURE_BYTES)
        import_capture(args.root, args.slot, read_input(args.events), read_input(args.rollout),
                       read_input(args.process), read_input(args.answer) if args.answer else None,
                       answer_state=args.answer_state)
    elif args.command == 'import-collector':
        import_collector(args.root, args.slot, args.attempt)
    elif args.command == 'record-review':
        record_review(args.root, args.slot, legacy._read_json(args.file))
    elif args.command == 'analyze':
        print(json.dumps(analyze_root(args.root), indent=2, allow_nan=False))
    elif args.command == 'export-review-packet':
        from benchmark_issue10_v3_review import export_packet
        print(json.dumps(export_packet(args.root, args.output), sort_keys=True))
    elif args.command == 'resolve-review-answer':
        from benchmark_issue10_v3_review import resolve_answer
        print(resolve_answer(args.root, args.packet, args.answer))


if __name__ == '__main__':
    main()
