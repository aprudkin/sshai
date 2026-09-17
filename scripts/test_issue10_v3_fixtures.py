#!/usr/bin/env python3
"""Offline tests for the new 18-case draft; no old runner or host execution."""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import plistlib
import re
from pathlib import Path
import tempfile
import unittest

from benchmark_issue10_v3_cases import build_cases
from prepare_issue10_v3_fixtures import CASE_IDS, bundle, prepare, validate_cases


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = build_cases()

    def test_case_set_and_deterministic_bytes(self):
        self.assertEqual(set(self.cases), set(CASE_IDS))
        self.assertEqual(bundle(self.cases), bundle(build_cases()))

    def test_all_source_references(self):
        validate_cases(self.cases)
        for case in self.cases.values():
            for fact in case['key']['required_facts'] + case['key'].get('supporting_facts', []):
                for ref in fact['evidence']:
                    lines = case['files'][ref['file']].splitlines()
                    self.assertEqual('\n'.join(lines[ref['start_line']-1:ref['end_line']]), ref['quote'])

    def test_rejects_corrupted_citations(self):
        for mode in ('quote', 'missing', 'range', 'boolean'):
            with self.subTest(mode=mode):
                cases = copy.deepcopy(self.cases)
                ref = cases['M01']['key']['required_facts'][0]['evidence'][0]
                if mode == 'quote':
                    ref['quote'] = 'not the cited text'
                elif mode == 'missing':
                    ref['file'] = 'absent.txt'
                elif mode == 'range':
                    ref['end_line'] = 100000000
                else:
                    ref['start_line'] = True
                with self.assertRaises(ValueError):
                    validate_cases(cases)

    def test_rejects_paths_and_encoding(self):
        for path in ('../escape.txt', '/absolute.txt', 'a/../b', 'a\\b', 'C:/bad', 'a//b'):
            with self.subTest(path=path):
                cases = copy.deepcopy(self.cases)
                cases['M01']['files'][path] = 'synthetic\n'
                with self.assertRaises(ValueError):
                    validate_cases(cases)
        cases = copy.deepcopy(self.cases)
        cases['M01']['files']['bad.txt'] = 'CRLF\r\n'
        with self.assertRaises(ValueError):
            validate_cases(cases)
        cases = copy.deepcopy(self.cases)
        cases['M01']['files']['collision'] = 'file\n'
        cases['M01']['files']['collision/child'] = 'child\n'
        with self.assertRaises(ValueError):
            validate_cases(cases)

    def test_missing_case_and_duplicate_facts(self):
        cases = copy.deepcopy(self.cases)
        del cases['W06']
        with self.assertRaises(ValueError):
            validate_cases(cases)
        cases = copy.deepcopy(self.cases)
        facts = cases['M01']['key']['required_facts']
        facts.append(copy.deepcopy(facts[0]))
        with self.assertRaises(ValueError):
            validate_cases(cases)

    def test_disk_output_hashes_isolation_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'bundle'
            manifest = prepare(root)
            self.assertEqual(manifest['case_count'], 18)
            self.assertFalse(manifest['frozen'])
            for entry in manifest['files']:
                data = (root / entry['path']).read_bytes()
                self.assertEqual(len(data), entry['bytes'])
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
            for case in manifest['cases']:
                input_root = root / 'inputs' / case['case_id']
                for entry in case['files']:
                    data = (input_root / entry['file']).read_bytes()
                    self.assertEqual(len(data.decode().splitlines()), entry['lines'])
                self.assertFalse((input_root / 'key.json').exists())
                self.assertTrue((root / 'evaluator' / case['case_id'] / 'key.json').exists())
            before = (root / 'manifest.json').read_bytes()
            with self.assertRaises(FileExistsError):
                prepare(root)
            self.assertEqual((root / 'manifest.json').read_bytes(), before)

    def test_deployment_inventories_match_exact_tree_bytes(self):
        for series in 'MLW':
            files = self.cases[f'{series}04']['files']
            for version in ('previous', 'current'):
                rows = list(csv.DictReader(io.StringIO('\n'.join(files[f'{version}/inventory.tsv'].splitlines()[1:])), delimiter='\t'))
                actual = {name[len(version)+1:] for name in files
                          if name.startswith(f'{version}/templates/') or name == f'{version}/README.txt'}
                self.assertEqual({row['path'] for row in rows}, actual)
                for row in rows:
                    data = files[f"{version}/{row['path']}"].encode()
                    self.assertEqual(int(row['bytes']), len(data))
                    self.assertEqual(row['sha256'], hashlib.sha256(data).hexdigest())
                manifest = json.loads(files[f'{version}/manifest.json'])
                self.assertTrue(manifest['summary_reports_enabled'])
                self.assertEqual(manifest['summary_template'], 'templates/summary.tpl')
            self.assertIn('previous/templates/summary.tpl', files)
            self.assertNotIn('current/templates/summary.tpl', files)

    def test_incident_order_and_authentication_precede_requests(self):
        for series in 'MLW':
            files = self.cases[f'{series}02']['files']
            for name in ('application.log', 'dependency.log'):
                lines = files[name].splitlines()
                stamps = [line.split()[0] for line in lines]
                self.assertEqual(stamps, sorted(stamps), (series, name))
                first_request = next(i for i, line in enumerate(lines) if 'request=' in line)
                first_auth = next(i for i, line in enumerate(lines)
                                  if 'authentication' in line and 'result=success' in line)
                self.assertLess(first_auth, first_request)

    def test_resource_accounting(self):
        def rows(case, name):
            text = '\n'.join(line for line in self.cases[case]['files'][name].splitlines()
                             if not line.startswith('#'))
            return list(csv.DictReader(io.StringIO(text), delimiter='\t'))
        descriptors = rows('M06', 'descriptors.tsv')
        limit = rows('M06', 'process-limits.txt')[0]
        self.assertEqual(len(descriptors), int(limit['soft']))
        self.assertEqual(len({row['fd'] for row in descriptors}), len(descriptors))
        self.assertEqual(int(limit['soft']), int(limit['hard']))
        for row in rows('L06', 'space.tsv'):
            self.assertEqual(int(row['used_bytes']) + int(row['available_bytes']), int(row['total_bytes']))
        inode = rows('L06', 'inodes.tsv')[0]
        self.assertEqual(inode['available_inodes'], '0')
        self.assertEqual(inode['used_inodes'], inode['total_inodes'])
        counts = rows('L06', 'file-counts.tsv')
        self.assertEqual(sum(int(row['regular_files']) for row in counts), 900000)
        memory = rows('W06', 'memory.tsv')[0]
        commit = int(memory['committed_bytes'])
        self.assertEqual(int(memory['commit_limit_bytes']) - commit, int(memory['commit_headroom_bytes']))
        self.assertLess(int(memory['commit_headroom_bytes']), 256 * 1024**2)
        physical = rows('W06', 'physical-memory.tsv')[0]
        processes = rows('W06', 'process-memory.tsv')
        self.assertLessEqual(sum(int(row['commit_bytes']) for row in processes), commit)
        self.assertLessEqual(sum(int(row['working_set_bytes']) for row in processes),
                             int(physical['installed_bytes']) - int(physical['available_bytes']))
        for row in processes:
            self.assertLessEqual(int(row['working_set_bytes']), int(row['commit_bytes']))
        pagefiles = rows('W06', 'pagefiles.tsv')
        self.assertLessEqual(int(memory['commit_limit_bytes']), int(physical['installed_bytes'])
                             + sum(int(row['allocated_bytes']) for row in pagefiles))

    def test_healthy_thresholds_and_native_startup(self):
        for series in 'MLW':
            files = self.cases[f'{series}01']['files']
            rows = list(csv.DictReader(io.StringIO(files['metrics.tsv']), delimiter='\t'))
            self.assertEqual([int(row['request_latency_ms']) for row in rows], [42,45,43])
            self.assertEqual([int(row['queue_depth_items']) for row in rows], [1,0,1])
            self.assertIn('request_latency_max_ms=200', files['context.txt'])
            self.assertIn('queue_depth_max_items=10', files['context.txt'])
            startup = self.cases[f'{series}05']['files']
            inventory = list(csv.DictReader(io.StringIO('\n'.join(startup['inventory.tsv'].splitlines()[1:])), delimiter='\t'))
            candidates = [row for row in inventory if row['identity'].startswith('report-worker')]
            self.assertEqual([row['exists'] for row in candidates], ['false', 'true'])
        plist = plistlib.loads(self.cases['M05']['files']['launch.plist'].encode())
        self.assertEqual(plist['ProgramArguments'][0], '/opt/report-worker/current/bin/report-worker')
        self.assertEqual(plist['ProgramArguments'][1], '--config')

    def test_native_attribution_and_argument_citations(self):
        mac = self.cases['M05']
        self.assertNotIn('status=78', mac['files']['launch.log'])
        self.assertNotIn('service_exit', mac['files']['launch.log'])
        self.assertIn('errno=2', mac['files']['launch.log'])
        win = self.cases['W05']
        self.assertEqual(len(win['files']['events.txt'].splitlines()), 1)
        for forbidden in ('ExecutablePath=', 'Action=StartRequested', 'State=Stopped'):
            self.assertNotIn(forbidden, win['files']['events.txt'])
        self.assertIn('EventID=7000', win['files']['events.txt'])
        self.assertIn('Win32Error=2', win['files']['events.txt'])
        self.assertIn('BinaryPathName="C:\\Program Files', win['files']['service-config.txt'])
        for case, source in ((mac, 'launch.plist'), (win, 'service-config.txt')):
            fact = next(f for f in case['key']['required_facts'] if f['id'] == 'effective-configured-path')
            quote = '\n'.join(r['quote'] for r in fact['evidence'] if r['file'] == source)
            self.assertIn('--config', quote)
            self.assertIn('worker.conf', quote)

    def test_resource_semantics_and_optional_inode_context(self):
        mac = self.cases['M06']
        self.assertIn('file_table_entries', mac['files']['system-files.txt'])
        self.assertNotIn('\tdescriptors', mac['files']['system-files.txt'])
        win = self.cases['W06']
        self.assertIn('allocated_bytes', win['files']['pagefiles.tsv'])
        self.assertNotIn('maximum_bytes', win['files']['pagefiles.tsv'])
        self.assertNotIn('current_bytes', win['files']['pagefiles.tsv'])
        self.assertNotIn('accounting overhead', win['files']['context.txt'])
        self.assertIn('usable physical RAM is not supplied', win['files']['context.txt'])
        inode = self.cases['L06']['key']
        self.assertNotIn('file-count-scope', {f['id'] for f in inode['required_facts']})
        self.assertEqual([f['id'] for f in inode['supporting_facts']], ['file-count-scope'])
        cases = copy.deepcopy(self.cases)
        cases['L06']['key']['supporting_facts'][0]['evidence'][0]['quote'] = 'incorrect'
        with self.assertRaises(ValueError):
            validate_cases(cases)
        complete = json.loads(bundle(self.cases)['evaluator/L06/calibration.json'])['complete']
        self.assertNotIn('file-counts.tsv', complete['answer'])
        self.assertEqual(complete['candidate_evidence_score'], 2)
        self.assertFalse(complete['human_qualified'])

    def test_calibration_and_missingness_are_not_observed_scores(self):
        files = bundle(self.cases)
        for case_id in CASE_IDS:
            anchors = json.loads(files[f'evaluator/{case_id}/calibration.json'])
            self.assertEqual(len(anchors), 6 if case_id.endswith('01') or case_id == 'L06' else 5)
            if case_id.endswith('01'):
                self.assertFalse(anchors['wrong-historical-diagnosis']['candidate_diagnosis_correct'])
            if case_id == 'L06':
                self.assertEqual(anchors['unsafe-remedy']['candidate_recommendation_score'], 0)
            for anchor in anchors.values():
                self.assertFalse(anchor['human_qualified'])
                self.assertEqual(anchor['answer'].count('## '), 4)
                for path, first, last in re.findall(r'([A-Za-z0-9_./-]+):(\d+)-(\d+)', anchor['answer']):
                    self.assertIn(path, self.cases[case_id]['files'])
                    lines = self.cases[case_id]['files'][path].splitlines()
                    self.assertTrue(1 <= int(first) <= int(last) <= len(lines))
            self.assertEqual(anchors['complete']['candidate_evidence_score'], 2)
            self.assertEqual(anchors['no-evidence']['candidate_evidence_score'], 0)
            self.assertEqual(anchors['no-recommendation']['candidate_recommendation_score'], 0)
        outcomes = json.loads(files['evaluator/outcome-calibration.json'])
        self.assertEqual(outcomes['collector_lost_answer'], {'quality': 'unknown'})
        self.assertEqual(outcomes['timeout_without_final']['evidence'], 0)


if __name__ == '__main__':
    unittest.main()
