"""Isolated topology and read-only recovery checks; no external services."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
ROOT = HERE.parent.parent

def module(name):
    spec=importlib.util.spec_from_file_location(name,HERE/(name+'.py'))
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj

compose=module('compose')
state=module('state')

class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.out=Path(self.tmp.name)

    def test_unique_projects_no_fixed_container_names_or_exposed_database_ports(self):
        names=set()
        for architecture in compose.ARCHITECTURES:
            directory=self.out/architecture
            model=compose.generate(architecture,'suite-'+architecture,directory)
            self.assertNotIn(model['name'],names);names.add(model['name'])
            for service,config in model['services'].items():
                self.assertNotIn('container_name',config)
                if service.endswith('-db'):
                    self.assertNotIn('ports',config)
                    self.assertEqual(config['image'],'postgres:15-alpine')
            self.assertEqual(model['services']['distributed-lb']['ports'],['8000:80'])
            if architecture=='choreographed-saga':
                self.assertIn('rabbitmq-data',model['volumes'])
                self.assertIn('rabbitmq',model['services']['order-service']['depends_on'])
            else:self.assertNotIn('rabbitmq',model['services'])
            for service in ['order','warehouse','billing','shipping']:
                env=model['services'][service+'-service']['environment']
                self.assertEqual(env['POSTGRES_HOST'],service+'-db')
                self.assertEqual(env['POSTGRES_PORT'],'5432')

    def test_pool_override_is_explicit_and_default_does_not_override_native_configuration(self):
        with patch.dict(os.environ,{},clear=True):
            model=compose.generate('tcc','a',self.out/'a')
            self.assertNotIn('DB_POOL_MAX',model['services']['order-service']['environment'])
        with patch.dict(os.environ,{'DB_POOL_MAX':'80'}):
            model=compose.generate('tcc','b',self.out/'b')
            self.assertEqual(model['services']['order-service']['environment']['DB_POOL_MAX'],'80')

    def test_nginx_only_adjusts_health_routes_for_legacy_apps(self):
        compose.generate('tcc','a',self.out/'a')
        nginx=(self.out/'a/nginx.conf').read_text()
        original=(ROOT/'environment/nginx/distributed.conf').read_text()
        for service in ['order','warehouse','billing','shipping']:
            nginx=nginx.replace(f'proxy_pass http://{service}_cluster/;',f'proxy_pass http://{service}_cluster/health;')
        self.assertEqual(nginx,original)

    def test_state_query_uses_its_own_read_only_session_and_a_specific_cohort(self):
        with patch.object(state.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"counts":{"confirmed":2}}','')) as run:
            self.assertEqual(state.snapshot('compose.json','customer-k6-tcc-run-rate1_run1-')['counts']['confirmed'],2)
            args,kwargs=run.call_args
            self.assertIn('default_transaction_read_only=on',' '.join(args[0]))
            self.assertIn('customer-k6-tcc-run-rate1_run1-',kwargs['input'])
            self.assertNotIn('UPDATE',kwargs['input'])
            self.assertNotIn('DELETE',kwargs['input'])

    def test_asynchronous_backlog_is_preserved_and_reported_without_aborting(self):
        output=self.out/'state.json'
        with patch.object(state,'snapshot',return_value={'counts':{'pending':1}}), \
                patch.object(state,'queues',return_value=[{'name':'order-saga-queue','ready':1,'unacknowledged':0}]):
            state.collect('compose','prefix',output,wait_seconds=0,broker=True)
        record=json.loads(output.read_text())
        self.assertFalse(record['settled'])
        self.assertEqual(record['counts']['pending'],1)
        self.assertEqual(record['recoveryStatus'], 'incomplete')

    def test_old_parked_messages_do_not_consume_recovery_budget_or_count_as_new(self):
        previous = self.out/'previous.json'
        previous.write_text(json.dumps({'queues': [{'name': 'order-saga-queue.errors', 'ready': 197, 'unacknowledged': 0}]}))
        with patch.object(state, 'snapshot', return_value={'counts': {'confirmed': 10}}), \
                patch.object(state, 'queues', return_value=[{'name': 'order-saga-queue.errors', 'ready': 197, 'unacknowledged': 0}]), \
                patch.object(state.time, 'sleep') as sleep:
            record = state.collect('compose', 'prefix', self.out/'next.json', 60, True, previous)
        sleep.assert_not_called()
        self.assertFalse(record['settled'])
        self.assertEqual(record['recoveryStatus'], 'settled_with_previous_errors')
        self.assertEqual(record['queueRecovery']['parkedNetChange'], 0)

    def test_new_parked_messages_are_recorded_as_incomplete_and_net_change(self):
        previous = self.out/'previous.json'
        previous.write_text(json.dumps({'queues': [{'name': 'order-saga-queue.errors', 'ready': 197, 'unacknowledged': 0}]}))
        with patch.object(state, 'snapshot', return_value={'counts': {'pending': 5}}), \
                patch.object(state, 'queues', return_value=[{'name': 'order-saga-queue.errors', 'ready': 210, 'unacknowledged': 0}]):
            record = state.collect('compose', 'prefix', self.out/'next.json', 0, True, previous)
        self.assertEqual(record['recoveryStatus'], 'incomplete')
        self.assertEqual(record['queueRecovery']['parkedMessages'], 210)
        self.assertEqual(record['queueRecovery']['parkedNetChange'], 13)

    def test_failed_broker_observation_is_saved_and_remains_fatal(self):
        with patch.object(state, 'snapshot', return_value={'counts': {'confirmed': 2}}), \
                patch.object(state, 'queues', side_effect=RuntimeError('RabbitMQ snapshot omitted expected saga queues')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify asynchronous drain'):
                state.collect('compose', 'prefix', self.out/'state.json', broker=True)
        self.assertEqual(json.loads((self.out/'state.json').read_text())['collectionStatus'], 'error')

    def test_synchronous_partial_state_is_reported_without_changing_business_rules(self):
        with patch.object(state,'snapshot',return_value={'counts':{'pending':1}}):
            record=state.collect('compose','prefix',self.out/'state.json')
        self.assertFalse(record['settled'])

    def test_synchronous_snapshot_timeout_is_saved_and_next_observation_can_succeed(self):
        failure = subprocess.CalledProcessError(3, ['psql'], stderr='ERROR: canceling statement due to statement timeout\n')
        with patch.object(state, 'snapshot', side_effect=[failure, {'counts': {'confirmed': 2}}]) as read:
            record = state.collect('compose', 'first-stage', self.out/'first.json')
            self.assertEqual(record['collectionStatus'], 'error')
            self.assertIsNone(record['counts'])
            self.assertIsNone(record['settled'])
            self.assertIn('statement timeout', record['error']['stderr'])
            self.assertEqual(record['error']['returnCode'], 3)
            self.assertEqual(json.loads((self.out/'first.json').read_text()), record)
            following = state.collect('compose', 'next-stage', self.out/'next.json')
            self.assertEqual(following['collectionStatus'], 'ok')
            self.assertEqual(following['counts']['confirmed'], 2)
            self.assertEqual(read.call_count, 2)

    def test_process_timeout_is_saved_as_unknown_not_zero(self):
        failure = subprocess.TimeoutExpired(['docker'], 15, stderr=b'process timed out')
        with patch.object(state, 'snapshot', side_effect=failure):
            record = state.collect('compose', 'prefix', self.out/'state.json')
        self.assertIsNone(record['counts'])
        self.assertEqual(record['error']['stderr'], 'process timed out')

    def test_failed_asynchronous_observation_cannot_claim_drain_success(self):
        with patch.object(state, 'snapshot', side_effect=subprocess.CalledProcessError(3, ['psql'], stderr='SQL timeout')):
            with self.assertRaisesRegex(RuntimeError, 'Cannot verify asynchronous drain'):
                state.collect('compose', 'prefix', self.out/'state.json', broker=True)
        record = json.loads((self.out/'state.json').read_text())
        self.assertEqual(record['collectionStatus'], 'error')
        self.assertIsNone(record['settled'])

if __name__=='__main__':unittest.main()
