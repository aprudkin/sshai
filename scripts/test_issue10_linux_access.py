"""Offline Linux access-adapter boundaries: no SSH, models or private study reads."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

_spec = importlib.util.find_spec('benchmark_issue10_linux_access')
if _spec is not None:
    import benchmark_issue10_linux_access as access
else:
    access = None


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(access, 'production Linux access adapter is not implemented')

    def test_nonce_separates_base_and_slot_without_model_fields(self):
        manifest = {'digest': 'a' * 64, 'remote_plan': {'parent': '/tmp/sshai-issue10-' + '1' * 32,
                    'uid': 60000, 'gid': 60000, 'bash': '/usr/bin/bash', 'setpriv': '/usr/bin/setpriv',
                    'python': '/usr/bin/python3', 'tools': {}, 'runtime_files': []},
                    'slot_material': {'1': {'fixture_files': {'context.txt': {'bytes': 4, 'sha256': '2' * 64}}}}}
        slot = {'slot': 1, 'case_id': 'L01', 'arm': 'baseline'}
        p = access.boundary_plan(manifest, slot, Path('/private/tmp/study/slots/001'))
        expected = hashlib.sha256(b'a' * 64 + b'\n/private/tmp/study/slots/001\n1').hexdigest()[:32]
        self.assertEqual(p['nonce'], expected)
        self.assertEqual(p['fixture_source'], manifest['remote_plan']['parent'] + '/fixtures/L01')
        self.assertEqual(p['fixture_files'], [{'path': 'context.txt', 'size': 4, 'sha256': '2' * 64}])
        q = access.boundary_plan(manifest, slot, Path('/private/tmp/study/readiness/L01'))
        self.assertNotEqual(p['root'], q['root'])

    def test_transport_argv_keeps_fixed_host_and_stdin_out_of_command(self):
        argv = access.ssh_argv('synthetic-host', '/private/tmp/short/cm', 'fixed-entry command')
        self.assertEqual(argv[0], '/usr/bin/ssh')
        self.assertEqual(argv[-2:], ['synthetic-host', 'fixed-entry command'])
        self.assertIn('ControlPath=/private/tmp/short/cm/%C', argv)
        self.assertNotIn('StrictHostKeyChecking=no', argv)
        self.assertNotIn('ProxyJump=none', argv)
        for host in ('-F', 'host user', 'host;rm', ''):
            with self.assertRaises(ValueError):
                access.ssh_argv(host, '/private/tmp/short/cm', 'fixed-entry command')

    def test_bounded_process_retains_output_and_exit(self):
        import os
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            result = access.bounded_process([sys.executable, '-I', '-c',
                'import sys; b=sys.stdin.buffer.read();sys.stdout.buffer.write(b);sys.stderr.buffer.write(b"err");sys.exit(7)'],
                b'raw\x00\xff\r\n', dict(os.environ), Path(tmp), 5, 1024)
        self.assertEqual(result['stdout'], b'raw\x00\xff\r\n')
        self.assertEqual(result['stderr'], b'err')
        self.assertEqual(result['exit_code'], 7)
        self.assertFalse(result['timed_out'])
        self.assertFalse(result['overflow'])

    def test_timeout_and_overflow_are_not_success(self):
        import os
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            deadline = access.bounded_process([sys.executable, '-I', '-c', 'import time;time.sleep(10)'],
                b'', dict(os.environ), Path(tmp), 0.1, 1024)
            large = access.bounded_process([sys.executable, '-I', '-c', 'print("x"*2048)'],
                b'', dict(os.environ), Path(tmp), 5, 1024)
        self.assertTrue(deadline['timed_out'])
        self.assertNotEqual(deadline['exit_code'], 0)
        self.assertTrue(large['overflow'])
        self.assertLessEqual(len(large['stdout']) + len(large['stderr']), 1024)

    def test_inspection_receipt_requires_exact_plan_and_all_files(self):
        plan = {'schema': 'sshai-issue10-linux-boundary-1', 'fixture_files': [{'path': 'context.txt'}]}
        expected = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        ok = {'schema': plan['schema'], 'plan_sha256': expected,
              'fixture_integrity': {'context.txt': True}, 'fixture_inventory_exact': True}
        self.assertTrue(access.inspection_passed(ok, plan))
        for field, value in [('plan_sha256', '0' * 64), ('fixture_integrity', {}),
                             ('fixture_integrity', {'context.txt': False}), ('fixture_inventory_exact', False)]:
            self.assertFalse(access.inspection_passed({**ok, field: value}, plan))


class AdapterLifecycleTests(unittest.TestCase):
    def diagnostic(self, result):
        s = access._Session.__new__(access._Session)
        s.plan = {'root': '/tmp/sshai-issue10-synthetic/issue10-linux-' + '1' * 32}
        s._remote = mock.Mock(return_value={'stdout': b'out\x00', 'stderr': b'err\xff',
             'exit_code': 125, 'timed_out': False, 'start_error': False, 'overflow': False, **result})
        with mock.patch.object(access.boundary, 'entry_command', return_value='fixed encoded entry') as encoded:
            value = s._diagnostic(b'body', b'unchanged\x00stdin')
        encoded.assert_called_once_with(s.plan, b'body')
        s._remote.assert_called_once_with('fixed encoded entry', b'unchanged\x00stdin', seconds=29)
        return value

    def test_smoke_labels_only_completed_no_model_ids_without_stopping_live_broker(self):
        import os
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve(strict=True)
            scratch = root / 'scratch'
            scratch.mkdir()
            (scratch / 'sshai-root').mkdir()
            evidence = root / 'evidence'
            evidence.mkdir()
            with access.FifoBroker(scratch, lambda command, stdin: (b'synthetic', b'', 0),
                                   audit_dir=evidence / 'audit') as broker:
                s = access._Session.__new__(access._Session)
                s.manifest = {'codex': {'path': sys.executable}, 'sshai': {'path': '/synthetic/sshai'}}
                s.scratch, s.evidence, s.broker = scratch, evidence, broker
                s.environment, s.config_overrides = dict(os.environ), []
                def smoke_client(*args):
                    r = subprocess.run([*broker.client_argv, 'issue10-target', 'echo smoke'],
                                       input=b'', capture_output=True, timeout=5)
                    self.assertEqual((r.returncode, r.stdout), (0, b'synthetic'))
                    return {'exit_code': 0, 'timed_out': False, 'overflow': False, 'start_error': False,
                            'stdout': json.dumps({'shim_stdout': True, 'shim_stderr': True,
                               'shim_exit': True, 'sshai_body_and_saved_artifact': True}).encode(), 'stderr': b''}
                with mock.patch.object(access, 'bounded_process', side_effect=smoke_client):
                    self.assertTrue(all(s._smoke().values()))
                self.assertEqual(len(s.no_model_transaction_ids), 1)
                with self.assertRaises(RuntimeError):
                    _ = broker.receipts
                r = subprocess.run([*broker.client_argv, 'issue10-target', 'echo model'],
                                   input=b'', capture_output=True, timeout=5)
                self.assertEqual((r.returncode, r.stdout), (0, b'synthetic'))
                all_ids = broker.completed_transaction_ids(timeout=5)
                self.assertEqual(len(all_ids), 2)
                self.assertEqual(s.no_model_transaction_ids, [all_ids[0]])
            self.assertEqual([row['transaction_id'] for row in broker.receipts], list(all_ids))

    def test_diagnostic_exit125_and_marker_are_not_bootstrap_failures(self):
        self.assertEqual(self.diagnostic({}), (b'out\x00', b'err\xff', 125))
        marker = b'issue10 boundary failure\n'
        self.assertEqual(self.diagnostic({'stderr': marker}), (b'out\x00', marker, 125))

    def test_transport_timeout_and_255_do_not_leak_raw_errors(self):
        for extra in ({'timed_out': True}, {'start_error': True}, {'exit_code': 255}):
            value = self.diagnostic({**extra, 'stderr': b'private diagnostic'})
            self.assertEqual(value, (b'', b'issue10 SSH transport failed or timed out\n', 255))

    def test_output_limit_is_not_a_successful_prefix(self):
        self.assertEqual(self.diagnostic({'overflow': True, 'exit_code': 0}),
                         (b'', b'issue10 transport output limit exceeded\n', 125))

    def finalize(self, directory, *, qualified, busy=False):
        s = access._Session.__new__(access._Session)
        s.plan = {'schema': 'sshai-issue10-linux-boundary-1', 'fixture_files': [{'path': 'context.txt'}]}
        inspection = {'schema': s.plan['schema'], 'plan_sha256': access.plan_digest(s.plan),
                      'fixture_integrity': {'context.txt': True}, 'fixture_inventory_exact': True}
        s.finalized = None
        s.broker = mock.Mock(receipts=[{'transaction_id': 'smoke'}, {'transaction_id': 'model'}])
        s.no_model_transaction_ids = ['smoke']
        s.broker.stop.return_value = {'status': 'stopped'}
        s.prepared = True
        s._control = mock.Mock(side_effect=ValueError('busy') if busy else [inspection, {'status': 'removed'}])
        s.evidence = Path(directory).resolve(strict=True)
        s.slot = {'slot': 1}
        s.manifest = {'slot_material': {'1': {'fixture_files': {'context.txt': {'bytes': 0}}}}}
        s.access_receipt = {'status': 'passed'} if qualified else None
        with mock.patch.object(access.boundary, 'inspection_body', return_value='inspect'), \
             mock.patch.object(access.boundary, 'cleanup_body', return_value='cleanup'):
            value = s.finalize()
            self.assertIs(s.finalize(), value)
        s.broker.stop.assert_called_once()
        return s, value

    def test_finalization_is_bound_to_qualification_and_hashes_evidence(self):
        for qualified in (True, False):
            with tempfile.TemporaryDirectory() as tmp:
                _, value = self.finalize(tmp, qualified=qualified)
                self.assertEqual(value['access_status'], 'passed' if qualified else 'unknown')
                data = Path(value['broker_evidence']['path']).read_bytes()
                self.assertEqual(value['broker_evidence']['sha256'], hashlib.sha256(data).hexdigest())
                receipt = json.loads(data)
                self.assertEqual(receipt['no_model_transaction_ids'], ['smoke'])
                self.assertEqual(receipt['model_transaction_ids'], ['model'])

    def test_busy_inspection_retains_root_without_cleanup_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            s, value = self.finalize(tmp, qualified=True, busy=True)
        s._control.assert_called_once_with('inspect', seconds=60)
        self.assertEqual(value['remote_cleanup'], {'status': 'retained-unknown'})
        self.assertEqual(value['access_status'], 'unknown')
        self.assertFalse(value['fixture_inventory_exact'])


if __name__ == '__main__':
    unittest.main()
