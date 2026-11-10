"""Historical fixture safety tests. No Docker, network or actual database."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('preload', Path(__file__).with_name('preload.py'))
preload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preload)

PRODUCTS = [{'id': '00000000-0000-0000-0000-000000000002', 'name': "Product B's", 'price': 20, 'stockQuantity': 100},
            {'id': '00000000-0000-0000-0000-000000000001', 'name': 'Product A', 'price': 10, 'stockQuantity': 100}]


class PreloadTest(unittest.TestCase):
    def test_deterministic_distribution_and_capacity_guard(self):
        rows = preload.catalog_rows(PRODUCTS, 5)
        self.assertEqual([r['consumed'] for r in rows], [3, 2])
        self.assertEqual(rows, preload.catalog_rows(list(reversed(PRODUCTS)), 5))
        with self.assertRaisesRegex(ValueError, 'Insufficient stock'):
            preload.catalog_rows(PRODUCTS, 201)
        for count in [0, -1, 10000001]:
            with self.assertRaises(ValueError):
                preload.catalog_rows(PRODUCTS, count)

    def test_plan_preserves_architecture_specific_history_and_has_no_resets(self):
        catalog = preload.catalog_rows(PRODUCTS, 5)
        for architecture in preload.ARCHITECTURES:
            for service in preload.SERVICES:
                sql = preload.plan_sql(architecture, service, 5, catalog)
                self.assertNotIn('TRUNCATE', sql)
                self.assertNotIn('DELETE', sql)
                self.assertIn('ANALYZE', sql)
                self.assertIn('ACCESS EXCLUSIVE', sql)
                self.assertIn('generate_series(1,5)', sql)
                if service == 'order':
                    self.assertIn("'confirmed'", sql)
                    self.assertIn('historical-preload-', sql)
                    self.assertNotIn('customer-k6-', sql)
                if service == 'warehouse':
                    self.assertEqual('INSERT INTO public.stock_reservations' in sql,
                                     architecture in ('tcc', 'two-phase-commit', 'choreographed-saga'))
                    if architecture == 'choreographed-saga':
                        self.assertIn("'reserved'", sql)
                    elif architecture in ('tcc', 'two-phase-commit'):
                        self.assertIn("'committed'", sql)

    def test_preflight_failure_performs_no_inserts_and_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'preload'
            with patch.object(preload.subprocess, 'run', return_value=subprocess.CompletedProcess([], 3, '', 'not empty')) as run:
                with self.assertRaises(RuntimeError):
                    preload.execute('compose', 'tcc', 5, PRODUCTS, out)
            self.assertEqual(run.call_count, 1)
            self.assertNotIn('INSERT', run.call_args.kwargs['input'])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'], 'failed')

    def test_all_preflights_precede_mutations_and_partial_failure_stops(self):
        calls = []
        def fake(command, **kwargs):
            calls.append(kwargs['input'])
            return subprocess.CompletedProcess(command, 0 if len(calls) <= 4 else 3, '', '')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'preload'
            with patch.object(preload.subprocess, 'run', side_effect=fake):
                with self.assertRaises(RuntimeError):
                    preload.execute('compose', 'tcc', 5, PRODUCTS, out)
            self.assertEqual(len(calls), 5)
            self.assertTrue(all('INSERT' not in sql for sql in calls[:4]))
            self.assertIn('INSERT', calls[4])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['services'], {})

    def test_completed_fixture_records_each_service_and_refuses_output_reuse(self):
        def fake(command, **kwargs):
            service = command[-1]
            output = '' if 'INSERT' not in kwargs['input'] else json.dumps({'service': service, 'historicalOrders': 5, 'validated': True})
            return subprocess.CompletedProcess(command, 0, output, '')
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)/'preload'
            with patch.object(preload.subprocess, 'run', side_effect=fake):
                record = preload.execute('compose', 'tcc', 5, PRODUCTS, out)
                with self.assertRaises(FileExistsError):
                    preload.execute('compose', 'tcc', 5, PRODUCTS, out)
            self.assertEqual(record['status'], 'completed')
            self.assertEqual(len(record['services']), 4)


if __name__ == '__main__':
    unittest.main()
