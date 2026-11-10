#!/usr/bin/env python3
"""Read cyclomatic complexity from an existing SonarQube main-branch analysis."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
PAGE_SIZE = 500


class CollectionError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the authentication header to a redirected destination.
        return None


class SonarAPI:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.netloc or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment):
            raise CollectionError('SONAR_URL must be an HTTP(S) URL without credentials, query or fragment')
        if not token or '\n' in token or '\r' in token:
            raise CollectionError('Invalid SONAR_TOKEN')
        self.url = url.rstrip('/')
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, endpoint, **params):
        request = Request(self.url + '/api/' + endpoint + '?' + urlencode(params),
                          headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'},
                          method='GET')
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            hints = {401: 'check token', 403: 'check Browse permission',
                     404: 'check project key, URL and API compatibility',
                     429: 'API rate limit; repeat collection later'}
            # Do not print response bodies, headers or credentials.
            raise CollectionError(f'{endpoint}: HTTP {error.code}; '
                                  + hints.get(error.code, 'check server/API availability; redirects are refused')) from None
        except (URLError, TimeoutError, OSError):
            raise CollectionError(f'{endpoint}: network, TLS or timeout failure') from None
        except (ValueError, UnicodeError):
            raise CollectionError(f'{endpoint}: invalid JSON response') from None


def metric(component, required=False):
    measures = [m for m in component.get('measures', []) if m['metric'] == 'complexity']
    if not measures and not required:
        return None  # Missing is not zero.
    if len(measures) != 1 or not re.fullmatch(r'[0-9]+', str(measures[0].get('value', ''))):
        raise CollectionError('Missing, duplicated or invalid complexity metric')
    return int(measures[0]['value'])


def latest_analysis(api, project):
    analyses = api.get('project_analyses/search', project=project, p=1, ps=1)['analyses']
    if not analyses:
        raise CollectionError('Project has no completed analysis')
    analysis = analyses[0]
    return {'analysis_key': analysis['key'], 'analysis_date': analysis['date']}


def project_total(api, project):
    component = api.get('measures/component', component=project, metricKeys='complexity')['component']
    if component['key'] != project:
        raise CollectionError('API returned an unexpected project')
    return metric(component, required=True)


def collect(api, project):
    analysis = latest_analysis(api, project)
    total = project_total(api, project)
    files = []
    keys, paths = set(), set()
    page = 1
    expected_total = None
    while True:
        response = api.get('measures/component_tree', component=project, metricKeys='complexity',
                           qualifiers='FIL', strategy='leaves', p=page, ps=PAGE_SIZE)
        paging = response['paging']
        if paging['pageIndex'] != page or paging['pageSize'] != PAGE_SIZE:
            raise CollectionError('API returned unexpected pagination')
        if expected_total is None:
            expected_total = paging['total']
            if not isinstance(expected_total, int) or expected_total < 0:
                raise CollectionError('Invalid pagination total')
        elif paging['total'] != expected_total:
            raise CollectionError('File list changed during collection; repeat without concurrent analyses')
        components = response['components']
        expected_count = min(PAGE_SIZE, expected_total - len(files))
        if len(components) != expected_count:
            raise CollectionError('Incomplete or inconsistent file pagination')
        for component in components:
            key, path = component['key'], component['path']
            if component['qualifier'] != 'FIL' or not path or key in keys or path in paths:
                raise CollectionError('Invalid or duplicated file in API response')
            keys.add(key)
            paths.add(path)
            files.append({'file_path': path, 'complexity': metric(component)})
        if len(files) == expected_total:
            break
        page += 1
    # Measures endpoints expose current values, not an arbitrary historical snapshot.
    if latest_analysis(api, project) != analysis or project_total(api, project) != total:
        raise CollectionError('Analysis or project metric changed during collection; no CSV written')
    files.sort(key=lambda row: row['file_path'])
    return {'project_key': project, **analysis}, total, files


def write_results(output, identity, total, files):
    output = output.resolve()
    if output == ROOT or ROOT in output.parents:
        raise CollectionError('Choose --output outside the repository; project files must remain unchanged')
    if output.exists():
        raise CollectionError('Output already exists; choose a new directory')
    output.mkdir(parents=True)
    for name, rows in [('complexity_total.csv', [{**identity, 'complexity': total}]),
                       ('complexity_files.csv', [{**identity, **row} for row in files])]:
        fields = list(identity) + ([] if name == 'complexity_total.csv' else ['file_path']) + ['complexity']
        with (output / name).open('x', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory outside the repository')
    args = parser.parse_args()
    try:
        config = {}
        for name in ('SONAR_URL', 'SONAR_TOKEN', 'SONAR_PROJECT_KEY'):
            config[name] = os.environ.get(name, '')
            if not config[name].strip():
                raise CollectionError(f'Missing environment variable: {name}')
        identity, total, files = collect(SonarAPI(config['SONAR_URL'], config['SONAR_TOKEN']),
                                         config['SONAR_PROJECT_KEY'])
        write_results(args.output, identity, total, files)
        missing = sum(row['complexity'] is None for row in files)
        if missing:
            print(f'WARNING: {missing} analyzed files have no complexity value; CSV cells left empty.', file=sys.stderr)
        print(f'CSV collection saved in {args.output.resolve()}')
    except (CollectionError, KeyError, TypeError, ValueError, OSError) as error:
        # Unexpected schema errors expose only their type, not server payloads.
        detail = str(error) if isinstance(error, CollectionError) else type(error).__name__
        print('Collection failed: ' + detail, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
