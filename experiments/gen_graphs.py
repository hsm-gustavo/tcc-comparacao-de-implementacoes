"""Analyze k6 constant-arrival-rate pilot CSVs, using the trial as the replication unit."""
import argparse
import re
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

FILE_PATTERN = re.compile(r"rate(?P<rate>\d+)_run(?P<run>\d+)\.csv$")
DURATION_PATTERN = re.compile(r"^(?P<n>\d+)(?P<unit>[smh])$")


def duration_seconds(text):
    match = DURATION_PATTERN.fullmatch(text)
    if not match:
        raise ValueError(f"Invalid duration: {text}")
    return int(match['n']) * {'s': 1, 'm': 60, 'h': 3600}[match['unit']]


def load_metadata(path):
    meta = path.with_suffix('.meta')
    if not meta.exists():
        raise ValueError(f"Missing run metadata: {meta.name}")
    return dict(line.strip().split('=', 1) for line in meta.read_text().splitlines() if '=' in line)


def read_run(path):
    match = FILE_PATTERN.fullmatch(path.name)
    if not match:
        raise ValueError(f"Unexpected name: {path.name}")
    rate, run = int(match['rate']), int(match['run'])
    meta = load_metadata(path)
    duration = duration_seconds(meta['duration'])
    df = pd.read_csv(path, low_memory=False)
    required = {'metric_name', 'metric_value', 'scenario', 'status'}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing k6 CSV columns: {sorted(missing)}")
    benchmark = df[df['scenario'].eq('sustained_orders')]
    requests = benchmark[
        benchmark['metric_name'].eq('http_req_duration')
    ].copy()
    if requests.empty:
        raise ValueError('No create-order HTTP duration samples')
    latency = pd.to_numeric(requests['metric_value'], errors='coerce').dropna()
    status = pd.to_numeric(requests['status'], errors='coerce')
    total = len(requests)
    accepted = int(status.eq(201).sum())
    rejected = total - accepted
    dropped = benchmark[benchmark['metric_name'].eq('dropped_iterations')]
    dropped_count = pd.to_numeric(dropped['metric_value'], errors='coerce').fillna(0).sum()
    # k6 counters in CSV are per-sample increments, not cumulative totals.
    dropped_count = int(dropped_count)
    return {
        'rate': rate,
        'run': run,
        'duration_s': duration,
        'expected_starts': rate * duration,
        'http_requests': total,
        'http_201': accepted,
        'http_non_201': rejected,
        'success_pct': 100 * accepted / total,
        'offered_req_s': rate,
        # Successes are divided by the scheduled load interval for comparability.
        # Completions may extend into gracefulStop: this is a nominal interval-based metric.
        'successful_req_s_nominal': accepted / duration,
        'http_req_duration_mean_ms': latency.mean(),
        'http_req_duration_p50_ms': latency.quantile(.5),
        'http_req_duration_p95_ms': latency.quantile(.95),
        'http_req_duration_p99_ms': latency.quantile(.99),
        'dropped_iterations': dropped_count,
        'k6_exit_code': int(meta.get('k6_exit_code', '-1')),
    }


def aggregate(runs):
    cols = ['success_pct', 'successful_req_s_nominal', 'http_req_duration_mean_ms',
            'http_req_duration_p50_ms', 'http_req_duration_p95_ms', 'http_req_duration_p99_ms',
            'dropped_iterations']
    agg = runs.groupby('rate', as_index=False).agg(
        repetitions=('run', 'count'),
        k6_nonzero_exits=('k6_exit_code', lambda s: (s != 0).sum()),
        **{f'{c}_mean': (c, 'mean') for c in cols},
    )
    return agg


def plot(summary, output, series, ylabel, title, ylim=None):
    fig, ax = plt.subplots(figsize=(9, 5.4))
    for col, label in series:
        ax.plot(summary['rate'], summary[col], marker='o', label=label)
    ax.set(xlabel='Taxa de chegada oferecida (pedidos/s)', ylabel=ylabel, title=title)
    if ylim:
        ax.set_ylim(*ylim)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output, dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset-dir', type=Path,
                        default=Path('experiments/datasets/monolith-arrival-pilot'))
    args = parser.parse_args()
    dataset = args.dataset_dir
    output = dataset / 'analysis'
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(dataset.glob('rate*_run*.csv'))
    if not files:
        raise SystemExit(f'No pilot CSVs found in {dataset}')
    rows = []
    for path in files:
        try:
            rows.append(read_run(path))
            print(f'Loaded: {path.name}')
        except (ValueError, KeyError, pd.errors.ParserError) as exc:
            print(f'SKIPPED {path.name}: {exc}')
    if not rows:
        raise SystemExit('No valid datasets found.')
    runs = pd.DataFrame(rows).sort_values(['rate', 'run'])
    summary = aggregate(runs)
    runs.to_csv(output / 'runs.csv', index=False)
    summary.to_csv(output / 'summary.csv', index=False)
    plot(summary, output / 'latency.png', [
        ('http_req_duration_mean_ms_mean', 'Média'),
        ('http_req_duration_p95_ms_mean', 'p95'),
        ('http_req_duration_p99_ms_mean', 'p99'),
    ], 'Latência HTTP (ms)', 'Latência HTTP vs taxa de chegada')
    plot(summary, output / 'throughput.png', [
        ('rate', 'Taxa oferecida'),
        ('successful_req_s_nominal_mean', 'HTTP 201/s (nominal)'),
    ], 'Pedidos por segundo', 'Taxa oferecida vs HTTP 201 por segundo')
    plot(summary, output / 'success.png', [
        ('success_pct_mean', 'Respostas HTTP 201'),
    ], 'Sucesso HTTP (%)', 'Taxa de sucesso HTTP vs carga', (0, 105))
    plot(summary, output / 'dropped_iterations.png', [
        ('dropped_iterations_mean', 'Iterações descartadas'),
    ], 'Iterações por ensaio', 'k6: iterações descartadas vs carga')
    print('\nSummary (one row per offered rate):')
    print(summary.to_string(index=False))
    print(f'\nSaved to: {output}')
    print('NOTE: HTTP 201 is acceptance, not business completion for asynchronous variants.')
    print('NOTE: HTTP 201/s is normalized by configured duration; requests may finish during gracefulStop.')

if __name__ == '__main__':
    main()
