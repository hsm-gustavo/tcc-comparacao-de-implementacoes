"""Run root run.sh with fake tools; no Docker, DB or actual load."""
import json
import csv
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
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
                        K6_CSV_STORAGE='disk', K6_CSV_TMPFS_ROOT='/dev/shm',
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
                          'type':'db_diagnostic_sample',
                          'pool':{'total':1,'idle':1,'inUse':0,'waiting':0},
                          'connectionAcquire':{'p95Ms':0},'connectionHold':{'p95Ms':0},
                          'queryDuration':{'p95Ms':0},'eventLoop':{'p99Ms':0}}), flush=True)
        time.sleep(.02)
''')
        self.tool('k6', '''#!/usr/bin/env python3
import csv, hashlib, json, os, pathlib, sys, time
if 'version' in sys.argv:
    print('fixture-k6'); sys.exit(0)
path = pathlib.Path(os.environ['SUMMARY_JSON'])
csv_path = pathlib.Path(sys.argv[sys.argv.index('--out') + 1].removeprefix('csv='))
if os.environ['K6_CSV_STORAGE'] == 'tmpfs':
    assert not list(path.parent.glob('*.csv')), 'CSVs were exported while load was still running'
start = int(time.time())
with csv_path.open('w', newline='') as stream:
    writer = csv.writer(stream)
    writer.writerow(['metric_name','timestamp','metric_value','scenario','status','error_code'])
    writer.writerow(['started_requests',start,1,'pilot_orders','',''])
    writer.writerow(['http_req_duration',start,10,'pilot_orders','201',''])
with (path.parent/'csv-observations.jsonl').open('a') as stream:
    stream.write(json.dumps({'path':str(csv_path),
                            'sha256':hashlib.sha256(csv_path.read_bytes()).hexdigest()})+'\\n')
if os.environ['PILOT_PHASE'] == 'benchmark' and os.environ.get('FAKE_K6_PAUSE') == '1':
    (pathlib.Path(os.environ['FAKE_ROOT'])/'k6-paused.pid').write_text(str(os.getpid()))
    while True: time.sleep(1)
if os.environ['PILOT_PHASE'] == 'benchmark' and int(os.environ['FAKE_K6_EXIT']):
    print('simulated k6 failure'); sys.exit(int(os.environ['FAKE_K6_EXIT']))
path.write_text(json.dumps({'phase':os.environ['PILOT_PHASE'], 'rate':int(os.environ['RATE']),
 'repetition':int(os.environ['REPETITION']), 'scenario_start_ms':start*1000,
 'duration_seconds':1, 'observation_seconds':1, 'started_requests':1,
 'requests':1, 'accepted_orders':1, 'dropped_iterations':0}))
''')

    def tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_runner(self, **overrides):
        return subprocess.run(['bash', str(self.root / 'run.sh')], env=dict(self.env, **overrides),
                              capture_output=True, text=True, timeout=20)

    def tmpfs_root(self):
        if not Path('/dev/shm').is_dir() or not shutil.which('findmnt'):
            self.skipTest('A writable /dev/shm tmpfs and findmnt are needed')
        mount = subprocess.run(['findmnt', '-n', '-T', '/dev/shm', '-o', 'FSTYPE'],
                               capture_output=True, text=True)
        if mount.stdout.strip() != 'tmpfs' or not os.access('/dev/shm', os.W_OK):
            self.skipTest('/dev/shm is not a writable tmpfs')
        directory = tempfile.TemporaryDirectory(prefix='tcc-csv-test-', dir='/dev/shm')
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def test_success_collects_all_stages_and_does_not_reset_containers_or_volumes(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['plots'], 'summary_only')
        self.assertTrue((self.output / 'analysis/levels.csv').exists())
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual((status['csvStorage'], status['csvExport']), ('disk', 'not_needed'))
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

    def test_repetitions_keep_order_and_separate_results_with_one_warmup_and_build(self):
        result = self.run_runner(REPETITIONS='3')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        phases = [json.loads(line)['phase'] for line in (self.output / 'phases.jsonl').read_text().splitlines()]
        expected = [f'rate{rate}_run{rep}' for rate in (1, 2) for rep in (1, 2, 3)]
        self.assertEqual([p for p in phases if p.startswith('rate')], expected)
        self.assertEqual(phases.count('warmup'), 1)
        self.assertEqual(phases.count('recovery'), 7)  # warmup and all six measurements
        for stage in expected:
            self.assertTrue((self.output / f'{stage}.csv').exists())
            summary = json.loads((self.output / f'{stage}.json').read_text())
            self.assertEqual(summary['repetition'], int(stage.rsplit('run', 1)[1]))
            self.assertEqual((self.output / f'{stage}.exit-code.txt').read_text().strip(), '0')
        metadata = json.loads((self.output / 'metadata.json').read_text())
        self.assertEqual(metadata['environment']['REPETITIONS'], '3')
        with (self.output / 'analysis/levels.csv').open() as stream:
            levels = list(csv.DictReader(stream))
        self.assertEqual(len(levels), 7)
        self.assertEqual({r['stage'] for r in levels}, {'warmup', *expected})
        calls = [json.loads(line) for line in (self.root / 'docker-calls.jsonl').read_text().splitlines()]
        self.assertEqual(sum('build' in call for call in calls), 1)
        self.assertFalse(any('down' in call or '--volumes' in call for call in calls))

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

    def test_tmpfs_exports_identical_csvs_only_after_all_repetitions_and_then_plots(self):
        memory_root = self.tmpfs_root()
        # The real summary analyzer still runs. Only the optional plotting process
        # is faked, checking that it receives all exported CSVs at the usual paths.
        self.tool('plot-python', '''#!/usr/bin/env python3
import pathlib, sys
out = pathlib.Path(sys.argv[2])
expected = {'warmup', 'rate1_run1', 'rate1_run2', 'rate2_run1', 'rate2_run2'}
assert {p.stem for p in out.glob('*.csv')} == expected
(out/'analysis/plots-invoked.txt').write_text('all CSVs already exported')
''')
        result = self.run_runner(K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(memory_root),
                                 REPETITIONS='2', AUTO_PLOT='1',
                                 PLOT_PYTHON=str(self.bin / 'plot-python'))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.output / 'metadata.json').read_text())
        self.assertEqual(metadata['environment']['K6_CSV_STORAGE'], 'tmpfs')
        self.assertEqual(Path(metadata['csvOutput']['pathDuringLoad']).parent, memory_root)
        self.assertGreater(metadata['csvOutput']['availableBytesBeforeRun'], 0)
        observations = [json.loads(line) for line in
                        (self.output / 'csv-observations.jsonl').read_text().splitlines()]
        self.assertEqual(len(observations), 5)
        for observed in observations:
            original = Path(observed['path'])
            self.assertEqual(original.parent.parent, memory_root)
            exported = self.output / original.name
            self.assertEqual(hashlib.sha256(exported.read_bytes()).hexdigest(), observed['sha256'])
            self.assertFalse(original.exists())
        self.assertEqual(list(memory_root.iterdir()), [])
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual((status['csvStorage'], status['csvExport'], status['plots']),
                         ('tmpfs', 'exported', 'generated'))
        self.assertTrue((self.output / 'analysis/plots-invoked.txt').exists())
        with (self.output / 'analysis/levels.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 5)

    def test_tmpfs_preserves_partial_csv_and_original_k6_failure(self):
        memory_root = self.tmpfs_root()
        result = self.run_runner(K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(memory_root),
                                 FAKE_K6_EXIT='99')
        self.assertEqual(result.returncode, 99, result.stdout + result.stderr)
        self.assertTrue((self.output / 'warmup.csv').exists())
        self.assertTrue((self.output / 'rate1_run1.csv').exists())
        self.assertFalse((self.output / 'rate2_run1.csv').exists())
        self.assertEqual(list(memory_root.iterdir()), [])
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual((status['exitCode'], status['csvExport']), (99, 'exported'))

    def test_tmpfs_can_generate_real_charts_when_matplotlib_is_available(self):
        if importlib.util.find_spec('matplotlib') is None:
            self.skipTest('Optional matplotlib is not installed in this Python environment')
        memory_root = self.tmpfs_root()
        result = self.run_runner(K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(memory_root),
                                 AUTO_PLOT='1', PLOT_PYTHON=sys.executable)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(status['plots'], 'generated', (self.output / 'analysis.log').read_text())
        for chart in ('availability.png', 'throughput.png', 'latency.png', 'capacity.png'):
            self.assertGreater((self.output / 'analysis' / chart).stat().st_size, 0)
        self.assertEqual(list(memory_root.iterdir()), [])

    def test_tmpfs_copy_failure_preserves_ram_originals_and_skips_analysis(self):
        memory_root = self.tmpfs_root()
        self.tool('cp', '''#!/usr/bin/env python3
import pathlib, shutil, sys
source, destination = map(pathlib.Path, sys.argv[2:])
if source.name == 'warmup.csv':
    sys.exit('simulated full destination after other CSVs were copied')
shutil.copyfile(source, destination)
''')
        result = self.run_runner(K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(memory_root))
        self.assertNotEqual(result.returncode, 0)
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual((status['phase'], status['csvExport']), ('csv_export', 'failed'))
        staging = Path(json.loads((self.output / 'metadata.json').read_text())['csvOutput']['pathDuringLoad'])
        self.assertEqual({p.name for p in staging.glob('*.csv')},
                         {'warmup.csv', 'rate1_run1.csv', 'rate2_run1.csv'})
        self.assertIn(str(staging), result.stderr)
        self.assertTrue((self.output / 'rate1_run1.csv').exists())
        self.assertFalse((self.output / 'analysis/levels.csv').exists())

    def test_tmpfs_sigterm_stops_load_and_exports_partial_csv(self):
        memory_root = self.tmpfs_root()
        env = dict(self.env, K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(memory_root),
                   FAKE_K6_PAUSE='1')
        process = subprocess.Popen(['bash', str(self.root / 'run.sh')], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 10
            flag = self.root / 'k6-paused.pid'
            while not flag.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(flag.exists(), 'Fixture never reached the paused benchmark')
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 143, stdout + stderr)
            self.assertTrue((self.output / 'rate1_run1.csv').exists())
            self.assertFalse((self.output / 'rate1_run1.json').exists())
            status = json.loads((self.output / 'status.json').read_text())
            self.assertEqual((status['exitCode'], status['csvExport']), (143, 'exported'))
            self.assertEqual(list(memory_root.iterdir()), [])
            with self.assertRaises(ProcessLookupError):
                os.kill(int(flag.read_text()), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)

    def test_tmpfs_rejects_a_disk_directory_without_fallback_before_docker(self):
        self.tool('findmnt', '#!/bin/sh\necho ext4\n')
        result = self.run_runner(K6_CSV_STORAGE='tmpfs', K6_CSV_TMPFS_ROOT=str(self.root))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must be on tmpfs', result.stderr)
        self.assertFalse((self.root / 'docker-calls.jsonl').exists())
        self.assertFalse(self.output.exists())

    def test_invalid_csv_storage_is_rejected_before_docker(self):
        result = self.run_runner(K6_CSV_STORAGE='ram')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('K6_CSV_STORAGE must be disk or tmpfs', result.stderr)
        self.assertFalse((self.root / 'docker-calls.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
