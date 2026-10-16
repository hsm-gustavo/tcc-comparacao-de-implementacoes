"""Small fixtures exercise measurement semantics, not the application."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from analyze_pilot import analyze_stage, percentile


class AnalyzePilotTest(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
