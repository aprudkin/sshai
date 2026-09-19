#!/usr/bin/env python3
"""Offline human-review packet tests; no model, SSH, or live study execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_v3 as runner
import benchmark_issue10_v3_collector as collector
import benchmark_issue10_v3_review as review_packet
from test_issue10_v3_capture import cli_records, rollout_records, process, jsonl


def result(session: str, answer: str, **changes):
    value = {
        'session_id': session, 'execution': 'completed',
        'answer_state': 'captured', 'final_answer': answer,
        'usage': {'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10},
        'usage_complete': True, 'instrumentation': 'valid',
        'boundary': 'compliant', 'review': None,
    }
    return {**value, **changes}


def human_review(**changes):
    value = {
        'status': 'assessed', 'diagnosis_correct': True,
        'evidence': 2, 'recommendation': 2,
        'reviewer': 'synthetic-human',
        'reason': 'Synthetic assessment used only to test append-only integration.',
    }
    return {**value, **changes}


def all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_keys(item)


class SpySystemRandom:
    def __init__(self):
        self.next_value = 0
        self.shuffle_sizes = []

    def getrandbits(self, bits):
        if bits != 128:
            raise AssertionError('unexpected random width')
        self.next_value += 1
        return self.next_value

    def shuffle(self, values):
        self.shuffle_sizes.append(len(values))
        values.reverse()


class ReviewPacketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'study'
        self.plan = runner.prepare(self.root, 'pilot')

    def tearDown(self):
        self.temp.cleanup()

    def export(self, name='review-output'):
        output = self.base / name
        summary = review_packet.export_packet(self.root, output)
        owner = json.loads((self.root / 'review-packets' / f"{summary['packet_id']}.json").read_bytes())
        packet = json.loads((output / 'packet.json').read_bytes())
        return output, summary, owner, packet

    def test_randomized_blind_export_preserves_answers_and_complete_private_inventory(self):
        first_slot, second_slot = self.plan['slots'][:2]
        self.assertEqual(first_slot['case_id'], second_slot['case_id'])
        self.assertNotEqual(first_slot['arm'], second_slot['arm'])
        first = '  Baseline-shaped wording and tool note.\n\nKeep trailing space:  '
        second = 'sshai-shaped wording — exact Unicode.\nNo added newline.'
        runner.import_result(self.root, first_slot['slot'], result('private-session-a', first))
        runner.import_result(self.root, second_slot['slot'], result('private-session-b', second))
        runner.record_review(self.root, first_slot['slot'], human_review(evidence=1))
        runner.import_result(
            self.root, self.plan['slots'][2]['slot'],
            result('private-session-c', '', execution='timeout', answer_state='absent',
                   final_answer=None),
        )
        runner.import_result(
            self.root, self.plan['slots'][3]['slot'],
            result('private-session-d', '', answer_state='lost', final_answer=None),
        )
        reserved_slot = self.plan['slots'][4]['slot']
        with patch.object(runner.legacy, '_bounded_process',
                          side_effect=RuntimeError('synthetic pre-spawn reservation stop')):
            with self.assertRaisesRegex(RuntimeError, 'reservation stop'):
                runner.collect_slot(
                    self.root, reserved_slot, [sys.executable, '-c', 'pass'],
                    prompt=b'synthetic reservation',
                    env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'},
                    cwd=self.base, timeout_seconds=2,
                )

        spy = SpySystemRandom()
        with patch.object(review_packet.random, 'SystemRandom', return_value=spy) as factory:
            output, summary, owner, packet = self.export()
        factory.assert_called_once_with()
        self.assertEqual(spy.shuffle_sizes, [2, 1])
        self.assertEqual([entry['answer_id'] for entry in packet['answers']],
                         list(reversed([entry['answer_id'] for entry in owner['answers']])))
        self.assertRegex(summary['packet_id'], r'^[0-9a-f]{32}$')
        self.assertEqual(summary['answer_count'], 2)
        self.assertEqual(summary['excluded_slot_count'], 10)
        self.assertIn('scripts/benchmark_issue10_v3_review.py', self.plan['sources'])
        self.assertEqual(len(packet['answers']), 2)
        self.assertEqual(len(packet['tasks']), 1)
        self.assertTrue(all(entry['answer_id'] not in {'1', '2'} for entry in packet['answers']))
        self.assertEqual(
            {(output / entry['answer']['path']).read_text() for entry in packet['answers']},
            {first, second},
        )
        task = packet['tasks'][0]
        case_id = first_slot['case_id']
        self.assertEqual(
            (output / task['prompt']['path']).read_bytes(),
            (self.root / 'prepared' / f'prompts/{case_id}.md').read_bytes(),
        )
        self.assertEqual(
            (output / task['semantic_key']['path']).read_bytes(),
            (self.root / 'prepared' / f'evaluator/{case_id}/key.json').read_bytes(),
        )
        self.assertFalse(any('planned-prompts' in item['path'] for item in owner['packet_files']))
        forbidden = {'slot', 'arm', 'session_id', 'usage', 'usage_complete', 'review',
                     'reviewer', 'diagnosis_correct', 'evidence', 'recommendation'}
        # The rubric necessarily names score fields, so apply the coordinator-
        # metadata check to packet answer/task presentation metadata only.
        presentation = {'tasks': packet['tasks'], 'answers': packet['answers']}
        self.assertTrue(forbidden.isdisjoint(set(all_keys(presentation))))
        serialized_packet = json.dumps(packet)
        self.assertNotIn('private-session-', serialized_packet)
        self.assertNotIn('"arm"', serialized_packet)
        self.assertNotIn('baseline branch', serialized_packet.lower())
        self.assertNotIn('sshai branch', serialized_packet.lower())
        self.assertNotIn('synthetic-human', serialized_packet)
        self.assertIn('lexical blinding', (output / 'STATUS.txt').read_text())

        self.assertEqual(len(owner['slots']), len(self.plan['slots']))
        inventory = {item['slot']: item for item in owner['slots']}
        self.assertEqual(inventory[first_slot['slot']]['reason'], 'captured_answer_exported')
        self.assertEqual(inventory[self.plan['slots'][2]['slot']]['record_state'], 'absent')
        self.assertEqual(inventory[self.plan['slots'][2]['slot']]['reason'],
                         'timeout_without_final_answer')
        self.assertEqual(inventory[self.plan['slots'][3]['slot']]['record_state'], 'lost')
        self.assertEqual(inventory[self.plan['slots'][3]['slot']]['reason'], 'final_answer_lost')
        self.assertEqual(inventory[reserved_slot]['record_state'], 'missing')
        self.assertEqual(inventory[reserved_slot]['reason'], 'reserved_without_result')
        unattempted_slot = self.plan['slots'][5]['slot']
        self.assertEqual(inventory[unattempted_slot]['record_state'], 'missing')
        self.assertEqual(inventory[unattempted_slot]['reason'], 'unattempted')
        for item in owner['answers']:
            self.assertEqual(len(item['record_sha256']), 64)
            self.assertEqual(len(item['record_envelope_sha256']), 64)
            self.assertIsNone(item['capture_sha256'])
        self.assertEqual(owner['plan_digest'], self.plan['digest'])

    def _collector_attempt(self):
        attempt = self.base / 'collector-attempt'
        answer = self.base / 'collector-answer.txt'
        rollout = self.base / 'collector-rollout.jsonl'
        events = cli_records()
        persisted = rollout_records()
        events[0]['thread_id'] = 'collector-thread'
        persisted[0]['payload']['id'] = 'collector-thread'
        event_bytes = jsonl(events)
        rollout.write_bytes(jsonl(persisted))
        command = (
            "import sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text('unqualified delivered answer'); "
            "sys.stdout.buffer.write(bytes.fromhex(sys.argv[2]))"
        )
        collector.collect_attempt(
            attempt, [sys.executable, '-c', command, str(answer), event_bytes.hex()],
            prompt=b'synthetic prompt',
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'},
            cwd=self.base, timeout_seconds=2,
            rollout_candidates=[rollout], answer_path=answer,
        )
        return attempt

    def test_collector_unknown_finality_stays_lost_and_is_excluded(self):
        runner.import_collector(self.root, 1, self._collector_attempt())
        output, summary, owner, packet = self.export()
        self.assertEqual(summary['answer_count'], 0)
        self.assertEqual(packet['answers'], [])
        self.assertEqual(packet['tasks'], [])
        slot = next(item for item in owner['slots'] if item['slot'] == 1)
        self.assertEqual(slot['record_state'], 'lost')
        self.assertEqual(slot['reason'], 'final_answer_lost')
        self.assertEqual(slot['record_binding']['provenance'], runner.COLLECTOR_PROVENANCE)
        self.assertIsNone(slot['answer_id'])
        with self.assertRaisesRegex(ValueError, 'not present'):
            review_packet.resolve_answer(self.root, summary['packet_id'], '0' * 32)
        self.assertTrue((output / 'packet.json').is_file())

    def test_resolve_rejects_map_answer_task_record_tampering_and_bad_ids(self):
        runner.import_result(self.root, 1, result('session-1', 'Exact answer'))

        output, summary, owner, packet = self.export('answer-tamper')
        answer_id = owner['answers'][0]['answer_id']
        (output / f'answers/{answer_id}.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'file mismatch'):
            review_packet.resolve_answer(self.root, summary['packet_id'], answer_id)

        output, summary, owner, packet = self.export('task-tamper')
        answer_id = owner['answers'][0]['answer_id']
        task_id = owner['answers'][0]['task_id']
        (output / f'tasks/{task_id}/task.md').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'file mismatch'):
            review_packet.resolve_answer(self.root, summary['packet_id'], answer_id)

        output, summary, owner, packet = self.export('map-tamper')
        answer_id = owner['answers'][0]['answer_id']
        owner_path = self.root / 'review-packets' / f"{summary['packet_id']}.json"
        owner['answers'][0]['slot'] = 2
        owner_path.write_bytes(runner.encoded(owner))
        with self.assertRaisesRegex(ValueError, 'completion binding'):
            review_packet.resolve_answer(self.root, summary['packet_id'], answer_id)

        output, summary, owner, packet = self.export('record-tamper')
        answer_id = owner['answers'][0]['answer_id']
        record_path = self.root / 'records/001.json'
        original = record_path.read_bytes()
        document = json.loads(original)
        document['record']['final_answer'] = 'tampered record'
        record_path.write_bytes(runner.encoded(document))
        with self.assertRaises(ValueError):
            review_packet.resolve_answer(self.root, summary['packet_id'], answer_id)
        record_path.write_bytes(original)

        with self.assertRaisesRegex(ValueError, 'invalid packet ID'):
            review_packet.resolve_answer(self.root, '../packet', answer_id)
        with self.assertRaisesRegex(ValueError, 'invalid answer ID'):
            review_packet.resolve_answer(self.root, summary['packet_id'], '../answer')
        with self.assertRaises(ValueError):
            review_packet.resolve_answer(self.root, 'f' * 32, answer_id)
        with self.assertRaisesRegex(ValueError, 'not present'):
            review_packet.resolve_answer(self.root, summary['packet_id'], 'e' * 32)

    def test_oversized_answer_and_owner_fail_before_publication(self):
        large_answer = 'x' * (review_packet.MAX_PACKET_FILE_BYTES + 1)
        runner.import_result(self.root, 1, result('large-session', large_answer))
        output = self.base / 'oversized-output'
        with self.assertRaisesRegex(ValueError, 'packet file exceeds'):
            review_packet.export_packet(self.root, output)
        self.assertFalse(output.exists())
        owner_directory = self.root / 'review-packets'
        self.assertEqual(list(owner_directory.iterdir()) if owner_directory.exists() else [], [])

        owner_output = self.base / 'oversized-owner-output'
        with patch.object(review_packet, 'MAX_PACKET_FILE_BYTES', len(large_answer) + 1):
            with patch.object(review_packet, 'MAX_OWNER_BYTES', 1):
                with self.assertRaisesRegex(ValueError, 'owner metadata exceeds'):
                    review_packet.export_packet(self.root, owner_output)
        self.assertFalse(owner_output.exists())
        self.assertEqual(list(owner_directory.iterdir()) if owner_directory.exists() else [], [])

    def test_private_permissions_overwrite_symlink_and_path_separation(self):
        runner.import_result(self.root, 1, result('session-1', 'answer'))
        output, summary, owner, packet = self.export()
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / 'review-packets').stat().st_mode), 0o700)
        for path in output.rglob('*'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        for suffix in ('.json', '.pending.json', '.complete.json'):
            path = self.root / 'review-packets' / f"{summary['packet_id']}{suffix}"
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        before = {path.relative_to(output): path.read_bytes()
                  for path in output.rglob('*') if path.is_file()}
        with self.assertRaisesRegex(ValueError, 'refusing existing'):
            review_packet.export_packet(self.root, output)
        self.assertEqual(before, {path.relative_to(output): path.read_bytes()
                                  for path in output.rglob('*') if path.is_file()})

        target = self.base / 'target'
        target.mkdir()
        link = self.base / 'linked-output'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            review_packet.export_packet(self.root, link)
        linked_parent = self.base / 'linked-parent'
        linked_parent.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            review_packet.export_packet(self.root, linked_parent / 'packet')
        with self.assertRaisesRegex(ValueError, 'separate'):
            review_packet.export_packet(self.root, self.root / 'packet')
        with self.assertRaisesRegex(ValueError, 'separate'):
            review_packet.export_packet(self.root, self.root.parent)

    def test_partial_publication_retains_pending_state_and_existing_files(self):
        runner.import_result(self.root, 1, result('session-1', 'answer'))
        output = self.base / 'partial-output'
        keep = self.base / 'keep.txt'
        keep.write_text('preserve me')
        original_write = runner.legacy._write_new

        def fail_on_rubric(path, data, mode=0o600):
            if Path(path) == output / 'rubric.json':
                raise OSError('synthetic publication failure')
            return original_write(path, data, mode)

        with patch.object(runner.legacy, '_write_new', side_effect=fail_on_rubric):
            with self.assertRaisesRegex(OSError, 'synthetic publication failure'):
                review_packet.export_packet(self.root, output)
        pending = list((self.root / 'review-packets').glob('*.pending.json'))
        self.assertEqual(len(pending), 1)
        packet_id = pending[0].name.removesuffix('.pending.json')
        receipt = json.loads(pending[0].read_bytes())
        self.assertIn('pending', receipt['status'])
        self.assertTrue(output.is_dir())
        self.assertFalse((output / 'packet.json').exists())
        self.assertFalse((self.root / 'review-packets' / f'{packet_id}.complete.json').exists())
        self.assertEqual(keep.read_text(), 'preserve me')
        with self.assertRaises(ValueError):
            review_packet.resolve_answer(self.root, packet_id, '0' * 32)

        late_output = self.base / 'late-partial-output'

        def fail_on_completion(path, data, mode=0o600):
            if str(path).endswith('.complete.json'):
                raise OSError('synthetic completion failure')
            return original_write(path, data, mode)

        with patch.object(runner.legacy, '_write_new', side_effect=fail_on_completion):
            with self.assertRaisesRegex(OSError, 'synthetic completion failure'):
                review_packet.export_packet(self.root, late_output)
        late_pending = [path for path in (self.root / 'review-packets').glob('*.pending.json')
                        if path != pending[0]]
        self.assertEqual(len(late_pending), 1)
        late_packet_id = late_pending[0].name.removesuffix('.pending.json')
        self.assertTrue((late_output / 'packet.json').is_file())
        self.assertTrue((self.root / 'review-packets' / f'{late_packet_id}.json').is_file())
        self.assertFalse((self.root / 'review-packets' / f'{late_packet_id}.complete.json').exists())
        with self.assertRaises(ValueError):
            review_packet.resolve_answer(self.root, late_packet_id, '0' * 32)
        self.assertEqual(keep.read_text(), 'preserve me')

    def test_resolve_then_existing_review_and_analysis_roundtrip(self):
        runner.import_capture(
            self.root, 1, jsonl(cli_records()), jsonl(rollout_records()),
            runner.encoded(process()), b'answer to assess',
        )
        output, summary, owner, packet = self.export()
        self.assertEqual(owner['answers'][0]['capture_sha256'],
                         runner._read_envelope(self.root / 'records/001.json')['capture_sha256'])
        answer_id = packet['answers'][0]['answer_id']
        slot = review_packet.resolve_answer(self.root, summary['packet_id'], answer_id)
        self.assertEqual(slot, 1)
        runner.record_review(self.root, slot, human_review())
        report = runner.analyze_root(self.root)
        row = next(item for item in report['slots'] if item['slot'] == slot)
        self.assertEqual(row['quality']['status'], 'success')
        self.assertFalse(report['experimental_claim_eligible'])
        self.assertEqual(review_packet.resolve_answer(self.root, summary['packet_id'], answer_id), 1)
        self.assertTrue((output / 'packet.json').exists())

    def test_coordinator_cli_exports_safe_summary_and_resolves_slot(self):
        runner.import_result(self.root, 1, result('session-1', 'answer'))
        output = self.base / 'cli-output'
        with patch('sys.argv', ['benchmark_issue10_v3.py', 'export-review-packet',
                                str(self.root), str(output)]):
            with patch('builtins.print') as printed:
                runner.main()
        summary = json.loads(printed.call_args.args[0])
        self.assertEqual(set(summary), {'packet_id', 'answer_count', 'excluded_slot_count'})
        packet = json.loads((output / 'packet.json').read_bytes())
        answer_id = packet['answers'][0]['answer_id']
        with patch('sys.argv', ['benchmark_issue10_v3.py', 'resolve-review-answer',
                                str(self.root), '--packet', summary['packet_id'],
                                '--answer', answer_id]):
            with patch('builtins.print') as printed:
                runner.main()
        self.assertEqual(printed.call_args.args, (1,))


if __name__ == '__main__':
    unittest.main()
