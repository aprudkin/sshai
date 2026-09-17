#!/usr/bin/env python3
"""Source-shaped completion comparisons, not live Codex qualification."""
import copy
import hashlib
import unittest

import benchmark_issue10_v3_capture as adapter
from test_issue10_v3_capture import cli_records, jsonl, rollout_records

ANSWER = "Synthetic final answer: проверка\n\nNo added newline."


def completion_streams():
    cli = cli_records(final=ANSWER)
    rollout = rollout_records()
    rollout[0]['payload'].update(cli_version='0.151.0', history_mode='paginated')
    rollout[2]['payload']['turn_id'] = 'synthetic-turn'
    rollout[-1]['payload'].update(turn_id='synthetic-turn', last_agent_message=ANSWER)
    rollout.insert(-1, {'type': 'event_msg', 'payload': {
        'type': 'item_completed', 'thread_id': 'synthetic-thread',
        'turn_id': 'synthetic-turn', 'completed_at_ms': 1,
        'item': {'type': 'AgentMessage', 'id': 'raw-message-id',
                 'content': [{'type': 'Text', 'text': ANSWER}], 'phase': 'final_answer'},
    }})
    return cli, rollout


class CompletionEvidenceTests(unittest.TestCase):
    def compare(self, cli=None, rollout=None, answer=ANSWER.encode()):
        default_cli, default_rollout = completion_streams()
        return adapter.completion_evidence_bytes(
            jsonl(default_cli if cli is None else cli),
            jsonl(default_rollout if rollout is None else rollout), answer)

    def assertUnmatched(self, report, reason):
        self.assertEqual(report['status'], 'unmatched')
        self.assertIn(reason, report['reasons'])
        self.assertEqual(report['finality'], 'unknown')
        self.assertFalse(report['live_qualified'])
        self.assertIsNone(report['binding'])

    def test_exact_match_has_hashes_and_source_local_record_binding_not_finality(self):
        cli, rollout = completion_streams()
        report = self.compare()
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['reasons'], [])
        self.assertEqual(report['finality'], 'unknown')
        self.assertFalse(report['live_qualified'])
        self.assertEqual(report['binding'], {
            'session_id': 'synthetic-thread', 'turn_id': 'synthetic-turn',
            'cli_message_record': 5, 'cli_terminal_record': 6,
            'rollout_message_record': 7, 'rollout_terminal_record': 8,
            'rollout_item_id': 'raw-message-id',
        })
        for name, data in [('events', jsonl(cli)), ('rollout', jsonl(rollout)),
                           ('answer', ANSWER.encode())]:
            self.assertEqual(report['inputs'][name], {
                'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        self.assertEqual(report['source_contract']['cli_version'], '0.151.0')
        self.assertNotIn('text', report)

    def test_completion_is_not_usage_or_raw_model_text_validation(self):
        cli, rollout = completion_streams()
        cli[-1]['usage']['input_tokens'] = True
        rollout.insert(-2, {'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': ANSWER + '<hidden-markup>'}],
        }})
        self.assertEqual(self.compare(cli, rollout)['status'], 'matched')
        report = adapter.capture_bytes(jsonl(cli), jsonl(rollout),
                                       b'{"timed_out":false,"exit_code":0}', answer_state='lost')
        self.assertEqual(report['instrumentation']['status'], 'invalid')
        self.assertFalse(report['usage']['complete'])
        self.assertEqual(report['answer']['state'], 'lost')

    def test_delivery_is_never_implicitly_captured_or_proven_absent(self):
        for answer in (None, b''):
            with self.subTest(answer=answer):
                report = self.compare(answer=answer)
                self.assertEqual(report['status'], 'unavailable')
                self.assertEqual(report['finality'], 'unknown')
                self.assertIsNone(report['binding'])
        for answer, reason in [(b'\xff', 'answer_invalid_utf8'),
                               (b' \n', 'answer_empty_text'),
                               (b'x' * (adapter.MAX_ANSWER_BYTES + 1), 'answer_too_large')]:
            self.assertUnmatched(self.compare(answer=answer), reason)

    def test_byte_exact_comparison_rejects_prefix_and_whitespace_normalization(self):
        for answer in (ANSWER.encode()[:-1], (ANSWER + '\n').encode(),
                       ANSWER.replace('\n', '\r\n').encode()):
            self.assertUnmatched(self.compare(answer=answer), 'cli_answer_mismatch')

    def test_file_and_cli_agreement_alone_do_not_match(self):
        cli, rollout = completion_streams()
        for mutate, reason in [
            (lambda r: r[-1]['payload'].update(last_agent_message='different'), 'terminal_answer_mismatch'),
            (lambda r: r[-2]['payload']['item']['content'][0].update(text='different'), 'rollout_answer_mismatch'),
            (lambda r: r[-2]['payload']['item'].pop('phase'), 'no_unique_final_phase_message'),
            (lambda r: r[-2]['payload']['item'].update(phase='commentary'), 'no_unique_final_phase_message'),
            (lambda r: r[-2]['payload']['item'].update(delivery='async'), 'unsupported_final_message_content_or_delivery'),
        ]:
            with self.subTest(reason=reason):
                changed = copy.deepcopy(rollout)
                mutate(changed)
                self.assertUnmatched(self.compare(cli, changed), reason)

    def test_thread_turn_version_and_exec_source_are_required(self):
        _, rollout = completion_streams()
        mutations = [
            (0, {'cli_version': '0.152.0'}, 'unsupported_rollout_identity_or_version'),
            (0, {'id': 'other'}, 'unsupported_rollout_identity_or_version'),
            (0, {'source': 'cli'}, 'unsupported_rollout_identity_or_version'),
            (0, {'history_mode': 'legacy'}, 'unsupported_rollout_identity_or_version'),
            (0, {'history_mode': None}, 'unsupported_rollout_identity_or_version'),
            (0, {'forked_from_id': 'other'}, 'unsupported_rollout_identity_or_version'),
            (-1, {'turn_id': 'other'}, 'unsupported_rollout_lifecycle'),
            (-2, {'turn_id': 'other'}, 'rollout_item_binding_mismatch'),
            (-2, {'thread_id': 'other'}, 'rollout_item_binding_mismatch'),
        ]
        for index, changes, reason in mutations:
            with self.subTest(changes=changes):
                changed = copy.deepcopy(rollout)
                changed[index]['payload'].update(changes)
                self.assertUnmatched(self.compare(rollout=changed), reason)

    def test_cli_terminal_missing_failed_duplicate_or_followed_by_activity(self):
        cli, _ = completion_streams()
        for changed in (cli[:-1], cli + [cli[-1]], cli + [cli[-2]],
                        [*cli[:-1], {'type': 'turn.failed'}],
                        [*cli[:-1], {'type': 'error'}],
                        [cli[0], *cli]):
            with self.subTest(changed=changed):
                self.assertNotEqual(self.compare(cli=changed)['status'], 'matched')

    def test_rollout_terminal_missing_failed_duplicate_error_or_later_activity(self):
        _, rollout = completion_streams()
        for changed in (rollout[:-1], rollout + [rollout[-1]], rollout + [rollout[-2]],
                        [*rollout[:-1], {'type': 'event_msg', 'payload': {
                            'type': 'turn_aborted', 'turn_id': 'synthetic-turn'}}]):
            self.assertNotEqual(self.compare(rollout=changed)['status'], 'matched')
        rollout[-1]['payload']['error'] = {'message': 'synthetic failure'}
        self.assertUnmatched(self.compare(rollout=rollout), 'unsupported_rollout_lifecycle')

    def test_duplicates_and_later_commentary_are_not_unique_completion_evidence(self):
        cli, rollout = completion_streams()
        changed = copy.deepcopy(cli)
        changed.insert(-1, copy.deepcopy(cli[-2]))
        self.assertUnmatched(self.compare(cli=changed), 'ambiguous_cli_message')
        changed = copy.deepcopy(rollout)
        changed.insert(-1, copy.deepcopy(rollout[-2]))
        self.assertUnmatched(self.compare(rollout=changed), 'ambiguous_rollout_item')
        for phase in ('final_answer', 'commentary'):
            changed = copy.deepcopy(rollout)
            extra = copy.deepcopy(rollout[-2])
            extra['payload']['item'].update(id='second-message', phase=phase)
            changed.insert(-1, extra)
            self.assertUnmatched(self.compare(rollout=changed), 'no_unique_final_phase_message')

    def test_earlier_commentary_is_allowed_and_ids_are_not_cross_stream_joined(self):
        cli, rollout = completion_streams()
        cli.insert(2, {'type': 'item.completed', 'item': {
            'id': 'comment-1', 'type': 'agent_message', 'text': 'Investigating'}})
        comment = copy.deepcopy(rollout[-2])
        comment['payload']['item'].update(id='raw-comment', phase='commentary',
                                         content=[{'type': 'Text', 'text': 'Investigating'}])
        rollout.insert(3, comment)
        self.assertEqual(self.compare(cli, rollout)['status'], 'matched')
        self.assertNotEqual(cli[-2]['item']['id'], rollout[-2]['payload']['item']['id'])

    def test_narrow_content_recipe_refuses_malformed_or_multiple_blocks(self):
        _, rollout = completion_streams()
        for content in (None, [], [{'type': 'text', 'text': ANSWER}],
                        [{'type': 'Text', 'text': 1}],
                        [{'type': 'Text', 'text': ANSWER}, {'type': 'Text', 'text': ''}]):
            changed = copy.deepcopy(rollout)
            changed[-2]['payload']['item']['content'] = content
            self.assertUnmatched(self.compare(rollout=changed), 'unsupported_final_message_content_or_delivery')

    def test_malformed_bounded_streams_never_match(self):
        cli, rollout = completion_streams()
        valid = jsonl(cli)
        for bad in (valid[:-1], valid + b'not json\n', b'\xff\n',
                    b'{"type":"thread.started","type":"thread.started"}\n',
                    b'x' * (adapter.MAX_CAPTURE_BYTES + 1)):
            self.assertUnmatched(adapter.completion_evidence_bytes(bad, jsonl(rollout), ANSWER.encode()),
                                 'incomplete_or_malformed_stream')
        self.assertUnmatched(self.compare(cli=[None]), 'malformed_record')
        rollout[-1]['payload'] = None
        self.assertUnmatched(self.compare(rollout=rollout), 'malformed_rollout_payload')


if __name__ == '__main__':
    unittest.main()
