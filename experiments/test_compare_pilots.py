"""Comparison semantics with tiny cached tables, no raw load or external state."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from compare_pilots import compare


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def dataset(self,name,pool=80,timeout=2000,business=True):
        p=self.root/name;(p/'analysis').mkdir(parents=True)
        (p/'metadata.json').write_text(json.dumps({'architecture':name,'environment':{'RATES':'1','DURATION':'3m'}}))
        (p/'status.json').write_text('{"exitCode":0}')
        (p/'application.log').write_text(json.dumps({'timestamp':'2026-10-28','type':'diagnostics_started',
            'pool':{'max':pool,'connectionTimeoutMillis':timeout},'nodeVersion':'v22'})+'\n')
        rows=[{'stage':'rate1_run'+str(i),'phase':'benchmark','rate':1,'repetition':i,'complete':True,
               'start_epoch':i*200,'started':2,'responses':2,'accepted':1,'accepted_rps':.5,
               'availability_pct':50,'successful_p95_ms':ms} for i,ms in [(1,10),(2,30)]]
        with (p/'analysis/levels.csv').open('w') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        if business:
            (p/'business').mkdir()
            for row in rows:(p/'business'/f'{row["stage"]}.json').write_text('{"counts":{"confirmed":2},"settled":true}')
        return p

    def test_preserves_http_acceptance_and_business_completion_as_separate_metrics(self):
        baseline=self.dataset('distributed-baseline',business=False)
        saga=self.dataset('choreographed-saga')
        original=(baseline/'analysis/levels.csv').read_bytes()
        out=self.root/'comparison';rows=compare([baseline,saga],out,plots=False)
        self.assertEqual(next(r['median'] for r in rows if r['architecture']=='choreographed-saga' and r['metric']=='availability_pct'),50)
        self.assertEqual(next(r['median'] for r in rows if r['architecture']=='choreographed-saga' and r['metric']=='confirmed_pct'),100)
        self.assertFalse(any(r['architecture']=='distributed-baseline' and r['metric']=='confirmed_pct' for r in rows))
        self.assertEqual(next(r['median'] for r in rows if r['metric']=='successful_p95_ms'),20)
        self.assertEqual((baseline/'analysis/levels.csv').read_bytes(),original)
        report=json.loads((out/'summary.json').read_text())
        self.assertTrue(any('acceptance' in w for w in report['warnings']))

    def test_warns_about_different_effective_timeouts_and_rejects_output_in_input(self):
        one=self.dataset('tcc',timeout=0);two=self.dataset('orchestrated-saga')
        out=self.root/'comparison';compare([one,two],out,plots=False)
        self.assertTrue(any('pool settings' in w for w in json.loads((out/'summary.json').read_text())['warnings']))
        with self.assertRaisesRegex(ValueError,'outside input'):
            compare([one],one/'analysis',plots=False)
        with self.assertRaisesRegex(ValueError,'Duplicate architecture'):
            compare([one,one],out,plots=False)

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'),'optional matplotlib')
    def test_generates_all_comparison_charts_from_cached_tables(self):
        out=self.root/'comparison';compare([self.dataset('tcc'),self.dataset('choreographed-saga')],out)
        for name in ['availability.png','throughput.png','latency.png','business-confirmed.png']:
            self.assertGreater((out/name).stat().st_size,0)

    def test_failed_business_snapshot_keeps_http_metrics_and_uses_only_valid_business_samples(self):
        root = self.dataset('orchestrated-saga')
        (root/'business/rate1_run1.json').write_text(json.dumps({
            'collectionStatus': 'error', 'counts': None, 'settled': None,
            'error': {'stderr': 'statement timeout'},
        }))
        out = self.root/'comparison'
        rows = compare([root], out, plots=False)
        business = next(r for r in rows if r['metric'] == 'confirmed_pct')
        self.assertEqual(business['valid_values'], 1)
        self.assertEqual(business['median'], 100)
        self.assertEqual(next(r for r in rows if r['metric'] == 'availability_pct')['valid_values'], 2)
        self.assertTrue(any('snapshot unavailable' in w for w in json.loads((out/'summary.json').read_text())['warnings']))
        (root/'business/rate1_run2.json').write_text('{"collectionStatus":"error","counts":null}')
        rows = compare([root], out, plots=False)
        self.assertFalse(any(r['metric'] == 'confirmed_pct' for r in rows))
        self.assertTrue(any(r['metric'] == 'availability_pct' for r in rows))


if __name__=='__main__':unittest.main()
