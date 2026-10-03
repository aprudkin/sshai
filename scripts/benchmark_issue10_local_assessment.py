#!/usr/bin/env python3
"""Bounded OFFLINE single-task model-assessment packet and response validator.

No model runner, controller importer, finality detector, grade generator or retry.
Only explicitly supplied UTF-8 synthetic/qualified inputs are read. The caller must
independently qualify delivery before supplying qualified answers: hashes and mere
completion/byte matches are not finality evidence. Receipts label qualification as
caller-declared; this bridge does not establish live pilot readiness.

CLI: packet --bundle BUNDLE.json --instructions INSTRUCTIONS.md --out NEW_DIRECTORY
     validate --packet PACKET_DIRECTORY --response RESPONSE --out NEW_DIRECTORY

Bundle schema (BUNDLE_SCHEMA): exactly schema, case_id, repetitions (1..3), prompt,
semantic_key, fixtures, slots. A blob is exactly {text: UTF-8 string, sha256: hex}.
Prompt is the unchanged original-case prompt; key is a nonempty JSON object blob.
Each fixture is {file: original relative POSIX path, text, sha256}.
Slots enumerate BOTH baseline/sshai arms for EVERY repetition, exactly once, with
{slot_id, arm, repetition, state, reason, answer, qualification}. State is qualified,
absent, lost or unqualified. Answer is a blob or null (absent requires null).
Qualification is null or {status, method, provenance}; provenance is a nonempty
blob of the independently established finality receipt, retained privately.
Qualified status MUST be independently_established_final; method MUST be
terminal_final_event or independent_attestation. These are explicit caller claims,
not inferred from contents. Unknown/not_final status is accepted ONLY for retained
noneligible slots. Unknown bytes never become assessment answers automatically.

Supply the reviewed full instruction file; only the exact Reusable assessor prompt
section is assessor-visible, with a separate full-file pin. Rubric is reused without
changes. One packet holds a single task and all eligible answers, shuffled together.
Assessor/input.json is self-contained, needs no tools, and contains no slot/arm/usage
metadata. Text is JSON-escaped losslessly; decoding and UTF-8 encoding recovers exact
bytes (CRLF and Unicode included). Owner/inventory.json retains ALL planned slots,
reasons, original retained answer blobs and provenance; it is NOT assessor input.
Expose ONLY assessor/ to an assessor, not the common parent or owner/. Permissions
alone do not create assessor isolation. Lexical blinding is explicitly incomplete.

Inputs and generated files are <=collector MAX_PROMPT_BYTES (1 MiB), with
assessor responses <=64 KiB, arrays <=32 items and response rows <=32.
The self-contained packet, including instruction/JSON expansion, must fit that cap.
Inputs must be physical regular files, never symlinks, including ancestors. Outputs
are new private 0700 directories / 0600 files; no overwrite. complete.json is written
last; interrupted publication remains incomplete, not repaired or retried.
Validation retains exact response bytes, malformed/duplicate/foreign rows, missing
IDs and independently valid rows. Exit 2 means invalid response (reports still
published) or rejected input (no assessment claimed). Citation range checks are
objective coordinates ONLY: not quote accuracy, causal relevance or real grades.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import random
import re
import stat
import sys
from typing import Any

from benchmark_issue10_v3_review import RUBRIC
from benchmark_issue10_v3_collector import MAX_PROMPT_BYTES

MAX_BYTES = MAX_PROMPT_BYTES
MAX_RESPONSE_BYTES = 64 * 1024
MAX_REFS = 32
MAX_RESPONSE_ROWS = 32
MAX_TEXT = 2048
BUNDLE_SCHEMA = 'sshai-benchmark/issue10-local-assessment-bundle-1'
INPUT_SCHEMA = 'sshai-benchmark/issue10-local-assessment-input-1'
PACKET_SCHEMA = 'sshai-benchmark/issue10-local-assessment-packet-1'
OWNER_SCHEMA = 'sshai-benchmark/issue10-local-assessment-owner-1'
RESPONSE_SCHEMA = 'sshai-benchmark/issue10-model-assessment-1'
VALIDATION_SCHEMA = 'sshai-benchmark/issue10-local-assessment-validation-1'
CITATION_SCHEMA = 'sshai-benchmark/issue10-local-assessment-citation-ranges-1'
_ID = re.compile(r'^[0-9a-f]{32}$')
_HASH = re.compile(r'^[0-9a-f]{64}$')
LINE_CONVENTION = ('Original fixture physical lines, one-based inclusive; split on LF only; '
                   'CRLF is one delimiter, blank/header lines count; a trailing LF adds no '
                   'phantom line; empty files have zero lines; Unicode separators are not LF.')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def compact(value: Any) -> bytes:
    # ASCII escaping also retains invalid escaped-surrogate strings for inspection.
    return (json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')


def bounded(data: bytes, label: str) -> bytes:
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError(f'{label} must be bytes bounded to {MAX_BYTES}')
    return data


def exact(value: Any, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f'{label}: exact fields required')
    return value


def text(value: Any, label: str, *, maximum: int = MAX_BYTES, empty: bool = False) -> str:
    if (not isinstance(value, str) or (not empty and not value.strip())
            or len(value.encode('utf-8')) > maximum):
        raise ValueError(f'{label}: bounded UTF-8 string required')
    return value


def checked_blob(value: Any, label: str, *, empty: bool = False) -> dict:
    exact(value, {'text', 'sha256'}, label)
    body = text(value['text'], label, empty=empty).encode('utf-8')
    if not isinstance(value['sha256'], str) or _HASH.fullmatch(value['sha256']) is None or digest(body) != value['sha256']:
        raise ValueError(f'{label}: SHA-256 mismatch')
    return copy.deepcopy(value)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON object key')
        result[key] = value
    return result


def parse(data: bytes) -> Any:
    bounded(data, 'JSON input')
    return json.loads(data.decode('utf-8'), object_pairs_hook=_json_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-JSON constant')))


def _response_float(lexeme: str) -> float | dict:
    """Retain overflow tokens in a serialization-safe INVALID parsing view.

    No field requiring a score, string, boolean or null accepts this tagged object.
    The exact response bytes remain authoritative, not this report representation.
    Ordinary finite floats remain floats and are likewise invalid integer scores.
    """
    value = float(lexeme)
    if not math.isfinite(value):
        return {'invalid_numeric_lexeme': lexeme, 'reason': 'nonfinite_float_overflow'}
    return value


class _ResponseObject(dict):
    """A parsing view, never an accepted repair; exact bytes remain authoritative."""
    def __init__(self, pairs: list[tuple[str, Any]]):
        super().__init__(pairs)
        counts = Counter(key for key, _ in pairs)
        self.duplicate_keys = [key for key, count in counts.items() if count > 1]


def _duplicates(value: Any, path: str = '') -> list[dict]:
    result = []
    if isinstance(value, _ResponseObject) and value.duplicate_keys:
        result.append({'path': path, 'keys': value.duplicate_keys})
    if isinstance(value, dict):
        for key, child in value.items():
            result.extend(_duplicates(child, f'{path}/{key}'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_duplicates(child, f'{path}/{index}'))
    return result


def relative_file(value: Any) -> str:
    text(value, 'fixture path', maximum=1024)
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or '..' in path.parts
            or value == '.' or '\\' in value or any(ord(c) < 32 for c in value)):
        raise ValueError('unsafe original fixture path')
    return value


def physical_lines(value: str) -> int:
    return value.count('\n') + (1 if value and not value.endswith('\n') else 0)


def assessor_section(instructions: bytes) -> bytes:
    bounded(instructions, 'instructions')
    instructions.decode('utf-8')
    start = b'## Reusable assessor prompt\n'
    end = b'## Static review scenarios (not collected-answer grades)\n'
    if instructions.count(start) != 1 or instructions.count(end) != 1:
        raise ValueError('instruction section markers must appear exactly once (original LF bytes)')
    left, right = instructions.index(start), instructions.index(end)
    if right <= left:
        raise ValueError('invalid instruction section order')
    section = instructions[left:right]
    rubric_marker = b'### Frozen rubric\n'
    if section.count(rubric_marker) != 1:
        raise ValueError('missing frozen rubric section')
    rubric_part = section.split(rubric_marker, 1)[1]
    try:
        rubric_bytes = rubric_part.split(b'```json\n', 1)[1].split(b'\n```', 1)[0]
    except IndexError as exc:
        raise ValueError('missing rubric JSON') from exc
    if parse(rubric_bytes) != RUBRIC:
        raise ValueError('instructions rubric differs from exact RUBRIC')
    return section


def _qualification(value: Any, state: str) -> None:
    if value is None:
        if state == 'qualified':
            raise ValueError('qualified answer requires independent finality provenance')
        return
    exact(value, {'status', 'method', 'provenance'}, 'qualification')
    checked_blob(value['provenance'], 'finality provenance')
    text(value['method'], 'finality method', maximum=256)
    if value['status'] not in ('independently_established_final', 'unknown', 'not_final'):
        raise ValueError('unknown qualification status')
    if state == 'qualified':
        if (value['status'] != 'independently_established_final'
                or value['method'] not in ('terminal_final_event', 'independent_attestation')):
            raise ValueError('independent finality required; completion/byte match is insufficient')
    elif value['status'] == 'independently_established_final':
        raise ValueError('independently qualified answers cannot be silently excluded')


def build_packet(bundle: dict, instructions: bytes, *, rng: Any = None,
                 source_sha256: str | None = None) -> tuple[dict, dict]:
    """Build from explicit supplied data, never import a study or infer finality.

    Supply source_sha256 to avoid the default bounded read of this module's bytes.
    rng injection is a synthetic-test seam; ordinary packets use SystemRandom.
    """
    bounded(encode(bundle), 'fixture bundle')
    exact(bundle, {'schema', 'case_id', 'repetitions', 'prompt', 'semantic_key', 'fixtures', 'slots'}, 'bundle')
    if bundle['schema'] != BUNDLE_SCHEMA:
        raise ValueError('wrong bundle schema')
    text(bundle['case_id'], 'case ID', maximum=256)
    repetitions = bundle['repetitions']
    if type(repetitions) is not int or not 1 <= repetitions <= 3:
        raise ValueError('repetitions must be integer 1..3')
    prompt = checked_blob(bundle['prompt'], 'original prompt')
    key = checked_blob(bundle['semantic_key'], 'semantic key')
    if not isinstance(parse(key['text'].encode()), dict) or not parse(key['text'].encode()):
        raise ValueError('semantic key must be a nonempty JSON object')
    fixtures = bundle['fixtures']
    if not isinstance(fixtures, list) or not 1 <= len(fixtures) <= MAX_REFS:
        raise ValueError('1..32 original fixtures required')
    public_fixtures, file_hashes = [], {}
    for fixture in fixtures:
        exact(fixture, {'file', 'text', 'sha256'}, 'fixture')
        name = relative_file(fixture['file'])
        if name in file_hashes:
            raise ValueError('duplicate original fixture file')
        body = checked_blob({k: fixture[k] for k in ('text', 'sha256')}, 'fixture', empty=True)
        file_hashes[name] = body['sha256']
        public_fixtures.append({'file': name, **body, 'line_count': physical_lines(body['text'])})
    slots = bundle['slots']
    if not isinstance(slots, list) or len(slots) != 2 * repetitions:
        raise ValueError('retain every planned arm/repetition slot')
    rng = random.SystemRandom() if rng is None else rng
    used_ids: set[str] = set()

    def new_id() -> str:
        while True:
            value = f'{rng.getrandbits(128):032x}'
            if value not in used_ids:
                used_ids.add(value)
                return value

    packet_id, task_id = new_id(), new_id()
    answers, inventory, combinations, slot_ids = [], [], set(), set()
    for slot in slots:
        exact(slot, {'slot_id', 'arm', 'repetition', 'state', 'reason', 'answer', 'qualification'}, 'planned slot')
        text(slot['slot_id'], 'private slot ID', maximum=256)
        if slot['slot_id'] in slot_ids:
            raise ValueError('duplicate private slot ID')
        slot_ids.add(slot['slot_id'])
        if (slot['arm'] not in ('baseline', 'sshai') or type(slot['repetition']) is not int
                or not 1 <= slot['repetition'] <= repetitions):
            raise ValueError('unknown arm/repetition')
        combination = (slot['arm'], slot['repetition'])
        if combination in combinations:
            raise ValueError('duplicate planned arm/repetition')
        combinations.add(combination)
        if slot['state'] not in ('qualified', 'absent', 'lost', 'unqualified'):
            raise ValueError('unknown retained-answer state')
        text(slot['reason'], 'retained slot reason', maximum=MAX_TEXT)
        answer = None if slot['answer'] is None else checked_blob(slot['answer'], 'exact answer', empty=True)
        if slot['state'] == 'qualified' and (answer is None or not answer['text']):
            raise ValueError('qualified answer must have exact nonempty UTF-8 bytes')
        if slot['state'] == 'absent' and (answer is not None or slot['qualification'] is not None):
            raise ValueError('absent slot cannot contain an answer/qualification')
        _qualification(slot['qualification'], slot['state'])
        answer_id = None
        if slot['state'] == 'qualified':
            answer_id = new_id()
            answers.append({'answer_id': answer_id, **answer})
        inventory.append({**copy.deepcopy(slot), 'answer_id': answer_id})
    rng.shuffle(answers)
    section = assessor_section(instructions)
    source_binding = {'prompt_sha256': prompt['sha256'], 'semantic_key_sha256': key['sha256'],
                      'fixture_sha256': file_hashes}
    source_hash = digest(encode(source_binding))
    pins = {'instructions_file_sha256': digest(instructions),
            'assessor_section_sha256': digest(section), 'rubric_sha256': digest(encode(RUBRIC)),
            'source_sha256': source_hash, 'source_binding': source_binding,
            'bundle_sha256': digest(encode(bundle)),
            'bridge_source_sha256': source_sha256 or digest(read_file(Path(__file__)))}
    if not isinstance(pins['bridge_source_sha256'], str) or _HASH.fullmatch(pins['bridge_source_sha256']) is None:
        raise ValueError('invalid implementation source pin')
    packet = {'schema': PACKET_SCHEMA, 'packet_id': packet_id, 'task_id': task_id,
              'privacy': 'private model-assessment working evidence; not publication material',
              'blinding': 'owner labels omitted; lexical blinding of unchanged wording is not guaranteed',
              'line_convention': LINE_CONVENTION, 'source_sha256': source_hash,
              'prompt': prompt, 'semantic_key': key, 'fixtures': public_fixtures,
              'rubric': copy.deepcopy(RUBRIC), 'answers': answers}
    assessment_input = {'schema': INPUT_SCHEMA, 'instructions': section.decode('utf-8'), 'packet': packet}
    bounded(encode(assessment_input), 'self-contained assessment input')
    owner = {'schema': OWNER_SCHEMA, 'packet_id': packet_id, 'task_id': task_id,
             'case_id': bundle['case_id'], 'repetitions': repetitions,
             'batching': 'same-task-both-arms-all-repetitions',
             'qualification_basis': 'caller-declared independent finality; not verified by this bridge',
             'pins': pins, 'assessment_input_sha256': digest(encode(assessment_input)), 'slots': inventory}
    bounded(encode(owner), 'owner inventory')
    return assessment_input, owner


def _source_ranges(refs: Any, fixtures: dict[str, int]) -> tuple[list[str], list[dict]]:
    errors, checks = [], []
    if not isinstance(refs, list) or len(refs) > MAX_REFS:
        return ['source_refs must be a bounded array'], [{'valid': False, 'errors': ['invalid source_refs array']}]
    for index, ref in enumerate(refs):
        problems = ['duplicate JSON fields in source_ref'] if _duplicates(ref) else []
        if not isinstance(ref, dict) or set(ref) != {'file', 'start_line', 'end_line', 'origin', 'supports'}:
            problems.append('source_ref exact fields required')
        else:
            name, start, end = ref['file'], ref['start_line'], ref['end_line']
            if not isinstance(name, str) or name not in fixtures:
                problems.append('file is not an original fixture')
            if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                problems.append('positive inclusive integer range required')
            elif isinstance(name, str) and name in fixtures and end > fixtures[name]:
                problems.append('range exceeds original physical lines')
            if ref['origin'] not in ('answer', 'assessor'):
                problems.append('invalid reference origin')
            try:
                text(ref['supports'], 'reference supports', maximum=MAX_TEXT)
            except ValueError as exc:
                problems.append(str(exc))
        errors.extend(problems)
        checks.append({'ref_index': index, 'raw': ref, 'valid': not problems, 'errors': problems})
    return errors, checks


def _strings(value: Any, label: str) -> None:
    if not isinstance(value, list) or len(value) > MAX_REFS:
        raise ValueError(f'{label}: bounded array required')
    for item in value:
        text(item, label, maximum=MAX_TEXT)


def _row_errors(row: dict) -> list[str]:
    errors = []
    fields = {'answer_id', 'status', 'diagnosis_correct', 'evidence', 'recommendation', 'rationale',
              'source_refs', 'unsupported_statements', 'unsafe_recommendations', 'issues'}
    try:
        exact(row, fields, 'assessment')
        if not isinstance(row['answer_id'], str) or _ID.fullmatch(row['answer_id']) is None:
            raise ValueError('malformed answer ID')
        status = row['status']
        if status not in ('assessed', 'unassessable', 'disputed'):
            raise ValueError('invalid status')
        if status == 'assessed':
            if type(row['diagnosis_correct']) is not bool:
                raise ValueError('diagnosis_correct must be JSON boolean')
            if any(type(row[k]) is not int or not 0 <= row[k] <= 2 for k in ('evidence', 'recommendation')):
                raise ValueError('scores must be integers 0..2, not booleans')
        elif any(row[k] is not None for k in ('diagnosis_correct', 'evidence', 'recommendation')):
            raise ValueError('unknown/disputed quality must be null')
        exact(row['rationale'], {'diagnosis', 'evidence', 'recommendation'}, 'rationale')
        for value in row['rationale'].values():
            text(value, 'rationale', maximum=MAX_TEXT)
        for label in ('unsupported_statements', 'unsafe_recommendations'):
            if not isinstance(row[label], list) or len(row[label]) > MAX_REFS:
                raise ValueError(f'{label}: bounded array required')
            for item in row[label]:
                exact(item, {'statement', 'reason'}, label)
                for value in item.values():
                    text(value, label, maximum=MAX_TEXT)
        _strings(row['issues'], 'issues')
        if status != 'assessed' and not row['issues']:
            raise ValueError('unassessable/disputed status requires specific issues')
    except (ValueError, UnicodeError) as exc:
        errors.append(str(exc))
    return errors


def validate_response(assessment_input: dict, response: bytes) -> tuple[dict, dict]:
    """Validate against a frozen input. Retain raw rows; never repair or grade them.

    CLI verifies packet publication hashes first; direct callers must supply their
    original built input rather than reconstructing or modifying its packet.
    """
    bounded(response, 'response')
    if len(response) > MAX_RESPONSE_BYTES:
        raise ValueError(f'response exceeds {MAX_RESPONSE_BYTES} bytes')
    packet = assessment_input['packet']
    expected = [a['answer_id'] for a in packet['answers']]
    fixtures = {f['file']: physical_lines(f['text']) for f in packet['fixtures']}
    batch_errors, rows, citation_rows = [], [], []
    raw = None
    bound = True
    try:
        raw = json.loads(response.decode('utf-8'), object_pairs_hook=_ResponseObject,
                         parse_float=_response_float,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-JSON constant')))
        # Only object_pairs_hook creates actual response objects. A tagged
        # overflow number is a reporting view, not a JSON envelope.
        if not isinstance(raw, _ResponseObject):
            raise ValueError('response must be a JSON object')
    except (ValueError, UnicodeError, RecursionError) as exc:
        batch_errors.append(f'malformed response: {exc}')
        bound = False
    assessments = []
    if isinstance(raw, _ResponseObject):
        if raw.duplicate_keys:
            batch_errors.append('duplicate JSON envelope fields; no value selected for acceptance')
            bound = False
        if set(raw) != {'schema', 'packet_id', 'task_id', 'assessments'}:
            batch_errors.append('response exact fields required')
        for field, value in (('schema', RESPONSE_SCHEMA), ('packet_id', packet['packet_id']), ('task_id', packet['task_id'])):
            if raw.get(field) != value:
                batch_errors.append(f'{field} binding mismatch')
                bound = False
        if not isinstance(raw.get('assessments'), list) or len(raw['assessments']) > MAX_RESPONSE_ROWS:
            batch_errors.append('assessments must be a bounded array')
        else:
            assessments = raw['assessments']
    counts = Counter(r.get('answer_id') for r in assessments
                     if isinstance(r, dict) and isinstance(r.get('answer_id'), str))
    missing = [answer_id for answer_id in expected if counts[answer_id] == 0]
    if missing:
        batch_errors.append('missing answer IDs')
    order = [r.get('answer_id') if isinstance(r, dict) else None for r in assessments]
    if order != expected:
        batch_errors.append('answer ID inventory/order mismatch')
    for index, row in enumerate(assessments):
        errors = []
        answer_id = row.get('answer_id') if isinstance(row, dict) else None
        if not bound:
            errors.append('response envelope is not bound to this packet')
        if not isinstance(answer_id, str) or answer_id not in expected:
            errors.append('foreign or malformed answer ID')
        elif counts[answer_id] != 1:
            errors.append('duplicate answer ID; no duplicate selected')
        duplicate_fields = _duplicates(row)
        if duplicate_fields:
            errors.append('duplicate JSON fields; row is invalid, exact bytes retained')
        if isinstance(row, dict):
            errors.extend(_row_errors(row))
            ref_errors, refs = _source_ranges(row.get('source_refs'), fixtures)
        else:
            errors.append('assessment must be an object')
            ref_errors, refs = ['source_refs unavailable'], [{'valid': False, 'errors': ['nonobject row']}]
        errors.extend(ref_errors)
        citation_rows.append({'row_index': index, 'answer_id': answer_id, 'refs': refs})
        quality = None
        if not errors and row['status'] == 'assessed':
            quality = {k: row[k] for k in ('diagnosis_correct', 'evidence', 'recommendation')}
        rows.append({'row_index': index, 'answer_id': answer_id, 'raw': row,
                     'duplicate_fields': duplicate_fields,
                     'valid': not errors, 'errors': list(dict.fromkeys(errors)), 'quality': quality})
    validation = {'schema': VALIDATION_SCHEMA, 'packet_id': packet['packet_id'], 'task_id': packet['task_id'],
                  'assessment_input_sha256': digest(encode(assessment_input)), 'response_sha256': digest(response),
                  'valid': not batch_errors and all(row['valid'] for row in rows),
                  'batch_errors': batch_errors, 'envelope_duplicate_fields': getattr(raw, 'duplicate_keys', []),
                  'missing_answer_ids': missing,
                  'missing_quality': [{'answer_id': answer_id, 'quality': None, 'reason': 'missing assessment'}
                                      for answer_id in missing],
                  'rows': rows, 'assessment_kind': 'model assessment, not objective truth or human calibration'}
    reference_count = sum(len(row['refs']) for row in citation_rows)
    citation_report = {'schema': CITATION_SCHEMA, 'packet_id': packet['packet_id'],
                       'response_sha256': digest(response), 'source_sha256': packet['source_sha256'],
                       'scope': 'Only supplied source_refs original-file existence and physical inclusive ranges',
                       'semantic_correctness_checked': False, 'quotation_accuracy_checked': False,
                       'checked_reference_count': reference_count,
                       'status': 'checked' if reference_count else 'no_references_checked',
                       'valid': all(ref['valid'] for row in citation_rows for ref in row['refs']) if reference_count else None,
                       'rows': citation_rows}
    return validation, citation_report


def physical_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    for entry in [*reversed(path.parents), path]:
        try:
            mode = entry.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError('symlink paths/ancestors are not accepted')
    return path


def read_file(path: Path) -> bytes:
    path = physical_path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('input must be a regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('input changed to a nonregular file')
        return bounded(stream.read(MAX_BYTES + 1), 'input file')


def publish(output: Path, files: dict[str, bytes]) -> None:
    """Exclusive publication; partial files remain on any error; completion last."""
    output = physical_path(output)
    for name, data in files.items():
        relative_file(name)
        bounded(data, 'output file')
    if 'complete.json' in files:
        raise ValueError('completion receipt is generated by publisher')
    output.mkdir(mode=0o700)
    output.chmod(0o700)
    receipt = {'schema': 'sshai-benchmark/issue10-offline-publication-1',
               'files': {name: digest(data) for name, data in files.items()}}
    for name, data in [*files.items(), ('complete.json', encode(receipt))]:
        path = output / name
        if path.parent != output and not path.parent.exists():
            path.parent.mkdir(mode=0o700)
            path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())


def load_packet(root: Path) -> tuple[dict, dict]:
    root = physical_path(root)
    receipt = parse(read_file(root / 'complete.json'))
    exact(receipt, {'schema', 'files'}, 'publication receipt')
    if receipt['schema'] != 'sshai-benchmark/issue10-offline-publication-1':
        raise ValueError('invalid publication schema')
    expected = {'assessor/input.json', 'owner/inventory.json'}
    if not isinstance(receipt['files'], dict) or set(receipt['files']) != expected:
        raise ValueError('incomplete packet publication')
    values = {}
    for name in expected:
        path = root / name
        body = read_file(path)
        if (digest(body) != receipt['files'][name]
                or stat.S_IMODE(path.stat().st_mode) != 0o600
                or stat.S_IMODE(path.parent.stat().st_mode) != 0o700):
            raise ValueError('packet hash/private-permission mismatch')
        values[name] = parse(body)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError('packet root must be private')
    supplied, owner = values['assessor/input.json'], values['owner/inventory.json']
    if (supplied.get('schema') != INPUT_SCHEMA or owner.get('schema') != OWNER_SCHEMA
            or owner.get('assessment_input_sha256') != digest(encode(supplied))):
        raise ValueError('assessment input/owner binding mismatch')
    return supplied, owner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    packet_cmd = commands.add_parser('packet', help='build one new private task packet, no model invocation')
    packet_cmd.add_argument('--bundle', type=Path, required=True)
    packet_cmd.add_argument('--instructions', type=Path, required=True)
    packet_cmd.add_argument('--out', type=Path, required=True)
    validate_cmd = commands.add_parser('validate', help='retain exact response and objective validation, never repair')
    validate_cmd.add_argument('--packet', type=Path, required=True)
    validate_cmd.add_argument('--response', type=Path, required=True)
    validate_cmd.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'packet':
            raw = read_file(args.bundle)
            supplied, owner = build_packet(parse(raw), read_file(args.instructions))
            owner['pins']['bundle_file_sha256'] = digest(raw)
            publish(args.out, {'assessor/input.json': encode(supplied), 'owner/inventory.json': encode(owner)})
            return 0
        supplied, owner = load_packet(args.packet)
        raw = read_file(args.response)
        report, citations = validate_response(supplied, raw)
        report['packet_owner_sha256'] = digest(encode(owner))
        report['validator_source_sha256'] = digest(read_file(Path(__file__)))
        publish(args.out, {'response.bin': raw, 'validation.json': compact(report),
                           'citation-ranges.json': compact(citations)})
        return 0 if report['valid'] else 2
    except (OSError, ValueError, UnicodeError, RecursionError) as exc:
        print(f'offline assessment rejected: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
