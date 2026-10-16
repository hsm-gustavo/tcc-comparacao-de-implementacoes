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
                        samples.append(value)
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

    measured = [r for r in rows if r['phase'] == 'benchmark' and r['complete']]
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    for repetition in sorted({r['repetition'] for r in measured}):
        group = sorted((r for r in measured if r['repetition'] == repetition), key=lambda r: r['rate'])
        x = [r['rate'] for r in group]
        suffix = f' (rep {repetition})'
        axes[0, 0].plot(x, [r['started_rps'] for r in group], 'o--', label='Iniciados/s' + suffix)
        axes[0, 0].plot(x, [r['accepted_rps'] for r in group], 'o-', label='HTTP 201/s, com drenagem' + suffix)
        axes[0, 1].plot(x, [r['successful_p95_ms'] for r in group], 'o-', label='p95 HTTP 201' + suffix)
        axes[0, 1].plot(x, [r['p95_ms'] for r in group], 'x--', label='p95 todas respostas' + suffix)
        axes[1, 0].plot(x, [100 * r['failure_fraction'] if r['failure_fraction'] is not None else None for r in group], 'o-', label='Falhas HTTP %' + suffix)
        axes[1, 0].plot(x, [100 * r['drop_fraction'] if r['drop_fraction'] is not None else None for r in group], 'x--', label='Iterações descartadas %' + suffix)
        axes[1, 1].plot(x, [r['successful_p50_ms'] for r in group], 'o-', label='p50 HTTP 201' + suffix)
        axes[1, 1].plot(x, [r['successful_p99_ms'] for r in group], 'x--', label='p99 HTTP 201' + suffix)
    if measured:
        x = sorted({r['rate'] for r in measured})
        axes[0, 0].plot(x, x, 'k:', label='Carga solicitada')
    titles = ['Carga entregue e vazão útil', 'Latência e efeito dos erros', 'Falhas e chegadas não iniciadas', 'Dispersão da latência dos sucessos']
    for axis, title, unit in zip(axes.flat, titles, ['pedidos/s', 'ms', '%', 'ms']):
        axis.set(title=title, xlabel='Taxa solicitada (pedidos/s)', ylabel=unit)
        axis.grid(alpha=.25)
        if axis.lines:
            axis.legend(fontsize=8)
    fig.suptitle(root.name + ' — PostgreSQL / distributed baseline')
    fig.savefig(output / 'capacity.png', dpi=160)
    plt.close(fig)

    origin = min(r['start_epoch'] for r in rows)
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    for row in rows:
        stage = [w for w in windows if w['stage'] == row['stage']]
        x = [(w['start_epoch'] - origin) / 60 for w in stage]
        axes[0].plot(x, [w['accepted_rps'] for w in stage], '.-')
        axes[0].plot(x, [w['started_rps'] for w in stage], ':', color='grey')
        axes[1].plot(x, [w['successful_p95_ms'] for w in stage], '.-')
        axes[2].plot(x, [w['failures'] for w in stage], '.-')
        axes[2].plot(x, [w['dropped'] for w in stage], 'x--')
        left = (row['start_epoch'] - origin) / 60
        for axis in axes:
            axis.axvspan(left, left + min(row['settling_s'], row['duration_s']) / 60, color='orange', alpha=.12)
        axes[0].annotate(f'{row["stage"]}: {row["rate"]}/s', (left, row['rate']), fontsize=8, rotation=20)
    for axis, label in zip(axes, ['HTTP 201/s (linha); inícios/s (cinza)', 'p95 HTTP 201 (ms)', 'Falhas (linha); descartadas (x) / janela']):
        axis.set_ylabel(label)
        axis.grid(alpha=.25)
    axes[-1].set_xlabel('Minutos desde o início do aquecimento; laranja = acomodação inicial')
    fig.suptitle('Evolução dentro dos patamares — janelas por instante de conclusão')
    fig.savefig(output / 'timeline.png', dpi=160)
    plt.close(fig)

    samples = read_pool(root / 'application.log')
    if samples:
        fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True, constrained_layout=True)
        x = [(dt.datetime.fromisoformat(s['timestamp'].replace('Z', '+00:00')).timestamp() - origin) / 60 for s in samples]
        for key in ('inUse', 'waiting'):
            axes[0].plot(x, [s['pool'][key] for s in samples], label=key)
        for key in ('connectionAcquire', 'connectionHold', 'queryDuration'):
            axes[1].plot(x, [s.get(key, {}).get('p95Ms') for s in samples], label=key + ' p95')
        axes[2].plot(x, [s.get('eventLoop', {}).get('p99Ms') for s in samples], label='event loop p99')
        for axis, label in zip(axes, ['Conexões', 'ms', 'ms']):
            axis.set_ylabel(label)
            axis.grid(alpha=.25)
            axis.legend()
            for row in rows:
                axis.axvline((row['start_epoch'] - origin) / 60, color='grey', alpha=.3)
        axes[-1].set_xlabel('Minutos desde o início do aquecimento')
        fig.suptitle('Pool real do order-service — percentis das janelas de diagnóstico')
        fig.savefig(output / 'pool.png', dpi=160)
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--no-plots', action='store_true', help='stdlib only: generate tables without matplotlib')
    parser.add_argument('--window-seconds', type=int, default=10)
    parser.add_argument('--settling-seconds', type=int)
    args = parser.parse_args()
    if args.window_seconds <= 0 or (args.settling_seconds is not None and args.settling_seconds < 0):
        parser.error('Window must be positive and settling must be nonnegative')
    root = args.directory.resolve()
    metadata = json.loads((root / 'metadata.json').read_text()) if (root / 'metadata.json').exists() else {}
    settling = args.settling_seconds if args.settling_seconds is not None else int(metadata.get('environment', {}).get('SETTLING_SECONDS', 30))
    paths = sorted(p for p in root.glob('*.json') if re.fullmatch(r'rate[0-9]+_run[0-9]+', p.stem))
    if (root / 'warmup.json').exists():
        paths.insert(0, root / 'warmup.json')
    rows, windows, warnings = [], [], []
    for path in paths:
        try:
            row, stage_windows = analyze_stage(path, settling, args.window_seconds)
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
    output = root / 'analysis'
    output.mkdir(exist_ok=True)
    write_csv(output / 'levels.csv', rows)
    write_csv(output / 'windows.csv', windows)
    report = {'run': root.name, 'stages': len(rows), 'warnings': warnings,
              'semantics': {
                  'accepted': 'HTTP 201 only; verify business state separately',
                  'accepted_rps': 'successful responses / observation including drain, excluding setup',
                  'tail': 'completion timestamps in [settling, duration); not a start-time cohort',
                  'windows': 'completion-time windows; CSV timestamps have ~1 second resolution; missing percentiles are null',
                  'low_medium_high': 'manual choice: use windows to check stability, success, achieved load and host contention',
                  'replications': 'independent stage percentiles; never averaged or pooled into a global percentile'}}
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Analyzed {len(rows)} stages: {output}')
    for warning in warnings:
        print('WARNING: ' + warning)
    if not args.no_plots:
        plot(root, rows, windows)
        print('Charts: capacity.png, timeline.png; pool.png when diagnostic samples exist')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError) as error:
        sys.exit(str(error))
