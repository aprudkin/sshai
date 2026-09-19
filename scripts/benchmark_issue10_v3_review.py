#!/usr/bin/env python3
"""Bounded offline human-review packet export for the Issue 10 v3 coordinator.

Reviewer packets contain exact captured answers, neutral prepared task material,
and no coordinator slot, arm, session, usage, or prior-grade metadata.  They are
private review working data, not publication artifacts.  Original answer text is
not rewritten, so wording or tool references can still reveal an arm; lexical
blinding is not guaranteed.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import stat
from typing import Any

import benchmark_issue10_v3 as coordinator

PACKET_SCHEMA = 'sshai-benchmark/issue10-v3-review-packet-1'
OWNER_SCHEMA = 'sshai-benchmark/issue10-v3-review-owner-1'
PENDING_SCHEMA = 'sshai-benchmark/issue10-v3-review-publication-pending-1'
COMPLETE_SCHEMA = 'sshai-benchmark/issue10-v3-review-publication-complete-1'
MAX_PACKET_FILE_BYTES = 4 * 1_048_576
MAX_OWNER_BYTES = 4 * 1_048_576
_ID = re.compile(r'^[0-9a-f]{32}$')

RUBRIC = {
    'diagnosis_correct': {
        'type': 'boolean',
        'criterion': 'The diagnosis is semantically correct against the supplied key and respects its uncertainty limits.',
    },
    'evidence': {
        'type': 'integer', 'minimum': 0, 'maximum': 2,
        'anchors': {
            '0': 'References are absent, incorrect, or do not support the diagnosis.',
            '1': 'Useful correct evidence is present, but a necessary causal link is missing.',
            '2': 'Accurate references establish all required causal links and address a material alternative.',
        },
    },
    'recommendation': {
        'type': 'integer', 'minimum': 0, 'maximum': 2,
        'anchors': {
            '0': 'The recommendation is missing, unrelated to the cause, or unsafe.',
            '1': 'The recommendation points in the correct direction but is materially incomplete.',
            '2': 'The recommendation targets the supported cause, respects constraints, and proposes verification.',
        },
    },
    'success_rule': 'Diagnosis correct and both evidence and recommendation equal 2.',
    'assessment_note': 'Score the answer substance independently. Do not infer or reward an execution branch.',
}

NOTICE = (
    'PRIVATE HUMAN-REVIEW WORKING PACKET\n\n'
    'This packet is not a publication artifact. packet.json only marks reviewer content written;\n'
    'the owner must also confirm that export succeeded and its private completion receipt exists.\n'
    'Coordinator branch labels and run metadata are omitted, and answer order is randomized.\n'
    'Answer text is preserved exactly; its wording or tool references may reveal its origin, so\n'
    'lexical blinding is not guaranteed. Assess only against the included neutral task, fixtures,\n'
    'semantic key, and rubric. Do not add grades to this packet; use the coordinator record-review\n'
    'command after the owner resolves an opaque answer ID.\n'
)


def _opaque(rng: random.SystemRandom) -> str:
    return f'{rng.getrandbits(128):032x}'


def _new_id(rng: random.SystemRandom, used: set[str]) -> str:
    while True:
        value = _opaque(rng)
        if value not in used:
            used.add(value)
            return value


def _valid_id(value: str, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise ValueError(f'invalid {label}')
    return value


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in ('', '.', '..') for part in path.parts)):
        raise ValueError('unsafe packet path')
    return path


def _read_prepared(root: Path, manifest: dict[str, Any], relative: str) -> bytes:
    expected = manifest['files'].get(relative)
    if expected is None:
        raise ValueError(f'prepared review source is not pinned: {relative}')
    data = coordinator.legacy._read_bounded(
        coordinator.legacy._physical(root / 'prepared' / relative), MAX_PACKET_FILE_BYTES)
    if coordinator.digest(data) != expected:
        raise ValueError(f'prepared review source changed: {relative}')
    return data


def _record_binding(root: Path, number: int, record: dict[str, Any]) -> dict[str, Any]:
    path = root / 'records' / f'{number:03}.json'
    body = coordinator.legacy._read_bounded(
        coordinator.legacy._physical(path), coordinator.MAX_RECORD_BYTES)
    envelope = json.loads(body)
    return {
        'record_sha256': coordinator.digest(coordinator.encoded(record)),
        'record_envelope_sha256': coordinator.digest(body),
        'provenance': envelope['provenance'],
        'capture_sha256': envelope.get('capture_sha256'),
    }


def _missing_reason(row: dict[str, Any]) -> tuple[str, str]:
    if not row['attempted']:
        return ('missing', row['quality']['reason'])
    state = row['answer_state']
    if state == 'captured':
        return ('captured', 'captured_answer_exported')
    if state == 'absent':
        return ('absent', row['quality']['reason'])
    if state == 'lost':
        return ('lost', row['quality']['reason'])
    raise ValueError('unsupported review answer state')


def _packet_file(relative: str, body: bytes) -> dict[str, Any]:
    return {'path': relative, 'bytes': len(body), 'sha256': coordinator.digest(body)}


def _packet_directories(files: dict[str, bytes]) -> list[str]:
    directories: set[str] = set()
    for name in files:
        path = PurePosixPath(name).parent
        while str(path) != '.':
            directories.add(str(path))
            path = path.parent
    return sorted(directories)


def _ensure_review_directory(root: Path) -> Path:
    directory = root / 'review-packets'
    if not directory.exists() and not directory.is_symlink():
        coordinator.legacy._mkdir_private(directory)
    else:
        coordinator.legacy._physical(directory)
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
            raise ValueError('review packet owner directory must be private')
    return directory


def export_packet(root: Path, output: Path) -> dict[str, Any]:
    """Export every currently imported captured answer into one new blind packet."""
    root = coordinator.legacy._physical(root)
    output = coordinator.legacy._physical(output, must_exist=False)
    if output == root or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError('review output and study root must be separate, non-nested paths')
    if output.exists() or output.is_symlink():
        raise ValueError(f'refusing existing path: {output}')

    manifest = coordinator.load_plan(root)
    # This is the existing full replay path: it validates plan/prepared bytes,
    # records, capture/collector evidence, review chains, and scheduled inventory.
    report = coordinator.analyze_root(root)
    rows = {row['slot']: row for row in report['slots']}
    rng = random.SystemRandom()
    owner_directory = _ensure_review_directory(root)
    while True:
        packet_id = _opaque(rng)
        owner_path = owner_directory / f'{packet_id}.json'
        pending_path = owner_directory / f'{packet_id}.pending.json'
        complete_path = owner_directory / f'{packet_id}.complete.json'
        if not any(path.exists() or path.is_symlink()
                   for path in (owner_path, pending_path, complete_path)):
            break

    packet_files: dict[str, bytes] = {
        'STATUS.txt': NOTICE.encode('utf-8'),
        'rubric.json': coordinator.encoded(RUBRIC),
    }
    task_by_case: dict[str, str] = {}
    used_ids: set[str] = {packet_id}
    public_answers: list[dict[str, Any]] = []
    owner_answers: list[dict[str, Any]] = []
    slot_inventory: list[dict[str, Any]] = []

    for slot in manifest['slots']:
        number = slot['slot']
        row = rows[number]
        state, reason = _missing_reason(row)
        inventory = {
            'slot': number, 'case_id': slot['case_id'], 'arm': slot['arm'],
            'record_state': state, 'reason': reason, 'answer_id': None,
            'record_binding': None,
        }
        record_path = root / 'records' / f'{number:03}.json'
        if record_path.is_symlink():
            raise ValueError('record path cannot be a symlink')
        if row['attempted']:
            record = coordinator._read_record(root, number, manifest)
            binding = _record_binding(root, number, record)
            inventory['record_binding'] = binding
            if record['answer_state'] == 'captured':
                case_id = slot['case_id']
                if case_id not in task_by_case:
                    task_by_case[case_id] = _new_id(rng, used_ids)
                task_id = task_by_case[case_id]
                answer_id = _new_id(rng, used_ids)
                answer_body = record['final_answer'].encode('utf-8')
                answer_path = f'answers/{answer_id}.md'
                packet_files[answer_path] = answer_body
                public_answers.append({
                    'answer_id': answer_id, 'task_id': task_id,
                    'answer': _packet_file(answer_path, answer_body),
                })
                owner_answers.append({
                    'answer_id': answer_id, 'task_id': task_id, 'slot': number,
                    'case_id': case_id, 'answer_sha256': coordinator.digest(answer_body),
                    **binding,
                })
                inventory['answer_id'] = answer_id
        slot_inventory.append(inventory)

    public_tasks: list[dict[str, Any]] = []
    owner_tasks: list[dict[str, Any]] = []
    for case_id, task_id in task_by_case.items():
        prefix = f'tasks/{task_id}'
        prompt_body = _read_prepared(root, manifest, f'prompts/{case_id}.md')
        key_body = _read_prepared(root, manifest, f'evaluator/{case_id}/key.json')
        prompt_path = f'{prefix}/task.md'
        key_path = f'{prefix}/semantic-key.json'
        packet_files[prompt_path] = prompt_body
        packet_files[key_path] = key_body
        fixture_entries = []
        input_prefix = f'inputs/{case_id}/'
        source_names = sorted(name for name in manifest['files'] if name.startswith(input_prefix))
        if not source_names:
            raise ValueError(f'prepared task has no fixtures: {case_id}')
        for source_name in source_names:
            relative = source_name[len(input_prefix):]
            _safe_relative(relative)
            body = _read_prepared(root, manifest, source_name)
            path = f'{prefix}/fixtures/{relative}'
            packet_files[path] = body
            fixture_entries.append(_packet_file(path, body))
        public_tasks.append({
            'task_id': task_id,
            'prompt': _packet_file(prompt_path, prompt_body),
            'semantic_key': _packet_file(key_path, key_body),
            'fixtures': fixture_entries,
        })
        owner_tasks.append({
            'task_id': task_id, 'case_id': case_id,
            'prompt_sha256': coordinator.digest(prompt_body),
            'semantic_key_sha256': coordinator.digest(key_body),
            'fixture_sha256': {entry['path']: entry['sha256'] for entry in fixture_entries},
        })

    rng.shuffle(public_answers)
    rng.shuffle(public_tasks)
    packet = {
        'schema': PACKET_SCHEMA,
        'packet_id': packet_id,
        'privacy': 'private human-review working data; not for publication',
        'blinding': 'coordinator metadata omitted; lexical blinding of exact answer text is not guaranteed',
        'rubric': _packet_file('rubric.json', packet_files['rubric.json']),
        'tasks': public_tasks,
        'answers': public_answers,
    }
    packet_body = coordinator.encoded(packet)
    packet_files['packet.json'] = packet_body
    packet_inventory = [_packet_file(name, body) for name, body in sorted(packet_files.items())]
    owner = {
        'schema': OWNER_SCHEMA, 'packet_id': packet_id,
        'output_directory': str(output), 'plan_digest': manifest['digest'],
        'packet_manifest_sha256': coordinator.digest(packet_body),
        'packet_files': packet_inventory,
        'packet_directories': _packet_directories(packet_files),
        'answers': owner_answers, 'tasks': owner_tasks,
        'slots': slot_inventory,
    }
    owner_body = coordinator.encoded(owner)
    oversized = sorted(name for name, body in packet_files.items()
                       if len(body) > MAX_PACKET_FILE_BYTES)
    if oversized:
        raise ValueError(f'review packet file exceeds {MAX_PACKET_FILE_BYTES} bytes: {oversized[0]}')
    if len(owner_body) > MAX_OWNER_BYTES:
        raise ValueError(f'review owner metadata exceeds {MAX_OWNER_BYTES} bytes')
    pending = {
        'schema': PENDING_SCHEMA, 'packet_id': packet_id,
        'status': 'publication_pending_until_complete_marker_exists',
        'output_directory': str(output), 'plan_digest': manifest['digest'],
        'owner_sha256': coordinator.digest(owner_body),
    }
    coordinator.legacy._write_new(pending_path, coordinator.encoded(pending))

    # Cross-directory publication cannot be atomic. packet.json is the reviewer
    # content marker; the private completion receipt is written last. Any error
    # deliberately leaves the pending receipt and partial output for inspection.
    coordinator.legacy._new_dir(output)
    for name, body in sorted(packet_files.items(), key=lambda item: (item[0] == 'packet.json', item[0])):
        coordinator.legacy._write_new(output / Path(name), body)
    coordinator.legacy._write_new(owner_path, owner_body)
    complete = {
        'schema': COMPLETE_SCHEMA, 'packet_id': packet_id,
        'owner_sha256': coordinator.digest(owner_body),
        'packet_manifest_sha256': coordinator.digest(packet_body),
    }
    coordinator.legacy._write_new(complete_path, coordinator.encoded(complete))
    return {
        'packet_id': packet_id,
        'answer_count': len(owner_answers),
        'excluded_slot_count': len(slot_inventory) - len(owner_answers),
    }


def _read_owner_file(path: Path) -> tuple[dict[str, Any], bytes]:
    path = coordinator.legacy._physical(path)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError('review owner metadata must be private')
    data = coordinator.legacy._read_bounded(path, MAX_OWNER_BYTES)
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError('invalid review owner metadata') from exc
    if not isinstance(value, dict):
        raise ValueError('invalid review owner metadata')
    return value, data


def _scan_packet(directory: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()

    def visit(current: Path, prefix: PurePosixPath | None = None) -> None:
        with os.scandir(current) as entries:
            for entry in entries:
                relative = PurePosixPath(entry.name) if prefix is None else prefix / entry.name
                if entry.is_symlink():
                    raise ValueError('review packet cannot contain symlinks')
                if entry.is_dir(follow_symlinks=False):
                    directories.add(str(relative))
                    if stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode) != 0o700:
                        raise ValueError('review packet directory is not private')
                    visit(Path(entry.path), relative)
                elif entry.is_file(follow_symlinks=False):
                    files.add(str(relative))
                    if stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode) != 0o600:
                        raise ValueError('review packet file is not private')
                else:
                    raise ValueError('review packet contains a non-regular entry')

    visit(directory)
    return files, directories


def _verify_packet(owner: dict[str, Any]) -> None:
    output_value = owner.get('output_directory')
    if not isinstance(output_value, str) or not Path(output_value).is_absolute():
        raise ValueError('invalid bound review output path')
    output = coordinator.legacy._physical(Path(output_value))
    if not output.is_dir() or stat.S_IMODE(output.stat().st_mode) != 0o700:
        raise ValueError('review packet root is not a private directory')
    expected_files = owner.get('packet_files')
    expected_directories = owner.get('packet_directories')
    if not isinstance(expected_files, list) or not isinstance(expected_directories, list):
        raise ValueError('invalid review packet inventory')
    by_path: dict[str, dict[str, Any]] = {}
    for item in expected_files:
        if (not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256'}
                or type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_PACKET_FILE_BYTES
                or not isinstance(item['sha256'], str) or len(item['sha256']) != 64):
            raise ValueError('invalid review packet file receipt')
        _safe_relative(item['path'])
        if item['path'] in by_path:
            raise ValueError('duplicate review packet file receipt')
        by_path[item['path']] = item
    if (any(not isinstance(name, str) for name in expected_directories)
            or len(set(expected_directories)) != len(expected_directories)):
        raise ValueError('invalid review packet directory inventory')
    for name in expected_directories:
        _safe_relative(name)
    actual_files, actual_directories = _scan_packet(output)
    if actual_files != set(by_path) or actual_directories != set(expected_directories):
        raise ValueError('review packet inventory mismatch')
    for name, item in by_path.items():
        body = coordinator.legacy._read_bounded(output / Path(name), MAX_PACKET_FILE_BYTES)
        if len(body) != item['bytes'] or coordinator.digest(body) != item['sha256']:
            raise ValueError(f'review packet file mismatch: {name}')
    packet = by_path.get('packet.json')
    if packet is None or packet['sha256'] != owner.get('packet_manifest_sha256'):
        raise ValueError('review packet manifest binding mismatch')


def resolve_answer(root: Path, packet_id: str, answer_id: str) -> int:
    """Verify a completed packet and its current source record, then return its slot."""
    packet_id = _valid_id(packet_id, 'packet ID')
    answer_id = _valid_id(answer_id, 'answer ID')
    root = coordinator.legacy._physical(root)
    owner_directory = coordinator.legacy._physical(root / 'review-packets')
    if not owner_directory.is_dir() or stat.S_IMODE(owner_directory.stat().st_mode) != 0o700:
        raise ValueError('review packet owner directory must be private')
    owner, owner_body = _read_owner_file(owner_directory / f'{packet_id}.json')
    complete, _ = _read_owner_file(owner_directory / f'{packet_id}.complete.json')
    if (set(complete) != {'schema', 'packet_id', 'owner_sha256', 'packet_manifest_sha256'}
            or complete.get('schema') != COMPLETE_SCHEMA
            or complete.get('packet_id') != packet_id
            or complete.get('owner_sha256') != coordinator.digest(owner_body)
            or complete.get('packet_manifest_sha256') != owner.get('packet_manifest_sha256')):
        raise ValueError('review packet completion binding mismatch')
    required = {
        'schema', 'packet_id', 'output_directory', 'plan_digest',
        'packet_manifest_sha256', 'packet_files', 'packet_directories',
        'answers', 'tasks', 'slots',
    }
    if set(owner) != required or owner.get('schema') != OWNER_SCHEMA or owner.get('packet_id') != packet_id:
        raise ValueError('review owner fields or identity mismatch')
    manifest = coordinator.load_plan(root)
    if owner.get('plan_digest') != manifest['digest']:
        raise ValueError('review packet plan binding mismatch')
    # Replay every current record/review before trusting a single answer mapping.
    coordinator.analyze_root(root)
    _verify_packet(owner)
    matches = [item for item in owner['answers']
               if isinstance(item, dict) and item.get('answer_id') == answer_id]
    if len(matches) != 1:
        raise ValueError('answer ID is not present exactly once in this packet')
    item = matches[0]
    fields = {
        'answer_id', 'task_id', 'slot', 'case_id', 'answer_sha256',
        'record_sha256', 'record_envelope_sha256', 'provenance', 'capture_sha256',
    }
    if set(item) != fields or type(item['slot']) is not int:
        raise ValueError('invalid review answer owner mapping')
    slot = coordinator._slot(manifest, item['slot'])
    if slot['case_id'] != item['case_id']:
        raise ValueError('review answer case binding mismatch')
    record = coordinator._read_record(root, item['slot'], manifest)
    if record['answer_state'] != 'captured':
        raise ValueError('review answer source is no longer gradable')
    binding = _record_binding(root, item['slot'], record)
    if any(item.get(field) != binding[field] for field in binding):
        raise ValueError('review answer record binding mismatch')
    answer_body = record['final_answer'].encode('utf-8')
    if coordinator.digest(answer_body) != item['answer_sha256']:
        raise ValueError('review answer text binding mismatch')
    packet_answers = [entry for entry in owner['packet_files']
                      if entry.get('path') == f'answers/{answer_id}.md']
    if (len(packet_answers) != 1 or packet_answers[0]['sha256'] != item['answer_sha256']
            or packet_answers[0]['bytes'] != len(answer_body)):
        raise ValueError('review answer packet binding mismatch')
    return item['slot']


__all__ = ['export_packet', 'resolve_answer']
