"""Suite orchestration with a fake child runner: no Docker and no load."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


class SuiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ['run-suite.sh', 'scripts/diagnostics/dataset-permissions.sh',
                     'experiments/compare_pilots.py', 'experiments/analyze_pilot.py']:
            target = self.root/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT/name, target)
        (self.root/'run.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
python3 - <<'FIXTURE'
import csv, json, os, shutil
from pathlib import Path
r = Path(os.environ['FIXTURE_ROOT'])
assert not (r/'active').exists(), 'two architectures overlapped'
(r/'active').write_text(os.environ['ARCHITECTURE'])
print('[fixture] Progress '+os.environ['ARCHITECTURE'], flush=True)
with (r/'calls').open('a') as s: s.write(os.environ['ARCHITECTURE']+'\\n')
out = r/'experiments/datasets/pilot'/os.environ['PILOT_SUITE_ID']/os.environ['ARCHITECTURE']
(out/'analysis').mkdir(parents=True)
(out/'metadata.json').write_text(json.dumps({'architecture':os.environ['ARCHITECTURE'], 'environment':{}}))
row = {'stage':'rate1_run1','phase':'benchmark','rate':1,'repetition':1,'complete':True,
       'start_epoch':0,'started':2,'responses':2,'accepted':2,'availability_pct':100,
       'accepted_rps':1,'successful_p95_ms':10}
with (out/'analysis/levels.csv').open('w') as s:
 w=csv.DictWriter(s,fieldnames=list(row));w.writeheader();w.writerow(row)
code = int(os.environ.get('FAIL_CODE', '42')) if os.environ['ARCHITECTURE'] in os.environ.get('FAIL_ARCH','').split(',') else 0
if code and os.environ.get('OMIT_FAILED_ANALYSIS') == '1': shutil.rmtree(out/'analysis')
(out/'status.json').write_text(json.dumps({'exitCode':code,'containerStop':os.environ.get('STOP_RESULT','stopped')}))
if os.environ.get('STOP_RESULT') == 'missing': (out/'status.json').unlink()
(r/'active').unlink()
FIXTURE
if [[ ",${FAIL_ARCH:-}," == *",$ARCHITECTURE,"* ]]; then exit "${FAIL_CODE:-42}"; fi
''')
        (self.root/'bin').mkdir()
        docker=self.root/'bin/docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "${FAKE_RUNNING_CONTAINER:-}"\n')
        docker.chmod(0o755)
        self.env = dict(os.environ, SUITE_ID='suite-test', FIXTURE_ROOT=str(self.root), AUTO_PLOT='0',
                        PATH=str(self.root/'bin')+os.pathsep+os.environ['PATH'])
        for key in ['ARCHITECTURES','BASELINE_RUN','FAIL_ARCH','FAIL_CODE','OMIT_FAILED_ANALYSIS',
                    'STOP_RESULT','SUDO_UID','SUDO_GID']:
            self.env.pop(key, None)

    def run_suite(self, **env):
        return subprocess.run(['bash',str(self.root/'run-suite.sh')],env=dict(self.env,**env),
                              capture_output=True,text=True,timeout=15)

    def test_default_runs_four_in_order_with_separate_outputs_and_comparison(self):
        result=self.run_suite()
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),
                         ['tcc','two-phase-commit','orchestrated-saga','choreographed-saga'])
        out=self.root/'experiments/datasets/pilot/suite-test'
        self.assertTrue((out/'comparison/by_rate.csv').is_file())
        self.assertEqual(len(json.loads((out/'comparison/summary.json').read_text())['runs']),4)
        self.assertEqual(json.loads((out/'status.json').read_text())['exitCode'],0)

    def test_can_run_baseline_explicitly_and_rejects_duplicates_and_unknown_names(self):
        result=self.run_suite(ARCHITECTURES='distributed-baseline,tcc')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['distributed-baseline','tcc'])
        for architectures in ['tcc,tcc','not-real','tcc,']:
            result=self.run_suite(SUITE_ID='invalid',ARCHITECTURES=architectures)
            self.assertNotEqual(result.returncode,0)

    def test_failure_continues_sequence_and_preserves_code_and_partial_comparison(self):
        result=self.run_suite(FAIL_ARCH='two-phase-commit')
        self.assertEqual(result.returncode,42,result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),
                         ['tcc','two-phase-commit','orchestrated-saga','choreographed-saga'])
        out=self.root/'experiments/datasets/pilot/suite-test'
        status=json.loads((out/'status.json').read_text())
        self.assertEqual(status['exitCode'],42)
        self.assertEqual(status['failedArchitectures'],['two-phase-commit'])
        self.assertEqual(len(json.loads((out/'comparison/summary.json').read_text())['runs']),4)

    def test_setup_exception_107_in_first_architecture_does_not_skip_other_architectures(self):
        result=self.run_suite(FAIL_ARCH='tcc',FAIL_CODE='107',OMIT_FAILED_ANALYSIS='1')
        self.assertEqual(result.returncode,107,result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),
                         ['tcc','two-phase-commit','orchestrated-saga','choreographed-saga'])
        out=self.root/'experiments/datasets/pilot/suite-test'
        status=json.loads((out/'status.json').read_text())
        self.assertEqual(status['failedArchitectures'],['tcc'])
        self.assertEqual(len(json.loads((out/'comparison/summary.json').read_text())['runs']),3)

    def test_live_logs_exist_before_following_and_progress_is_displayed_for_all_architectures(self):
        real_tail = shutil.which('tail')
        wrapper = self.root/'bin/tail'
        wrapper.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
if any(arg.startswith('--pid=') for arg in sys.argv):
    assert pathlib.Path(sys.argv[-1]).is_file(), 'live log did not exist before tail started'
    assert '-F' in sys.argv, 'tail must retry following the log by name'
os.execv(REAL_TAIL, [REAL_TAIL, *sys.argv[1:]])
'''.replace('REAL_TAIL', repr(real_tail)))
        wrapper.chmod(0o755)
        result = self.run_suite()
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        for architecture in ['tcc','two-phase-commit','orchestrated-saga','choreographed-saga']:
            self.assertIn('[fixture] Progress '+architecture, result.stdout)

    def test_multiple_failures_are_all_recorded(self):
        result=self.run_suite(FAIL_ARCH='tcc,choreographed-saga')
        self.assertEqual(result.returncode,42,result.stdout+result.stderr)
        out=self.root/'experiments/datasets/pilot/suite-test'
        status=json.loads((out/'status.json').read_text())
        self.assertEqual(status['failedArchitectures'],['tcc','choreographed-saga'])
        self.assertEqual(len((self.root/'calls').read_text().splitlines()),4)

    def test_does_not_advance_when_containers_could_not_stop(self):
        result=self.run_suite(STOP_RESULT='failed')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['tcc'])

    def test_does_not_advance_without_shutdown_status(self):
        result=self.run_suite(STOP_RESULT='missing')
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['tcc'])

    def test_child_interrupt_stops_sequence_even_after_containers_stop(self):
        for code in ['130','143']:
            with self.subTest(code=code):
                result=self.run_suite(SUITE_ID='interrupt-'+code,FAIL_ARCH='tcc',FAIL_CODE=code)
                self.assertEqual(result.returncode,int(code),result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['tcc','tcc'])

    def test_reuses_baseline_artifacts_without_running_baseline(self):
        result=self.run_suite(SUITE_ID='old',ARCHITECTURES='distributed-baseline')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        baseline=self.root/'experiments/datasets/pilot/old/distributed-baseline'
        original=(baseline/'analysis/levels.csv').read_bytes()
        result=self.run_suite(BASELINE_RUN=str(baseline),ARCHITECTURES='tcc')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual((self.root/'calls').read_text().splitlines(),['distributed-baseline','tcc'])
        self.assertEqual((baseline/'analysis/levels.csv').read_bytes(),original)
        runs=json.loads((self.root/'experiments/datasets/pilot/suite-test/comparison/summary.json').read_text())['runs']
        self.assertEqual([r['architecture'] for r in runs],['distributed-baseline','tcc'])

    def test_refuses_to_measure_with_an_existing_experimental_stack_running(self):
        result=self.run_suite(FAKE_RUNNING_CONTAINER='order-db distributed-lab-base')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Stop the existing experimental stacks',result.stderr)
        self.assertFalse((self.root/'calls').exists())

    def test_interrupt_terminates_the_child_before_exiting(self):
        (self.root/'run.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
out="$FIXTURE_ROOT/experiments/datasets/pilot/$PILOT_SUITE_ID/$ARCHITECTURE"
mkdir "$out"
cleanup_child() {
  printf '%s\\n' '{"containerStop":"stopped","exitCode":143}' >"$out/status.json"
  exit 143
}
trap cleanup_child TERM
echo "$$" >"$FIXTURE_ROOT/child.pid"
while true; do sleep 0.05; done
''')
        process=subprocess.Popen(['bash',str(self.root/'run-suite.sh')],env=self.env,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            deadline=time.monotonic()+5
            while not (self.root/'child.pid').exists() and process.poll() is None and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue((self.root/'child.pid').exists())
            pid=int((self.root/'child.pid').read_text())
            process.send_signal(signal.SIGTERM)
            stdout,stderr=process.communicate(timeout=10)
            self.assertEqual(process.returncode,143,stdout+stderr)
            with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            status=json.loads((self.root/'experiments/datasets/pilot/suite-test/tcc/status.json').read_text())
            self.assertEqual(status['containerStop'],'stopped')
        finally:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
