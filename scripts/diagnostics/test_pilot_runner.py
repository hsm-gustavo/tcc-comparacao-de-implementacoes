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
        for name in ('run.sh', 'experiments/k6/pilot.js', 'experiments/analyze_pilot.py',
                     'scripts/seed.sh', 'scripts/diagnostics/dataset-permissions.sh',
                     'scripts/pilot/compose.py', 'scripts/pilot/state.py', 'scripts/pilot/preload.py',
                     'scripts/pilot/mock_state.py',
                     'environment/nginx/distributed.conf', 'environment/pilot/Dockerfile',
                     'environment/pilot/pnpm-workspace.yaml',
                     'environment/pilot/register-diagnostics.cjs'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, path)
        scripts = self.root / 'scripts/diagnostics'
        scripts.mkdir(exist_ok=True)
        (scripts / 'collect-postgres.py').write_text('''import pathlib, signal, sys
out = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])
(out/'postgres-activity.jsonl').write_text('{}\\n')
signal.pause()
''')
        (scripts / 'collect-host.py').write_text('')
        for architecture in ('distributed-baseline', 'tcc', 'two-phase-commit', 'orchestrated-saga', 'choreographed-saga'):
            for service in ('order', 'warehouse', 'billing', 'shipping'):
                path = self.root / f'implementation/{architecture}/{service}-service'
                path.mkdir(parents=True)
                for name in ('package.json', 'pnpm-lock.yaml', 'Dockerfile'):
                    (path / name).write_text('test fixture')
        (self.root / 'environment/compose').mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RUN_ID='pilot-test', RATES='1,2', DURATION='1s', WARMUP_DURATION='1s',
                        SETTLING_SECONDS='0', COOLDOWN_SECONDS='0', RECOVERY_TIMEOUT_SECONDS='1',
                        AUTO_PLOT='0', COLLECT_HOST='0', DB_DIAGNOSTICS='1', SEED_DATA='auto',
                        ARCHITECTURE='distributed-baseline', PILOT_ISOLATED='0', PILOT_SUITE_ID='',
                        PRE_ALLOCATED_VUS='2', MAX_VUS='2', REPETITIONS='1',
                        K6_CSV_STORAGE='disk', K6_CSV_TMPFS_ROOT='/dev/shm', PRELOAD_ORDERS='0',
                        FAKE_BUILD_EXIT='0', FAKE_K6_EXIT='0', FAKE_STOP_EXIT='0',
                        FAKE_ROOT=str(self.root))
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
if 'ps' in sys.argv and '--quiet' in sys.argv: print('fixture-db-id')
if 'exec' in sys.argv and 'node' in sys.argv:
    print(json.dumps({'counts': {'confirmed': 1}, 'tables': {'Order': 1}, 'database': 'mock'}))
if 'exec' in sys.argv and 'psql' in sys.argv:
    if os.environ.get('FAKE_STATE_EXIT') == '3':
        print('ERROR: canceling statement due to statement timeout', file=sys.stderr)
        sys.exit(3)
    print(json.dumps({'counts': {'pending': 1} if os.environ.get('FAKE_ASYNC_BACKLOG') else {'confirmed': 1}}))
if 'exec' in sys.argv and 'rabbitmqctl' in sys.argv:
    print('\\n'.join(f'{s}-saga-queue\\t0\\t0' for s in ['order','warehouse','billing','shipping']))
    if os.environ.get('FAKE_ASYNC_BACKLOG'): print('order-saga-queue.errors\\t197\\t0')
if 'stop' in sys.argv:
    out = pathlib.Path(os.environ['FAKE_ROOT'])/'experiments/datasets/pilot'/os.environ['RUN_ID']
    if os.environ.get('PILOT_SUITE_ID'):
        out = out.parent/os.environ['PILOT_SUITE_ID']/os.environ['ARCHITECTURE']
    (out/'shutdown-observation.json').write_text(json.dumps({
        'csvs': sorted(p.name for p in out.glob('*.csv')),
        'phases': [json.loads(line)['phase'] for line in (out/'phases.jsonl').read_text().splitlines()]}))
    print('fixture container stop', flush=True)
    sys.exit(int(os.environ['FAKE_STOP_EXIT']))
if 'logs' in sys.argv and '--follow' in sys.argv:
    while True:
        print(json.dumps({'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                          'type':'db_diagnostic_sample',
                          'pool':{'total':1,'idle':0 if os.environ.get('FAKE_POOL_BUSY') else 1,
                                  'inUse':1 if os.environ.get('FAKE_POOL_BUSY') else 0,'waiting':0},
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
        self.assertEqual(status['containerStop'], 'stopped')
        for stage in ('warmup', 'rate1_run1', 'rate2_run1'):
            self.assertTrue((self.output / f'{stage}.csv').exists())
        calls = [json.loads(line) for line in (self.root / 'docker-calls.jsonl').read_text().splitlines()]
        self.assertFalse(any('down' in call or '--volumes' in call or 'rm' in call for call in calls))
        stops = [call for call in calls if 'stop' in call]
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0][-3:], ['stop', '--timeout', '60'])
        observation = json.loads((self.output / 'shutdown-observation.json').read_text())
        self.assertEqual(observation['phases'][-1], 'completed')
        self.assertEqual(observation['csvs'], ['rate1_run1.csv', 'rate2_run1.csv', 'warmup.csv'])
        self.assertIn('fixture container stop', (self.output / 'logs/shutdown.log').read_text())

    def test_build_failure_preserves_status_and_evidence(self):
        result = self.run_runner(FAKE_BUILD_EXIT='42')
        self.assertEqual(result.returncode, 42, result.stderr)
        status = json.loads((self.output / 'status.json').read_text())
        self.assertEqual(status['phase'], 'startup')
        self.assertEqual(status['exitCode'], 42)
        self.assertEqual(status['containerStop'], 'stopped')
        self.assertTrue((self.output / 'logs/startup.log').exists())

    def test_stop_failure_is_reported_without_masking_an_existing_load_failure(self):
        for k6_exit, expected_exit, phase in (('0', 1, 'container_stop'), ('99', 99, 'rate1_run1')):
            with self.subTest(k6_exit=k6_exit):
                run_id = f'stop-failure-{k6_exit}'
                result = self.run_runner(RUN_ID=run_id, FAKE_K6_EXIT=k6_exit, FAKE_STOP_EXIT='23')
                self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
                out = self.output.parent / run_id
                status = json.loads((out / 'status.json').read_text())
                self.assertEqual((status['exitCode'], status['phase'], status['containerStop']),
                                 (expected_exit, phase, 'failed'))
                self.assertIn('Could not stop experiment containers', result.stderr)
                self.assertTrue((out / 'logs/shutdown.log').is_file())

    def test_sudo_permissions_repair_ancestors_and_partial_outputs_after_failure(self):
        helper = self.root / 'scripts/diagnostics/dataset-permissions.sh'
        with helper.open('a') as stream:
            stream.write(f'\ndataset_sudo_owner() {{ printf "%s" "{os.getuid()}:{os.getgid()}"; }}\n')
        self.output.parent.mkdir(parents=True, mode=0o700)
        datasets = self.root / 'experiments/datasets'
        datasets.chmod(0o000)
        self.addCleanup(datasets.chmod, 0o700)
        result = self.run_runner(FAKE_BUILD_EXIT='42')
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        for directory in (datasets, self.output.parent, self.output, self.output / 'logs'):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['phase'], 'startup')
        self.assertEqual((self.output / 'logs/startup.log').stat().st_mode & 0o777, 0o600)

    def test_permission_failure_is_reported_without_masking_previous_failure(self):
        helper = self.root / 'scripts/diagnostics/dataset-permissions.sh'
        with helper.open('a') as stream:
            stream.write('\ndataset_return_ownership() { return 1; }\n')
        for build_exit, expected_exit, phase in (('0', 1, 'permissions'), ('42', 42, 'startup')):
            with self.subTest(build_exit=build_exit):
                run_id = f'permissions-{build_exit}'
                result = self.run_runner(RUN_ID=run_id, FAKE_BUILD_EXIT=build_exit)
                self.assertEqual(result.returncode, expected_exit, result.stdout + result.stderr)
                status = json.loads((self.output.parent / run_id / 'status.json').read_text())
                self.assertEqual((status['exitCode'], status['phase']), (expected_exit, phase))
                self.assertIn('Could not return dataset ownership', result.stderr)

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
        self.assertEqual(json.loads((self.output / 'status.json').read_text())['containerStop'], 'stopped')

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
        self.assertEqual(json.loads((self.output / 'shutdown-observation.json').read_text())['csvs'],
                         ['rate1_run1.csv', 'rate1_run2.csv', 'rate2_run1.csv', 'rate2_run2.csv', 'warmup.csv'])
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
            self.assertEqual(status['containerStop'], 'stopped')
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

    def test_other_architectures_reuse_protocol_with_isolated_state_and_collection(self):
        for architecture in ('tcc', 'two-phase-commit', 'orchestrated-saga', 'choreographed-saga'):
            with self.subTest(architecture=architecture):
                result = self.run_runner(ARCHITECTURE=architecture, PILOT_ISOLATED='1',
                                         PILOT_SUITE_ID='suite-test', RUN_ID='suite-' + architecture)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                out = self.output.parent/'suite-test'/architecture
                metadata = json.loads((out/'metadata.json').read_text())
                self.assertEqual(metadata['architecture'], architecture)
                self.assertEqual(metadata['environment']['REPETITIONS'], '1')
                self.assertTrue((out/'postgres-activity.jsonl').exists())
                self.assertTrue((out/'analysis/levels.csv').exists())
                self.assertTrue((out/'business/rate2_run1.json').exists())
                model = json.loads((out/'runtime/compose.json').read_text())
                self.assertNotEqual(model['name'], 'distributed-lab-base')
                self.assertNotIn('container_name', model['services']['order-db'])
                self.assertEqual('rabbitmq' in model['services'], architecture == 'choreographed-saga')
                self.assertEqual(json.loads((out/'status.json').read_text())['containerStop'], 'stopped')

    def test_mock_runner_never_starts_or_collects_postgres(self):
        result = self.run_runner(DB_MODE='mock', ARCHITECTURE='choreographed-saga', PILOT_ISOLATED='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        metadata = json.loads((self.output/'metadata.json').read_text())
        self.assertEqual(metadata['database'], 'mock')
        self.assertEqual(metadata['environment']['DB_DIAGNOSTICS'], '0')
        self.assertFalse((self.output/'postgres-activity.jsonl').exists())
        calls = [json.loads(line) for line in (self.root/'docker-calls.jsonl').read_text().splitlines()]
        self.assertFalse(any('psql' in call or 'order-db' in call or 'inspect' in call for call in calls))
        self.assertTrue(any('down' in call for call in calls))
        self.assertFalse(any('--volumes' in call or '-v' in call for call in calls))
        self.assertTrue((self.output/'business/rate2_run1.json').exists())
        self.assertTrue((self.output/'analysis/levels.csv').exists())

    def test_failed_diagnostic_snapshot_does_not_abort_synchronous_load_stages(self):
        result = self.run_runner(ARCHITECTURE='orchestrated-saga', PILOT_ISOLATED='1',
                                 FAKE_STATE_EXIT='3')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        status = json.loads((self.output/'status.json').read_text())
        self.assertEqual(status['phase'], 'completed')
        self.assertEqual(status['containerStop'], 'stopped')
        self.assertIn('statement timeout', result.stderr)
        for stage in ['warmup', 'rate1_run1', 'rate2_run1']:
            record = json.loads((self.output/'business'/f'{stage}.json').read_text())
            self.assertEqual(record['collectionStatus'], 'error')
            self.assertIsNone(record['counts'])
            self.assertTrue((self.output/f'{stage}.csv').exists())

    def test_asynchronous_business_failure_continues_all_stages_and_preserves_errors(self):
        result = self.run_runner(ARCHITECTURE='choreographed-saga', PILOT_ISOLATED='1',
                                 FAKE_ASYNC_BACKLOG='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('WARNING: recovery incomplete', result.stdout)
        self.assertEqual(json.loads((self.output/'status.json').read_text())['phase'], 'completed')
        for index, stage in enumerate(['warmup', 'rate1_run1', 'rate2_run1']):
            record = json.loads((self.output/'business'/f'{stage}.json').read_text())
            self.assertEqual(record['counts']['pending'], 1)
            self.assertFalse(record['settled'])
            self.assertEqual(record['queueRecovery']['parkedMessages'], 197)
            self.assertEqual(record['queueRecovery']['parkedNetChange'], 197 if index == 0 else 0)
            self.assertTrue((self.output/f'{stage}.csv').exists())
        analysis = json.loads((self.output/'analysis/summary.json').read_text())
        self.assertTrue(any('business recovery incomplete' in w for w in analysis['warnings']))
        self.assertTrue((self.output/'analysis/business.csv').exists())

    def test_asynchronous_snapshot_failure_still_stops_load_and_stops_containers(self):
        result = self.run_runner(ARCHITECTURE='choreographed-saga', PILOT_ISOLATED='1', FAKE_STATE_EXIT='3')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.output/'rate1_run1.csv').exists())
        self.assertEqual(json.loads((self.output/'status.json').read_text())['containerStop'], 'stopped')

    def test_pool_that_does_not_recover_still_stops_asynchronous_load(self):
        result = self.run_runner(ARCHITECTURE='choreographed-saga', PILOT_ISOLATED='1', FAKE_POOL_BUSY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Pool did not recover', result.stderr)
        self.assertFalse((self.output/'rate1_run1.csv').exists())
        self.assertEqual(json.loads((self.output/'status.json').read_text())['containerStop'], 'stopped')

    def test_preload_requires_isolated_stack_before_startup(self):
        result = self.run_runner(PRELOAD_ORDERS='10')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires PILOT_ISOLATED=1', result.stderr)
        self.assertFalse(self.output.exists())

    def test_preload_precedes_collectors_and_only_one_warmup_without_resets(self):
        script = self.root/'scripts/pilot/preload.py'
        script.write_text("import pathlib,sys,json\n"
                          "out=pathlib.Path(sys.argv[sys.argv.index('--output')+1]);out.mkdir()\n"
                          "(out/'manifest.json').write_text(json.dumps({'status':'completed'}))\n")
        result = self.run_runner(PRELOAD_ORDERS='10', PILOT_ISOLATED='1')
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        phases = [json.loads(line)['phase'] for line in (self.output/'phases.jsonl').read_text().splitlines()]
        self.assertLess(phases.index('preload'), phases.index('collectors'))
        self.assertLess(phases.index('collectors'), phases.index('warmup'))
        self.assertEqual(phases.count('preload'), 1)
        self.assertEqual(phases.count('warmup'), 1)
        self.assertEqual(json.loads((self.output/'metadata.json').read_text())['environment']['PRELOAD_ORDERS'], '10')

    def test_preload_failure_stops_before_any_load_and_preserves_evidence(self):
        (self.root/'scripts/pilot/preload.py').write_text('raise SystemExit(43)\n')
        result = self.run_runner(PRELOAD_ORDERS='10', PILOT_ISOLATED='1')
        self.assertEqual(result.returncode, 43, result.stdout+result.stderr)
        status = json.loads((self.output/'status.json').read_text())
        self.assertEqual(status['phase'], 'preload')
        self.assertEqual(status['containerStop'], 'stopped')
        self.assertFalse((self.output/'warmup.json').exists())


if __name__ == '__main__':
    unittest.main()
