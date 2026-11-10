#!/usr/bin/env python3
"""Combine disjoint load ranges from two comparisons without modifying sources."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from analyze_pilot import read_table, write_csv
from compare_pilots import plot


def combine(low, high, output, plots=True, before_after=False, max_rate=None, plain_charts=False, selection=None):
    low, high, output = (p.resolve() for p in (low, high, output))
    if output.exists():
        raise ValueError('Output must not exist; refusing to overwrite')
    if any(p == output or p in output.parents for p in (low, high)):
        raise ValueError('Output must be outside source comparisons')
    rows, trials, coverage, manifest, runs, warnings = [], [], [], [], {}, []
    seen = set()
    segments = [('60-90', low, set(range(60, 100, 10))),
                ('100-200', high, set(range(100, 201, 10)))]
    if before_after:
        segments = [('1-50 e 100 (versão anterior)', low, {1, 2, 5, 10, 15, 20, 30, 40, 50, 100}),
                    ('60-90 (versão corrigida)', high, {60, 70, 80, 90})]
        warnings.append('Historical visualization mixes code versions; not a controlled single-version capacity curve.')
    if max_rate is not None:
        segments = [(label, folder, {r for r in rates if r <= max_rate})
                    for label, folder, rates in segments]
    selected_architectures = {}
    if selection is not None:
        specs = json.loads(selection.read_text())
        segments = []
        for spec in specs:
            label = spec['label']
            if label in selected_architectures:
                raise ValueError(f'Duplicate selection label: {label}')
            selected_architectures[label] = set(spec['architectures'])
            folder = Path(spec['comparison']).resolve()
            if folder == output or folder in output.parents:
                raise ValueError('Output must be outside source comparisons')
            segments.append((label, folder, set(spec['rates'])))
        warnings.append('Selected campaigns include different code versions and initial states; not a continuous single-version experiment.')
    for label, folder, rates in segments:
        summary = json.loads((folder / 'summary.json').read_text())
        warnings.extend(f'{label}: {w}' for w in summary.get('warnings', []))
        def record(path):
            manifest.append({'source_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        record(folder / 'summary.json')
        record(folder / 'by_rate.csv')
        allowed = selected_architectures.get(label)
        with (folder / 'by_rate.csv').open(newline='') as stream:
            for row in csv.DictReader(stream):
                if allowed is not None and row['architecture'] not in allowed:
                    continue
                row['rate'] = int(row['rate'])
                if row['rate'] not in rates:
                    if before_after or max_rate is not None or selection is not None:
                        continue
                    raise ValueError(f'Unexpected rate in {folder}: {row["rate"]}')
                key = (row['architecture'], row['rate'], row['metric'])
                if key in seen:
                    raise ValueError(f'Overlapping aggregate: {key}')
                seen.add(key)
                for field in ('repetitions', 'valid_values', 'min', 'p25', 'median', 'p75', 'max'):
                    row[field] = float(row[field]) if row[field] else None
                rows.append({**row, 'campaign_range': label, 'source_comparison': str(folder)})
        for run in summary['runs']:
            architecture = run['architecture']
            if allowed is not None and architecture not in allowed:
                continue
            runs.setdefault(architecture, {'architecture': architecture, 'segments': []})['segments'].append(run)
            source = Path(run['path'])
            table = source / 'analysis/levels.csv'
            metadata = source / 'metadata.json'
            record(table)
            record(metadata)
            expected = int(json.loads(metadata.read_text())['environment']['REPETITIONS'])
            stages = [r for r in read_table(table) if r['phase'] == 'benchmark' and r['rate'] in rates]
            for stage in stages:
                trials.append({'architecture': architecture, 'campaign_range': label,
                               'source_dataset': str(source), **stage})
            for rate in sorted(rates):
                completed = [s for s in stages if s['rate'] == rate and s['complete']]
                repetitions = [s['repetition'] for s in completed]
                if len(repetitions) != len(set(repetitions)):
                    raise ValueError(f'Duplicate repetitions: {source}, {rate}')
                coverage.append({'architecture': architecture, 'campaign_range': label, 'rate': rate,
                                 'expected_repetitions': expected, 'complete_repetitions': len(completed),
                                 'source_dataset': str(source)})
                if len(completed) != expected:
                    warnings.append(f'{architecture}, {rate} req/s: {len(completed)}/{expected} complete repetitions')
    low_arch = {r['architecture'] for r in coverage if r['campaign_range'] == segments[0][0]}
    high_arch = {r['architecture'] for r in coverage if r['campaign_range'] == segments[1][0]}
    if selection is None and low_arch != high_arch:
        raise ValueError('Architecture sets differ between campaigns')
    rows.sort(key=lambda r: (r['architecture'], r['rate'], r['metric']))
    trials.sort(key=lambda r: (r['architecture'], r['rate'], r['repetition']))
    output.mkdir(parents=True)
    write_csv(output / 'by_rate.csv', rows)
    write_csv(output / 'runs.csv', trials)
    write_csv(output / 'coverage.csv', coverage)
    write_csv(output / 'sources.csv', manifest)
    (output / 'summary.json').write_text(json.dumps({'runs': list(runs.values()), 'warnings': warnings,
        'semantics': 'Disjoint campaigns; existing per-trial percentile summaries are preserved, not pooled percentiles.'}, indent=2) + '\n')
    missing = '\n'.join(f'- {c["architecture"]}, {c["rate"]} req/s: {c["complete_repetitions"]}/{c["expected_repetitions"]}'
                        for c in coverage if c['complete_repetitions'] != c['expected_repetitions'])
    title = 'Visão histórica de 1 a 100 req/s' if before_after else 'Comparação de 60 a 200 req/s'
    if max_rate is not None:
        title = f'Comparação até {max_rate} req/s'
    version_note = '''
## Versões e seleção

1–50 e 100 req/s são da campanha antiga de 31/10; 60–90 são das campanhas corrigidas
de novembro. Não se trata da continuação da mesma versão. Houve correções funcionais
e de configuração entre esses conjuntos; portanto diferenças de desempenho não podem
ser atribuídas somente à carga ou ao padrão. Os pontos antigos de 60–90 foram excluídos
desta seleção, sem apagar os originais. Nenhuma run recente de 100–200 foi utilizada.
As linhas em 55 e 95 req/s delimitam o trecho substituído. Não há dados antigos de 100
req/s para 2PC. Essa lacuna permanece vazia.
''' if before_after else ''
    if max_rate == 90 and before_after:
        version_note = '''
## Versões e seleção

1–50 req/s são da campanha de 31/10; 60–90 são das campanhas corrigidas de novembro.
Houve correções funcionais e de configuração entre as campanhas; a junção não representa
uma execução contínua da mesma versão. Diferenças não podem ser atribuídas somente à carga.
O nível de 100 req/s foi excluído de todas as tabelas e gráficos desta consolidação.
'''
    if selection is not None:
        title = f'Comparação de {min(r["rate"] for r in rows)} a {max(r["rate"] for r in rows)} req/s'
        version_note = '\n## Seleção explícita\n\nConsulte `selection.json` para a origem de cada faixa e implementação.\nAs campanhas incluem versões de código, estados iniciais e configurações diferentes.\nA combinação é retrospectiva; não representa uma execução contínua da mesma versão.\n'
        (output / 'selection.json').write_text(json.dumps(specs, indent=2) + '\n')
    (output / 'README.md').write_text('# ' + title + '\n' + version_note + '''

Consolidação retrospectiva de campanhas distintas; não representa uma execução contínua.
Os arquivos originais não foram alterados.
Faixas são P25–P75 entre repetições, não intervalos de confiança. Medianas de p95 não são percentis globais.
HTTP 201 mede aceitação, especialmente na Saga coreografada; confirmação é um snapshot após recuperação,
não latência fim a fim. Dados ausentes não foram substituídos por zeros.
Histórico sintético não reproduz caches, pendências e condições operacionais de uma execução contínua.
Curvas com menos repetições são exploratórias e têm cobertura diferente.

- `by_rate.csv`: agregados originais com identificação da campanha.
- `runs.csv`: ensaios individuais, incluindo o indicador `complete`; gráficos usam apenas completos.
- `coverage.csv`: repetições completas e esperadas em cada tratamento.
- `sources.csv`: caminhos e SHA-256 dos insumos.
- `summary.json`: proveniência das configurações e avisos das comparações de origem.

## Cobertura incompleta

''' + (missing or 'Nenhuma lacuna detectada.') + '\n')
    if plots:
        for run in runs.values():
            run['historical_versions'] = before_after and not plain_charts
        boundary = None if plain_charts else ([55, 95] if before_after else 95)
        plot(rows, list(runs.values()), output, campaign_boundary=boundary)
    return coverage


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--low', type=Path, required=True)
    parser.add_argument('--high', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-plots', action='store_true')
    parser.add_argument('--max-rate', type=int)
    parser.add_argument('--plain-charts', action='store_true', help='Omit campaign dividers and version subtitle; keep provenance in documentation')
    parser.add_argument('--before-after', action='store_true',
                        help='Use 1-50 and 100 from --low, and 60-90 from --high; historical mixed versions')
    parser.add_argument('--selection', type=Path, help='Explicit JSON list of label/comparison/rates/architectures selections')
    args = parser.parse_args()
    try:
        combine(args.low, args.high, args.output, not args.no_plots, args.before_after,
                args.max_rate, args.plain_charts, args.selection)
    except (OSError, ValueError, KeyError, ImportError) as error:
        parser.exit(1, f'Combination failed: {error}\n')
