"""Synthetic offline tests; no model, authentication, SSH or study reads."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import stat
import subprocess
import sys
import tempfile
import unittest

import benchmark_issue10_local_assessment as bridge
from benchmark_issue10_v3_review import RUBRIC
from benchmark_issue10_v3_cases import build_cases
from benchmark_issue10_v3_collector import MAX_PROMPT_BYTES

INSTRUCTIONS = Path(__file__).resolve().parents[1] / 'docs/benchmarks/issue10-assessment-instructions.md'


def blob(text):
    return {'text': text, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def bundle(repetitions=1):
    slots = []
    for rep in range(1, repetitions + 1):
        for arm in ('baseline', 'sshai'):
            slots.append({
                'slot_id': f'private-{rep}-{arm}', 'arm': arm, 'repetition': rep,
                'state': 'qualified', 'reason': 'independent final event attested',
                'answer': blob(f'{arm} verbatim\r\nα 😀\r\n'),
                'qualification': {
                    'status': 'independently_established_final',
                    'method': 'terminal_final_event',
                    'provenance': blob('Synthetic independent final-event receipt; not a live qualification.'),
                },
            })
    return {'schema': bridge.BUNDLE_SCHEMA, 'case_id': 'synthetic-case',
            'repetitions': repetitions,
            'prompt': blob('Original task\r\nDiagnose only.\r\n'),
            'semantic_key': blob('{"required_facts": ["synthetic only"]}\r\n'),
            'fixtures': [{'file': 'logs/example.txt', **blob('header\r\n\r\nα\u2028β\r\nlast')}],
            'slots': slots}


def selected_case_bundle(case_id):
    """Actual public synthetic case, with TEST-ONLY invented delivery receipts."""
    case = build_cases()[case_id]
    data = bundle()
    data.update(case_id=case_id, prompt=blob(case['prompt']),
                semantic_key=blob(bridge.encode(case['key']).decode('utf-8')),
                fixtures=[{'file': name, **blob(body)} for name, body in sorted(case['files'].items())])
    for slot in data['slots']:
        slot['reason'] = 'Synthetic test qualification only; no real pilot finality'
    return data


def response(packet):
    return {'schema': bridge.RESPONSE_SCHEMA, 'packet_id': packet['packet_id'],
            'task_id': packet['task_id'], 'assessments': [
                {'answer_id': answer['answer_id'], 'status': 'assessed',
                 'diagnosis_correct': True, 'evidence': 2, 'recommendation': 2,
                 'rationale': {'diagnosis': 'Synthetic test', 'evidence': 'Synthetic test',
                               'recommendation': 'Synthetic test'},
                 'source_refs': [{'file': 'logs/example.txt', 'start_line': 1, 'end_line': 4,
                                  'origin': 'answer', 'supports': 'Synthetic test span'}],
                 'unsupported_statements': [], 'unsafe_recommendations': [], 'issues': []}
                for answer in packet['answers']]}


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.instructions = INSTRUCTIONS.read_bytes()
        self.original = bundle(3)
        self.input, self.owner = bridge.build_packet(self.original, self.instructions, rng=random.Random(7))
        self.packet = self.input['packet']

    def validate(self, value):
        return bridge.validate_response(self.input, bridge.encode(value))

    def test_exact_sources_answers_rubric_prompt_pins(self):
        packet = self.packet
        self.assertEqual(packet['rubric'], RUBRIC)
        expected_section = self.instructions[
            self.instructions.index(b'## Reusable assessor prompt\n'):
            self.instructions.index(b'## Static review scenarios (not collected-answer grades)\n')]
        self.assertEqual(self.input['instructions'].encode(), expected_section)
        self.assertEqual(self.input['instructions'].encode(), bridge.assessor_section(self.instructions))
        self.assertEqual(self.owner['pins']['instructions_file_sha256'], bridge.digest(self.instructions))
        self.assertEqual(self.owner['pins']['assessor_section_sha256'], bridge.digest(self.input['instructions'].encode()))
        self.assertEqual(self.owner['pins']['bridge_source_sha256'], bridge.digest(Path(bridge.__file__).read_bytes()))
        self.assertEqual(packet['fixtures'][0]['line_count'], 4)
        self.assertEqual(packet['fixtures'][0]['text'], self.original['fixtures'][0]['text'])
        self.assertEqual(sorted(a['text'] for a in packet['answers']),
                         sorted(s['answer']['text'] for s in self.original['slots']))
        for answer in packet['answers']:
            self.assertEqual(answer['sha256'], bridge.digest(answer['text'].encode()))
        for field in ('prompt', 'semantic_key'):
            self.assertEqual(packet[field], self.original[field])
        self.assertEqual(packet['source_sha256'], self.owner['pins']['source_sha256'])
        self.assertEqual(bridge.physical_lines(''), 0)
        self.assertEqual(bridge.physical_lines('a\n'), 1)
        self.assertEqual(bridge.physical_lines('a\n\n'), 2)

    def test_hidden_labels_randomized_all_repetitions(self):
        self.assertEqual(len(self.packet['answers']), 6)
        visible = copy.deepcopy(self.input)
        for answer in visible['packet']['answers']:
            answer['text'] = 'exact wording may reveal labels'
        body = bridge.encode(visible)
        for secret in (b'private-', b'"arm"', b'"repetition"', b'"slot_id"', b'"usage"', b'synthetic-case'):
            self.assertNotIn(secret, body)
        public_order = [a['answer_id'] for a in self.packet['answers']]
        owner_order = [s['answer_id'] for s in self.owner['slots']]
        self.assertNotEqual(public_order, owner_order)
        self.assertEqual(set(public_order), set(owner_order))
        self.assertIn('not guaranteed', self.packet['blinding'])
        self.assertEqual(self.owner['qualification_basis'], 'caller-declared independent finality; not verified by this bridge')

    def test_unknown_inventory_retains_exact_bytes(self):
        data = bundle(3)
        for index, state in enumerate(('absent', 'lost', 'unqualified')):
            slot = data['slots'][index]
            slot['state'], slot['reason'] = state, f'retained {state}'
            slot['qualification'] = None
            if state == 'absent':
                slot['answer'] = None
        supplied, owner = bridge.build_packet(data, self.instructions)
        self.assertEqual(len(supplied['packet']['answers']), 3)
        self.assertEqual(len(owner['slots']), 6)
        for index in range(3):
            self.assertIsNone(owner['slots'][index]['answer_id'])
            self.assertEqual(owner['slots'][index]['answer'], data['slots'][index]['answer'])
            self.assertEqual(owner['slots'][index]['reason'], data['slots'][index]['reason'])

    def test_refuse_unknown_finality_and_completion_only(self):
        for qualification in (None, {'status': 'unknown', 'method': 'terminal_final_event',
                                     'provenance': blob('unknown')},
                              {'status': 'independently_established_final', 'method': 'byte_match',
                               'provenance': blob('match is not finality')}):
            data = bundle()
            data['slots'][0]['qualification'] = qualification
            with self.assertRaises(ValueError):
                bridge.build_packet(data, self.instructions)

    def test_pins_and_full_planned_population_required(self):
        for mutate in (lambda d: d['prompt'].update(text='changed'),
                       lambda d: d['semantic_key'].update(sha256='0' * 64),
                       lambda d: d['fixtures'][0].update(text='changed'),
                       lambda d: d['slots'][0]['answer'].update(text='changed'),
                       lambda d: d['slots'].pop(),
                       lambda d: d['fixtures'][0].update(file='../secret'),
                       lambda d: d.update(repetitions=True)):
            data = bundle()
            mutate(data)
            with self.assertRaises(ValueError):
                bridge.build_packet(data, self.instructions)

    def test_contract_and_objective_ranges_not_semantics(self):
        report, citations = self.validate(response(self.packet))
        self.assertTrue(report['valid'])
        self.assertTrue(all(r['valid'] for r in report['rows']))
        self.assertTrue(citations['valid'])
        self.assertFalse(citations['semantic_correctness_checked'])
        self.assertFalse(citations['quotation_accuracy_checked'])

    def test_partial_errors_retained_no_fix(self):
        value = response(self.packet)
        value['assessments'][0]['evidence'] = True
        value['assessments'][1]['rationale']['extra'] = 'invalid'
        value['assessments'][2]['status'] = 'disputed'
        report, _ = self.validate(value)
        self.assertFalse(report['valid'])
        self.assertEqual([r['valid'] for r in report['rows']], [False, False, False, True, True, True])
        self.assertIsNone(report['rows'][0]['quality'])
        self.assertEqual(report['rows'][0]['raw'], value['assessments'][0])

    def test_unknowns_and_disputes_null_and_explained(self):
        for status in ('unassessable', 'disputed'):
            value = response(self.packet)
            value['assessments'][0].update(status=status, diagnosis_correct=None,
                                            evidence=None, recommendation=None, issues=['synthetic gap'])
            report, _ = self.validate(value)
            self.assertTrue(report['valid'])
            self.assertIsNone(report['rows'][0]['quality'])
            value['assessments'][0]['issues'] = []
            self.assertFalse(self.validate(value)[0]['rows'][0]['valid'])

    def test_duplicates_foreign_missing_and_order(self):
        value = response(self.packet)
        missing_id = value['assessments'][1]['answer_id']
        value['assessments'].pop(1)
        value['assessments'].append(copy.deepcopy(value['assessments'][0]))
        value['assessments'].append({**value['assessments'][2], 'answer_id': 'f' * 32})
        report, _ = self.validate(value)
        self.assertFalse(report['valid'])
        self.assertEqual(len(report['rows']), 7)
        self.assertFalse(report['rows'][0]['valid'])
        self.assertFalse(report['rows'][-1]['valid'])
        self.assertEqual(report['missing_answer_ids'], [missing_id])
        self.assertTrue(any(row['valid'] for row in report['rows']))
        value = response(self.packet)
        value['assessments'].reverse()
        report, _ = self.validate(value)
        self.assertFalse(report['valid'])
        self.assertTrue(all(row['valid'] for row in report['rows']))

    def test_duplicate_json_fields_do_not_discard_other_valid_rows(self):
        raw = bridge.encode(response(self.packet)).replace(b'"evidence": 2', b'"evidence": 2, "evidence": 1', 1)
        report, _ = bridge.validate_response(self.input, raw)
        self.assertFalse(report['valid'])
        self.assertFalse(report['rows'][0]['valid'])
        self.assertTrue(report['rows'][1]['valid'])
        self.assertEqual(report['rows'][0]['duplicate_fields'][0]['keys'], ['evidence'])
        self.assertIsNone(report['rows'][0]['quality'])
        self.assertEqual(report['response_sha256'], bridge.digest(raw))

    def test_nonobject_row_retained_and_dimensions_typed(self):
        value = response(self.packet)
        value['assessments'][0] = 'not an assessment'
        report, _ = self.validate(value)
        self.assertFalse(report['rows'][0]['valid'])
        self.assertEqual(report['rows'][0]['raw'], 'not an assessment')
        self.assertTrue(report['rows'][1]['valid'])
        for field, invalid in (('diagnosis_correct', 1), ('evidence', -1), ('recommendation', 3),
                               ('evidence', 1.0), ('recommendation', '2'), ('status', 'unknown')):
            value = response(self.packet)
            value['assessments'][0][field] = invalid
            self.assertFalse(self.validate(value)[0]['rows'][0]['valid'])

    def test_source_contract_invalid_rows_only(self):
        for bad in ({'file': 'unknown.txt'}, {'start_line': True}, {'end_line': 5},
                    {'start_line': 0}, {'start_line': 3, 'end_line': 2},
                    {'origin': 'tool'}, {'extra': 'field'}):
            value = response(self.packet)
            value['assessments'][0]['source_refs'][0].update(bad)
            report, citations = self.validate(value)
            self.assertFalse(report['rows'][0]['valid'])
            self.assertTrue(report['rows'][1]['valid'])
            self.assertFalse(citations['valid'])

    def test_missing_source_fields_and_no_reference_claim(self):
        for field in ('file', 'start_line', 'end_line'):
            value = response(self.packet)
            del value['assessments'][0]['source_refs'][0][field]
            report, citations = self.validate(value)
            self.assertFalse(report['rows'][0]['valid'])
            self.assertFalse(citations['valid'])
            self.assertTrue(report['rows'][1]['valid'])
        value = response(self.packet)
        for row in value['assessments']:
            row['source_refs'] = []
        report, citations = self.validate(value)
        self.assertTrue(report['valid'])
        self.assertEqual(citations['status'], 'no_references_checked')
        self.assertIsNone(citations['valid'])

    def test_malformed_and_binding_failures(self):
        for raw in (b'not json', b'[]', b'{"schema":1,"schema":2}', b'\xff'):
            report, _ = bridge.validate_response(self.input, raw)
            self.assertFalse(report['valid'])
            self.assertEqual(len(report['missing_answer_ids']), 6)
        for field in ('schema', 'packet_id', 'task_id'):
            value = response(self.packet)
            value[field] = 'foreign'
            report, _ = self.validate(value)
            self.assertFalse(report['valid'])
            self.assertTrue(all(not r['valid'] for r in report['rows']))

    def test_selected_cases_fit_collector_cap_with_exact_material(self):
        for case_id in ('M01', 'M02'):
            with self.subTest(case_id=case_id):
                data = selected_case_bundle(case_id)
                case = build_cases()[case_id]
                if case_id == 'M02':
                    self.assertGreater(len(bridge.encode(data)), 512 * 1024)
                supplied, owner = bridge.build_packet(data, self.instructions)
                self.assertLessEqual(len(bridge.encode(supplied)), MAX_PROMPT_BYTES)
                self.assertEqual(supplied['packet']['prompt']['text'].encode(), case['prompt'].encode())
                self.assertEqual(supplied['packet']['semantic_key'], data['semantic_key'])
                for fixture in supplied['packet']['fixtures']:
                    self.assertEqual(fixture['text'].encode(), case['files'][fixture['file']].encode())
                    self.assertEqual(fixture['sha256'], bridge.digest(case['files'][fixture['file']].encode()))
                self.assertEqual(sorted(a['text'].encode() for a in supplied['packet']['answers']),
                                 sorted(s['answer']['text'].encode() for s in data['slots']))
                self.assertEqual(owner['slots'][0]['answer'], data['slots'][0]['answer'])
                self.assertEqual(len(owner['slots']), 2)

    def test_bounds(self):
        data = bundle()
        data['prompt'] = blob('x' * bridge.MAX_BYTES)
        with self.assertRaises(ValueError):
            bridge.build_packet(data, self.instructions)
        with self.assertRaises(ValueError):
            bridge.validate_response(self.input, b'x' * (bridge.MAX_RESPONSE_BYTES + 1))
        self.assertLess(bridge.MAX_RESPONSE_BYTES, bridge.MAX_BYTES)
        self.assertEqual(bridge.MAX_BYTES, MAX_PROMPT_BYTES)
        data = bundle()
        overhead = len(bridge.encode(data)) - len(data['prompt']['text'].encode())
        data['prompt'] = blob('x' * (bridge.MAX_BYTES - overhead - 1))
        self.assertLessEqual(len(bridge.encode(data)), bridge.MAX_BYTES)
        with self.assertRaisesRegex(ValueError, 'self-contained assessment input'):
            bridge.build_packet(data, self.instructions)


class CLITests(unittest.TestCase):
    def run_cli(self, *args, ok=True):
        proc = subprocess.run([sys.executable, str(Path(bridge.__file__)), *map(str, args)],
                              capture_output=True, timeout=10)
        self.assertEqual(proc.returncode == 0, ok, proc.stderr.decode())
        return proc

    def test_large_selected_case_private_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            data = selected_case_bundle('M02')
            source = root / 'bundle.json'
            source.write_bytes(bridge.encode(data))
            output = root / 'packet'
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output)
            supplied, owner = bridge.load_packet(output)
            self.assertLessEqual((output / 'assessor/input.json').stat().st_size, MAX_PROMPT_BYTES)
            self.assertEqual(supplied['packet']['prompt'], data['prompt'])
            self.assertEqual(supplied['packet']['semantic_key'], data['semantic_key'])
            self.assertEqual({f['file']: f['text'].encode() for f in supplied['packet']['fixtures']},
                             {f['file']: f['text'].encode() for f in data['fixtures']})
            self.assertEqual(sorted(a['text'].encode() for a in supplied['packet']['answers']),
                             sorted(s['answer']['text'].encode() for s in data['slots']))
            self.assertEqual(owner['pins']['bundle_file_sha256'], bridge.digest(source.read_bytes()))
            # Synthetic null-quality response, not an actual assessor grade.
            value = response(supplied['packet'])
            for row in value['assessments']:
                row.update(status='unassessable', diagnosis_correct=None, evidence=None,
                           recommendation=None, source_refs=[], issues=['synthetic roundtrip only'])
            raw = bridge.encode(value) + b'\r\n'
            reply = root / 'reply.json'
            reply.write_bytes(raw)
            assessment = root / 'assessment'
            self.run_cli('validate', '--packet', output, '--response', reply, '--out', assessment)
            self.assertEqual((assessment / 'response.bin').read_bytes(), raw)
            self.assertTrue(json.loads((assessment / 'validation.json').read_bytes())['valid'])
            for tree in (output, assessment):
                for entry in [tree, *tree.rglob('*')]:
                    self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)
                    if entry.is_file():
                        self.assertLessEqual(entry.stat().st_size, MAX_PROMPT_BYTES)

    def test_private_roundtrip_no_overwrite_and_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'bundle.json'
            source.write_bytes(bridge.encode(bundle()))
            output = root / 'packet'
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output)
            supplied = json.loads((output / 'assessor/input.json').read_bytes())
            owner = json.loads((output / 'owner/inventory.json').read_bytes())
            self.assertEqual(owner['pins']['bundle_file_sha256'], bridge.digest(source.read_bytes()))
            answer = supplied['packet']['answers'][0]
            self.assertEqual(answer['text'].encode(), bundle()['slots'][0]['answer']['text'].encode()
                             if 'baseline' in answer['text'] else bundle()['slots'][1]['answer']['text'].encode())
            raw = bridge.encode(response(supplied['packet'])) + b'\r\n'
            reply = root / 'reply.json'
            reply.write_bytes(raw)
            assessment = root / 'assessment'
            self.run_cli('validate', '--packet', output, '--response', reply, '--out', assessment)
            self.assertEqual((assessment / 'response.bin').read_bytes(), raw)
            for tree in (output, assessment):
                for entry in [tree, *tree.rglob('*')]:
                    self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output, ok=False)
            self.run_cli('validate', '--packet', output, '--response', reply, '--out', assessment, ok=False)
            (output / 'assessor/input.json').write_bytes(b'{}')
            self.run_cli('validate', '--packet', output, '--response', reply, '--out', root / 'tampered', ok=False)

    def test_nonregular_symlink_ancestor_and_missing_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'input.json'
            source.write_bytes(bridge.encode(bundle()))
            link = root / 'linked.json'
            link.symlink_to(source)
            linkdir = root / 'linked-directory'
            linkdir.symlink_to(root, target_is_directory=True)
            fifo = root / 'fifo'
            os.mkfifo(fifo)
            for path in (link, linkdir / 'input.json', root, fifo, root / 'missing'):
                self.run_cli('packet', '--bundle', path, '--instructions', INSTRUCTIONS,
                             '--out', root / 'out', ok=False)
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS,
                         '--out', linkdir / 'out', ok=False)

    def test_overflow_numeric_lexemes_retain_private_reports_and_valid_sibling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'input.json'
            source.write_bytes(bridge.encode(bundle()))
            output = root / 'packet'
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output)
            supplied = json.loads((output / 'assessor/input.json').read_bytes())
            original = bridge.encode(response(supplied['packet']))
            variants = (
                (b'"evidence": 2', b'"evidence": 1e999', '1e999'),
                (b'"evidence": 2', b'"evidence": -1e999', '-1e999'),
                (b'"start_line": 1', b'"start_line": 1e999', '1e999'),
                (b'"diagnosis": "Synthetic test"', b'"diagnosis": -1e999', '-1e999'),
            )
            for index, (old, new, lexeme) in enumerate(variants):
                with self.subTest(variant=index):
                    raw = original.replace(old, new, 1) + b'\r\n'
                    self.assertNotEqual(raw, original + b'\r\n')
                    reply = root / f'reply-{index}.json'
                    reply.write_bytes(raw)
                    destination = root / f'validation-{index}'
                    proc = self.run_cli('validate', '--packet', output, '--response', reply,
                                        '--out', destination, ok=False)
                    self.assertEqual(proc.returncode, 2)
                    self.assertTrue((destination / 'complete.json').is_file(), proc.stderr.decode())
                    self.assertEqual((destination / 'response.bin').read_bytes(), raw)
                    report = json.loads((destination / 'validation.json').read_bytes())
                    self.assertFalse(report['valid'])
                    self.assertFalse(report['rows'][0]['valid'])
                    self.assertIsNone(report['rows'][0]['quality'])
                    self.assertTrue(report['rows'][1]['valid'])
                    self.assertEqual(report['rows'][1]['quality'],
                                     {'diagnosis_correct': True, 'evidence': 2, 'recommendation': 2})
                    self.assertIn(lexeme, json.dumps(report['rows'][0]['raw']))
                    citations = json.loads((destination / 'citation-ranges.json').read_bytes())
                    if index == 2:
                        self.assertFalse(citations['valid'])
                    for entry in [destination, *destination.rglob('*')]:
                        self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)

    def test_escaped_invalid_unicode_response_is_retained_not_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'input.json'
            source.write_bytes(bridge.encode(bundle()))
            output = root / 'packet'
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output)
            supplied = json.loads((output / 'assessor/input.json').read_bytes())
            value = response(supplied['packet'])
            value['assessments'][0]['rationale']['diagnosis'] = '\ud800'
            raw = json.dumps(value, ensure_ascii=True).encode()
            reply = root / 'reply'
            reply.write_bytes(raw)
            destination = root / 'validation'
            self.run_cli('validate', '--packet', output, '--response', reply, '--out', destination, ok=False)
            report = json.loads((destination / 'validation.json').read_bytes())
            self.assertFalse(report['rows'][0]['valid'])
            self.assertTrue(report['rows'][1]['valid'])
            self.assertEqual((destination / 'response.bin').read_bytes(), raw)

    def test_malformed_response_retained_with_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'input.json'
            source.write_bytes(bridge.encode(bundle()))
            output = root / 'packet'
            self.run_cli('packet', '--bundle', source, '--instructions', INSTRUCTIONS, '--out', output)
            for index, raw in enumerate((b'{ malformed\r\n', b'1e999', b'-1e999', b'null', b'[]', b'1.5')):
                with self.subTest(response=raw):
                    reply = root / f'reply-{index}'
                    reply.write_bytes(raw)
                    destination = root / f'validation-{index}'
                    proc = self.run_cli('validate', '--packet', output, '--response', reply,
                                        '--out', destination, ok=False)
                    self.assertEqual(proc.returncode, 2)
                    self.assertTrue((destination / 'complete.json').is_file())
                    self.assertEqual((destination / 'response.bin').read_bytes(), raw)
                    report = json.loads((destination / 'validation.json').read_bytes())
                    self.assertFalse(report['valid'])
                    self.assertEqual(len(report['missing_answer_ids']), 2)
                    self.assertTrue(all(item['quality'] is None for item in report['missing_quality']))


if __name__ == '__main__':
    unittest.main()
