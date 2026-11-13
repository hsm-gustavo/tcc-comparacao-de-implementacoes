"""Independent protocol checks with fake runner/Docker. No external state or load."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('independent',ROOT/'scripts/pilot/independent.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class IndependentTest(unittest.TestCase):
    def test_real_analyzer_reads_linked_trial_files(self):
        from scripts.diagnostics.test_pilot_runner import PilotRunnerTest
        fixture=PilotRunnerTest();fixture.setUp()
        try:
            arch=fixture.output.parent/'independent-analysis/tcc'
            for rep in [1,2]:
                stage=f'rate1_run{rep}'
                result=fixture.run_runner(ARCHITECTURE='tcc',PILOT_ISOLATED='1',PILOT_SUITE_ID='independent-analysis',
                    PILOT_TRIAL_ID=stage,PILOT_REPETITION_START=str(rep),RATES='1')
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                (arch/'business').mkdir(exist_ok=True)
                m.link_trial(arch,arch/'trials'/stage,stage)
            metadata=json.loads((arch/'trials/rate1_run1/metadata.json').read_text())
            metadata['environment'].update(REPETITIONS='2')
            (arch/'metadata.json').write_text(json.dumps(metadata))
            m.analyze(arch.parent,[arch],False)
            import csv
            with (arch/'analysis/levels.csv').open() as stream:rows=list(csv.DictReader(stream))
            self.assertEqual({r['stage'] for r in rows},{'rate1_run1','rate1_run2'})
            self.assertTrue((arch.parent/'comparison/by_rate.csv').exists())
        finally:fixture.doCleanups()

    def test_refuses_stopped_existing_project(self):
        with patch.object(m,'command',return_value=subprocess.CompletedProcess([],0,stdout='old-container\n')) as call:
            with self.assertRaisesRegex(RuntimeError,'existing Docker project'):m.assert_unused('pilot-existing')
            self.assertIn('-a',call.call_args.args[0])

    def test_cleanup_only_own_project_and_rejects_external_volumes(self):
        with tempfile.TemporaryDirectory() as directory:
            trial=Path(directory);(trial/'runtime').mkdir();(trial/'logs').mkdir()
            (trial/'status.json').write_text('{"containerStop":"stopped"}')
            model={'name':m.owned_project('tcc','test'),'volumes':{'order-data':{}}}
            path=trial/'runtime/compose.json';path.write_text(json.dumps(model))
            with patch.object(m,'command',return_value=subprocess.CompletedProcess([],0,stdout='')) as call:
                m.cleanup(trial,'tcc','test')
                args=call.call_args_list[0].args[0]
                self.assertIn('--volumes',args);self.assertIn(str(path),args)
                self.assertFalse(any('prune' in c.args[0] for c in call.call_args_list))
            for bad in [{'name':'different','volumes':{}}, {'name':model['name'],'volumes':{'order-data':{'external':True}}}]:
                path.write_text(json.dumps(bad))
                with patch.object(m,'command') as call:
                    with self.assertRaises(RuntimeError):m.cleanup(trial,'tcc','test')
                    call.assert_not_called()

    def test_fresh_trial_matrix_build_once_and_keeps_canonical_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'experiments/datasets/pilot/fresh'
            env=dict(SUITE_ID='fresh',ARCHITECTURES='tcc,choreographed-saga',RATES='1,2',REPETITIONS='2',
                     PRELOAD_ORDERS='0',DB_MODE='postgres',BASELINE_RUN='',AUTO_PLOT='0')
            calls=[];cleaned=[]
            class Child:
                def __init__(self,args,**kwargs):
                    e=kwargs['env'];calls.append(e)
                    trial=out/e['ARCHITECTURE']/'trials'/e['PILOT_TRIAL_ID']
                    (trial/'runtime').mkdir(parents=True);(trial/'business').mkdir();(trial/'logs').mkdir()
                    (trial/'metadata.json').write_text(json.dumps({'environment':e,'architecture':e['ARCHITECTURE']}))
                    (trial/'status.json').write_text('{"containerStop":"stopped"}')
                    (trial/'runtime/compose.json').write_text(json.dumps({'name':m.owned_project(e['ARCHITECTURE'],e['RUN_ID']),'volumes':{'data':{}}}))
                    if e['PILOT_SKIP_BUILD']=='0':(trial/'build-completed.json').write_text('{}')
                    stage=e['PILOT_TRIAL_ID']
                    for suffix in ['.json','.csv','.exit-code.txt']:(trial/(stage+suffix)).write_text('fixture')
                    (trial/'business'/(stage+'.json')).write_text('{}')
                    self.pid=123
                def wait(self,**kwargs):return 0
            with patch.dict(os.environ,env),patch.object(m,'ROOT',root),patch.object(m,'command',return_value=subprocess.CompletedProcess([],0,stdout='')),patch.object(m,'assert_unused') as unused,patch.object(m,'cleanup',side_effect=lambda t,a,r:cleaned.append((t,a,r))),patch.object(m.subprocess,'Popen',Child),patch.object(m,'analyze'):
                self.assertEqual(m.run(out),0)
            self.assertEqual(len(calls),8);self.assertEqual(len(cleaned),8);self.assertEqual(unused.call_count,8)
            self.assertEqual(len({e['RUN_ID'] for e in calls}),8)
            for arch in ['tcc','choreographed-saga']:
                es=[e for e in calls if e['ARCHITECTURE']==arch]
                self.assertEqual([e['PILOT_SKIP_BUILD'] for e in es],['0','1','1','1'])
                self.assertEqual(len({e['PILOT_IMAGE_PREFIX'] for e in es}),1)
                self.assertTrue(all(e['REPETITIONS']=='1' and e['PRELOAD_ORDERS']=='0' for e in es))
                self.assertTrue((out/arch/'rate2_run2.csv').is_file())
                self.assertTrue((out/arch/'business/rate2_run2.json').is_file())
                meta=json.loads((out/arch/'metadata.json').read_text())
                self.assertEqual(meta['environment']['REPETITIONS'],'2')
                self.assertEqual(meta['experimentProtocol'],'independent-fresh-volumes')
            self.assertEqual(json.loads((out/'status.json').read_text())['completedTrials'],8)

    def test_rejects_preload_and_old_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'experiments/datasets/pilot/fresh'
            for overrides in [dict(PRELOAD_ORDERS='1'),dict(BASELINE_RUN='old')]:
                with patch.dict(os.environ,dict(SUITE_ID='fresh',PRELOAD_ORDERS='0',DB_MODE='postgres',BASELINE_RUN='',**{})|overrides),patch.object(m,'ROOT',root),patch.object(m,'command') as call:
                    with self.assertRaises(ValueError):m.run(out)
                    call.assert_not_called()

    def test_cleanup_failure_stops_before_next_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'experiments/datasets/pilot/fresh'
            class Child:
                pid=123
                def __init__(self,args,**kwargs):
                    e=kwargs['env'];trial=out/e['ARCHITECTURE']/'trials'/e['PILOT_TRIAL_ID']
                    trial.mkdir();(trial/'metadata.json').write_text(json.dumps({'environment':e,'architecture':e['ARCHITECTURE']}))
                def wait(self,**kwargs):return 0
            with patch.dict(os.environ,dict(SUITE_ID='fresh',ARCHITECTURES='tcc',RATES='1,2',REPETITIONS='2',PRELOAD_ORDERS='0',DB_MODE='postgres',BASELINE_RUN='')),patch.object(m,'ROOT',root),patch.object(m,'command',return_value=subprocess.CompletedProcess([],0,stdout='')),patch.object(m,'assert_unused'),patch.object(m,'cleanup',side_effect=RuntimeError('cleanup failed')) as cleanup,patch.object(m.subprocess,'Popen',side_effect=Child) as child,patch.object(m,'analyze'):
                self.assertEqual(m.run(out),1)
                self.assertEqual(child.call_count,1)
                self.assertEqual(cleanup.call_count,1)
                self.assertEqual(json.loads((out/'status.json').read_text())['completedTrials'],0)

if __name__=='__main__':unittest.main()
