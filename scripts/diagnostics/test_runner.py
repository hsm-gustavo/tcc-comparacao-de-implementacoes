"""Run the real runner in a temporary tree with fake Docker/k6/collectors."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RunnerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        scripts = self.root / 'scripts'
        (scripts / 'diagnostics').mkdir(parents=True)
        shutil.copyfile(Path(__file__).parents[1] / 'run-diagnostics.sh', scripts / 'run-diagnostics.sh')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ['PATH'],
                        RUN_ID='runner-test', RATE='40', SEED_DATA='0', COLLECT_HOST='0',
                        FAKE_BUILD_EXIT='0', FAKE_WARMUP_EXIT='0', FAKE_K6_EXIT='0', FAKE_KILL_COLLECTOR='0')
        self.output = self.root / 'experiments/datasets/diagnostics/runner-test'
        self.tool('git', '#!/bin/sh\necho fake-git\n')
        self.tool('curl', '#!/bin/sh\nexit 0\n')
        self.tool('docker', '''#!/usr/bin/env python3
import json, os, signal, sys
if 'up' in sys.argv:
    print('fake build output', flush=True)
    if int(os.environ['FAKE_BUILD_EXIT']):
        print('simulated Compose build failure', file=sys.stderr)
    sys.exit(int(os.environ['FAKE_BUILD_EXIT']))
if 'logs' in sys.argv:
    print('fake application log', flush=True)
    if '--follow' in sys.argv:
        signal.pause()
if 'ps' in sys.argv:
    print('fake-container-id')
if 'inspect' in sys.argv:
    print(json.dumps({'id': 'fake-container-id', 'name': '/warehouse-db',
                      'state': {'Health': {'Status': 'unhealthy'}}}))
''')
        self.tool('k6', '''#!/usr/bin/env python3
import os, pathlib, signal, sys
if sys.argv[-1].endswith('warmup.js'):
    print('fake warmup output')
    sys.exit(int(os.environ['FAKE_WARMUP_EXIT']))
csv = pathlib.Path(sys.argv[sys.argv.index('--out') + 1].removeprefix('csv='))
csv.write_text('fake-k6-csv\\n')
if os.environ['FAKE_KILL_COLLECTOR'] == '1':
    os.kill(int((csv.parent / 'fake-postgres.pid').read_text()), signal.SIGTERM)
print('fake benchmark output')
sys.exit(int(os.environ['FAKE_K6_EXIT']))
''')
        (scripts / 'diagnostics/collect-postgres.py').write_text('''import os, pathlib, signal, sys
output = pathlib.Path(sys.argv[sys.argv.index('--output-dir') + 1])
(output / 'fake-postgres.pid').write_text(str(os.getpid()))
(output / 'postgres-session.json').write_text('{}\\n')
(output / 'postgres-activity.jsonl').write_text('{}\\n')
signal.pause()
''')

    def tool(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_runner(self, **overrides):
        return subprocess.run(['bash', str(self.root / 'scripts/run-diagnostics.sh')],
                              env=dict(self.env, **overrides), capture_output=True, text=True, timeout=20)

    def status(self):
        return json.loads((self.output / 'status.json').read_text())

    def test_build_failure_is_visible_and_preserves_exit_and_startup_evidence(self):
        result = self.run_runner(FAKE_BUILD_EXIT='42')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertIn(str(self.output), result.stderr)
        self.assertIn('simulated Compose build failure', result.stderr)
        self.assertIn("Failed in phase 'startup'", result.stderr)
        self.assertEqual(self.status()['exitCode'], 42)
        self.assertEqual(self.status()['status'], 'failed')
        self.assertEqual(self.status()['phase'], 'startup')
        self.assertFalse((self.output / 'application.log').exists())
        self.assertFalse((self.output / 'k6.csv').exists())
        self.assertIn('fake application log', (self.output / 'startup-containers.log').read_text())
        state = json.loads((self.output / 'startup-state.jsonl').read_text())
        self.assertEqual(state['state']['Health']['Status'], 'unhealthy')
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.output / 'startup.log').stat().st_mode & 0o777, 0o600)

    def test_warmup_failure_reports_its_log_and_never_starts_benchmark(self):
        result = self.run_runner(FAKE_WARMUP_EXIT='37')
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn('fake warmup output', result.stderr)
        self.assertEqual(self.status()['phase'], 'warmup')
        self.assertFalse((self.output / 'k6.csv').exists())

    def test_success_keeps_artifacts_and_records_completion(self):
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.status()['status'], 'completed')
        self.assertEqual((self.output / 'k6-exit-code.txt').read_text(), '0\n')
        self.assertTrue((self.output / 'application.log').is_file())
        self.assertTrue((self.output / 'k6.csv').is_file())
        self.assertFalse((self.output / 'startup-state.jsonl').exists())
        phases = [json.loads(line)['phase'] for line in (self.output / 'phases.jsonl').read_text().splitlines()]
        self.assertEqual(phases, ['metadata', 'startup', 'readiness', 'collectors',
                                  'warmup', 'benchmark', 'collection_tail'])

    def test_k6_failure_is_reported_as_benchmark_failure(self):
        result = self.run_runner(FAKE_K6_EXIT='99')
        self.assertEqual(result.returncode, 99, result.stderr)
        self.assertEqual(self.status()['phase'], 'benchmark')
        self.assertEqual(self.status()['exitCode'], 99)
        self.assertIn('fake benchmark output', result.stderr)
        self.assertEqual((self.output / 'k6-exit-code.txt').read_text(), '99\n')

    def test_existing_run_is_never_overwritten(self):
        self.output.mkdir(parents=True)
        marker = self.output / 'evidence.txt'
        marker.write_text('preserve me')
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to overwrite', result.stderr)
        self.assertEqual(marker.read_text(), 'preserve me')
        self.assertEqual(list(self.output.iterdir()), [marker])

    def test_postgres_failure_does_not_lose_the_completed_k6_exit_code(self):
        result = self.run_runner(FAKE_KILL_COLLECTOR='1', FAKE_K6_EXIT='99')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('PostgreSQL collector stopped', result.stderr)
        self.assertEqual((self.output / 'k6-exit-code.txt').read_text(), '99\n')
        self.assertEqual(self.status()['phase'], 'collection_tail')


if __name__ == '__main__':
    unittest.main()
