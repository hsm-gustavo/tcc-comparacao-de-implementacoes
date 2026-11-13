#!/usr/bin/env python3
"""Plot median/IQR of per-trial all-response p95; never execute load or modify inputs."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path

from compare_pilots import ARCHITECTURE_LABELS


def percentile(values, q):
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def write_csv(path, rows):
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(run, output):
    run, output = run.resolve(), output.resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; refusing to overwrite')
    trials, grouped, sources = [], [], []

    def record(path):
        sources.append({'source_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

    for architecture in ARCHITECTURE_LABELS:
        root = run / architecture
        metadata_path = root / 'metadata.json'
        metadata = json.loads(metadata_path.read_text())
        if metadata['database'] != 'mock':
            raise ValueError(f'{architecture}: input is not a mock dataset')
        expected = int(metadata['environment']['REPETITIONS'])
        rates = [int(rate) for rate in metadata['environment']['RATES'].split(',')]
        record(metadata_path)
        levels = root / 'analysis/levels.csv'
        record(levels)
        with levels.open(newline='') as stream:
            rows = [r for r in csv.DictReader(stream)
                    if r['phase'] == 'benchmark' and r['complete'] == 'True']
        seen = set()
        for row in rows:
            rate, repetition = int(row['rate']), int(row['repetition'])
            if (rate, repetition) in seen:
                raise ValueError(f'{architecture}: duplicate trial {rate}/{repetition}')
            seen.add((rate, repetition))
            value = float(row['p95_ms'])
            summary_path = root / (row['stage'] + '.json')
            summary = json.loads(summary_path.read_text())
            if not math.isfinite(value) or value < 0 or not math.isclose(value, summary['p95'], rel_tol=1e-12, abs_tol=1e-9):
                raise ValueError(f'{summary_path}: p95 disagrees with levels.csv or is invalid')
            record(summary_path)
            trials.append({'architecture': architecture, 'rate': rate, 'repetition': repetition,
                           'stage': row['stage'], 'p95_ms': value, 'p95_s': value / 1000})
        if {int(r['rate']) for r in rows} != set(rates):
            raise ValueError(f'{architecture}: missing or unexpected load levels')
        for rate in rates:
            values = [r['p95_s'] for r in trials if r['architecture'] == architecture and r['rate'] == rate]
            if len(values) != expected:
                raise ValueError(f'{architecture}/{rate}: {len(values)}/{expected} repetitions')
            grouped.append({'architecture': architecture, 'rate': rate, 'repetitions': expected,
                            'p25_of_trial_p95_s': percentile(values, .25),
                            'median_of_trial_p95_s': percentile(values, .5),
                            'p75_of_trial_p95_s': percentile(values, .75)})
    output.mkdir(parents=True)
    write_csv(output / 'trials.csv', trials)
    write_csv(output / 'by_rate.csv', grouped)
    write_csv(output / 'sources.csv', sources)
    os.environ['MPLCONFIGDIR'] = str(output / '.matplotlib')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(14, 8), constrained_layout=True)
    for architecture, label in ARCHITECTURE_LABELS.items():
        points = sorted([r for r in grouped if r['architecture'] == architecture], key=lambda r: r['rate'])
        x = [r['rate'] for r in points]
        line, = ax.plot(x, [r['median_of_trial_p95_s'] for r in points], 'o-', label=label)
        ax.fill_between(x, [r['p25_of_trial_p95_s'] for r in points],
                        [r['p75_of_trial_p95_s'] for r in points], color=line.get_color(), alpha=.12)
    repetitions = {r['repetitions'] for r in grouped}
    subtitle = ('Mediana e faixa P25–P75 entre oito repetições' if repetitions == {8}
                else 'Mediana e faixa P25–P75 entre repetições')
    ax.set(title='Mock - p95 de todas as respostas HTTP\n' + subtitle,
           xlabel='Taxa solicitada (requisições/s)', ylabel='p95 da latência HTTP (segundos)')
    ax.set_xticks([1] + list(range(10, 101, 10)))
    ax.grid(alpha=.25)
    ax.legend(loc='upper left')
    fig.savefig(output / 'mock-http-p95.png', dpi=160)
    plt.close(fig)
    (output / 'README.md').write_text('''# Mock — p95 de todas as respostas HTTP

Dados: `mock-1a100-20261110T221441Z`, cinco implementações, 14 cargas e oito repetições.
Foram conferidos os 560 p95 de `analysis/levels.csv` contra o campo `p95` dos resumos k6.
O campo usado é **p95_ms**, não successful_p95_ms. Inclui respostas com erro.
Cada ponto é a mediana dos oito p95; a faixa é P25–P75 desses oito valores,
com interpolação linear. Não é p95 global de todas as requisições nem intervalo de confiança.
Valores foram convertidos de milissegundos para segundos, sem arredondamento nos CSVs.
HTTP mede resposta/aceitação, não conclusão fim a fim da saga assíncrona.
Dados originais não foram alterados; não houve nova execução de carga.

- `trials.csv`: os 560 valores individuais.
- `by_rate.csv`: os 70 pontos e suas faixas.
- `sources.csv`: arquivos de entrada e SHA-256.
- `mock-http-p95.png`: gráfico.
''')
    print(f'Generated {output / "mock-http-p95.png"}; {len(trials)} verified trials')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.run, args.output)
