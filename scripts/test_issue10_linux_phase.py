"""Offline prospective Linux phase contracts; no SSH, model or user-state access."""
import importlib.util
import unittest

import benchmark_issue10_v3 as coordinator

_spec = importlib.util.find_spec('benchmark_issue10_linux_phase')
if _spec is not None:
    import benchmark_issue10_linux_phase as phase
else:
    phase = None


class LinuxPhaseContractTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(phase, 'prospective Linux phase contract is not implemented')

    def test_fixed_pilot_allocation_and_balanced_pair_order(self):
        slots = phase.schedule('pilot')
        self.assertEqual(len(slots), 4)
        self.assertEqual({s['case_id'] for s in slots}, {'L01', 'L02'})
        self.assertEqual([s['slot'] for s in slots], [1, 2, 3, 4])
        self.assertEqual({slots[0]['arm'], slots[2]['arm']}, {'baseline', 'sshai'})
        for i in (0, 2):
            self.assertEqual(slots[i]['case_id'], slots[i + 1]['case_id'])
            self.assertEqual({slots[i]['arm'], slots[i + 1]['arm']}, {'baseline', 'sshai'})
        self.assertEqual(slots, phase.schedule('pilot'))

    def test_measurement_is_original_linux_projection(self):
        expected = [s for s in coordinator.schedule('measurement', 1010) if s['series'] == 'L']
        actual = phase.schedule('measurement')
        self.assertEqual(len(actual), 36)
        for i, (old, new) in enumerate(zip(expected, actual), 1):
            self.assertEqual(new, {**old, 'protocol_slot': old['slot'], 'slot': i})

    def test_no_alternative_population_or_seed(self):
        for value in ('local', 'windows', '', None, True):
            with self.assertRaises(ValueError):
                phase.schedule(value)

    def test_prompt_preserves_goal_and_remote_only_evidence(self):
        source = b'Investigate fixed snapshots. Root: {fixture_root}\nDo not repair.\n'
        baseline = phase.render_prompt(source, '/scratch/local', 'baseline', '/tools/sshai').decode()
        routed = phase.render_prompt(source, '/scratch/local', 'sshai', '/tools/sshai').decode()
        for text in (baseline, routed):
            self.assertTrue(text.startswith('Investigate fixed snapshots. Root: /fixture\nDo not repair.\n'))
            self.assertIn('issue10-target', text)
            self.assertIn('system OpenSSH', text)
            self.assertIn('source fixtures are read-only', text)
            self.assertIn('Do not execute the proposed remedy', text)
            self.assertIn('/scratch/local', text)
            self.assertNotIn('local --shell', text)
        self.assertIn('without sshai', baseline)
        self.assertIn('/tools/sshai run', routed)
        self.assertIn('already captured', routed)

    def test_prompt_rejects_ambiguous_or_nonutf8_goal(self):
        for source in (b'no placeholder', b'{fixture_root} {fixture_root}', b'\xff{fixture_root}'):
            with self.assertRaises(ValueError):
                phase.render_prompt(source, '/scratch/local', 'baseline', '/tools/sshai')
        with self.assertRaises(ValueError):
            phase.render_prompt(b'{fixture_root}', '/scratch/local', 'other', '/tools/sshai')


# Synthetic adapter seams do not exercise SSH, credentials or installed Codex.
import json
from pathlib import Path
from unittest.mock import patch
import benchmark_issue10_local_pilot as pilot
import test_issue10_local_pilot as fixtures


class SyntheticSession:
    def __init__(self, owner, manifest, slot, base, environment):
        self.owner, self.manifest, self.slot, self.base = owner, manifest, slot, base
        self.environment = dict(environment, PATH=str(base / 'scratch/bin') + ':' + environment['PATH'])
        self.config_overrides = pilot.sandbox.config_overrides(
            base / 'scratch', [Path(manifest['root']) / 'prepared', base / 'evidence',
                               base / 'codex-home', Path(manifest['fixture_bundle']['path']),
                               Path(manifest['auth']['path'])], [Path(manifest['sshai']['path'])])

    def __enter__(self):
        self.owner.entered += 1
        return self

    def __exit__(self, *args):
        self.owner.closed += 1

    def qualify(self):
        if self.owner.failure:
            raise phase.PhaseInputError('synthetic failed canary')
        fixture = self.manifest['slot_material'][str(self.slot['slot'])]['fixture_files']
        receipt = {'schema': phase.ACCESS_SCHEMA, 'status': 'passed',
                   'manifest_digest': self.manifest['digest'], 'slot': self.slot,
                   'base': str(self.base), 'checks': {'local:scratch': True, 'remote:fixture': True},
                   'effective_config_overrides': self.config_overrides,
                   'environment_sha256': pilot._sha(pilot._encoded(self.environment)),
                   'remote_fixture_pins': fixture, 'runtime_pins': self.manifest['remote_plan']['runtime_files']}
        receipt['digest'] = pilot._digest_object(receipt)
        return receipt

    def finalize(self):
        self.owner.finalized += 1
        material = self.manifest['slot_material'][str(self.slot['slot'])]['fixture_files']
        path = self.base / 'evidence/broker.json'
        fixtures.write(path, pilot._pretty({
            'schema': 'sshai-issue10-linux-broker-evidence-1',
            'lifecycle': {'reason': self.owner.broker_reason, 'calls': len(self.owner.broker_receipts)},
            'receipts': self.owner.broker_receipts}))
        return {'fixture_integrity': {name: True for name in material}, 'fixture_inventory_exact': True,
                'broker_evidence': {'path': str(path), 'sha256': fixtures.sha(path.read_bytes())},
                'remote_cleanup': {'status': 'removed-terminal-owned'}, 'access_status': 'passed'}


class SyntheticAdapter:
    def __init__(self, manifest, root):
        self.manifest, self.root = manifest, root
        self.entered = self.closed = self.finalized = 0
        self.failure = False
        self.broker_reason = 'cancelled'
        self.broker_receipts = []

    def session(self, slot, base, environment):
        return SyntheticSession(self, self.manifest, slot, base, environment)


class LinuxControllerFixture(fixtures.PilotFixture):
    def setUp(self):
        super().setUp()
        self.root = self.private / 'linux-pilot'
        config = self.config_value()
        config['sshai']['revision'] = 'ad1532b'
        fixtures.write(self.config, pilot._pretty(config))
        inventory = {'schema_version': 'issue10-synthetic-v3-draft-2', 'cases': [], 'files': []}
        for number in range(1, 7):
            case = f'L{number:02}'
            data = f'synthetic={case}\n'.encode()
            prompt = b'Diagnose {fixture_root}. Do not repair.\n'
            for relative, body in ((f'inputs/{case}/context.txt', data), (f'prompts/{case}.md', prompt)):
                fixtures.write(self.bundle / relative, body)
                inventory['files'].append({'path': relative, 'bytes': len(body), 'sha256': fixtures.sha(body)})
            inventory['cases'].append({'case_id': case, 'files': [{'file': 'context.txt'}]})
        fixtures.write(self.bundle / 'manifest.json', pilot._pretty(inventory))
        self.plan = self.private / 'staging/remote-plan.json'
        self.plan_value = {'schema': phase.REMOTE_PLAN_SCHEMA, 'host_alias': 'synthetic-target',
                          'parent': '/tmp/sshai-issue10-' + 'a' * 32, 'uid': 12000, 'gid': 12000,
                          'bash': '/bin/bash', 'setpriv': '/usr/bin/setpriv', 'python': '/usr/bin/python3',
                          'runtime_files': [{'source': name, 'path': name, 'size': 1,
                                             'sha256': '1' * 64, 'executable': True}
                                            for name in ('/bin/bash', '/usr/bin/setpriv', '/usr/bin/python3')],
                          'tools': {name: {'path': '/usr/bin/' + name, 'sha256': '2' * 64}
                                    for name in ('python', 'mount', 'unshare')},
                          'canaries': [{'name': 'home', 'path': '/tmp/synthetic-home/canary',
                                        'size': 1, 'sha256': '3' * 64}]}

        fixtures.write(self.plan, pilot._pretty(self.plan_value))
        source = self.base / 'source'
        for name in phase.SOURCE_PATHS:
            fixtures.write(source / name, b'synthetic frozen source\n')
        self.source_patch = patch.object(phase, 'REPO', source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)
        self.pin_patch = patch.object(pilot, 'EXPECTED_CODEX_PATH', self.codex)
        self.pin_patch.start()
        self.addCleanup(self.pin_patch.stop)
        self.adapters = []

    def adapter(self, manifest, root):
        item = SyntheticAdapter(manifest, root)
        self.adapters.append(item)
        return item

    def prepare(self, population='pilot', **kwargs):
        return phase.prepare(self.root, self.bundle, self.codex, self.sshai, self.config,
                             self.catalog, self.tool_overrides, self.auth,
                             self.assessment_instructions, self.assessment_rubric, self.plan,
                             population=population, _binary_probe=self.fake_probe, **kwargs)

    def ready(self):
        return phase.preflight(self.root, _adapter_factory=self.adapter)

    def approve(self, manifest):
        value = phase.approval_template(manifest)
        value.update(approved=True, approved_at_utc='2030-01-01T00:00:00Z',
                     authorization_note='Synthetic explicit issue10 scope, not a real launch.')
        path = self.private / 'approval.json'
        fixtures.write(path, pilot._pretty(value))
        return path

    def run_slot(self, number, approval, collector=None, allow=True, factory=None):
        return phase.run_slot(self.root, number, approval, allow_model_run=allow,
                              _adapter_factory=factory or self.adapter,
                              _attempt_collector=collector or fixtures.CollectionTests.successful_collector)


class LinuxControllerTests(LinuxControllerFixture):
    def test_prepare_pins_six_cases_but_never_copies_source_into_model_scratch(self):
        manifest = self.prepare()
        self.assertEqual(manifest['session_count'], 4)
        self.assertEqual(manifest['capture_capacity'], {'stream_limit': 8388608,
                                                      'capture_limit': 8388608, 'line_limit': 4194304})
        self.assertEqual(manifest['budget']['diagnostic_consumed_before_linux'], 40)
        self.assertEqual(manifest['budget']['assessment_consumed_before_linux'], 7)
        self.assertEqual(len(list((self.root / 'prepared/inputs').iterdir())), 6)
        self.assertFalse(any((self.root / 'slots').iterdir()))
        self.assertFalse(manifest['experimental_savings_claim_eligible'])
        self.assertEqual(phase.load_manifest(self.root), manifest)
        self.assertFalse(json.loads((self.root / 'approval-template.json').read_bytes())['approved'])

    def test_missing_production_adapter_does_not_qualify_or_consume_slot(self):
        self.prepare()
        with patch.dict('sys.modules', {'benchmark_issue10_linux_access': None}):
            with self.assertRaisesRegex(phase.PhaseInputError, 'capability'):
                phase.preflight(self.root)
        self.assertFalse(any((self.root / 'slots').iterdir()))

    def test_no_readiness_no_approval_no_allow_and_out_of_order_refuse(self):
        manifest = self.prepare()
        approval = self.approve(manifest)
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, approval)
        self.ready()
        for number in (0, True, 2, 5):
            with self.assertRaises(phase.PhaseInputError):
                self.run_slot(number, approval)
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, approval, allow=False)
        self.assertFalse(any((self.root / 'slots').iterdir()))

    def test_actual_per_slot_profile_and_environment_match_canaries(self):
        manifest = self.prepare()
        ready = self.ready()
        self.assertEqual(len(ready['cases']), 6)
        self.assertEqual(self.adapters[0].entered, 6)
        self.assertEqual(self.adapters[0].finalized, 6)
        approval = self.approve(manifest)
        def collect(attempt, argv, **kwargs):
            session = SyntheticSession(self.adapters[-1], manifest, manifest['slots'][0],
                                       self.root / 'slots/001', kwargs['env'])
            self.assertIn('permissions.issue10.network={enabled=false}', argv)
            self.assertTrue(kwargs['env']['PATH'].startswith(str(self.root / 'slots/001/scratch/bin')))
            self.assertEqual(kwargs['timeout_seconds'], 600)
            self.assertEqual(kwargs['capture_limit'], 8388608)
            self.assertFalse((self.root / 'slots/001/fixture').exists())
            self.assertEqual(kwargs['prompt'], phase.render_prompt(
                b'Diagnose {fixture_root}. Do not repair.\n', str(self.root / 'slots/001/scratch'),
                manifest['slots'][0]['arm'], str(self.sshai)))
            return fixtures.CollectionTests.successful_collector(attempt, argv, **kwargs)
        result = self.run_slot(1, approval, collect)
        self.assertEqual(result['execution'], 'completed')
        self.assertTrue(result['continuation']['allowed'])
        self.assertEqual(self.adapters[-1].closed, 1)
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, approval)

    def test_fresh_access_failure_consumes_slot_and_stops_prior_gate(self):
        manifest = self.prepare()
        self.ready()
        def failed(manifest, root):
            item = self.adapter(manifest, root)
            item.failure = True
            return item
        approval = self.approve(manifest)
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, approval, factory=failed)
        result = phase.summarize(self.root)['slots'][0]
        self.assertFalse(result['continuation']['allowed'])
        self.assertEqual(self.adapters[-1].closed, 1)
        for number in (1, 2):
            with self.assertRaises(phase.PhaseInputError):
                self.run_slot(number, approval)

    def test_abnormal_broker_stops_continuation_before_next_reservation(self):
        for reason in ('rejected-request', 'concurrent-request', 'protocol-error', 'supervisor-failed'):
            with self.subTest(reason=reason):
                self.root = self.private / ('linux-' + reason)
                manifest = self.prepare()
                self.ready()
                def abnormal(manifest, root):
                    owner = self.adapter(manifest, root)
                    owner.broker_reason = reason
                    return owner
                result = self.run_slot(1, self.approve(manifest), factory=abnormal)
                self.assertEqual(result['access']['status'], 'passed')
                self.assertFalse(result['continuation']['allowed'])
                self.assertIn('broker_supervisor_abnormal', result['continuation']['blockers'])
                with self.assertRaises(phase.PhaseInputError):
                    self.run_slot(2, self.approve(manifest))
                self.assertFalse((self.root / 'slots/002').exists())

    def test_cancelled_active_or_unpublished_request_is_not_normal_idle_stop(self):
        for row in ({'status': 'cancelled', 'publication': 'fifo-published'},
                    {'status': 'completed', 'publication': 'not-published'},
                    {'status': 'response-missing', 'publication': 'not-published'}):
            with self.subTest(row=row):
                self.root = self.private / ('linux-' + row['status'] + '-' + row['publication'])
                manifest = self.prepare()
                self.ready()
                def abnormal(manifest, root):
                    owner = self.adapter(manifest, root)
                    owner.broker_receipts = [row]
                    return owner
                result = self.run_slot(1, self.approve(manifest), factory=abnormal)
                self.assertFalse(result['continuation']['allowed'])
                self.assertIn('broker_transaction_incomplete_or_failed', result['continuation']['blockers'])

    def test_abnormal_no_model_preflight_is_retained_without_reserving_slot(self):
        self.prepare()
        def abnormal(manifest, root):
            owner = self.adapter(manifest, root)
            owner.broker_reason = 'rejected-request'
            return owner
        with self.assertRaises(phase.PhaseInputError):
            phase.preflight(self.root, _adapter_factory=abnormal)
        self.assertFalse(any((self.root / 'slots').iterdir()))
        self.assertEqual(json.loads((self.root / 'readiness/result.json').read_bytes())['status'], 'blocked')

    def test_malformed_broker_evidence_is_not_a_valid_continuation(self):
        good = {'schema': 'sshai-issue10-linux-broker-evidence-1',
                'lifecycle': {'reason': 'cancelled', 'calls': 0}, 'receipts': []}
        self.assertEqual(phase._broker_blockers(good), [])
        for value in ({}, {**good, 'lifecycle': {'reason': 'cancelled', 'calls': True}},
                      {**good, 'receipts': [{}]}, {**good, 'receipts': None}):
            with self.assertRaises(phase.PhaseInputError):
                phase._broker_blockers(value)

    def test_normal_idle_cancel_with_completed_transaction_remains_nonblocking(self):
        manifest = self.prepare()
        self.ready()
        def normal(manifest, root):
            owner = self.adapter(manifest, root)
            owner.broker_receipts = [{'status': 'completed', 'publication': 'fifo-published'}]
            return owner
        result = self.run_slot(1, self.approve(manifest), factory=normal)
        self.assertTrue(result['continuation']['allowed'])

    def test_reserved_interruption_never_becomes_unattempted_or_retryable(self):
        manifest = self.prepare()
        self.ready()
        base = self.root / 'slots/001'
        base.mkdir()
        row = phase.summarize(self.root)['slots'][0]
        self.assertEqual(row['state'], 'reserved-incomplete')
        self.assertEqual(row['launch'], 'attempted-or-unknown')
        self.assertFalse(row['retryable'])
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, self.approve(manifest))

    def test_prepared_and_remote_plan_source_and_binary_tampering_fail_closed(self):
        self.prepare()
        for path in (self.root / 'prepared/inputs/L01/context.txt', self.codex,
                     phase.REPO / next(iter(phase.SOURCE_PATHS)), self.plan):
            original, mode = path.read_bytes(), path.stat().st_mode & 0o777
            path.chmod(0o700)
            fixtures.write(path, original + b'tamper', mode=0o700)
            with self.assertRaises(phase.PhaseInputError):
                phase.load_manifest(self.root)
            fixtures.write(path, original, mode=mode)

    def test_readiness_receipt_tamper_refuses_before_reservation(self):
        manifest = self.prepare()
        self.ready()
        path = self.root / 'readiness/access-L01.json'
        value = json.loads(path.read_bytes())
        value['checks']['remote:fixture'] = False
        value['digest'] = pilot._digest_object(value)
        fixtures.write(path, pilot._pretty(value))
        with self.assertRaises(phase.PhaseInputError):
            self.run_slot(1, self.approve(manifest))
        self.assertFalse((self.root / 'slots/001').exists())

    def test_measurement_requires_actual_bound_pilot_and_qualification(self):
        with self.assertRaises(phase.PhaseInputError):
            self.prepare('measurement')

    def test_full_pilot_then_original_36_measurement_with_bound_caller_review(self):
        manifest = self.prepare()
        self.ready()
        approval = self.approve(manifest)
        for number in range(1, 5):
            self.assertTrue(self.run_slot(number, approval)['continuation']['allowed'])
        pilot_root = self.root
        qualification = self.private / 'synthetic-qualified-evidence.json'
        fixtures.write(qualification, b'{"synthetic_caller_qualification":true}\n')
        review = self.private / 'linux-pilot-review.json'
        fixtures.write(review, pilot._pretty({
            'schema': phase.PILOT_REVIEW_SCHEMA, 'manifest_digest': manifest['digest'],
            'status': 'passed', 'provenance': 'Synthetic caller-qualified prerequisite, not live assessment.',
            'evidence': {str(qualification): fixtures.sha(qualification.read_bytes())}}))
        self.root = self.private / 'linux-measured'
        measured = self.prepare('measurement', pilot_root=pilot_root, pilot_review_path=review)
        self.assertEqual(measured['slots'], phase.schedule('measurement'))
        self.assertEqual(measured['session_count'], 36)
        self.ready()
        measured_approval = self.approve(measured)
        for number in range(1, 37):
            self.assertTrue(self.run_slot(number, measured_approval)['continuation']['allowed'])
        summary = phase.summarize(self.root)
        self.assertEqual(len(summary['slots']), 36)
        self.assertIsNone(summary['comparative_savings_claim'])
        self.assertNotIn(str(self.private), json.dumps(summary))
        fixtures.write(qualification, b'changed caller evidence\n')
        with self.assertRaises(phase.PhaseInputError):
            phase.load_manifest(self.root)

    def test_model_notice_context_mismatch_lost_answer_and_unsupported_records_stop(self):
        from test_issue10_v3_capture import jsonl
        variants = ('notice', 'model', 'effort', 'answer', 'rollout', 'unsupported', 'record_bound')
        for variant in variants:
            with self.subTest(variant=variant):
                self.root = self.private / ('linux-' + variant)
                manifest = self.prepare()
                self.ready()
                cli, rollout = fixtures.completion_streams()
                if variant == 'notice':
                    cli.append({'type': 'item.completed', 'item': {'type': 'error', 'message': 'model rerouted'}})
                if variant in ('model', 'effort'):
                    for row in rollout:
                        if row.get('type') == 'turn_context':
                            row['payload'][variant] = 'other'
                cli_bytes = jsonl(cli)
                if variant == 'record_bound':
                    cli_bytes += jsonl([{'type': 'item.completed', 'item': {'type': 'agent_message',
                                                                         'text': 'x' * (4194304 + 1)}}])
                def collect(attempt, argv, **kwargs):
                    if variant == 'unsupported':
                        return fixtures.CollectionTests.unsupported_record_collector(attempt, argv, **kwargs)
                    return fixtures.CollectionTests.successful_collector(attempt, argv, cli_data=cli_bytes,
                        rollout_data=jsonl(rollout), capture_answer=variant != 'answer',
                        capture_rollout=variant != 'rollout', **kwargs)
                result = self.run_slot(1, self.approve(manifest), collect)
                self.assertFalse(result['continuation']['allowed'])
                for number in (1, 2):
                    with self.assertRaises(phase.PhaseInputError):
                        self.run_slot(number, self.approve(manifest))

    def test_collector_interrupt_retains_failure_unknown_attempt_and_finalizes(self):
        manifest = self.prepare()
        self.ready()
        def collect(*args, **kwargs):
            raise KeyboardInterrupt('synthetic interrupted model attempt')
        with self.assertRaises(KeyboardInterrupt):
            self.run_slot(1, self.approve(manifest), collect)
        row = phase.summarize(self.root)['slots'][0]
        self.assertEqual(row['launch'], 'attempted-or-unknown')
        self.assertFalse(row['continuation']['allowed'])
        self.assertEqual(self.adapters[-1].finalized, 1)
        self.assertEqual(self.adapters[-1].closed, 1)

    def test_receipt_is_not_a_success_boolean_or_a_different_exec_profile(self):
        manifest = self.prepare()
        self.ready()
        for variant in ('boolean', 'config', 'env', 'pins'):
            with self.subTest(variant=variant):
                self.root = self.private / ('bad-' + variant)
                current = self.prepare()
                self.ready()
                def factory(manifest, root):
                    adapter = self.adapter(manifest, root)
                    original = adapter.session
                    def session(*args):
                        instance = original(*args)
                        original_qualify = instance.qualify
                        def qualify():
                            value = original_qualify()
                            if variant == 'boolean':
                                return {'status': 'passed', 'approved': True}
                            if variant == 'config':
                                value['effective_config_overrides'] = ['network=true']
                            if variant == 'env':
                                value['environment_sha256'] = '0' * 64
                            if variant == 'pins':
                                value['remote_fixture_pins'] = {}
                            value['digest'] = pilot._digest_object(value)
                            return value
                        instance.qualify = qualify
                        return instance
                    adapter.session = session
                    return adapter
                with self.assertRaises(phase.PhaseInputError):
                    self.run_slot(1, self.approve(current), factory=factory)

    def test_private_plan_rejects_host_options_traversal_and_unpinned_runtime(self):
        import copy
        for key, value in [('host_alias', '-oProxyCommand=sh'), ('parent', '/tmp/../private'),
                           ('uid', 0), ('runtime_files', []), ('canaries', True)]:
            plan = copy.deepcopy(self.plan_value)
            plan[key] = value
            with self.assertRaises(phase.PhaseInputError):
                phase._remote_plan(plan)

    def test_intercepted_helper_add_profile_is_explicit_frozen_and_default_off(self):
        import benchmark_issue10_intercepted_patch as patch_profile
        with self.assertRaises(phase.PhaseInputError):
            self.prepare(intercepted_patch_profile={'allow_all_file_changes': True})
        manifest = self.prepare(intercepted_patch_profile=dict(patch_profile.ADD_PROFILE))
        self.assertEqual(manifest['intercepted_patch_profile'], patch_profile.ADD_PROFILE)
        self.assertEqual(phase.load_manifest(self.root), manifest)
        self.ready()
        with patch.object(patch_profile, 'audit', wraps=patch_profile.audit) as audit:
            self.run_slot(1, self.approve(manifest))
        self.assertEqual(audit.call_args.kwargs['profile'], patch_profile.ADD_PROFILE)


if __name__ == '__main__':
    unittest.main()
