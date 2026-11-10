"""Small fixtures exercise measurement semantics, not the application."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analyze_pilot import analyze_stage, business_outcomes, enrich_http_metrics, measured_stages, percentile, plot, read_table, summarize_repetitions, write_csv


class AnalyzePilotTest(unittest.TestCase):
    def test_business_counts_separate_http_acceptance_and_global_queue_net_change(self):
        root = Path(self.directory.name)
        (root/'business').mkdir()
        rows = []
        for index, parked in enumerate([197, 197, 210]):
            stage = f'rate60_run{index + 1}'
            rows.append(dict(stage=stage, rate=60, repetition=index + 1, phase='benchmark',
                             start_epoch=index * 200, started=100, accepted=90))
            (root/'business'/f'{stage}.json').write_text(json.dumps({
                'counts': {'confirmed': 80, 'pending': 10}, 'settled': False,
                'queues': [{'name': 'order-saga-queue.errors', 'ready': parked, 'unacknowledged': 0}]}))
        results = business_outcomes(root, list(reversed(rows)))
        self.assertEqual([r['parked_net_change'] for r in results], [197, 0, 13])
        self.assertEqual(results[0]['http_accepted'], 90)
        self.assertEqual(results[0]['confirmed'], 80)
        self.assertEqual(results[0]['pending'], 10)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'rate2_run1.json'
        self.summary = {'rate': 2, 'repetition': 1, 'phase': 'benchmark',
                        'duration_seconds': 30, 'observation_seconds': 32,
                        'scenario_start_ms': 1000000, 'started_requests': 3,
                        'requests': 3, 'accepted_orders': 2, 'dropped_iterations': 2}
        self.records = [
            ('http_req_duration', 1000, 9000, '', '200', ''),  # setup is excluded
            ('started_requests', 1000, 1, 'pilot_orders', '', ''),
            ('http_req_duration', 1001, 100, 'pilot_orders', '201', ''),
            ('started_requests', 1020, 1, 'pilot_orders', '', ''),
            ('dropped_iterations', 1020, 2, 'pilot_orders', '', ''),
            ('http_req_duration', 1021, 2, 'pilot_orders', '500', ''),
            ('started_requests', 1029, 1, 'pilot_orders', '', ''),
            ('http_req_duration', 1031, 300, 'pilot_orders', '201', ''),
        ]

    def analyze(self):
        self.path.write_text(json.dumps(self.summary))
        self.path.with_suffix('.exit-code.txt').write_text('0')
        with self.path.with_suffix('.csv').open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['metric_name', 'timestamp', 'metric_value', 'scenario', 'status', 'error_code'])
            writer.writerows(self.records)
        return analyze_stage(self.path, settling=10, window_seconds=10)

    def test_setup_excluded_successes_separated_and_drain_in_denominator(self):
        row, _ = self.analyze()
        self.assertEqual(row['responses'], 3)
        self.assertEqual(row['accepted'], 2)
        self.assertEqual(row['dropped'], 2)
        self.assertAlmostEqual(row['accepted_rps'], 2 / 32)
        self.assertAlmostEqual(row['started_rps'], 3 / 30)
        self.assertEqual(row['successful_p50_ms'], 200)
        self.assertEqual(row['p50_ms'], 100)
        self.assertAlmostEqual(row['latency_mean_ms'], 134)
        self.assertEqual(row['successful_latency_mean_ms'], 200)
        self.assertEqual(row['tail_response_count'], 1)  # excludes settling and drain
        self.assertEqual(row['tail_accepted_rps'], 0)
        self.assertIsNone(row['tail_successful_p95_ms'])

    def test_empty_window_is_present_with_null_latency_and_drain_is_identified(self):
        _, windows = self.analyze()
        self.assertEqual(len(windows), 4)
        self.assertEqual(windows[1]['responses'], 0)
        self.assertEqual(windows[1]['accepted_rps'], 0)
        self.assertIsNone(windows[1]['p95_ms'])
        self.assertTrue(windows[-1]['in_drain'])
        self.assertEqual(windows[-1]['window_s'], 2)
        self.assertIsNone(windows[-1]['started_rps'])

    def test_truncated_csv_is_rejected(self):
        self.records.pop()
        with self.assertRaisesRegex(ValueError, 'counts disagree'):
            self.analyze()

    def test_percentiles_are_interpolated_from_samples_and_empty_is_null(self):
        self.assertEqual(percentile([100, 300], .95), 290)
        self.assertIsNone(percentile([], .95))

    def test_repetition_summary_keeps_stage_percentiles_and_excludes_warmup_and_incomplete(self):
        row, _ = self.analyze()
        other = dict(row, repetition=2, stage='rate2_run2', successful_p95_ms=1000)
        warmup = dict(row, phase='warmup', successful_p95_ms=100000)
        incomplete = dict(row, complete=False, successful_p95_ms=100000)
        summary = summarize_repetitions([row, other, warmup, incomplete])
        value = next(r for r in summary if r['metric'] == 'successful_p95_ms')
        self.assertEqual(value['repetitions'], 2)
        self.assertEqual(value['valid_values'], 2)
        self.assertEqual(value['median'], 645)  # median of stage p95s 290 and 1000
        self.assertEqual(value['p25'], 467.5)
        self.assertEqual(value['p75'], 822.5)

    def test_missing_success_percentiles_remain_missing_in_summary(self):
        row, _ = self.analyze()
        row['successful_p95_ms'] = None
        value = next(r for r in summarize_repetitions([row]) if r['metric'] == 'successful_p95_ms')
        self.assertEqual(value['valid_values'], 0)
        self.assertIsNone(value['median'])
        self.assertIsNone(value['min'])

    def test_cached_tables_restore_false_booleans_nulls_and_preserve_status_json(self):
        row, _ = self.analyze()
        row.update(complete=False, successful_p95_ms=None)
        path = self.path.parent / 'levels.csv'
        write_csv(path, [row])
        restored = read_table(path)[0]
        self.assertEqual(restored, row)
        self.assertEqual(measured_stages([restored]), [])

    def test_repetitions_sort_numerically(self):
        row, _ = self.analyze()
        stages = [dict(row, repetition=n) for n in (20, 1, 10, 2)]
        self.assertEqual([r['repetition'] for r in measured_stages(stages)], [1, 2, 10, 20])

    def test_availability_uses_observed_requests_including_failures_but_not_dropped(self):
        row, _ = self.analyze()
        enriched = enrich_http_metrics([row])[0]
        self.assertAlmostEqual(enriched['availability_pct'], 100 * 2 / 3)
        self.assertNotIn('availability_pct', row)
        self.assertIsNone(enrich_http_metrics([dict(row, accepted=0, responses=0)])[0]['availability_pct'])
        self.assertEqual(enrich_http_metrics([dict(row, accepted=0)])[0]['availability_pct'], 0)

    def test_cached_means_are_backfilled_from_summary_not_estimated_from_percentiles(self):
        row, _ = self.analyze()
        del row['latency_mean_ms'], row['successful_latency_mean_ms']
        self.path.write_text(json.dumps({'latency_mean_ms': 134, 'successful_latency_mean_ms': 200}))
        enriched = enrich_http_metrics([row], self.path.parent)[0]
        self.assertEqual(enriched['latency_mean_ms'], 134)
        self.assertEqual(enriched['successful_latency_mean_ms'], 200)
        self.assertIsNone(enrich_http_metrics([row])[0]['latency_mean_ms'])

    @unittest.skipUnless(importlib.util.find_spec('matplotlib'), 'optional matplotlib is not installed')
    def test_throughput_chart_scale_includes_highest_requested_rate(self):
        row, windows = self.analyze()
        high = dict(row, stage='rate40_run1', rate=40, start_epoch=1100,
                    started_rps=40, response_rps=39, accepted_rps=38)
        rows = enrich_http_metrics([row, high])
        root = self.path.parent
        (root / 'analysis').mkdir()
        (root / 'metadata.json').write_text(json.dumps({'architecture': 'choreographed-saga'}))
        (root / 'business').mkdir()
        (root / 'business' / (row['stage'] + '.json')).write_text(json.dumps({
            'counts': {'confirmed': 1, 'pending': 1}, 'settled': False,
            'queues': [{'name': 'order-saga-queue.errors', 'ready': 1, 'unacknowledged': 0}]}))
        saved = {}

        def capture(figure, path, **kwargs):
            if path.name == 'throughput.png':
                saved['limits'] = figure.axes[0].get_ylim()
            if path.name == 'business.png':
                saved['business_confirmed'] = list(figure.axes[0].lines[1].get_ydata())
                saved['business_pending'] = list(figure.axes[1].lines[0].get_ydata())

        with patch.dict('os.environ', {'MPLCONFIGDIR': str(root / 'analysis/.matplotlib')}), \
                patch('matplotlib.figure.Figure.savefig', capture):
            plot(root, rows, windows)
        self.assertEqual(saved['limits'][0], 0)
        self.assertGreater(saved['limits'][1], 40)
        self.assertAlmostEqual(saved['business_confirmed'][0], 100 / 3)
        self.assertEqual(saved['business_pending'], [1])


if __name__ == '__main__':
    unittest.main()
