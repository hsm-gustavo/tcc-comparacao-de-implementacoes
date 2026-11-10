import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import itertools
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('fault_runner',Path(__file__).with_name('run.py'))
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)


class FaultTest(unittest.TestCase):
    def state(self,architecture='distributed-baseline'):
        return dict(architecture=architecture,orders=[dict(id='o',status='confirmed',totalAmount='10.00')],
            products=[dict(id='p',stockQuantity=99)],items=[dict(productId='p',quantity=1)],
            payments=[dict(status='completed',amount='10.00')],shipments=[dict(status='shipped')],
            reservations=[],queues=[])

    def test_success_and_cross_domain_invariants(self):
        self.assertEqual(runner.classify(self.state()),'completed_valid')
        for field,value in [('payments',[]),('shipments',[]),('products',[dict(id='p',stockQuantity=100)]),
                            ('items',[dict(productId='other',quantity=1)])]:
            s=self.state();s[field]=value
            self.assertEqual(runner.classify(s),'terminal_inconsistent')

    def test_reservation_required_and_duplicates_not_hidden(self):
        s=self.state('choreographed-saga')
        self.assertEqual(runner.classify(s),'terminal_inconsistent')
        s['reservations']=[dict(status='reserved',quantity=1,productId='p')]
        self.assertEqual(runner.classify(s),'completed_valid')
        s['payments']*=2
        self.assertEqual(runner.classify(s),'terminal_inconsistent')

    def test_valid_abort_and_partial_effect(self):
        s=self.state();s['orders'][0]['status']='cancelled';s['payments']=[];s['shipments']=[]
        self.assertEqual(runner.classify(s),'terminal_inconsistent')  # Stock still consumed.
        s['products'][0]['stockQuantity']=100
        self.assertEqual(runner.classify(s),'aborted_valid')
        s['orders'][0]['status']='pending'
        self.assertEqual(runner.classify(s),'pending')

    def test_absent_order_is_unknown_not_success(self):
        s=self.state();s['orders']=[]
        self.assertEqual(runner.classify(s),'inconclusive')

    def test_prepare_only_generates_independent_projects_and_no_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'test-suite'
            with patch('sys.argv',['run.py','--output',str(out),'--repetitions','2','--prepare-only']), \
                 patch.object(runner.subprocess,'run') as run, patch.object(runner.subprocess,'check_output') as output:
                exit_code=runner.main()
                self.assertEqual(exit_code,0,(out/'runner-error.json').read_text() if (out/'runner-error.json').exists() else '')
            run.assert_not_called();output.assert_not_called()
            plans=list(out.glob('*/*/compose.json'))
            self.assertEqual(len(plans),20)
            projects=set();images={}
            for path in plans:
                plan=json.loads(path.read_text());projects.add(plan['name'])
                billing=plan['services']['billing-service']
                self.assertIn('./fault-test.cjs',billing['command'])
                self.assertEqual(billing['environment']['PAYMENT_FAULT_TEST'],'1')
                self.assertIn(billing['environment']['PAYMENT_FAULT_MODE'],('control','transient'))
                self.assertNotIn('container_name',billing)
                for s,c in plan['services'].items():
                    if s.endswith('-service'):
                        key=(path.parent.parent.name,s)
                        self.assertEqual(images.setdefault(key,c['image']),c['image'])
            self.assertEqual(len(projects),20)
            self.assertNotIn('monolith',[p.parent.parent.name for p in plans])

    def test_audit_query_failure_is_not_empty_state(self):
        failed=subprocess.CompletedProcess([],3,'','statement timeout')
        with patch.object(runner.subprocess,'run',return_value=failed):
            with self.assertRaisesRegex(RuntimeError,'audit failed'):
                runner.sql(['docker'],'billing','SELECT 1')

    def test_complete_runner_continues_business_failure_and_cleans_only_trial_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp)/'mock-suite'
            def model(command):
                return json.loads(Path(command[command.index('-f')+1]).read_text())
            def event_rows(command):
                m=model(command);mode=m['services']['billing-service']['environment']['PAYMENT_FAULT_MODE']
                e=[dict(event='boundary_reached',orderId='o',timestamp=runner.utc())]
                if mode=='transient':e.extend(dict(event=x,orderId='o',timestamp=runner.utc()) for x in ('payment_rejected','fault_restored'))
                return e
            def request(url,payload=None):
                if url.endswith('/health'):return dict(status=200,body={})
                if url.endswith('/warehouse/products'):
                    return dict(status=201,body={'id':'00000000-0000-0000-0000-000000000001'})
                return dict(status=201,body={},durationMs=1,completedAt=runner.utc())
            def audit(command,customer,product,architecture):
                s=self.state();s['sampledAt']=runner.utc()
                if not customer.endswith('-warmup') and model(command)['services']['billing-service']['environment']['PAYMENT_FAULT_MODE']=='transient':
                    s['orders'][0]['status']='cancelled';s['payments']=[];s['shipments']=[]
                if customer.endswith('-warmup') is False and not getattr(audit,'seen',set()).__contains__(customer):
                    audit.seen=getattr(audit,'seen',set());audit.seen.add(customer)
                    s['orders']=[];s['products'][0]['stockQuantity']=100
                return s
            with patch('sys.argv',['run.py','--output',str(out),'--architectures','distributed-baseline','--repetitions','2']), \
                 patch.object(runner,'http',side_effect=request),patch.object(runner,'audit',side_effect=audit), \
                 patch.object(runner,'start_stack'), \
                 patch.object(runner,'events',side_effect=event_rows),patch.object(runner.time,'sleep'), \
                 patch.object(runner.time,'monotonic',side_effect=itertools.count(0,20)), \
                 patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],0)) as calls:
                exit_code=runner.main()
                self.assertEqual(exit_code,0,(out/'runner-error.json').read_text() if (out/'runner-error.json').exists() else '')
            status=json.loads((out/'status.json').read_text());self.assertEqual(status['completedTrials'],4)
            results=[json.loads(p.read_text()) for p in out.glob('*/*/result.json')]
            self.assertEqual(sum(r['classification']=='terminal_inconsistent' for r in results),2)
            commands=[c.args[0] for c in calls.call_args_list]
            self.assertEqual(sum('build' in c for c in commands),1)
            self.assertEqual(sum('down' in c and '--volumes' in c for c in commands),4)
            self.assertTrue(all(str(out) in c[c.index('-f')+1] for c in commands))
            # Teardown failure must not erase or reclassify the already audited trial.
            out=Path(tmp)/'timeout-suite'
            def fail_cleanup(command,**kwargs):
                if 'down' in command:
                    self.assertEqual(kwargs['timeout'],123)
                    raise subprocess.TimeoutExpired(command,123)
                return subprocess.CompletedProcess(command,0)
            with patch('sys.argv',['run.py','--output',str(out),'--architectures','distributed-baseline','--repetitions','1','--cleanup-timeout','123']), \
                 patch.object(runner,'http',side_effect=request),patch.object(runner,'audit',side_effect=audit), \
                 patch.object(runner,'start_stack'), \
                 patch.object(runner,'events',side_effect=event_rows),patch.object(runner.time,'sleep'), \
                 patch.object(runner.time,'monotonic',side_effect=itertools.count(0,20)), \
                 patch.object(runner.subprocess,'run',side_effect=fail_cleanup):
                self.assertEqual(runner.main(),1)
            saved=json.loads((out/'distributed-baseline/control-rep01/result.json').read_text())
            self.assertEqual(saved['classification'],'completed_valid')
            self.assertEqual(saved['cleanup_status'],'failed')
            self.assertTrue((out/'results.csv').exists())

    def test_database_startup_waits_through_temporary_unhealthy_before_apps(self):
        calls=[];health=iter(['running unhealthy','running healthy',*['running healthy']*3])
        def run(command,**kwargs):
            calls.append(command)
            stdout=''
            if 'inspect' in command:stdout=next(health)
            elif 'ps' in command:stdout='fixture-id\n'
            return subprocess.CompletedProcess(command,0,stdout)
        with patch.object(runner.subprocess,'run',side_effect=run),patch.object(runner.time,'sleep') as sleep:
            runner.start_stack(['docker','compose','-f','fixture.json'],None)
        self.assertEqual(calls[0][-4:],['order-db','warehouse-db','billing-db','shipping-db'])
        self.assertEqual(calls[-1][-3:],['up','-d','--no-build'])
        sleep.assert_called_once_with(2)
        self.assertTrue(all('127.0.0.1' in c for c in calls if 'pg_isready' in c))


if __name__=='__main__':unittest.main()
