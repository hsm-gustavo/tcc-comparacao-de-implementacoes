#!/usr/bin/env python3
"""Analyze run.sh artifacts without touching raw data. Charts require matplotlib."""
import argparse
import csv
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sys


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def latency(values, prefix=''):
    return {prefix + 'p50_ms': percentile(values, .50),
            prefix + 'p95_ms': percentile(values, .95),
            prefix + 'p99_ms': percentile(values, .99)}


def analyze_stage(path, settling, window_seconds):
    summary = json.loads(path.read_text())
    duration = float(summary['duration_seconds'])
    observation = float(summary['observation_seconds'])
    start = summary.get('scenario_start_ms')
    if start is None:
        raise ValueError(f'{path.name}: missing scenario_start_ms; use the current pilot.js')
    start /= 1000
    exit_path = path.with_suffix('.exit-code.txt')
    complete = exit_path.exists() and exit_path.read_text().strip() == '0'
    buckets, all_latencies, success_latencies, tail_latencies = {}, [], [], []
    started = dropped = responses = accepted = failures = timeouts = tail_accepted = tail_responses = 0
    statuses = {}
    with path.with_suffix('.csv').open(newline='') as stream:
        for record in csv.DictReader(stream):
            metric = record['metric_name']
            if metric not in ('started_requests', 'dropped_iterations', 'http_req_duration'):
                continue
            if record.get('scenario') != 'pilot_orders':
                continue
            elapsed = max(0., float(record['timestamp']) - start)
            index = int(elapsed // window_seconds)
            bucket = buckets.setdefault(index, {'started': 0, 'dropped': 0, 'responses': 0,
                                                'accepted': 0, 'failures': 0, 'timeouts': 0,
                                                'latencies': [], 'successful_latencies': []})
            value = float(record['metric_value'])
            if metric == 'started_requests':
                started += value
                bucket['started'] += value
            elif metric == 'dropped_iterations':
                dropped += value
                bucket['dropped'] += value
            else:
                responses += 1
                bucket['responses'] += 1
                all_latencies.append(value)
                bucket['latencies'].append(value)
                status = record.get('status') or '0'
                statuses[status] = statuses.get(status, 0) + 1
                if status == '201':
                    accepted += 1
                    bucket['accepted'] += 1
                    success_latencies.append(value)
                    bucket['successful_latencies'].append(value)
                else:
                    failures += 1
                    bucket['failures'] += 1
                if record.get('error_code') == '1050':
                    timeouts += 1
                    bucket['timeouts'] += 1
                # Completion-time window, not a cohort of requests started here.
                if settling <= elapsed < duration:
                    tail_responses += 1
                    if status == '201':
                        tail_accepted += 1
                        tail_latencies.append(value)
    if int(started) != summary['started_requests'] or responses != summary['requests']:
        raise ValueError(f'{path.name}: CSV/summary counts disagree (truncated or incompatible artifacts)')
    if accepted != summary['accepted_orders'] or int(dropped) != summary['dropped_iterations']:
        raise ValueError(f'{path.name}: CSV/summary success or dropped counts disagree')
    row = {'stage': path.stem, 'phase': summary.get('phase', 'benchmark'),
           'rate': summary['rate'], 'repetition': summary['repetition'], 'complete': complete,
           'start_epoch': start, 'duration_s': duration, 'observation_s': observation,
           'started': int(started), 'responses': responses, 'accepted': accepted,
           'failed': failures, 'timeouts': timeouts, 'dropped': int(dropped),
           'started_rps': started / duration, 'accepted_rps': accepted / observation,
           'response_rps': responses / observation,
           'failure_fraction': failures / responses if responses else None,
           'drop_fraction': dropped / (started + dropped) if started + dropped else None,
           'settling_s': settling, 'tail_response_count': tail_responses,
           'tail_accepted_rps': tail_accepted / (duration - settling) if duration > settling else None,
           'http_status_counts': json.dumps(statuses, sort_keys=True),
           'latency_mean_ms': sum(all_latencies) / responses if responses else None,
           'successful_latency_mean_ms': sum(success_latencies) / accepted if accepted else None,
           **latency(all_latencies), **latency(success_latencies, 'successful_'),
           **latency(tail_latencies, 'tail_successful_')}
    windows = []
    # Include empty windows: a stalled server must not disappear from the plot.
    for index in range(max(math.ceil(observation / window_seconds), max(buckets, default=0) + 1)):
        elapsed = index * window_seconds
        exposure = min(window_seconds, max(0., observation - elapsed))
        bucket = buckets.get(index, {'started': 0, 'dropped': 0, 'responses': 0, 'accepted': 0,
                                     'failures': 0, 'timeouts': 0, 'latencies': [], 'successful_latencies': []})
        active = max(0., min(duration, elapsed + window_seconds) - elapsed)
        windows.append({'stage': path.stem, 'phase': row['phase'], 'rate': row['rate'],
                        'repetition': row['repetition'], 'start_epoch': start + elapsed,
                        'elapsed_s': elapsed, 'window_s': exposure, 'arrival_window_s': active,
                        'in_settling': elapsed < settling, 'in_drain': elapsed >= duration,
                        **{k: int(bucket[k]) for k in ('started', 'dropped', 'responses', 'accepted', 'failures', 'timeouts')},
                        'accepted_rps': bucket['accepted'] / exposure if exposure else None,
                        'started_rps': bucket['started'] / active if active else None,
                        **latency(bucket['latencies']), **latency(bucket['successful_latencies'], 'successful_')})
    return row, windows


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_table(path):
    """Restore numeric/null/bool values in existing analyzer tables."""
    rows = []
    with path.open(newline='') as stream:
        for record in csv.DictReader(stream):
            row = {}
            for key, value in record.items():
                if key in ('stage', 'phase', 'http_status_counts'):
                    row[key] = value
                elif value == '':
                    row[key] = None
                elif key in ('complete', 'in_settling', 'in_drain'):
                    if value not in ('True', 'False'):
                        raise ValueError(f'{path.name}: invalid boolean {key}={value}')
                    row[key] = value == 'True'
                else:
                    number = float(value)
                    row[key] = int(number) if number.is_integer() else number
            rows.append(row)
    return rows


def measured_stages(rows):
    return sorted((r for r in rows if r['phase'] == 'benchmark' and r['complete']),
                  key=lambda r: (r['rate'], r['repetition']))


def enrich_http_metrics(rows, root=None):
    """Derived HTTP success, with means backfilled from small per-stage summaries.

    Old cached levels.csv tables have percentiles but no means. Do not estimate
    a mean from percentiles, or reread gigabytes of raw k6 CSV just for charts.
    """
    result = []
    for source in rows:
        row = dict(source)
        row['availability_pct'] = 100 * row['accepted'] / row['responses'] if row['responses'] else None
        missing = [key for key in ('latency_mean_ms', 'successful_latency_mean_ms') if key not in row]
        summary = {}
        if missing and root is not None:
            path = root / (row['stage'] + '.json')
            if path.exists():
                summary = json.loads(path.read_text())
        for key in missing:
            row[key] = summary.get(key)
        result.append(row)
    return result


def summarize_repetitions(rows):
    """Distribution of stage metrics, never a pooled request percentile or CI."""
    measured = measured_stages(rows)
    result = []
    for rate in sorted({r['rate'] for r in measured}):
        group = [r for r in measured if r['rate'] == rate]
        for metric in ('started_rps', 'response_rps', 'accepted_rps', 'availability_pct',
                       'failure_fraction', 'drop_fraction', 'latency_mean_ms',
                       'successful_latency_mean_ms', 'successful_p50_ms',
                       'successful_p95_ms', 'successful_p99_ms', 'p95_ms'):
            values = [r[metric] for r in group if r.get(metric) is not None]
            result.append({'rate': rate, 'metric': metric, 'repetitions': len(group),
                           'valid_values': len(values), 'min': min(values) if values else None,
                           'p25': percentile(values, .25), 'median': percentile(values, .50),
                           'p75': percentile(values, .75), 'max': max(values) if values else None})
    return result


def read_pool(path):
    samples = []
    if path.exists():
        with path.open(errors='replace') as stream:
            for line in stream:
                position = line.find('{"timestamp"')
                if position < 0:
                    continue
                try:
                    value = json.loads(line[position:])
                    if value.get('type') == 'db_diagnostic_sample':
                        # Keep only chart fields from long experiments; omit holders/errors/SQL metadata.
                        samples.append({key: value.get(key, {}) for key in
                                        ('timestamp', 'pool', 'connectionAcquire', 'connectionHold',
                                         'queryDuration', 'eventLoop')})
                except ValueError:
                    continue
    return samples


def plot(root, rows, windows):
    output = root / 'analysis'
    os.environ.setdefault('MPLCONFIGDIR', str(output / '.matplotlib'))
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError('Charts require matplotlib. Install requirements.txt in a venv, then rerun this command.') from error

    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False,
                         'axes.spines.right': False, 'legend.framealpha': .9})
    measured = measured_stages(rows)
    rates = sorted({r['rate'] for r in measured})
    counts = {sum(r['rate'] == rate for r in measured) for rate in rates}
    repetitions_note = f'{next(iter(counts))} repetições por nível' if len(counts) == 1 else 'quantidade de repetições varia por nível; consulte by_rate.csv'
    blue, orange, red = '#2166ac', '#b87512', '#b2182b'

    def save(fig, path):
        path.parent.mkdir(exist_ok=True)
        fig.savefig(path, dpi=160)
        plt.close(fig)

    def distribution(axis, metric, label, color, scale=1, linestyle='-'):
        medians, lower, upper = [], [], []
        for index, rate in enumerate(rates):
            values = [r[metric] * scale for r in measured if r['rate'] == rate and r.get(metric) is not None]
            medians.append(percentile(values, .50))
            lower.append(percentile(values, .25))
            upper.append(percentile(values, .75))
            # Small deterministic offsets expose repeated observations at the same rate.
            jitter = [index + .12 * (2 * j / max(1, len(values) - 1) - 1) for j in range(len(values))]
            axis.scatter(jitter, values, s=12, alpha=.3, color=color, edgecolors='none')
        x = list(range(len(rates)))
        axis.plot(x, medians, 'o', linestyle=linestyle, color=color, label=label, markersize=4)
        axis.fill_between(x, [v if v is not None else math.nan for v in lower],
                          [v if v is not None else math.nan for v in upper], color=color, alpha=.15)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)
    distribution(axes[0, 0], 'started_rps', 'Disparos iniciados/s', orange, linestyle='--')
    distribution(axes[0, 0], 'accepted_rps', 'HTTP 201/s, com drenagem', blue)
    if rates:
        axes[0, 0].plot(range(len(rates)), rates, ':', color='#333333', label='Taxa solicitada')
    distribution(axes[0, 1], 'successful_p95_ms', 'p95 dos HTTP 201', blue, .001)
    distribution(axes[0, 1], 'p95_ms', 'p95 de todas as respostas', orange, .001, '--')
    distribution(axes[1, 0], 'failure_fraction', 'Falhas HTTP', red, 100)
    distribution(axes[1, 0], 'drop_fraction', 'Iterações não iniciadas', orange, 100, '--')
    distribution(axes[1, 1], 'successful_p50_ms', 'p50 dos HTTP 201', blue, .001)
    distribution(axes[1, 1], 'successful_p99_ms', 'p99 dos HTTP 201', orange, .001, '--')
    for axis, title, unit in zip(axes.flat,
                               ['Carga entregue e vazão útil', 'Latência: sucessos e todas as respostas',
                                'Falhas e descartes', 'Mediana e cauda dos sucessos'],
                               ['requisições/s', 'segundos', '%', 'segundos']):
        axis.set(title=title, xlabel='Nível solicitado (requisições/s)', ylabel=unit)
        axis.set_xticks(range(len(rates)), [str(r) for r in rates])
        axis.set_ylim(bottom=0)
        axis.grid(axis='y', alpha=.2)
        axis.legend(fontsize=9)
    fig.suptitle(root.name + ' | ' + repetitions_note + '\nLinha = mediana entre repetições; faixa = P25–P75; pontos = repetições individuais\n'
                 'Percentis resumidos são de cada repetição, não de todas as requisições agrupadas.', fontsize=12)
    save(fig, output / 'capacity.png')

    def standalone_axes(title, unit, height=6):
        fig, axis = plt.subplots(figsize=(10, height), constrained_layout=True)
        axis.set(title=title, xlabel='Nível solicitado (requisições/s)', ylabel=unit)
        axis.set_xticks(range(len(rates)), [str(r) for r in rates])
        axis.grid(axis='y', alpha=.2)
        fig.suptitle(root.name + ' | ' + repetitions_note + '\n'
                     'Linha = mediana entre repetições; faixa = P25–P75; pontos = repetições individuais', fontsize=11)
        return fig, axis

    fig, axis = standalone_axes('Disponibilidade HTTP — proporção de respostas 201', 'HTTP 201 / requisições observadas (%)')
    distribution(axis, 'availability_pct', 'Sucesso HTTP por repetição', blue)
    axis.set_ylim(0, 105)
    axis.legend(loc='lower left', fontsize=9)
    save(fig, output / 'availability.png')

    fig, axis = standalone_axes('Throughput HTTP — respostas concluídas por segundo', 'requisições/s ou respostas/s')
    if rates:
        axis.plot(range(len(rates)), rates, ':', color='#333333', label='Disparos/s solicitados')
    distribution(axis, 'started_rps', 'Disparos/s iniciados', '#777777', linestyle='--')
    distribution(axis, 'response_rps', 'Respostas/s totais, incluindo falhas', orange, linestyle='--')
    distribution(axis, 'accepted_rps', 'Respostas HTTP 201/s', blue)
    axis.set_ylim(bottom=0)
    axis.legend(loc='upper left', fontsize=9)
    axis.set_xlabel('Nível solicitado (requisições/s)\nRespostas/s incluem drenagem; disparos usam a duração nominal.', fontsize=9)
    save(fig, output / 'throughput.png')

    fig, axes = plt.subplots(2, 1, figsize=(10, 9), sharex=True, constrained_layout=True)
    for axis, prefix, title in [(axes[0], '', 'Todas as respostas HTTP, incluindo falhas'),
                                 (axes[1], 'successful_', 'Somente respostas HTTP 201')]:
        mean_key = 'successful_latency_mean_ms' if prefix else 'latency_mean_ms'
        if any(r.get(mean_key) is not None for r in measured):
            distribution(axis, mean_key, 'Média HTTP por repetição', blue, .001)
        distribution(axis, prefix + 'p95_ms', 'p95 por repetição', orange, .001)
        distribution(axis, prefix + 'p99_ms', 'p99 por repetição', red, .001, '--')
        axis.set(title=title, ylabel='Latência HTTP (segundos)')
        axis.set_ylim(bottom=0)
        axis.grid(axis='y', alpha=.2)
        axis.legend(loc='upper left', fontsize=9)
    axes[-1].set_xticks(range(len(rates)), [str(r) for r in rates])
    axes[-1].set_xlabel('Nível solicitado (requisições/s)')
    fig.suptitle(root.name + ' | Latência HTTP | ' + repetitions_note + '\n'
                 'Linha = mediana das métricas por repetição; faixa = P25–P75\n'
                 'p95/p99 não são percentis globais; a curva Média resume médias individuais.', fontsize=11)
    save(fig, output / 'latency.png')

    if measured:
        repetitions = sorted({r['repetition'] for r in measured})
        lookup = {(r['rate'], r['repetition']): r for r in measured}
        fig, axes = plt.subplots(3, 1, figsize=(max(12, .5 * len(repetitions) + 3), 11), constrained_layout=True)
        definitions = [
            ('p95 HTTP 201 por repetição (s)', lambda r: None if r['successful_p95_ms'] is None else r['successful_p95_ms'] / 1000, 'YlOrRd'),
            ('Falhas HTTP por repetição (%)', lambda r: None if r['failure_fraction'] is None else r['failure_fraction'] * 100, 'YlOrRd'),
            ('HTTP 201/s em relação à taxa solicitada (%)', lambda r: r['accepted_rps'] / r['rate'] * 100, 'YlGn'),
        ]
        for axis, (title, value, palette) in zip(axes, definitions):
            matrix = [[value(lookup[(rate, rep)]) if (rate, rep) in lookup else None for rep in repetitions] for rate in rates]
            finite = [v for row in matrix for v in row if v is not None]
            cmap = plt.get_cmap(palette).copy()
            cmap.set_bad('#d9d9d9')
            image = axis.imshow([[math.nan if v is None else v for v in row] for row in matrix],
                                aspect='auto', cmap=cmap, vmin=0, vmax=max(finite, default=1) or 1)
            axis.set(title=title, ylabel='Disparos/s', xlabel='Repetição')
            axis.set_xticks(range(len(repetitions)), [str(r) for r in repetitions])
            axis.set_yticks(range(len(rates)), [str(r) for r in rates])
            fig.colorbar(image, ax=axis, fraction=.02, pad=.02)
            if len(repetitions) <= 25:
                for y, row in enumerate(matrix):
                    for x, number in enumerate(row):
                        if number is None:
                            continue
                        if '(%)' in title and 0 < number < .1:
                            text = '<0.1'
                        else:
                            text = f'{number:.2f}' if title.endswith('(s)') and number < 1 else f'{number:.1f}'
                        color = 'white' if image.norm(number) > .65 else '#202020'
                        axis.text(x, y, text, ha='center', va='center', fontsize=7, color=color)
        fig.suptitle(root.name + '\nCada célula representa uma repetição; cinza = dado ausente ou percentil indefinido.', fontsize=12)
        save(fig, output / 'repetitions.png')

    by_stage = {}
    for window in windows:
        by_stage.setdefault(window['stage'], []).append(window)
    chronological = sorted(rows, key=lambda r: r['start_epoch'])

    def boundaries(axes, selected, origin, detailed=False):
        blocks = []
        for row in sorted(selected, key=lambda r: r['start_epoch']):
            key = row['stage'] if detailed else (row['phase'], row['rate'])
            start = (row['start_epoch'] - origin) / 60
            end = start + row['observation_s'] / 60
            label = f'r{row["repetition"]}' if detailed else ('aquecimento' if row['phase'] == 'warmup' else f'{row["rate"]}/s')
            if blocks and blocks[-1][0] == key:
                blocks[-1][3] = end
            else:
                blocks.append([key, label, start, end])
            if detailed:
                for axis in axes:
                    axis.axvspan(start, start + min(row['settling_s'], row['duration_s']) / 60,
                                 color=orange, alpha=.08)
        for index, (_, label, left, right) in enumerate(blocks):
            for axis in axes:
                axis.axvline(left, color='#555555', alpha=.2, linewidth=.7)
                if not detailed and index % 2 == 0:
                    axis.axvspan(left, right, color=blue, alpha=.035)
            axes[0].text((left + right) / 2, 1.015, label, transform=axes[0].get_xaxis_transform(),
                         ha='center', va='bottom', fontsize=8)

    def timeline(selected, path, detailed=False):
        origin = min(r['start_epoch'] for r in selected)
        fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True, constrained_layout=True)
        for index, row in enumerate(selected):
            stage = sorted(by_stage.get(row['stage'], []), key=lambda w: w['start_epoch'])
            x = [(w['start_epoch'] - origin) / 60 for w in stage]
            axes[0].plot(x, [w['accepted_rps'] for w in stage], color=blue, linewidth=1, label='HTTP 201/s' if index == 0 else None)
            axes[0].plot(x, [w['started_rps'] for w in stage], color=orange, linewidth=.8, linestyle=':', label='Disparos iniciados/s' if index == 0 else None)
            axes[1].plot(x, [None if w['successful_p95_ms'] is None else w['successful_p95_ms'] / 1000 for w in stage], color=blue, linewidth=1)
            axes[2].plot(x, [w['failures'] for w in stage], color=red, linewidth=1, label='Falhas HTTP' if index == 0 else None)
            axes[2].plot(x, [w['dropped'] for w in stage], color=orange, linestyle='--', linewidth=1, label='Iterações não iniciadas' if index == 0 else None)
        boundaries(axes, selected, origin, detailed)
        for axis, unit in zip(axes, ['requisições/s', 'p95 HTTP 201 (s)', 'contagem / janela']):
            axis.set_ylabel(unit)
            axis.set_ylim(bottom=0)
            axis.grid(axis='y', alpha=.2)
        axes[0].legend(loc='upper left', fontsize=9)
        axes[2].legend(loc='upper left', fontsize=9)
        axes[-1].set_xlabel('Minutos desde o início deste gráfico')
        suffix = f'{selected[0]["rate"]} disparos/s — r = repetição; laranja = acomodação inicial' if detailed else 'Visão geral — rótulos indicam a taxa solicitada'
        fig.suptitle('Evolução temporal | ' + suffix + '\nJanelas por instante de conclusão; a drenagem pode gerar picos de vazão.', fontsize=12)
        save(fig, path)

    timeline(chronological, output / 'timeline.png')
    for rate in rates:
        selected = [r for r in chronological if r['phase'] == 'benchmark' and r['rate'] == rate]
        timeline(selected, output / 'timeline_by_rate' / f'rate{rate}.png', detailed=True)

    print('Reading pool diagnostics for overview and per-rate charts...', flush=True)
    samples = read_pool(root / 'application.log')
    for sample in samples:
        sample['_epoch'] = dt.datetime.fromisoformat(sample['timestamp'].replace('Z', '+00:00')).timestamp()

    def pool_chart(selected, data, path, detailed=False):
        origin = min(r['start_epoch'] for r in selected)
        x = [(s['_epoch'] - origin) / 60 for s in data]
        fig, axes = plt.subplots(4, 1, figsize=(14, 11), sharex=True, constrained_layout=True)
        axes[0].plot(x, [s['pool']['inUse'] for s in data], color=blue, linewidth=.8, label='Em uso')
        axes[0].plot(x, [s['pool']['total'] for s in data], color='#555555', linestyle='--', linewidth=.8, label='Total no pool')
        axes[1].plot(x, [s['pool']['waiting'] for s in data], color=orange, linewidth=.8, label='Aguardando conexão')
        for key, label, color in [('connectionAcquire', 'Aquisição p95', blue),
                                   ('connectionHold', 'Posse p95', orange),
                                   ('queryDuration', 'SQL p95', '#31824c')]:
            axes[2].plot(x, [None if s.get(key, {}).get('p95Ms') is None else s[key]['p95Ms'] / 1000 for s in data], label=label, color=color, linewidth=.8)
        axes[3].plot(x, [s.get('eventLoop', {}).get('p99Ms') for s in data], color=blue, linewidth=.8, label='Event loop p99')
        boundaries(axes, selected, origin, detailed)
        for axis, unit in zip(axes, ['Conexões', 'Conexões na fila', 'segundos', 'milissegundos']):
            axis.set_ylabel(unit)
            axis.set_ylim(bottom=0)
            axis.grid(axis='y', alpha=.2)
            axis.legend(loc='upper left', fontsize=9)
        axes[-1].set_xlabel('Minutos desde o início deste gráfico')
        suffix = f'{selected[0]["rate"]} disparos/s — r = repetição' if detailed else 'visão geral por nível de carga'
        fig.suptitle('Pool do order-service | ' + suffix + '\nPercentis das janelas de diagnóstico; ocupação e fila em escalas separadas.', fontsize=12)
        save(fig, path)

    if samples:
        pool_chart(chronological, samples, output / 'pool.png')
        for rate in rates:
            selected = [r for r in chronological if r['phase'] == 'benchmark' and r['rate'] == rate]
            start = selected[0]['start_epoch']
            end = max(r['start_epoch'] + r['observation_s'] for r in selected)
            data = [s for s in samples if start <= s['_epoch'] <= end]
            if data:
                pool_chart(selected, data, output / 'pool_by_rate' / f'rate{rate}.png', detailed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--no-plots', action='store_true', help='stdlib only: generate tables without matplotlib')
    mode.add_argument('--plots-only', action='store_true', help='reuse analysis/levels.csv and windows.csv without reading raw k6 CSVs')
    parser.add_argument('--window-seconds', type=int)
    parser.add_argument('--settling-seconds', type=int)
    args = parser.parse_args()
    if (args.window_seconds is not None and args.window_seconds <= 0) or (args.settling_seconds is not None and args.settling_seconds < 0):
        parser.error('Window must be positive and settling must be nonnegative')
    root = args.directory.resolve()
    output = root / 'analysis'
    if args.plots_only:
        if args.window_seconds is not None or args.settling_seconds is not None:
            parser.error('--plots-only uses existing windows; omit window/settling overrides')
        rows = enrich_http_metrics(read_table(output / 'levels.csv'), root)
        windows = read_table(output / 'windows.csv')
        if not rows:
            sys.exit('No stages in analysis/levels.csv')
        grouped = summarize_repetitions(rows)
        if grouped:
            write_csv(output / 'by_rate.csv', grouped)
        plot(root, rows, windows)
        print(f'Charts regenerated from existing tables: {output}')
        return
    metadata = json.loads((root / 'metadata.json').read_text()) if (root / 'metadata.json').exists() else {}
    settling = args.settling_seconds if args.settling_seconds is not None else int(metadata.get('environment', {}).get('SETTLING_SECONDS', 30))
    paths = sorted(p for p in root.glob('*.json') if re.fullmatch(r'rate[0-9]+_run[0-9]+', p.stem))
    paths.sort(key=lambda p: tuple(map(int, re.findall(r'\d+', p.stem))))
    if (root / 'warmup.json').exists():
        paths.insert(0, root / 'warmup.json')
    rows, windows, warnings = [], [], []
    for path in paths:
        try:
            row, stage_windows = analyze_stage(path, settling, args.window_seconds or 10)
        except (ValueError, KeyError, OSError) as error:
            warnings.append(str(error))
            continue
        rows.append(row)
        windows.extend(stage_windows)
        if not row['complete']:
            warnings.append(f'{path.stem}: missing/suspect exit code; excluded from capacity curves')
        if row['dropped']:
            warnings.append(f'{path.stem}: dropped_iterations > 0; offered load was not fully delivered')
    if not rows:
        sys.exit('No analyzable stages: ' + '; '.join(warnings))
    rows = enrich_http_metrics(rows)
    output.mkdir(exist_ok=True)
    write_csv(output / 'levels.csv', rows)
    write_csv(output / 'windows.csv', windows)
    grouped = summarize_repetitions(rows)
    if grouped:
        write_csv(output / 'by_rate.csv', grouped)
    report = {'run': root.name, 'stages': len(rows), 'warnings': warnings,
              'semantics': {
                  'accepted': 'HTTP 201 only; verify business state separately',
                  'accepted_rps': 'successful responses / observation including drain, excluding setup',
                  'availability_pct': 'HTTP 201 / observed HTTP requests * 100; includes client timeouts in denominator, excludes dropped iterations; not uptime',
                  'response_rps': 'all observed HTTP responses, including failures, / observation including drain, excluding setup',
                  'tail': 'completion timestamps in [settling, duration); not a start-time cohort',
                  'windows': 'completion-time windows; CSV timestamps have ~1 second resolution; missing percentiles are null',
                  'low_medium_high': 'manual choice: use windows to check stability, success, achieved load and host contention',
                  'replications': 'stage metrics summarized by median and P25-P75 across repetitions; no pooled request percentile or confidence interval; stages reuse database state'}}
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Analyzed {len(rows)} stages: {output}')
    for warning in warnings:
        print('WARNING: ' + warning)
    if not args.no_plots:
        plot(root, rows, windows)
        print('Charts: availability.png, throughput.png, latency.png, capacity.png, repetitions.png, timeline.png and timeline_by_rate/; pool.png and pool_by_rate/ when diagnostics exist')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        sys.exit(str(error))
