#!/usr/bin/env python3
"""Compare existing pilot tables; never run load or modify input datasets."""
import argparse
import csv
import json
import os
from pathlib import Path
import sys

from analyze_pilot import enrich_http_metrics, percentile, read_table, summarize_repetitions, write_csv

ARCHITECTURE_LABELS = {
    'distributed-baseline': 'Arquitetura de referência',
    'tcc': 'Try-Confirm/Cancel',
    'two-phase-commit': 'Two-Phase Commit (2PC)',
    'orchestrated-saga': 'Saga orquestrada',
    'choreographed-saga': 'Saga coreografada',
}


def effective_pool(root):
    path = root / 'application.log'
    if not path.exists():
        return None
    with path.open(errors='replace') as stream:
        for i, line in enumerate(stream):
            if i >= 2000:
                break
            if 'diagnostics_started' not in line:
                continue
            try:
                record = json.loads(line[line.index('{"timestamp"'):])
                return {'pool': record['pool'], 'nodeVersion': record.get('nodeVersion')}
            except (ValueError, KeyError):
                pass
    return None


def compare(roots, output, plots=True):
    if any(root == output or root in output.parents for root in roots):
        raise ValueError('Comparison output must be outside input datasets')
    runs, rows, warnings = [], [], []
    seen = set()
    for root in roots:
        metadata = json.loads((root / 'metadata.json').read_text())
        architecture = metadata['architecture']
        if architecture in seen:
            raise ValueError(f'Duplicate architecture: {architecture}; compare one dataset per implementation')
        seen.add(architecture)
        stages = enrich_http_metrics(read_table(root / 'analysis/levels.csv'), root)
        measured = [r for r in stages if r['phase'] == 'benchmark' and r['complete']]
        if not measured:
            raise ValueError(f'No complete benchmark stages in {root}')
        for stage in measured:
            outcome = root / 'business' / f'{stage["stage"]}.json'
            if outcome.exists() and stage['started']:
                state = json.loads(outcome.read_text())
                counts = state.get('counts')
                if state.get('collectionStatus') == 'error' or not isinstance(counts, dict):
                    warnings.append(f'{architecture}: {stage["stage"]}: business snapshot unavailable; HTTP metrics preserved')
                else:
                    for status in ('confirmed', 'cancelled', 'pending'):
                        stage[status + '_pct'] = 100 * counts.get(status, 0) / stage['started']
                    stage['unrecorded_pct'] = 100 * (stage['started'] - sum(counts.values())) / stage['started']
        # summarize_repetitions explicitly lists HTTP metrics. Add cohort state
        # separately so an old baseline without snapshots never becomes zero.
        grouped = summarize_repetitions(measured)
        for rate in sorted({r['rate'] for r in measured}):
            cohort = [r for r in measured if r['rate'] == rate]
            for metric in ('confirmed_pct', 'cancelled_pct', 'pending_pct', 'unrecorded_pct'):
                values = [r[metric] for r in cohort if metric in r]
                if values:
                    grouped.append({'rate': rate, 'metric': metric, 'repetitions': len(cohort),
                                    'valid_values': len(values), 'min': min(values),
                                    'p25': percentile(values, .25), 'median': percentile(values, .5),
                                    'p75': percentile(values, .75), 'max': max(values)})
        rows.extend({'architecture': architecture, 'run': root.name, **r} for r in grouped)
        status = json.loads((root / 'status.json').read_text()) if (root / 'status.json').exists() else {}
        host_info = root / 'host-info.txt'
        host = host_info.read_text().splitlines()[0] if host_info.exists() else None
        pg_session = root / 'postgres-session.json'
        postgres = json.loads(pg_session.read_text()) if pg_session.exists() else None
        run = {'architecture': architecture, 'path': str(root), 'environment': metadata.get('environment', {}),
               'responseSemantics': metadata.get('responseSemantics', 'HTTP 201; verify business state separately'),
               'effectiveOrderPool': effective_pool(root), 'host': host, 'postgres': postgres, 'status': status}
        runs.append(run)
        if status.get('exitCode') != 0:
            warnings.append(f'{architecture}: runner failed/unknown; only complete stages are displayed')
        if architecture == 'choreographed-saga':
            warnings.append('choreographed-saga: HTTP latency/201 measure acceptance; use confirmed_pct for completed business state')
    if not rows:
        raise ValueError('No comparable stages')
    reference = runs[0]
    preload_sizes = {int(run['environment'].get('PRELOAD_ORDERS') or 0) for run in runs}
    if len(preload_sizes) > 1:
        warnings.append('Different PRELOAD_ORDERS: initial historical datasets are not equivalent')
    if any(preload_sizes):
        warnings.append('Synthetic successful history was preloaded; not a replay of lower load levels')
    for run in runs[1:]:
        for key in ('RATES', 'DURATION', 'REPETITIONS', 'WARMUP_DURATION', 'WARMUP_RATE',
                    'SETTLING_SECONDS', 'COOLDOWN_SECONDS', 'PRE_ALLOCATED_VUS', 'MAX_VUS',
                    'DB_DIAGNOSTICS', 'COLLECT_HOST', 'K6_CSV_STORAGE'):
            if run['environment'].get(key) != reference['environment'].get(key):
                warnings.append(f'{run["architecture"]} vs {reference["architecture"]}: different {key}')
        if run['effectiveOrderPool'] != reference['effectiveOrderPool']:
            warnings.append(f'{run["architecture"]} vs {reference["architecture"]}: different/unknown pool settings or Node version')
        if run['host'] != reference['host']:
            warnings.append(f'{run["architecture"]} vs {reference["architecture"]}: different/unknown host')
        if run['postgres'] != reference['postgres']:
            warnings.append(f'{run["architecture"]} vs {reference["architecture"]}: different/unknown PostgreSQL version or observer settings')
    warnings.append('Architecture runs are sequential; database history, host state and execution order can affect comparison')
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / 'by_rate.csv', rows)
    (output / 'summary.json').write_text(json.dumps({'runs': runs, 'warnings': warnings,
        'semantics': {'http': '201 = HTTP acceptance; not uptime or guaranteed business completion',
                      'statistics': 'Median and P25-P75 of per-repetition metrics; not pooled request percentiles or confidence intervals',
                      'business': 'Cohort status snapshot after recovery; no end-to-end completion latency is inferred'}}, indent=2) + '\n')
    if plots:
        plot(rows, runs, output)
    for warning in warnings:
        print('WARNING:', warning)
    return rows


def plot(rows, runs, output, campaign_boundary=None):
    os.environ['MPLCONFIGDIR'] = str(output / '.matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    charts = [('availability.png', 'availability_pct', 'Sucesso HTTP (201 / requisições)', '%', 1),
              ('throughput.png', 'accepted_rps', 'Respostas HTTP 201 por segundo, incluindo drenagem', 'HTTP 201/s', 1),
              ('latency.png', 'successful_p95_ms', 'p95 da latência HTTP dos 201', 'segundos', .001),
              ('business-confirmed.png', 'confirmed_pct', 'Pedidos confirmados após recuperação / requisições iniciadas na repetição', '%', 1)]
    for filename, metric, title, unit, scale in charts:
        fig, ax = plt.subplots(figsize=(11, 6), constrained_layout=True)
        if metric == 'accepted_rps':
            offered = sorted({r['rate'] for r in rows})
            ax.plot(offered, offered, ':', color='#555555', label='Disparos/s solicitados')
        for run in runs:
            name = run['architecture']
            values = sorted((r for r in rows if r['architecture'] == name and r['metric'] == metric
                             and r['median'] is not None), key=lambda r: r['rate'])
            if not values:
                continue
            label = ARCHITECTURE_LABELS.get(name, name)
            x = [r['rate'] for r in values]
            line, = ax.plot(x, [r['median'] * scale for r in values], 'o-', label=label)
            ax.fill_between(x, [r['p25'] * scale for r in values],
                            [r['p75'] * scale for r in values], color=line.get_color(), alpha=.12)
        ax.set(title=title, xlabel='Taxa solicitada (disparos/s)', ylabel=unit)
        if campaign_boundary is not None:
            boundaries = campaign_boundary if isinstance(campaign_boundary, (tuple, list)) else [campaign_boundary]
            for index, boundary in enumerate(boundaries):
                ax.axvline(boundary, color='#777777', linestyle='--',
                           label='Separação entre campanhas' if index == 0 else None)
        ax.set_ylim(bottom=0)
        if unit == '%':
            ax.set_ylim(0, 105)
        ax.grid(alpha=.25)
        if ax.lines:
            ax.legend()
        subtitle = 'Mediana entre repetições; faixa P25–P75. HTTP não equivale a conclusão da saga assíncrona.'
        if any(run.get('historical_versions') for run in runs):
            subtitle += '\nVisão histórica: campanhas e versões de código distintas; cobertura incompleta.'
        fig.suptitle(subtitle, fontsize=10)
        fig.savefig(output / filename, dpi=160)
        plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-plots', action='store_true')
    parser.add_argument('runs', type=Path, nargs='+')
    args = parser.parse_args()
    try:
        compare([p.resolve() for p in args.runs], args.output.resolve(), not args.no_plots)
    except (ValueError, OSError, KeyError, ImportError) as error:
        sys.exit(str(error))
