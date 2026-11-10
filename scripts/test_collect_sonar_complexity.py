"""API fixtures only: no SonarQube server, credentials or project changes."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import collect_sonar_complexity as collector


def file(path, value):
    return {'key': 'project:' + path, 'path': path, 'qualifier': 'FIL',
            'measures': [] if value is None else [{'metric': 'complexity', 'value': str(value)}]}


class API:
    def __init__(self, files):
        self.files = files
        self.calls = []
        self.analysis_calls = 0
        self.change = False

    def get(self, endpoint, **params):
        self.calls.append((endpoint, params))
        if endpoint == 'project_analyses/search':
            self.analysis_calls += 1
            return {'analyses': [{'key': 'changed' if self.change and self.analysis_calls > 1 else 'analysis',
                                  'date': '2026-11-02T00:00:00+0000'}]}
        if endpoint == 'measures/component':
            return {'component': {'key': 'project', 'measures': [{'metric': 'complexity', 'value': '20'}]}}
        assert endpoint == 'measures/component_tree'
        page, size = params['p'], params['ps']
        return {'paging': {'pageIndex': page, 'pageSize': size, 'total': len(self.files)},
                'components': self.files[(page - 1) * size:page * size]}


class ComplexityTest(unittest.TestCase):
    def test_pagination_sorting_missing_values_and_server_total(self):
        api = API([file('z.ts', 2), file('a.ts', None), file('b.ts', 0)])
        with patch.object(collector, 'PAGE_SIZE', 2):
            identity, total, files = collector.collect(api, 'project')
        self.assertEqual([row['file_path'] for row in files], ['a.ts', 'b.ts', 'z.ts'])
        self.assertEqual([row['complexity'] for row in files], [None, 0, 2])
        self.assertEqual(total, 20)  # Do not replace Sonar's aggregate with a local sum.
        self.assertEqual(identity['analysis_key'], 'analysis')
        for endpoint, params in api.calls:
            if endpoint.startswith('measures/'):
                self.assertEqual(params['metricKeys'], 'complexity')
            if endpoint == 'measures/component_tree':
                self.assertEqual(params['qualifiers'], 'FIL')
                self.assertNotIn('metricSortFilter', params)

    def test_changed_analysis_fails_before_export(self):
        api = API([file('a.ts', 1)])
        api.change = True
        with self.assertRaisesRegex(collector.CollectionError, 'changed during collection'):
            collector.collect(api, 'project')

    def test_duplicate_files_fail(self):
        with self.assertRaisesRegex(collector.CollectionError, 'duplicated file'):
            collector.collect(API([file('a.ts', 1), file('a.ts', 1)]), 'project')

    def test_truncated_page_fails(self):
        api = API([file('a.ts', 1)])
        original = api.get
        def get(endpoint, **params):
            response = original(endpoint, **params)
            if endpoint.endswith('component_tree'):
                response['paging']['total'] = 2
            return response
        api.get = get
        with self.assertRaisesRegex(collector.CollectionError, 'pagination'):
            collector.collect(api, 'project')

    def test_missing_total_or_invalid_metric_fails(self):
        for value in ['-1', '1.5', 'NaN']:
            with self.assertRaises(collector.CollectionError):
                collector.metric({'measures': [{'metric': 'complexity', 'value': value}]})
        with self.assertRaises(collector.CollectionError):
            collector.metric({'measures': []}, required=True)

    def test_csv_bytes_are_repeatable_and_existing_output_is_preserved(self):
        identity, total, files = collector.collect(API([file('a.ts', None)]), 'project')
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a', Path(directory)/'b'
            collector.write_results(a, identity, total, files)
            collector.write_results(b, identity, total, files)
            for name in ('complexity_files.csv', 'complexity_total.csv'):
                self.assertEqual((a/name).read_bytes(), (b/name).read_bytes())
            with (a/'complexity_files.csv').open() as stream:
                self.assertEqual(next(csv.DictReader(stream))['complexity'], '')
            with self.assertRaisesRegex(collector.CollectionError, 'already exists'):
                collector.write_results(a, identity, total, files)

    def test_repository_output_is_refused(self):
        with self.assertRaisesRegex(collector.CollectionError, 'outside the repository'):
            collector.write_results(collector.ROOT/'forbidden-output', {}, 1, [])

    def test_http_errors_are_explicit_without_echoing_secrets(self):
        api = collector.SonarAPI('https://example.test/sonar', 'secret-test-token')
        for failure in [HTTPError('https://example.test', 403, 'secret-test-token', {}, None),
                        URLError('secret-test-token')]:
            with patch.object(api.opener, 'open', side_effect=failure):
                with self.assertRaises(collector.CollectionError) as result:
                    api.get('measures/component', component='project')
                self.assertNotIn('secret-test-token', str(result.exception))

    def test_authentication_get_and_json_validation(self):
        api = collector.SonarAPI('https://example.test/sonar', 'test-token')
        with patch.object(api.opener, 'open') as opened:
            opened.return_value.__enter__.return_value.read.return_value = b'not json'
            with self.assertRaisesRegex(collector.CollectionError, 'invalid JSON'):
                api.get('measures/component', component='project')
            request = opened.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        self.assertIsNone(collector.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))


if __name__ == '__main__':
    unittest.main()
