"""One-context launcher tests: synthetic receipts/processes only, never live auth/model."""
import copy
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import benchmark_issue10_assessment_runner as runner
import benchmark_issue10_local_assessment as bridge
import benchmark_issue10_local_pilot as pilot
from test_issue10_local_assessment import bundle, selected_case_bundle, INSTRUCTIONS
from test_issue10_v3_completion import ANSWER, completion_streams
from test_issue10_v3_capture import jsonl


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(body)
    path.chmod(0o600)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.private = self.base / 'private'
        self.private.mkdir(mode=0o700)
        self.native = self.base / 'native'
        write(self.native, b'\xcf\xfa\xed\xfe synthetic native TEST ONLY')
        self.native.chmod(0o700)
        self.native_patch = patch.object(pilot, 'EXPECTED_CODEX_PATH', self.native)
        self.native_patch.start(); self.addCleanup(self.native_patch.stop)
        self.auth = self.private / 'auth.json'
        write(self.auth, b'{"synthetic-auth-placeholder":true}\n')
        self.packet = self.private / 'original-packet'
        supplied, owner = bridge.build_packet(bundle(), INSTRUCTIONS.read_bytes())
        bridge.publish(self.packet, {'assessor/input.json': bridge.encode(supplied), 'owner/inventory.json': bridge.encode(owner)})
        self.catalog = self.private / 'catalog.json'
        model = {'slug': 'gpt-5.6-sol', 'shell_type': 'unified_exec', 'apply_patch_tool_type': None,
                 'tool_mode': 'direct', 'supports_search_tool': False, 'experimental_supported_tools': [],
                 'multi_agent_version': 'v2', 'supported_reasoning_levels': [{'effort': 'high'}],
                 'display_name': 'Synthetic', 'description': 'Synthetic', 'model_messages': {},
                 'context_window': 1000, 'truncation_policy': {}, 'input_modalities': ['text'],
                 'default_reasoning_level': 'high'}
        write(self.catalog, bridge.encode({'models': [model]}))
        self.config_path = self.private / 'config.json'
        self.config = {'schema': runner.CONFIG_SCHEMA,
            'native': {'version': runner.sandbox.CODEX_VERSION, 'source_revision': pilot.SOURCE_CONTRACT['revision'],
                       'sha256': bridge.digest(self.native.read_bytes())},
            'model': {'id': 'gpt-5.6-sol', 'version': 'synthetic full-catalog pin only', 'reasoning_effort': 'high'},
            'catalog_sha256': bridge.digest(self.catalog.read_bytes()),
            'environment': {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
                            'SHELL': '/bin/bash', 'USER': 'synthetic', 'LOGNAME': 'synthetic'},
            'config_overrides': list(runner.CONTROLS), 'protected_paths': [str(self.private)],
            'budget': {'context_id': 'a' * 32, 'consumed_before': 0, 'ceiling': 24,
                       'ledger_sha256': 'b' * 64, 'provenance': 'Synthetic caller budget, not global attestation'},
            'instructions_file_sha256': owner['pins']['instructions_file_sha256'],
            'rubric_sha256': owner['pins']['rubric_sha256']}
        write(self.config_path, bridge.encode(self.config))
        self.root = self.private / 'runner'
        self.approval = self.private / 'approval.json'
        self.access_patch = patch.object(runner, '_ACCESS_QUALIFIER', self.synthetic_access)
        self.access_patch.start(); self.addCleanup(self.access_patch.stop)

    def synthetic_access(self, manifest):
        return {'permission_overrides': manifest['permission_overrides'],
                'checks': {key: True for key in ('packet_read', 'packet_write_denied', 'packet_create_denied', 'tool_network_denied')},
                'provenance': 'Synthetic injection; not installed-binary qualification'}

    def prepare(self):
        self.manifest = runner.prepare(self.root, self.packet, self.config_path, self.catalog, self.native, self.auth)
        return self.manifest

    def ready(self):
        self.prepare()
        self.assertTrue(runner.preflight(self.root)['passed'])
        approval = {'schema': runner.APPROVAL_SCHEMA, 'manifest_digest': self.manifest['digest'],
                    'context_id': self.config['budget']['context_id'], 'ledger_sha256': self.config['budget']['ledger_sha256'],
                    'decision': 'advance-this-context-once', 'authorization_note': 'Synthetic explicit advance; not a real permission'}
        write(self.approval, bridge.encode(approval))

    def model_process(self, *, tools=False, answer=None, overflow=False, timed_out=False, mismatch=False,
                      user_records=False, native_overflow=None):
        if answer is None:
            answer = b'{"synthetic":"not an actual grade"}\r\nUnicode \xce\xb1 \xf0\x9f\x98\x80\r\n'
        def fake(argv, prompt, environment, cwd, timeout):
            self.assertTrue((self.root / 'context/reservation.json').is_file())
            self.assertTrue((self.root / 'context/attempt/attempt.json').is_file())
            self.assertEqual(argv, self.manifest['argv'])
            self.assertEqual(prompt, (self.packet / 'assessor/input.json').read_bytes())
            self.assertEqual(environment, self.manifest['environment'])
            self.assertEqual(timeout, 600)
            cli, rollout = completion_streams()
            if not tools:
                cli = [r for r in cli if not isinstance(r.get('item'), dict) or r['item'].get('type') != 'command_execution']
                rollout = [r for r in rollout if not isinstance(r.get('payload'), dict) or r['payload'].get('type') != 'function_call']
            rollout[1]['payload'].update(model='wrong' if mismatch else 'gpt-5.6-sol', effort='high')
            if user_records:
                body = prompt.decode('utf-8')
                rollout[3:3] = [
                    {'type': 'response_item', 'payload': {'type': 'message', 'id': 'synthetic-user',
                     'role': 'user', 'content': [{'type': 'input_text', 'text': body}],
                     'internal_chat_message_metadata_passthrough': None}},
                    {'type': 'event_msg', 'payload': {'type': 'item_completed',
                     'thread_id': 'synthetic-thread', 'turn_id': 'synthetic-turn',
                     'started_at_ms': None, 'completed_at_ms': 1,
                     'item': {'type': 'UserMessage', 'id': 'synthetic-user',
                              'content': [{'type': 'text', 'text': body, 'text_elements': []}]}}}]
            if native_overflow is not None:
                count, size = (3, 3 * 1024 * 1024) if native_overflow == 'file' else (1, 4 * 1024 * 1024 + 1)
                rollout.extend({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                                'content': [{'type': 'input_text', 'text': 'x' * size}]}} for _ in range(count))
            old, new = json.dumps(ANSWER)[1:-1].encode(), json.dumps(answer.decode())[1:-1].encode()
            cli_bytes = jsonl(cli).replace(old, new)
            # Match the pinned producer's compact UTF-8 JSONL, retaining full strings.
            replaced = jsonl(rollout).replace(old, new)
            rollout_bytes = b''.join((json.dumps(json.loads(line), ensure_ascii=False,
                                                separators=(',', ':')) + '\n').encode('utf-8')
                                     for line in replaced.splitlines())
            write(Path(environment['CODEX_HOME']) / 'sessions/rollout.jsonl', rollout_bytes)
            write(Path(argv[argv.index('--output-last-message') + 1]), answer)
            self.expected_events, self.expected_rollout, self.expected_answer = cli_bytes, rollout_bytes, answer
            return {'stdout': cli_bytes, 'stderr': b'synthetic stderr\r\n', 'pid': 123,
                    'exit_code': None if timed_out else 0, 'timed_out': timed_out, 'capture_overflow': overflow,
                    'interrupted': False, 'start_error': None, 'duration_seconds': 0.1}
        return fake

    def run_model(self, **kwargs):
        with patch.object(runner.legacy, '_bounded_process', self.model_process(**kwargs)):
            return runner.run(self.root, self.approval, allow_model_run=True)

    def test_fixed_context_and_packet_owner_isolation(self):
        with patch.dict(os.environ, {'CODEX_HOME': 'foreign', 'OPENAI_API_KEY': 'synthetic-do-not-inherit'}):
            manifest = self.prepare()
        self.assertEqual(manifest['config']['model']['id'], 'gpt-5.6-sol')
        self.assertIn('features.shell_tool=false', manifest['argv'])
        self.assertIn('features.unified_exec=false', manifest['argv'])
        self.assertNotIn('features.shell_tool=true', manifest['argv'])
        self.assertIn('--ignore-user-config', manifest['argv'])
        self.assertIn('--ignore-rules', manifest['argv'])
        self.assertNotIn('OPENAI_API_KEY', manifest['environment'])
        self.assertEqual(set((self.root / 'packet').iterdir()), {self.root / 'packet/input.json'})
        self.assertEqual((self.root / 'packet/input.json').read_bytes(), (self.packet / 'assessor/input.json').read_bytes())
        fs = next(value for value in manifest['permission_overrides'] if '.filesystem=' in value)
        self.assertIn(f'"{self.packet}"="deny"', fs)
        self.assertIn(f'"{self.private}"="deny"', fs)
        self.assertIn(f'"{self.root / "packet/input.json"}"="read"', fs)
        self.assertNotIn(f'"{self.root / "packet"}"="read"', fs)
        self.assertNotIn('owner', str(list((self.root / 'packet').iterdir())))
        self.assertIn('caller-declared', manifest['budget_basis'])
        self.assertEqual(manifest['auth_source']['contents'], 'unread and unpinned; native auth may refresh this source')

    def test_m04_full_fixture_packet_pins_offline(self):
        supplied, owner = bridge.build_packet(selected_case_bundle('M04', repetitions=3),
                                               INSTRUCTIONS.read_bytes())
        packet_root = self.private / 'm04-packet'
        bridge.publish(packet_root, {'assessor/input.json': bridge.encode(supplied),
                                     'owner/inventory.json': bridge.encode(owner)})
        raw, pins = runner._packet_pins(packet_root, self.config)
        self.assertEqual(raw, bridge.encode(supplied))
        self.assertEqual(len(supplied['packet']['fixtures']), 369)
        self.assertEqual(pins['eligible_answer_count'], 6)
        self.assertEqual(pins['source_sha256'], supplied['packet']['source_sha256'])
        self.assertEqual(pins['input_sha256'], bridge.digest(raw))
        changed = copy.deepcopy(supplied)
        changed['packet']['fixtures'][-1]['line_count'] += 1
        changed_owner = copy.deepcopy(owner)
        changed_owner['assessment_input_sha256'] = bridge.digest(bridge.encode(changed))
        invalid_root = self.private / 'm04-invalid-packet'
        bridge.publish(invalid_root, {'assessor/input.json': bridge.encode(changed),
                                      'owner/inventory.json': bridge.encode(changed_owner)})
        with self.assertRaisesRegex(ValueError, 'fixture inventory/line mismatch'):
            runner._packet_pins(invalid_root, self.config)
        self.assertFalse(self.root.exists())

    def use_case_packet(self, case_id):
        supplied, owner = bridge.build_packet(selected_case_bundle(case_id, repetitions=3), INSTRUCTIONS.read_bytes())
        self.packet = self.private / f'{case_id}-packet'
        bridge.publish(self.packet, {'assessor/input.json': bridge.encode(supplied),
                                     'owner/inventory.json': bridge.encode(owner)})
        self.assertLessEqual((self.packet / 'assessor/input.json').stat().st_size, 1048576)

    def assert_large_packet_captured(self, case_id):
        self.use_case_packet(case_id)
        self.ready()
        result = self.run_model(user_records=True)
        self.assertEqual(result['status'], 'captured-unqualified', result['blockers'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(result['finality'], 'unknown')
        self.assertEqual(result['quality'], 'unknown')
        self.assertFalse(result['retry_allowed'])
        self.assertTrue(result['usage']['complete'])
        self.assertEqual(result['recorded_call_entries'], 0)
        native = self.expected_rollout
        self.assertGreater(max(map(len, native.splitlines())), 262144)
        if case_id == 'M02':
            self.assertGreater(len(native), 1048576)
        self.assertFalse(runner.capture.parse_jsonl(native, 'unchanged-defaults')['complete'])
        retained = self.root / 'context/attempt/rollout.jsonl'
        self.assertEqual(retained.read_bytes(), native)
        self.assertEqual((self.root / 'context/attempt/answer.txt').read_bytes(), self.expected_answer)
        completion = runner.obj(self.root / 'context/completion.json')
        self.assertEqual(completion['status'], 'matched')
        self.assertEqual(completion['finality'], 'unknown')
        self.assertFalse(completion['live_qualified'])
        request = runner.obj(self.root / 'context/attempt/attempt.json')
        self.assertEqual(request['limits']['rollout_candidate_bytes_each'], 8388608)
        self.assertEqual(request['limits']['jsonl_record_bytes'], 4194304)
        self.assertEqual(request['limits']['stdout_bytes'], 1000000)
        self.assertEqual(request['limits']['stderr_bytes'], 1000000)
        self.assertEqual(request['limits']['prompt_bytes'], 1048576)
        rows = runner.capture.parse_jsonl(native, 'test-only', capture_limit=8388608, line_limit=4194304)['records']
        copies = []
        for row in rows:
            p = row['payload']
            if row['type'] == 'response_item' and p.get('role') == 'user':
                copies.extend(c['text'].encode('utf-8') for c in p['content'] if c['type'] == 'input_text')
            elif row['type'] == 'event_msg' and p.get('type') == 'item_completed' and p['item']['type'] == 'UserMessage':
                copies.extend(c['text'].encode('utf-8') for c in p['item']['content'] if c['type'] == 'text')
        self.assertEqual(copies, [(self.packet / 'assessor/input.json').read_bytes()] * 2)

    def test_m02_full_packet_native_copies_retained_with_frozen_capacity(self):
        self.assert_large_packet_captured('M02')

    def test_m04_full_packet_native_copies_retained_with_frozen_capacity(self):
        self.assert_large_packet_captured('M04')

    def test_native_capacity_contract_is_prospective_and_digest_bound(self):
        proper = self.prepare()
        self.assertEqual(proper['schema'], 'sshai-benchmark/issue10-assessor-manifest-2')
        self.assertEqual(proper['native_capture_capacity'], {'capture_limit': 8388608, 'line_limit': 4194304})
        for value in (None, {'capture_limit': 1048576, 'line_limit': 262144},
                      {'capture_limit': 8388609, 'line_limit': 4194304},
                      {'capture_limit': 8388608, 'line_limit': 4194305},
                      {'capture_limit': 8388608.0, 'line_limit': 4194304},
                      {'capture_limit': 8388608, 'line_limit': True},
                      {'capture_limit': 8388608, 'line_limit': 4194304, 'waiver': True}):
            with self.subTest(capacity=value):
                changed = copy.deepcopy(proper)
                if value is None:
                    changed.pop('native_capture_capacity')
                else:
                    changed['native_capture_capacity'] = value
                changed['digest'] = runner.sha_object(changed)
                write(self.root / 'manifest.json', bridge.encode(changed))
                with self.assertRaises(ValueError):
                    runner.load(self.root)
        old = copy.deepcopy(proper)
        old['schema'] = 'sshai-benchmark/issue10-assessor-manifest-1'
        old.pop('native_capture_capacity')
        old['digest'] = runner.sha_object(old)
        write(self.root / 'manifest.json', bridge.encode(old))
        with self.assertRaisesRegex(ValueError, 'manifest identity/digest'):
            runner.load(self.root)
        tampered = copy.deepcopy(proper)
        tampered['native_capture_capacity']['capture_limit'] += 1
        write(self.root / 'manifest.json', bridge.encode(tampered))
        with self.assertRaisesRegex(ValueError, 'manifest identity/digest'):
            runner.load(self.root)
        write(self.root / 'manifest.json', bridge.encode(proper))
        self.assertEqual(runner.load(self.root)['native_capture_capacity'], proper['native_capture_capacity'])
        self.assertFalse((self.root / 'context').exists())

    def assert_native_overflow_blocked(self, kind):
        self.ready()
        result = self.run_model(native_overflow=kind)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('rollout_capture_lost', result['blockers'])
        self.assertEqual(result['finality'], 'unknown')
        self.assertEqual(result['quality'], 'unknown')
        self.assertFalse(result['retry_allowed'])
        delivery = runner.obj(self.root / 'context/attempt/delivery.json')
        candidate = delivery['rollout']['candidates'][0]
        if kind == 'file':
            self.assertGreater(len(self.expected_rollout), 8388608)
            self.assertLess(max(map(len, self.expected_rollout.splitlines())), 4194304)
            self.assertEqual(candidate['status'], 'input_error')
        else:
            self.assertLess(len(self.expected_rollout), 8388608)
            self.assertGreater(max(map(len, self.expected_rollout.splitlines())), 4194304)
            self.assertEqual(candidate['status'], 'malformed')
            self.assertIn('jsonl_record_too_large', candidate['parser_issue_codes'])
            self.assertEqual((self.root / 'context/attempt' / candidate['retained']).read_bytes(), self.expected_rollout)

    def test_native_file_over_8MiB_still_blocks(self):
        self.assert_native_overflow_blocked('file')

    def test_native_record_over_4MiB_still_blocks(self):
        self.assert_native_overflow_blocked('line')

    def test_preflight_no_model_one_shot(self):
        self.prepare()
        with patch.object(pilot, '_collect_local_attempt') as model:
            result = runner.preflight(self.root)
            model.assert_not_called()
        self.assertTrue(result['passed'])
        with self.assertRaises(ValueError):
            runner.preflight(self.root)
        self.assertFalse((self.root / 'context').exists())

    def test_foreign_home_config_refuses_before_any_probe(self):
        self.prepare()
        for path in (self.root / 'runtime/codex-home/config.toml', self.root / 'runtime/home/AGENTS.md'):
            write(path, b'synthetic foreign config/rules')
            with patch.object(runner, '_ACCESS_QUALIFIER') as probe:
                with self.assertRaisesRegex(ValueError, 'foreign config/rules/state'):
                    runner._access(self.manifest)
                probe.assert_not_called()
            path.unlink()
        self.assertFalse((self.root / 'context').exists())

    def test_default_canaries_compose_existing_probe_and_exact_controls(self):
        self.prepare()
        calls = []
        def probe(argv, prompt, env, cwd, timeout):
            calls.append(argv)
            spec = json.loads(argv[-1])
            self.assertEqual(Path(spec['packet']).read_bytes(), (self.packet / 'assessor/input.json').read_bytes())
            expected = set(spec['denied']) | set(spec['denied_open']) | set(spec['denied_writes']) | {
                'allowed_read', 'allowed_write', 'descendant_read_denied', 'tool_network_denied',
                'packet_read', 'packet_write_denied', 'packet_create_denied'}
            return {'stdout': bridge.encode({key: True for key in expected}), 'stderr': b'', 'exit_code': 0,
                    'timed_out': False, 'capture_overflow': False, 'interrupted': False, 'start_error': None}
        with patch.object(runner, '_ACCESS_QUALIFIER', None), patch.object(runner.platform, 'system', return_value='Darwin'), \
             patch.object(runner.legacy, '_offline_probe'), patch.object(runner.legacy, '_bounded_process', probe):
            self.assertTrue(runner.preflight(self.root)['passed'])
        self.assertEqual(len(calls), 1)
        self.assertIn('sandbox', calls[0]); self.assertNotIn('exec', calls[0])
        for control in runner.CONTROLS + self.manifest['permission_overrides']:
            self.assertIn(control, calls[0])

    def test_delivery_bytes_not_finality_or_grades_and_no_retry(self):
        self.ready()
        result = self.run_model()
        self.assertEqual(result['status'], 'captured-unqualified')
        self.assertEqual(result['finality'], 'unknown'); self.assertEqual(result['quality'], 'unknown')
        self.assertFalse(result['retry_allowed'])
        self.assertTrue(result['usage']['complete'])
        for name, body in (('events.jsonl', self.expected_events), ('rollout.jsonl', self.expected_rollout), ('answer.txt', self.expected_answer)):
            self.assertEqual((self.root / 'context/attempt' / name).read_bytes(), body)
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval, allow_model_run=True)
        for entry in [self.root, *self.root.rglob('*')]:
            if entry.is_symlink():
                continue
            self.assertEqual(stat.S_IMODE(entry.stat().st_mode), 0o700 if entry.is_dir() else 0o600)

    def test_tools_invalidate_context(self):
        self.ready()
        result = self.run_model(tools=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('recorded_tool_activity_forbidden', result['blockers'])
        self.assertGreater(result['recorded_call_entries'], 0)

    def assert_unknown_activity_blocked(self, stream):
        self.ready()
        original_streams = completion_streams
        def future_streams():
            cli, rollout = original_streams()
            if stream == 'cli':
                cli.insert(-1, {'type': 'future_unobserved_event', 'payload': {'type': 'future_activity'}})
            else:
                rollout.insert(-1, {'type': 'future_unobserved_record', 'payload': {'type': 'future_activity'}})
            return cli, rollout
        with patch(__name__ + '.completion_streams', side_effect=future_streams):
            with patch.object(runner.legacy, '_bounded_process', self.model_process()):
                result = runner.run(self.root, self.approval, allow_model_run=True)
        self.assertEqual(result['status'], 'blocked')
        code = 'unknown_event_type' if stream == 'cli' else 'unknown_record_type'
        self.assertIn(f'capture:{stream}:{code}', result['blockers'])
        self.assertNotIn('recorded_tool_activity_forbidden', result['blockers'])
        self.assertEqual(result['quality'], 'unknown')
        self.assertEqual(result['finality'], 'unknown')
        self.assertFalse(result['retry_allowed'])
        self.assertEqual(result['recorded_call_entries'], 0)
        captured = runner.obj(self.root / 'context/capture.json')
        self.assertTrue(any(issue['source'] == stream and issue['code'] == code and issue['effect'] == 'uncertain'
                            for issue in captured['issues']))
        for name, body in (('events.jsonl', self.expected_events), ('rollout.jsonl', self.expected_rollout),
                           ('answer.txt', self.expected_answer)):
            self.assertEqual((self.root / 'context/attempt' / name).read_bytes(), body)
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval, allow_model_run=True)

    def test_unknown_cli_activity_blocks_without_malicious_label(self):
        self.assert_unknown_activity_blocked('cli')

    def test_unknown_rollout_activity_blocks_without_malicious_label(self):
        self.assert_unknown_activity_blocked('rollout')

    def assert_cli_notice_blocked(self, message, *, user_records=False):
        self.ready()
        original_streams = completion_streams
        def notice_streams():
            cli, rollout = original_streams()
            # Pinned ModelRerouted maps to ItemCompleted/ErrorItem with a message.
            cli.insert(-1, {'type': 'item.completed', 'item': {
                'id': 'synthetic-notice', 'type': 'error', 'message': message}})
            return cli, rollout
        with patch(__name__ + '.completion_streams', side_effect=notice_streams):
            result = self.run_model(user_records=user_records)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('cli_error_or_warning_item', result['blockers'])
        self.assertNotIn('recorded_tool_activity_forbidden', result['blockers'])
        self.assertEqual(result['recorded_call_entries'], 0)
        self.assertEqual(result['quality'], 'unknown')
        self.assertEqual(result['finality'], 'unknown')
        self.assertFalse(result['retry_allowed'])
        captured = runner.obj(self.root / 'context/capture.json')
        self.assertEqual(captured['issues'], [])  # General parser accepts this non-tool item.
        for name, body in (('events.jsonl', self.expected_events), ('rollout.jsonl', self.expected_rollout),
                           ('answer.txt', self.expected_answer)):
            self.assertEqual((self.root / 'context/attempt' / name).read_bytes(), body)
        self.assertEqual(runner.obj(self.root / 'context/result.json')['status'], 'blocked')
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval, allow_model_run=True)

    def test_pinned_model_rerouted_error_item_blocks_despite_requested_model_context(self):
        self.assert_cli_notice_blocked('model rerouted: gpt-5.6-sol -> synthetic-other (SyntheticReason)')

    def test_generic_warning_error_item_blocks_as_qualification_gap(self):
        self.assert_cli_notice_blocked('warning: synthetic native qualification gap')

    def test_large_native_capture_does_not_waive_tool_activity(self):
        self.use_case_packet('M02')
        self.ready()
        result = self.run_model(user_records=True, tools=True)
        self.assertGreater(len(self.expected_rollout), 1048576)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('recorded_tool_activity_forbidden', result['blockers'])
        self.assertGreater(result['recorded_call_entries'], 0)
        self.assertEqual(result['quality'], 'unknown')
        self.assertEqual(result['finality'], 'unknown')
        self.assertEqual((self.root / 'context/attempt/rollout.jsonl').read_bytes(), self.expected_rollout)

    def test_large_native_capture_does_not_waive_model_reroute_notice(self):
        self.use_case_packet('M02')
        self.assert_cli_notice_blocked('model rerouted: gpt-5.6-sol -> synthetic-other (SyntheticReason)',
                                      user_records=True)
        self.assertGreater(len(self.expected_rollout), 1048576)

    def test_model_context_mismatch_is_not_substitution(self):
        self.ready()
        self.assertIn('observed_model_context_missing_or_mismatched', self.run_model(mismatch=True)['blockers'])

    def test_overflow_and_timeout_preserved(self):
        self.ready()
        result = self.run_model(overflow=True, timed_out=True)
        self.assertEqual(result['status'], 'blocked')
        process = runner.obj(self.root / 'context/attempt/process.json')
        self.assertTrue(process['capture_overflow']); self.assertTrue(process['timed_out'])
        self.assertTrue((self.root / 'context/reservation.json').is_file())
        self.assertEqual((self.root / 'context/attempt/answer.txt').read_bytes(), self.expected_answer)

    def test_response_64KiB_boundary_remains_ungraded(self):
        self.ready()
        result = self.run_model(answer=b'x' * 65536)
        self.assertEqual(result['status'], 'captured-unqualified')
        self.assertEqual(result['quality'], 'unknown')
        self.assertEqual(result['finality'], 'unknown')
        self.assertEqual((self.root / 'context/attempt/answer.txt').stat().st_size, 65536)
        self.assertEqual(self.manifest['response_bytes'], 65536)

    def test_native_capacity_does_not_raise_packet_config_or_publication_caps(self):
        raw = b'x' * 1048577
        with self.assertRaisesRegex(ValueError, 'private runner output'):
            runner.write(self.private / 'oversized-output.json', raw)
        self.assertFalse((self.private / 'oversized-output.json').exists())
        for path in (self.config_path, self.packet / 'assessor/input.json'):
            with self.subTest(path=path.name):
                saved = path.read_bytes()
                write(path, raw)
                with self.assertRaisesRegex(ValueError, 'input file'):
                    self.prepare()
                self.assertFalse(self.root.exists())
                write(path, saved)

    def test_oversized_response_retained_but_never_eligible(self):
        self.ready()
        result = self.run_model(answer=b'x' * (bridge.MAX_RESPONSE_BYTES + 1))
        self.assertIn('response_missing_or_over_64KiB', result['blockers'])
        self.assertEqual((self.root / 'context/attempt/answer.txt').stat().st_size, bridge.MAX_RESPONSE_BYTES + 1)

    def test_failed_fresh_access_retains_reservation_without_model(self):
        self.ready()
        with patch.object(runner, '_ACCESS_QUALIFIER', side_effect=ValueError('synthetic denial failed')), \
             patch.object(pilot, '_collect_local_attempt') as model:
            with self.assertRaisesRegex(ValueError, 'synthetic denial'):
                runner.run(self.root, self.approval, allow_model_run=True)
            model.assert_not_called()
        result = runner.obj(self.root / 'context/result.json')
        self.assertEqual(result['launch'], 'not-started'); self.assertFalse(result['retry_allowed'])
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval, allow_model_run=True)

    def test_collector_exception_retains_unknown_attempt_and_no_retry(self):
        self.ready()
        with patch.object(pilot, '_collect_local_attempt', side_effect=OSError('synthetic capture failure')):
            with self.assertRaises(OSError):
                runner.run(self.root, self.approval, allow_model_run=True)
        result = runner.obj(self.root / 'context/result.json')
        self.assertEqual(result['launch'], 'attempted-or-unknown')
        self.assertTrue((self.root / 'context/reservation.json').exists())

    def test_start_failure_is_retained_not_zero_usage_or_quality(self):
        self.ready()
        outcome = {'stdout': b'', 'stderr': b'synthetic start failure', 'pid': None,
                   'exit_code': None, 'timed_out': False, 'capture_overflow': False,
                   'interrupted': False, 'start_error': 'synthetic', 'duration_seconds': 0.1}
        with patch.object(runner.legacy, '_bounded_process', return_value=outcome):
            result = runner.run(self.root, self.approval, allow_model_run=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['quality'], 'unknown')
        self.assertIsNone(result['usage']['totals'])
        self.assertEqual((self.root / 'context/attempt/stderr.txt').read_bytes(), b'synthetic start failure')
        self.assertTrue((self.root / 'context/reservation.json').is_file())

    def test_approval_missing_flag_foreign_digest_and_denial(self):
        self.ready()
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval)
        value = runner.obj(self.approval); value['manifest_digest'] = 'f' * 64
        write(self.approval, bridge.encode(value))
        with self.assertRaises(ValueError):
            runner.run(self.root, self.approval, allow_model_run=True)
        self.assertFalse((self.root / 'context').exists())
        value['manifest_digest'] = self.manifest['digest']
        outside = self.base / 'unprotected-approval.json'; write(outside, bridge.encode(value))
        with self.assertRaisesRegex(ValueError, 'explicit denial'):
            runner.run(self.root, outside, allow_model_run=True)

    def test_config_native_catalog_packet_and_permission_drift(self):
        self.prepare()
        for path in (self.config_path, self.catalog, self.native, self.root / 'packet/input.json'):
            original = path.read_bytes(); path.write_bytes(original + b' ')
            with self.assertRaises(ValueError):
                runner.load(self.root)
            path.write_bytes(original)
        (self.root / 'packet/input.json').chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'private'):
            runner.load(self.root)
        (self.root / 'packet/input.json').chmod(0o600)
        write(self.root / 'packet/owner.json', b'{}')
        with self.assertRaisesRegex(ValueError, 'foreign metadata'):
            runner.load(self.root)

    def test_config_invalid_tools_model_budget(self):
        for changes in ({'config_overrides': pilot.REQUIRED_CODEX_OVERRIDES},
                        {'model': {**self.config['model'], 'id': 'other'}},
                        {'budget': {**self.config['budget'], 'consumed_before': 24}},
                        {'budget': {**self.config['budget'], 'consumed_before': True}}):
            value = copy.deepcopy(self.config); value.update(changes)
            with self.assertRaises(ValueError):
                runner.validate_config(value)

    def test_empty_packet_refuses_without_context_or_model(self):
        data = bundle()
        for slot in data['slots']:
            slot.update(state='absent', answer=None, qualification=None, reason='original planned missing outcome')
        supplied, owner = bridge.build_packet(data, INSTRUCTIONS.read_bytes())
        empty = self.private / 'empty'
        bridge.publish(empty, {'assessor/input.json': bridge.encode(supplied), 'owner/inventory.json': bridge.encode(owner)})
        with patch.object(pilot, '_collect_local_attempt') as model:
            with self.assertRaisesRegex(ValueError, 'nonempty eligible'):
                runner.prepare(self.root, empty, self.config_path, self.catalog, self.native, self.auth)
            model.assert_not_called()
        self.assertFalse(self.root.exists())
        self.assertEqual(len(bridge.load_packet(empty)[1]['slots']), 2)

    def test_prepare_and_preflight_immutable(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.prepare()
        with patch.object(runner, '_ACCESS_QUALIFIER', side_effect=ValueError('synthetic preflight failure')):
            result = runner.preflight(self.root)
        self.assertFalse(result['passed'])
        self.assertTrue((self.root / 'preflight/result.json').is_file())
        with self.assertRaises(ValueError):
            runner.preflight(self.root)


if __name__ == '__main__':
    unittest.main()
