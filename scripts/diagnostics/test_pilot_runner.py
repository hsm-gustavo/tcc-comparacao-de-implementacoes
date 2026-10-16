"""Run root run.sh with fake tools; no Docker, DB or actual load."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class PilotRunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = Path(__file__).resolve().parents[2]
        for name in ('run.sh', 'experiments/k6/pilot.js', 'experiments/analyze_pilot.py', 'scripts/seed.sh'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, path)
        scripts = self.root / 'scripts/diagnostics'
        scripts.mkdir()
        (scripts / 'collect-postgres.py').write_text('''import pathlib, signal, sys
out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])
(out/'postgres-activity.jsonl').write_text('{}\\n')
signal.pause()
''')
        (scripts / 'collect-host.py').write_text('')
        for service in ('order', 'warehouse', 'billing', 'shipping'):
            path = self.root / f'implementation/distributed-baseline/{service}-service'
            path.mkdir(parents=True)
            for name in ('package.json', 'pnpm-lock.yaml', 'Dockerfile'):
                (path / name).write_text('test fixture')
        (self.root / 'environment/compose').mkdir(parents=True)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RUN_ID='pilot-test', RATES='1,2', DURATION='1s', WARMUP_DURATION='1s',
                        SETTLING_SECONDS='0', COOLDOWN_SECONDS='0', RECOVERY_TIMEOUT_SECONDS='1',
                        AUTO_PLOT='0', COLLECT_HOST='0', DB_DIAGNOSTICS='1', SEED_DATA='auto',
                        PRE_ALLOCATED_VUS='2', MAX_VUS='2', REPETITIONS='1',
                        FAKE_BUILD_EXIT='0', FAKE_K6_EXIT='0', FAKE_ROOT=str(self.root))
        self.output = self.root / 'experiments/datasets/pilot/pilot-test'
        self.tool('sleep', '#!/bin/sh\n/bin/sleep 0.02\n')
        self.tool('git', '#!/bin/sh\necho fixture-head\n')
        self.tool('curl', '#!/bin/sh\necho \'[{"id":"product","price":10,"stockQuantity":100000}]\'\n')
        self.tool('docker', '''#!/usr/bin/env python3
import datetime, json, os, pathlib, sys, time
with (pathlib.Path(os.environ['FAKE_ROOT'])/'docker-calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:])+'\\n')
if 'build' in sys.argv:
    print('fixture build')
    sys.exit(int(os.environ['FAKE_BUILD_EXIT']))
if 'inspect' in sys.argv: print('healthy')
if 'logs' in sys.argv and '--follow' in sys.argv:
    while True:
        print(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'type':'db_diagnostic_sample','pool':{'inUse':0,'waiting':0}}), flush=True)
        time.sleep(.02)
''')
        self.tool('k6', '''#!/usr/bin/env python3
import csv, json, os, pathlib, sys, time
if 'version' in sys.argv:
    print('fixture-k6'); sys.exit(0)
if os.environ['PILOT_PHASE'] == 'benchmark' and int(os.environ['FAKE_K6_EXIT']):
    print('simulated k6 failure'); sys.exit(int(os.environ['FAKE_K6_EXIT']))
path = pathlib.Path(os.environ['SUMMARY_JSON'])
start = int(time.time())
path.write_text(json.dumps({'phase':os.environ['PILOT_PHASE'], 'rate':int(os.environ['RATE']),
 'repetition':int(os.environ['REPETITION']), 'scenario_start_ms':start*1000,
 'duration_seconds':1, 'observation_seconds':1, 'started_requests':1,
 'requests':1, 'accepted_orders':1, 'dropped_iterations':0}))
with path.with_suffix('.csv').open('w', newline='') as stream:
    writer = csv.writer(stream)
    writer.writerow(['metric_name','timestamp','metric_value','scenario','status','error_code'])
    writer.writerow(['started_requests',start,1,'pilot_orders','',''])
    writer.writerow(['http_req_duration',start,10,'pilot_orders','201',''])
''')

    def tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_runner(self, **overrides):
        return subprocess.run(['bash', str(self.root / 'run.sh')], env=dict(self.env, **overrides),
                              capture_output=True, text=True, timeout=20)

    def test_success_collects_all_stages_and_does_not_reset_containers_or_volumes(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['plots'], 'summary_only')
        self.assertTrue((self.output / 'analysis/levels.csv').exists())
        for stage in ('warmup', 'rate1_run1', 'rate2_run1'):
            self.assertTrue((self.output / f'{stage}.csv').exists())
        calls = [json.loads(line) for line in (self.root / 'docker-calls.jsonl').read_text().splitlines()]
        self.assertFalse(any('down' in call or '--volumes' in call or 'rm' in call for call in calls))

    def test_build_failure_preserves_status_and_evidence(self):
        result = self.run_runner(FAKE_BUILD_EXIT='42')
        self.assertEqual(result.returncode, 42, result.stderr)
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(status['phase'], 'startup')
        self.assertEqual(status['exitCode'], 42)
        self.assertTrue((self.output / 'logs/startup.log').exists())

    def test_k6_failure_stops_before_next_plateau(self):
        result = self.run_runner(FAKE_K6_EXIT='99')
        self.assertEqual(result.returncode, 99, result.stdout + result.stderr)
        self.assertEqual((self.output / 'rate1_run1.exit-code.txt').read_text().strip(), '99')
        self.assertFalse((self.output / 'rate2_run1.json').exists())

    def test_existing_output_and_invalid_rates_are_rejected_before_docker(self):
        self.output.mkdir(parents=True)
        marker = self.output / 'keep.txt'
        marker.write_text('evidence')
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'evidence')
        self.assertFalse((self.root / 'docker-calls.jsonl').exists())
        result = self.run_runner(RUN_ID='other-run', RATES='10,5')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'docker-calls.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
