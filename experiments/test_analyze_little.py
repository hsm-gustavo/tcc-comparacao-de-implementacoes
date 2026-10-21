"""Arithmetic, cohort attribution and runner checks; no external services/load."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('analyze_little', ROOT / 'experiments/analyze_little.py')
little = importlib.util.module_from_spec(spec)
spec.loader.exec_module(little)


class LittleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'metadata.json').write_text(json.dumps({'environment': {'RUN_ID': 'test'}}))

    def summary(self, repetition, started=2):
        stage = f'rate100_run{repetition}'
        (self.root / f'{stage}.json').write_text(json.dumps({
            'rate': 100, 'repetition': repetition, 'started_requests': started,
            'requests': started, 'accepted_orders': started, 'dropped_iterations': 0,
            'scenario_start_ms': repetition * 1000000, 'observation_seconds': 180,
        }))
        (self.root / f'{stage}.exit-code.txt').write_text('0\n')

    def sample(self, stage, requests, count, total, last=0, **overrides):
        return {'timestamp': '2026-10-21T12:00:00Z', 'type': 'db_diagnostic_sample',
                'service': 'order-service', 'runId': 'test', 'pid': 1,
                'poolHoldByStage': [{'stage': stage, 'requests': requests,
                                    'acquired': count, 'released': count,
                                    'acquireFailures': 0, 'removedWithoutRelease': 0,
                                    'holdMsTotal': total, 'lastReleaseEpochMs': last,
                                    **overrides}]}

    def write_log(self, records):
        (self.root / 'application.log').write_text(''.join(
            'order-service-1 | ' + json.dumps(record) + '\n' for record in records))

    def test_weights_by_checkout_count_and_excludes_warmup_and_other_runs(self):
        self.summary(1, started=2)
        self.summary(2, started=1)
        self.write_log([
            self.sample('test-warmup', 1, 1, 99999),
            self.sample('test-rate100_run1', 2, 1, 100),
            self.sample('test-rate100_run1', 0, 3, 900),
            self.sample('test-rate100_run2', 1, 1, 500),
            {**self.sample('test-rate100_run1', 9, 9, 99999), 'runId': 'foreign'},
        ])
        report = little.analyze(self.root, expected_repetitions=2)
        first = report['repetitions'][0]
        self.assertEqual(first['pool_hold_ms'], 250)  # not (100 + 300)/2
        self.assertEqual(first['C_single_checkout_assumption'], 25)
        self.assertEqual(first['C_at_target_request_rate'], 50)  # two checkouts/request
        self.assertEqual(report['aggregate']['pool_hold_ms'], 300)  # 1500 / 5
        self.assertEqual(report['aggregate']['pool_hold_per_request_ms'], 500)
        self.assertEqual(report['aggregate']['C_at_target_request_rate'], 50)
        self.assertAlmostEqual(report['aggregate']['C_observed'], 1.5 / 360)
        self.assertEqual(report['warnings'], [])

    def test_delayed_release_belongs_to_original_stage_and_extends_observation(self):
        self.summary(1)
        self.write_log([
            self.sample('test-rate100_run1', 2, 0, 0, acquired=1),
            self.sample('test-rate100_run2', 3, 3, 99999),
            self.sample('test-rate100_run1', 0, 1, 200, last=1200000, acquired=0),
        ])
        row = little.analyze(self.root, expected_repetitions=1)['repetitions'][0]
        self.assertEqual(row['requests'], 2)
        self.assertEqual(row['observation_s'], 200)
        self.assertEqual(row['pool_hold_ms'], 200)
        self.assertEqual(row['warnings'], [])

    def test_unreturned_clients_and_partial_runs_are_explicitly_flagged(self):
        self.summary(1)
        (self.root / 'rate100_run1.exit-code.txt').write_text('99')
        self.write_log([self.sample('test-rate100_run1', 1, 1, 100,
                                   acquired=2, acquireFailures=2, removedWithoutRelease=1)])
        report = little.analyze(self.root, expected_repetitions=20)
        self.assertEqual(len(report['repetitions'][0]['warnings']), 4)
        self.assertEqual(report['measured_repetitions'], 1)
        self.assertIn('Expected 20', report['warnings'][0])

    def test_missing_instrumentation_does_not_invent_zero_hold_or_C(self):
        self.summary(1)
        self.write_log([])
        report = little.analyze(self.root)
        self.assertEqual(report['measured_repetitions'], 0)
        self.assertIsNone(report['aggregate']['pool_hold_ms'])
        self.assertIsNone(report['aggregate']['C_at_target_request_rate'])
        self.assertIsNone(report['aggregate']['C_observed'])


class LittleRunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in ('run-little.sh', 'experiments/analyze_little.py'):
            destination = self.root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        # Replace the pilot itself, not Docker: only fixture files are generated.
        (self.root / 'run.sh').write_text('''#!/usr/bin/env bash
python3 - <<'PY'
import json, os, pathlib
out = pathlib.Path('experiments/datasets/pilot') / os.environ['RUN_ID']
out.mkdir(parents=True)
keys = ['RUN_ID', 'RATES', 'DURATION', 'REPETITIONS', 'DB_DIAGNOSTICS']
(out/'metadata.json').write_text(json.dumps({'environment': {k:os.environ[k] for k in keys}}))
lines = []
for rep in range(1, int(os.environ['REPETITIONS']) + 1):
    stage = f'rate100_run{rep}'
    (out/f'{stage}.json').write_text(json.dumps({'rate':100,'repetition':rep,
      'started_requests':2,'requests':2,'accepted_orders':2,'dropped_iterations':0,
      'scenario_start_ms':1000000,'observation_seconds':180}))
    (out/f'{stage}.exit-code.txt').write_text('0')
    lines.append(json.dumps({'timestamp':'2026-10-21','type':'db_diagnostic_sample',
      'service':'order-service','runId':os.environ['RUN_ID'],'pid':1,
      'poolHoldByStage':[{'stage':os.environ['RUN_ID']+'-'+stage,'requests':2,
        'acquired':3,'released':3,'acquireFailures':0,'removedWithoutRelease':0,
        'holdMsTotal':600,'lastReleaseEpochMs':1000500}]}))
(out/'application.log').write_text('\\n'.join(lines)+'\\n')
PY
exit "${FAKE_EXIT:-0}"
''')
        self.env = dict(os.environ, RUN_ID='test-wrapper')
        for key in ('DURATION', 'REPETITIONS', 'SUDO_UID', 'SUDO_GID', 'FAKE_EXIT'):
            self.env.pop(key, None)

    def run_wrapper(self, **overrides):
        return subprocess.run(['bash', str(self.root / 'run-little.sh')], cwd=self.root,
                              env=dict(self.env, **overrides), capture_output=True, text=True, timeout=10)

    def test_defaults_and_final_calculation_without_any_real_load(self):
        result = self.run_wrapper(RATES='1,2', DB_DIAGNOSTICS='0')
        self.assertEqual(result.returncode, 0, result.stderr)
        out = self.root / 'experiments/datasets/pilot/test-wrapper'
        env = json.loads((out / 'metadata.json').read_text())['environment']
        self.assertEqual([env[k] for k in ('RATES', 'DURATION', 'REPETITIONS', 'DB_DIAGNOSTICS')],
                         ['100', '3m', '20', '1'])
        report = json.loads((out / 'analysis/little.json').read_text())
        self.assertEqual(report['measured_repetitions'], 20)
        self.assertEqual(report['aggregate']['C_at_target_request_rate'], 30)
        self.assertIn('= 30.000', result.stdout)
        self.assertTrue((out / 'analysis/little.csv').exists())
        original = (out / 'analysis/little.json').read_bytes()
        self.assertNotEqual(self.run_wrapper().returncode, 0)
        self.assertEqual((out / 'analysis/little.json').read_bytes(), original)

    def test_preserves_pilot_failure_status_even_when_partial_report_is_possible(self):
        result = self.run_wrapper(FAKE_EXIT='42', REPETITIONS='1')
        self.assertEqual(result.returncode, 42)
        self.assertIn('C na taxa alvo', result.stdout)


if __name__ == '__main__':
    unittest.main()
