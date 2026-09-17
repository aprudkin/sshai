#!/usr/bin/env python3
"""Offline three-series study coordinator. No launch or live capture is implemented.

Preparation never probes Codex, sshai, hosts, or credentials. Imported records
are operator-supplied offline evidence, not proof of capture qualification.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import random
from typing import Any

import benchmark_issue10 as legacy
import benchmark_issue10_v3_capture as capture_adapter
from benchmark_issue10_v3_cases import build_cases
from prepare_issue10_v3_fixtures import bundle

SCHEMA = 'sshai-benchmark/issue10-v3-offline-1'
SOURCE = Path(__file__).resolve().parent
REPO = SOURCE.parent
# One atomic private envelope retains raw bytes, report, binding and projection.
# JSON/base64 and parsed-report expansion need more room than one input stream.
MAX_RECORD_BYTES = 32 * 1_048_576
CAPTURE_PROVENANCE = 'operator-supplied-offline-capture'


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
                 'benchmark_issue10_v3_capture.py',
                 'benchmark_issue10_v3_cases.py', 'prepare_issue10_v3_fixtures.py',
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
    for path in (root / 'records').glob('*.json'):
        previous = _read_envelope(path)['record']
        if previous.get('session_id') == record['session_id']:
            raise ValueError('session_id already used by an imported slot')
    payload = {**record, 'slot': number, 'review': None}
    envelope = {'plan_digest': manifest['digest'], 'provenance': 'operator-supplied-offline-record',
                'record': payload, 'record_sha256': digest(encoded(payload))}
    legacy._write_new(root / 'records' / f'{number:03}.json', encoded(envelope))


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
    for path in (root / 'records').glob('*.json'):
        if _read_envelope(path)['record']['session_id'] == record['session_id']:
            raise ValueError('session_id already used by an imported slot')
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
    legacy._write_new(root / 'records' / f'{number:03}.json', data)


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
    if envelope.get('provenance') == CAPTURE_PROVENANCE:
        _verify_capture(envelope, manifest, number)
    elif (envelope.get('provenance') != 'operator-supplied-offline-record'
          or 'capture' in envelope or 'capture_sha256' in envelope):
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
        provenance.append({'slot': number, 'kind': envelope['provenance'],
                           'capture_sha256': envelope.get('capture_sha256'),
                           'identity_origin': envelope.get('capture', {}).get('identity_origin')})
    report = analyze(manifest, records)
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
    p = sub.add_parser('analyze')
    p.add_argument('root', type=Path)
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
    elif args.command == 'record-review':
        record_review(args.root, args.slot, legacy._read_json(args.file))
    elif args.command == 'analyze':
        print(json.dumps(analyze_root(args.root), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
