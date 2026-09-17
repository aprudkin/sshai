#!/usr/bin/env python3
"""Offline v3 coordinator tests with synthetic records, never model/SSH sessions."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_v3 as runner
import benchmark_issue10_v3_collector as collector
from test_issue10_v3_capture import cli_records, rollout_records, process, jsonl
from test_issue10_v3_completion import completion_streams, ANSWER


def result(session='synthetic-session-1', **changes):
    record = {'session_id': session, 'execution': 'completed', 'answer_state': 'captured',
              'final_answer': 'Synthetic final answer', 'usage': {'input_tokens': 100,
              'cached_input_tokens': 20, 'output_tokens': 10}, 'usage_complete': True,
              'instrumentation': 'valid', 'boundary': 'compliant', 'review': None}
    return {**record, **changes}


def review(**changes):
    return {'diagnosis_correct': True, 'evidence': 2, 'recommendation': 2,
            'status': 'assessed', 'reviewer': 'synthetic-test-reviewer',
            'reason': 'Synthetic test assessment, not a human study grade', **changes}


class ScheduleTests(unittest.TestCase):
    def test_counts_determinism_pairs_and_balance(self):
        for phase, count in [('pilot', 12), ('measurement', 108)]:
            slots = runner.schedule(phase, 1010)
            self.assertEqual(len(slots), count)
            self.assertEqual(slots, runner.schedule(phase, 1010))
            self.assertNotEqual(slots, runner.schedule(phase, 1011))
            self.assertEqual([s['slot'] for s in slots], list(range(1, count+1)))
            for index in range(0, count, 2):
                a,b = slots[index:index+2]
                self.assertEqual(a['pair_id'], b['pair_id'])
                self.assertEqual({a['arm'],b['arm']}, {'baseline','sshai'})
            for series in 'MLW':
                first = [s for s in slots[::2] if s['series'] == series]
                self.assertEqual(sum(s['arm']=='baseline' for s in first), len(first)//2)
                if phase == 'measurement':
                    for task in range(1,7):
                        order = [s['arm'] for s in first if s['task']==task]
                        self.assertEqual(len(order),3)
                        self.assertIn(order.count('baseline'),(1,2))
        with self.assertRaises(ValueError):
            runner.schedule('other',1010)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()/'study'
        with patch.object(runner.legacy, '_POPEN', side_effect=AssertionError('no probes')):
            with patch.object(runner.legacy, '_bounded_process', side_effect=AssertionError('no subprocess')):
                self.plan = runner.prepare(self.root, 'pilot')

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_and_empty_report(self):
        self.assertEqual(len(self.plan['slots']),12)
        self.assertFalse(self.plan['launch_enabled'])
        report = runner.analyze_root(self.root)
        self.assertFalse(report['experimental_claim_eligible'])
        self.assertIn('offline',report['record_provenance'])
        self.assertEqual(runner.load_plan(self.root),self.plan)
        self.assertNotIn('key.json', [p.name for p in (self.root/'prepared/inputs/M01').iterdir()])
        self.assertTrue((self.root/'prepared/evaluator/M01/key.json').exists())
        prompt = (self.root/'prepared/planned-prompts/M01-sshai.md').read_text()
        self.assertIn('{fixture_root}', prompt)
        self.assertIn('Route every new target diagnostic command', prompt)
        with self.assertRaises(ValueError):
            runner.prepare(self.root,'pilot')

    def test_result_immutable_session_unique_and_reviews_append_only(self):
        runner.import_result(self.root,1,result())
        with self.assertRaises(ValueError):
            runner.import_result(self.root,1,result('synthetic-session-2'))
        with self.assertRaises(ValueError):
            runner.import_result(self.root,2,result())
        runner.record_review(self.root,1,review(status='disputed'))
        first = (self.root/'reviews/001/001.json').read_bytes()
        runner.record_review(self.root,1,review(reason='Synthetic adjudication'))
        self.assertEqual((self.root/'reviews/001/001.json').read_bytes(),first)
        self.assertTrue((self.root/'reviews/001/002.json').exists())
        self.assertFalse(runner.analyze_root(self.root)['experimental_claim_eligible'])

    def test_tamper_and_unscheduled_result(self):
        runner.import_result(self.root,1,result())
        path=self.root/'records/001.json'
        data=json.loads(path.read_text());data['record']['usage']['input_tokens']=99
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            runner.analyze_root(self.root)
        with self.assertRaises(ValueError):
            runner.import_result(self.root,999,result('other'))

    def test_review_binds_record_and_preserves_chain(self):
        runner.import_result(self.root,1,result())
        runner.record_review(self.root,1,review())
        runner.record_review(self.root,1,review(evidence=1))
        path=self.root/'reviews/001/002.json'
        data=json.loads(path.read_text());data['previous_sha256']='bad'
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            runner.analyze_root(self.root)

    def test_changed_prepared_source_rejected(self):
        (self.root/'prepared/inputs/M01/context.txt').write_text('modified\n')
        with self.assertRaises(ValueError):
            runner.load_plan(self.root)

    def test_missing_final_cannot_be_graded(self):
        runner.import_result(self.root,1,result(execution='timeout',answer_state='absent',final_answer=None))
        with self.assertRaises(ValueError):
            runner.record_review(self.root,1,review())
        self.assertFalse(runner.analyze_root(self.root)['experimental_claim_eligible'])

    def test_bad_record_and_review_types(self):
        for changes in ({'usage_complete':1}, {'session_id':''}, {'final_answer':''},
                        {'unexpected':1}, {'slot':True},
                        {'usage_complete':True,'usage':None}, {'boundary':'safe'},
                        {'answer_state':'lost'}, {'usage':{'input_tokens':True,'cached_input_tokens':0,'output_tokens':1}}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    runner.import_result(self.root,1,result(**changes))
        runner.import_result(self.root,1,result())
        for changes in ({'evidence':True},{'recommendation':3},{'diagnosis_correct':1},{'reason':''},{'unexpected':1}):
            with self.assertRaises(ValueError):
                runner.record_review(self.root,1,review(**changes))

    def capture(self, number=1, **changes):
        args = {'cli_data': jsonl(cli_records()), 'rollout_data': jsonl(rollout_records()),
                'process_data': runner.encoded(process()), 'answer_data': b'Synthetic final answer'}
        with patch.object(runner.legacy, '_bounded_process', side_effect=AssertionError('no process')):
            runner.import_capture(self.root, number, **{**args, **changes})
        return runner._read_envelope(self.root / 'records' / f'{number:03}.json')

    def collector_attempt(self, name='collector-attempt', *, thread='synthetic-thread',
                          timeout=False, extra_candidates=(), completion=False, exit_code=0):
        attempt = self.root.parent / name
        answer = self.root.parent / f'{name}-answer'
        rollout = self.root.parent / f'{name}-rollout'
        cli, persisted = completion_streams() if completion else (cli_records(), rollout_records())
        cli[0]['thread_id'] = thread
        persisted[0]['payload']['id'] = thread
        events = jsonl(cli)
        rollout.write_bytes(jsonl(persisted))
        code = (
            "import sys,time; from pathlib import Path; "
            f"Path(sys.argv[1]).write_bytes({ANSWER.encode() if completion else b'Synthetic final answer'!r}); "
            "sys.stdout.buffer.write(bytes.fromhex(sys.argv[2])); sys.stdout.flush(); "
            + ("time.sleep(60)" if timeout else f"sys.exit({exit_code})")
        )
        collector.collect_attempt(
            attempt, [sys.executable, '-c', code, str(answer), events.hex()],
            prompt=b'synthetic prompt',
            env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'},
            cwd=self.root.parent, timeout_seconds=0.1 if timeout else 2,
            rollout_candidates=[rollout, *extra_candidates], answer_path=answer,
        )
        return attempt

    def test_collector_completion_match_is_retained_replayed_and_not_gradable(self):
        for execution, timeout, exit_code in [('completed', False, 0), ('timeout', True, 0),
                                               ('failed', False, 3)]:
            with self.subTest(execution=execution):
                attempt = self.collector_attempt(name=f'completion-{execution}', completion=True,
                                                 timeout=timeout, exit_code=exit_code)
                # Each subcase uses its own slot store so the same synthetic
                # thread does not bypass the coordinator's identity uniqueness.
                root = self.root if execution == 'completed' else self.root.parent / f'{execution}-study'
                if execution != 'completed':
                    runner.prepare(root, 'pilot')
                runner.import_collector(root, 1, attempt)
                path = root / 'records/001.json'
                envelope = runner._read_envelope(path)
                completion = envelope['collector']['report']['answer_completion']
                self.assertEqual(completion['status'], 'matched')
                self.assertEqual(completion['finality'], 'unknown')
                self.assertFalse(completion['live_qualified'])
                record = runner._read_record(root, 1, runner.load_plan(root))
                self.assertEqual(record['execution'], execution)
                self.assertEqual(record['answer_state'], 'lost')
                self.assertIsNone(record['final_answer'])
                with self.assertRaises(ValueError):
                    runner.record_review(root, 1, review())
                self.assertFalse(runner.analyze_root(root)['experimental_claim_eligible'])
                # Even a rehashed report cannot substitute claimed qualification.
                completion['finality'] = 'confirmed'
                envelope['collector_sha256'] = runner.digest(runner.encoded(envelope['collector']))
                path.write_bytes(runner.encoded(envelope))
                with self.assertRaisesRegex(ValueError, 'report/receipts differ'):
                    runner.analyze_root(root)

    def test_capture_roundtrip_raw_bytes_binding_and_review(self):
        envelope = self.capture()
        evidence = envelope['capture']
        self.assertEqual(base64.b64decode(evidence['raw']['events']['base64']), jsonl(cli_records()))
        self.assertEqual(evidence['slot'], self.plan['slots'][0])
        self.assertEqual(evidence['plan_digest'], self.plan['digest'])
        self.assertIn('scripts/benchmark_issue10_v3_capture.py', self.plan['sources'])
        record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(record['instrumentation'], 'unknown')
        self.assertEqual(record['boundary'], 'unknown')
        self.assertFalse(runner.analyze_root(self.root)['experimental_claim_eligible'])
        runner.record_review(self.root, 1, review())
        receipt = json.loads((self.root / 'reviews/001/001.json').read_bytes())
        self.assertEqual(receipt['capture_sha256'], envelope['capture_sha256'])
        self.assertEqual((self.root / 'records/001.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.root / 'records').stat().st_mode & 0o777, 0o700)

    def test_call_evidence_roundtrip_rejects_rehashed_execution_claim(self):
        cli = cli_records()
        change = {'id': 'patch-1', 'type': 'file_change', 'status': 'failed',
                  'changes': [{'path': 'synthetic.txt', 'kind': 'update'}]}
        cli.insert(-1, {'type': 'item.completed', 'item': change})
        envelope = self.capture(cli_data=jsonl(cli))
        calls = envelope['capture']['report']['calls']
        patch_entry = next(c for c in calls['inventory'] if c['call_id'] == 'patch-1')
        self.assertEqual(patch_entry['raw_observations'][0]['item'], change)
        self.assertFalse(patch_entry['actual_call_confirmed'])
        request = next(c for c in calls['inventory'] if c['source'] == 'rollout.response_item')
        self.assertFalse(request['actual_call_confirmed'])
        self.assertIsNone(calls['unique_session_call_count'])
        self.assertFalse(runner.analyze_root(self.root)['experimental_claim_eligible'])
        request['actual_call_confirmed'] = True
        envelope['capture_sha256'] = runner.digest(runner.encoded(envelope['capture']))
        (self.root / 'records/001.json').write_bytes(runner.encoded(envelope))
        with self.assertRaises(ValueError):
            runner.analyze_root(self.root)

    def test_capture_overwrite_and_duplicate_across_import_paths(self):
        self.capture()
        original = (self.root / 'records/001.json').read_bytes()
        with self.assertRaises(ValueError):
            self.capture()
        with self.assertRaises(ValueError):
            self.capture(2)
        with self.assertRaises(ValueError):
            runner.import_result(self.root, 2, result('synthetic-thread'))
        self.assertEqual((self.root / 'records/001.json').read_bytes(), original)
        self.assertFalse((self.root / 'records/002.json').exists())

    def test_invalid_capture_without_identity_retained_not_upgraded(self):
        envelope = self.capture(cli_data=b'not json\n', rollout_data=b'\xff',
                                process_data=b'{}', answer_data=None, answer_state='lost')
        evidence = envelope['capture']
        self.assertIsNone(evidence['report']['session_id'])
        self.assertEqual(evidence['identity_origin'], 'local-slot-key')
        self.assertEqual(base64.b64decode(evidence['raw']['rollout']['base64']), b'\xff')
        record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(record['answer_state'], 'lost')
        self.assertEqual(record['instrumentation'], 'invalid')
        self.assertFalse(record['usage_complete'])
        self.assertFalse(runner.analyze_root(self.root)['experimental_claim_eligible'])

    def test_capture_tampering_and_projection_rejected(self):
        envelope = self.capture()
        path = self.root / 'records/001.json'
        original = runner.encoded(envelope)
        for kind in ('raw', 'report', 'slot', 'projection'):
            with self.subTest(kind=kind):
                data = json.loads(original)
                if kind == 'raw':
                    data['capture']['raw']['events']['base64'] = base64.b64encode(b'changed').decode()
                elif kind == 'report':
                    data['capture']['report']['boundary']['status'] = 'compliant'
                elif kind == 'slot':
                    data['capture']['slot'] = self.plan['slots'][1]
                else:
                    data['record']['instrumentation'] = 'valid'
                    data['record_sha256'] = runner.digest(runner.encoded(data['record']))
                # Even refreshed envelope hashes cannot bypass byte replay/binding.
                data['capture_sha256'] = runner.digest(runner.encoded(data['capture']))
                path.write_bytes(runner.encoded(data))
                with self.assertRaises(ValueError):
                    runner.analyze_root(self.root)
        path.write_bytes(original)
        runner.analyze_root(self.root)

    def test_review_binds_capture_even_when_projection_unchanged(self):
        envelope = self.capture()
        runner.record_review(self.root, 1, review())
        # Whitespace-only process-byte change preserves the parsed report/record.
        raw = envelope['capture']['raw']['process']
        body = base64.b64decode(raw['base64']) + b' '
        raw.update(base64=base64.b64encode(body).decode(), bytes=len(body), sha256=runner.digest(body))
        envelope['capture_sha256'] = runner.digest(runner.encoded(envelope['capture']))
        (self.root / 'records/001.json').write_bytes(runner.encoded(envelope))
        with self.assertRaisesRegex(ValueError, 'review does not bind to capture'):
            runner.analyze_root(self.root)

    def test_capture_cli_and_process_answer_outcomes(self):
        for name, body in {'events': jsonl(cli_records()), 'rollout': jsonl(rollout_records()),
                           'process': runner.encoded(process(timed_out=True, exit_code=None)),
                           'answer': b'Synthetic final answer'}.items():
            (self.root / name).write_bytes(body)
        argv = ['runner', 'import-capture', str(self.root), '--slot', '1']
        for name in ('events', 'rollout', 'process', 'answer'):
            argv += [f'--{name}', str(self.root / name)]
        with patch('sys.argv', argv):
            runner.main()
        record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(record['execution'], 'timeout')
        self.assertEqual(record['answer_state'], 'captured')
        runner.record_review(self.root, 1, review())
        report = runner.analyze_root(self.root)
        self.assertEqual(report['slot_provenance'][0]['kind'], runner.CAPTURE_PROVENANCE)
        self.assertFalse(report['experimental_claim_eligible'])

    def test_capture_atomic_failure_and_bounds(self):
        with patch.object(runner.legacy, '_atomic_new', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                self.capture()
        self.assertFalse((self.root / 'records/001.json').exists())
        with self.assertRaises(ValueError):
            self.capture(cli_data=b'x' * (runner.capture_adapter.MAX_CAPTURE_BYTES + 1))
        self.assertEqual(list((self.root / 'records').iterdir()), [])
        self.capture()

    def test_large_capture_envelope_and_cli_input_validation(self):
        # Above the historical 1 MiB JSON limit after base64/report expansion.
        envelope = self.capture(answer_data=b'x' * 800_000)
        self.assertGreater((self.root / 'records/001.json').stat().st_size, 1_048_576)
        self.assertEqual(runner._read_record(self.root, 1, self.plan), envelope['record'])
        runner.analyze_root(self.root)
        source = self.root / 'synthetic.jsonl'
        source.write_bytes(b'{}\n')
        link = self.root / 'symlink.jsonl'
        link.symlink_to(source)
        with patch('sys.argv', ['runner', 'import-capture', str(self.root), '--slot', '2',
                               '--events', str(link), '--rollout', str(source), '--process', str(source)]):
            with self.assertRaises(ValueError):
                runner.main()
        self.assertFalse((self.root / 'records/002.json').exists())

    def test_collector_import_retains_attempt_but_forces_unknown_finality(self):
        self.assertIn('scripts/benchmark_issue10_v3_collector.py', self.plan['sources'])
        attempt = self.collector_attempt()
        with patch.object(collector, 'collect_attempt', side_effect=AssertionError('no collection')):
            with patch.object(runner.legacy, '_bounded_process', side_effect=AssertionError('no process')):
                runner.import_collector(self.root, 1, attempt)
        envelope = runner._read_envelope(self.root / 'records/001.json')
        self.assertEqual(envelope['provenance'], runner.COLLECTOR_PROVENANCE)
        self.assertFalse(envelope['collector']['binding']['pre_spawn_attestation'])
        self.assertEqual(envelope['collector']['receipt_authenticity'], 'not-attested')
        retained = envelope['collector']['files']
        self.assertIn('answer.txt', retained)
        self.assertIn('rollout-candidates/001.jsonl', retained)
        self.assertEqual(base64.b64decode(retained['answer.txt']['base64']),
                         b'Synthetic final answer')
        record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(record['execution'], 'completed')
        self.assertEqual(record['answer_state'], 'lost')
        self.assertIsNone(record['final_answer'])
        with self.assertRaisesRegex(ValueError, 'cannot grade'):
            runner.record_review(self.root, 1, review())
        report = runner.analyze_root(self.root)
        row = next(item for item in report['slots'] if item['slot'] == 1)
        self.assertEqual(row['quality']['status'], 'unknown')
        self.assertEqual(row['quality']['reason'], 'final_answer_lost')
        self.assertEqual(report['slot_provenance'][0]['kind'], runner.COLLECTOR_PROVENANCE)

    def test_collector_timeout_and_lost_selection_retain_unknown_answers_and_candidates(self):
        timeout_attempt = self.collector_attempt('timeout-attempt', thread='timeout-thread', timeout=True)
        runner.import_collector(self.root, 1, timeout_attempt)
        timeout_record = runner._read_record(self.root, 1, self.plan)
        self.assertEqual(timeout_record['execution'], 'timeout')
        self.assertEqual(timeout_record['answer_state'], 'lost')
        self.assertIsNone(timeout_record['final_answer'])

        malformed = self.root.parent / 'malformed-candidate'
        malformed.write_bytes(b'{bad json}\n')
        lost_attempt = self.collector_attempt(
            'lost-rollout-attempt', thread='lost-rollout-thread',
            extra_candidates=[malformed],
        )
        runner.import_collector(self.root, 2, lost_attempt)
        envelope = runner._read_envelope(self.root / 'records/002.json')
        files = envelope['collector']['files']
        self.assertIn('rollout-candidates/001.jsonl', files)
        self.assertIn('rollout-candidates/002.jsonl', files)
        self.assertEqual(base64.b64decode(files['rollout.jsonl']['base64']), b'')
        record = runner._read_record(self.root, 2, self.plan)
        self.assertTrue(record['session_id'].startswith('unidentified-capture:'))
        self.assertEqual(record['answer_state'], 'lost')

    def test_collector_failure_deliveries_remain_importable_unknown_quality(self):
        for number, kind in enumerate(('nonzero', 'empty', 'missing', 'start-failure',
                                       'overflow', 'unreadable-candidate'), 1):
            with self.subTest(kind=kind):
                attempt = self.root.parent / f'failure-{kind}'
                answer = self.root.parent / f'answer-{kind}'
                candidate = self.root.parent / f'candidate-{kind}'
                body = (
                    "from pathlib import Path; import sys; "
                    "Path(sys.argv[1]).write_bytes(b'candidate answer'); "
                )
                if kind == 'empty':
                    body = "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b''); "
                elif kind == 'missing':
                    body = "import sys; "
                body += "sys.exit(3)" if kind == 'nonzero' else "pass"
                if kind == 'overflow':
                    body += f"; sys.stdout.write('x' * {collector.MAX_STREAM_BYTES + 1})"
                argv = [sys.executable, '-c', body, str(answer)]
                if kind == 'start-failure':
                    argv = [str(self.root.parent / 'nonexistent-executable')]
                collector.collect_attempt(
                    attempt, argv, prompt=b'synthetic', env={}, cwd=self.root.parent,
                    timeout_seconds=2, answer_path=answer,
                    rollout_candidates=[candidate] if kind == 'unreadable-candidate' else [],
                )
                runner.import_collector(self.root, number, attempt)
                record = runner._read_record(self.root, number, self.plan)
                self.assertEqual(record['answer_state'], 'lost')
                self.assertIsNone(record['final_answer'])
                report = runner.analyze_root(self.root)
                row = next(item for item in report['slots'] if item['slot'] == number)
                self.assertEqual(row['quality']['status'], 'unknown')
                self.assertFalse(report['experimental_claim_eligible'])

    def test_collector_replay_rejects_receipt_data_finality_slot_and_projection_tampering(self):
        runner.import_collector(self.root, 1, self.collector_attempt())
        path = self.root / 'records/001.json'
        original = path.read_bytes()

        def refreshed(document):
            document['collector_sha256'] = runner.digest(runner.encoded(document['collector']))
            path.write_bytes(runner.encoded(document))

        for kind in ('receipt', 'data', 'finality', 'slot', 'projection'):
            with self.subTest(kind=kind):
                document = json.loads(original)
                evidence = document['collector']
                if kind == 'receipt':
                    receipt = dict(evidence['receipts']['process'])
                    receipt['execution'] = 'failed'
                    body = runner.legacy._canon(receipt)
                    evidence['files']['process.json'] = runner._blob(body)
                    evidence['receipts']['process'] = receipt
                elif kind == 'data':
                    body = base64.b64decode(evidence['files']['events.jsonl']['base64']) + b'\n'
                    evidence['files']['events.jsonl'] = runner._blob(body)
                elif kind == 'finality':
                    receipt = json.loads(base64.b64decode(
                        evidence['files']['delivery.json']['base64']))
                    receipt['answer']['finality'] = 'captured'
                    body = runner.legacy._canon(receipt)
                    evidence['files']['delivery.json'] = runner._blob(body)
                    evidence['receipts']['delivery'] = receipt
                elif kind == 'slot':
                    evidence['binding']['slot'] = self.plan['slots'][1]
                else:
                    document['record']['answer_state'] = 'absent'
                    document['record_sha256'] = runner.digest(runner.encoded(document['record']))
                refreshed(document)
                with self.assertRaises(ValueError):
                    runner.analyze_root(self.root)
        path.write_bytes(original)
        runner.analyze_root(self.root)

    def test_collector_missing_partial_and_unsafe_attempts_publish_nothing(self):
        complete = self.collector_attempt()
        partial = self.root.parent / 'partial-attempt'
        runner.legacy._new_dir(partial)
        runner.legacy._write_new(partial / 'attempt.json', (complete / 'attempt.json').read_bytes())
        before = (partial / 'attempt.json').read_bytes()
        with self.assertRaises(ValueError):
            runner.import_collector(self.root, 1, partial)
        self.assertEqual((partial / 'attempt.json').read_bytes(), before)
        self.assertEqual([path.name for path in partial.iterdir()], ['attempt.json'])
        self.assertFalse((self.root / 'records/001.json').exists())

        missing = self.collector_attempt('missing-answer-attempt', thread='missing-answer-thread')
        (missing / 'answer.txt').unlink()
        with self.assertRaises(ValueError):
            runner.import_collector(self.root, 1, missing)
        self.assertFalse((self.root / 'records/001.json').exists())

        unsafe = self.collector_attempt('unsafe-attempt', thread='unsafe-thread')
        delivery_path = unsafe / 'delivery.json'
        delivery = json.loads(delivery_path.read_bytes())
        delivery['answer']['retained'] = '../outside'
        delivery_path.write_bytes(runner.legacy._canon(delivery))
        with self.assertRaisesRegex(ValueError, 'unsafe collector retained answer path'):
            runner.import_collector(self.root, 1, unsafe)
        self.assertFalse((self.root / 'records/001.json').exists())

        linked = self.collector_attempt('linked-attempt', thread='linked-thread')
        target = linked / 'answer-target'
        (linked / 'answer.txt').rename(target)
        (linked / 'answer.txt').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            runner.import_collector(self.root, 1, linked)
        self.assertFalse((self.root / 'records/001.json').exists())

    def test_collector_duplicate_overwrite_and_cross_path_session_rejected(self):
        attempt = self.collector_attempt()
        runner.import_collector(self.root, 1, attempt)
        original = (self.root / 'records/001.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'session_id already used'):
            runner.import_collector(self.root, 2, attempt)
        with self.assertRaisesRegex(ValueError, 'session_id already used'):
            runner.import_result(self.root, 2, result('synthetic-thread'))
        other = self.collector_attempt('other-attempt', thread='other-thread')
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            runner.import_collector(self.root, 1, other)
        self.assertEqual((self.root / 'records/001.json').read_bytes(), original)
        self.assertFalse((self.root / 'records/002.json').exists())

    def test_collector_cli_and_missing_source_pin(self):
        attempt = self.collector_attempt()
        with patch('sys.argv', ['runner', 'import-collector', str(self.root),
                                '--slot', '1', '--attempt', str(attempt)]):
            with patch.object(collector, 'collect_attempt', side_effect=AssertionError('no collection')):
                runner.main()
        self.assertTrue((self.root / 'records/001.json').is_file())

        other = self.root.parent / 'study-without-collector-pin'
        plan = runner.prepare(other, 'pilot')
        plan['sources'].pop('scripts/benchmark_issue10_v3_collector.py')
        plan['digest'] = runner.legacy._manifest_digest(plan)
        (other / 'plan.json').write_bytes(runner.encoded(plan))
        unpinned = self.collector_attempt('unpinned-attempt', thread='unpinned-thread')
        with self.assertRaisesRegex(ValueError, 'does not pin'):
            runner.import_collector(other, 1, unpinned)
        self.assertFalse((other / 'records/001.json').exists())

    def test_collector_envelope_bound_fails_before_atomic_publication(self):
        candidates = []
        for index in range(24):
            path = self.root.parent / f'large-candidate-{index}'
            path.write_bytes(b'x' * runner.capture_adapter.MAX_CAPTURE_BYTES)
            candidates.append(path)
        attempt = self.collector_attempt('large-attempt', extra_candidates=candidates)
        with self.assertRaisesRegex(ValueError, 'envelope exceeds storage bound'):
            runner.import_collector(self.root, 1, attempt)
        self.assertFalse((self.root / 'records/001.json').exists())
        self.assertTrue((attempt / 'delivery.json').is_file())

    def test_launch_refuses_without_probes(self):
        with patch('sys.argv',['benchmark_issue10_v3.py','run-one',str(self.root)]):
            with patch.object(runner.legacy,'_bounded_process',side_effect=AssertionError('no launch')):
                with self.assertRaises(SystemExit) as exc:
                    runner.main()
                self.assertEqual(exc.exception.code,2)


if __name__ == '__main__':
    unittest.main()
