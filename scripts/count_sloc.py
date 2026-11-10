#!/usr/bin/env python3
"""Count physical TypeScript SLOC with tokei, keeping a per-file audit trail."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = ('monolith', 'distributed-baseline', 'tcc', 'two-phase-commit',
                 'orchestrated-saga', 'choreographed-saga')


def category(path):
    if path.name.endswith(('.spec.ts', '.test.ts', '.d.ts')) or any(
            p in ('__tests__', 'test', 'tests', 'migrations', 'generated') for p in path.parts):
        return 'test_or_generated'
    if path.name == 'seed.ts' or 'seed' in path.parts:
        return 'seed'
    if 'diagnostics' in path.parts or 'instrumentation' in path.parts:
        return 'isolated_diagnostics'
    return 'application'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    # Fail rather than silently replacing a historical measurement.
    if args.output.exists():
        parser.error('Output already exists; choose a new directory')
    version = subprocess.check_output(['tokei', '--version'], text=True).strip()
    files = []
    for architecture in ARCHITECTURES:
        base = ROOT / 'implementation' / architecture
        sources = [base / 'src'] if architecture == 'monolith' else sorted(base.glob('*-service/src'))
        if not sources or not all(source.is_dir() for source in sources):
            raise RuntimeError(f'Missing sources: {architecture}')
        for source in sources:
            for path in sorted(source.rglob('*.ts')):
                files.append((architecture, 'monolith' if architecture == 'monolith' else source.parent.name, path))
    command = ['tokei', '--no-ignore', '--types', 'TypeScript', '--output', 'json']
    result = json.loads(subprocess.check_output(command + [str(p) for _, _, p in files], text=True))
    language = result['TypeScript']
    if language['inaccurate']:
        raise RuntimeError('tokei marked the result as inaccurate')
    reports = {Path(r['name']).resolve(): r['stats'] for r in language['reports']}
    if set(reports) != {p.resolve() for _, _, p in files}:
        raise RuntimeError('tokei did not return exactly the selected files')
    per_file = []
    for architecture, service, path in files:
        stats = reports[path.resolve()]
        per_file.append(dict(architecture=architecture, service=service,
                             path=str(path.relative_to(ROOT)), category=category(path.relative_to(source_root(path))),
                             code=stats['code'], comments=stats['comments'], blanks=stats['blanks'],
                             lines=stats['code'] + stats['comments'] + stats['blanks'],
                             sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    scopes = {
        'all_src': {'test_or_generated', 'seed', 'isolated_diagnostics', 'application'},
        'application_with_diagnostics': {'isolated_diagnostics', 'application'},
        'application_adjusted': {'application'},
    }
    summaries = []
    services = []
    for architecture in ARCHITECTURES:
        for scope, categories in scopes.items():
            selected = [f for f in per_file if f['architecture'] == architecture and f['category'] in categories]
            def total(rows):
                return dict(files=len(rows), **{key: sum(r[key] for r in rows) for key in ('lines', 'code', 'comments', 'blanks')})
            summaries.append(dict(architecture=architecture, scope=scope, **total(selected)))
            if scope == 'application_adjusted':
                for service in sorted({f['service'] for f in selected}):
                    services.append(dict(architecture=architecture, service=service,
                                         **total([f for f in selected if f['service'] == service])))
    args.output.mkdir(parents=True)
    for name, rows in [('files', per_file), ('summary', summaries), ('services', services)]:
        with (args.output / (name + '.csv')).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    (args.output / 'tokei.json').write_text(json.dumps(result, indent=2) + '\n')
    metadata = dict(timestamp=datetime.now(timezone.utc).isoformat(), tokei=version,
                    gitHead=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    gitStatus=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
                    collectorSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    command=command + ['<explicit absolute paths listed in files.csv>'],
                    note='Working tree, not just HEAD. Adjusted excludes isolated diagnostics, not mixed functional/diagnostic source. No AST rewrite or reformatting.')
    (args.output / 'metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
    for row in summaries:
        print(f"{row['architecture']:22} {row['scope']:30} files={row['files']:3} code={row['code']:5} comments={row['comments']:4} blanks={row['blanks']:4}")


def source_root(path):
    return next(parent for parent in path.parents if parent.name == 'src')


if __name__ == '__main__':
    main()
