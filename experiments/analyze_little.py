#!/usr/bin/env python3
"""Little's law for the order-service pool. Reads artifacts only; stdlib only."""
import argparse
import csv
import json
from pathlib import Path


COUNTERS = ('requests', 'acquired', 'acquireFailures', 'released',
            'removedWithoutRelease', 'holdMsTotal')


def calculate(stats, started, observation_s, target_rps):
    """Use arithmetic means weighted by checkouts, never average window means."""
    requests, released = stats['requests'], stats['released']
    mean_hold = stats['holdMsTotal'] / released if released else None
    per_request = stats['holdMsTotal'] / requests if requests else None
    return {
        'pool_hold_ms': mean_hold,
        'pool_hold_per_request_ms': per_request,
        'checkouts_per_received_request': released / requests if requests else None,
        'lambda_pool_observed_per_s': released / observation_s if observation_s > 0 else None,
        'lambda_requests_observed_per_s': requests / observation_s if observation_s > 0 else None,
        'started_requests': started,
        'observation_s': observation_s,
        'target_rps': target_rps,
        # The user's direct expression is conditional on ONE checkout/request.
        'C_single_checkout_assumption': target_rps * mean_hold / 1000 if mean_hold is not None else None,
        'C_at_target_request_rate': target_rps * per_request / 1000 if per_request is not None else None,
        'C_observed': stats['holdMsTotal'] / (1000 * observation_s) if observation_s > 0 else None,
    }


def analyze(root, target_rps=100, expected_repetitions=20):
    metadata = json.loads((root / 'metadata.json').read_text())
    run_id = metadata['environment']['RUN_ID']
    cohorts, pool_limits, pids = {}, set(), set()
    malformed = 0
    with (root / 'application.log').open(errors='replace') as stream:
        for line in stream:
            offset = line.find('{"timestamp"')
            if offset < 0:
                continue
            try:
                record = json.loads(line[offset:])
            except ValueError:
                malformed += 1
                continue
            if record.get('runId') != run_id or record.get('service') != 'order-service':
                continue
            if record.get('type') == 'diagnostics_started':
                if record.get('pool', {}).get('max') is not None:
                    pool_limits.add(record['pool']['max'])
                continue
            if record.get('type') != 'db_diagnostic_sample':
                continue
            pids.add(record.get('pid'))
            for delta in record.get('poolHoldByStage', []):
                stage = delta['stage']
                stats = cohorts.setdefault(stage, {**dict.fromkeys(COUNTERS, 0),
                                                    'lastReleaseEpochMs': 0})
                for name in COUNTERS:
                    stats[name] += delta[name]
                stats['lastReleaseEpochMs'] = max(stats['lastReleaseEpochMs'],
                                                   delta.get('lastReleaseEpochMs') or 0)
    warnings = []
    if malformed:
        warnings.append(f'{malformed} malformed diagnostic JSON lines; cohort totals may be incomplete.')
    if len(pids) > 1:
        warnings.append('Multiple order-service PIDs: verify restarts/replicas and possible lost in-flight counters.')
    rows = []
    summaries = sorted(root.glob('rate*_run*.json'),
                       key=lambda p: (int(p.stem.split('_')[0][4:]), int(p.stem.split('run')[1])))
    for path in summaries:
        summary = json.loads(path.read_text())
        stage = path.stem
        stats = cohorts.get(f'{run_id}-{stage}')
        row_warnings = []
        exit_file = path.with_suffix('.exit-code.txt')
        complete = exit_file.exists() and exit_file.read_text().strip() == '0'
        if not complete:
            row_warnings.append('Incomplete k6 execution.')
        if not stats:
            warnings.append(f'{stage}: no cohort counters; rebuild the instrumented order-service.')
            continue
        if stats['acquired'] != stats['released'] or stats['removedWithoutRelease']:
            row_warnings.append('Unbalanced checkouts or clients removed without release: hold mean is censored.')
        if stats['requests'] != summary['started_requests']:
            row_warnings.append('Received requests differ from k6 starts: delivery loss or missing diagnostics.')
        if stats['acquireFailures']:
            row_warnings.append('Pool acquisition failures: failed requests can have fewer checkouts.')
        if summary['dropped_iterations']:
            row_warnings.append('Dropped iterations: requested arrival rate was not fully delivered.')
        if summary['accepted_orders'] != summary['requests']:
            row_warnings.append('HTTP failures: estimates include the observed failure paths, not 100% business success.')
        start_ms = summary['scenario_start_ms']
        # Include all HTTP drain and any checkout returned AFTER the HTTP client
        # gave up. Cohort tags exclude warmup, setup and other repetitions.
        observation = max(summary['observation_seconds'],
                          (stats['lastReleaseEpochMs'] - start_ms) / 1000)
        row = {'stage': stage, 'rate': summary['rate'], 'repetition': summary['repetition'],
               'complete': complete, **stats,
               **calculate(stats, summary['started_requests'], observation, target_rps),
               'accepted_orders': summary['accepted_orders'],
               'http_responses': summary['requests'],
               'dropped_iterations': summary['dropped_iterations'], 'warnings': row_warnings}
        rows.append(row)
    if len(rows) != expected_repetitions:
        warnings.append(f'Expected {expected_repetitions} repetitions; found {len(rows)} with pool counters.')
    if any(row['rate'] != target_rps for row in rows):
        warnings.append('Some repetitions used a rate different from the projection target.')
    if any(row['warnings'] for row in rows):
        warnings.append('See per-repetition warnings before interpreting the pooled calculation.')
    combined = dict.fromkeys(COUNTERS, 0)
    for row in rows:
        for key in COUNTERS:
            combined[key] += row[key]
    total = {**combined,
             **calculate(combined, sum(row['started_requests'] for row in rows),
                         sum(row['observation_s'] for row in rows), target_rps)}
    return {
        'runId': run_id, 'service': 'order-service', 'pool_max_observed': sorted(pool_limits),
        'expected_repetitions': expected_repetitions, 'measured_repetitions': len(rows),
        'repetitions': rows, 'aggregate': total, 'warnings': warnings,
        'semantics': {
            'pool_hold_ms': 'Sum of acquire-to-release durations / released checkouts; excludes acquisition wait.',
            'pool_hold_per_request_ms': 'Sum of ALL checkout durations / received tagged HTTP requests, including failures.',
            'C_single_checkout_assumption': 'target requests/s * pool_hold_ms / 1000; assumes exactly ONE checkout/request.',
            'C_at_target_request_rate': 'target requests/s * pool_hold_per_request_ms / 1000; projection using observed visit count and failure mix.',
            'C_observed': 'released checkouts / observation seconds * mean hold seconds; mean occupancy for the measured request cohort.',
            'observation': 'Scenario start through HTTP drain or last returned cohort checkout, whichever is later; recovery gaps excluded.',
            'aggregate': 'Sums over repetitions; means weighted by checkout/request counts, not by logging windows.',
            'limitations': 'Full cohorts include initial settling and drain. Steady state is not certified. C is a mean, not a safe pool size or proof of capacity; failures, missing counters and unreturned clients limit interpretation.',
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--target-rps', type=float, default=100)
    parser.add_argument('--expected-repetitions', type=int, default=20)
    args = parser.parse_args()
    if args.target_rps <= 0 or args.expected_repetitions <= 0:
        parser.error('target rate and repetitions must be positive')
    result = analyze(args.run_dir, args.target_rps, args.expected_repetitions)
    output = args.run_dir / 'analysis'
    output.mkdir(exist_ok=True)
    (output / 'little.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    if result['repetitions']:
        with (output / 'little.csv').open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(result['repetitions'][0]))
            writer.writeheader()
            for row in result['repetitions']:
                writer.writerow({**row, 'warnings': json.dumps(row['warnings'])})
    def number(value):
        return 'indisponível' if value is None else f'{value:.3f}'
    print('[little] repetição | pool_hold_ms | posse total/pedido ms | C na taxa alvo | C observado')
    for row in result['repetitions']:
        print(f"[little] {row['stage']} | {number(row['pool_hold_ms'])} | "
              f"{number(row['pool_hold_per_request_ms'])} | {number(row['C_at_target_request_rate'])} | {number(row['C_observed'])}")
    total = result['aggregate']
    print(f"[little] Repetições medidas: {result['measured_repetitions']}/{args.expected_repetitions}")
    print(f"[little] Pool max observado: {result['pool_max_observed'] or 'indisponível'}")
    print(f"[little] Pedidos recebidos: {total['requests']}; checkouts devolvidos: "
          f"{total['released']}; falhas de aquisição: {total['acquireFailures']}")
    print(f"[little] pool_hold_ms (média por checkout): {number(total['pool_hold_ms'])}")
    print(f"[little] Checkouts/pedido recebido: {number(total['checkouts_per_received_request'])}")
    print(f"[little] C = {args.target_rps:g} × {number(total['pool_hold_ms'])}/1000 = "
          f"{number(total['C_single_checkout_assumption'])} (hipótese: UM checkout/pedido)")
    print(f"[little] C na taxa alvo, considerando TODOS os checkouts/pedido = {number(total['C_at_target_request_rate'])}")
    print(f"[little] C observado (lambda efetivo do pool × posse média) = {number(total['C_observed'])}")
    print('[little] C é ocupação média; não comprova que um pool desse tamanho seja suficiente.')
    for warning in result['warnings']:
        print(f'[little] AVISO: {warning}')
    print(f'[little] Relatórios: {output / "little.json"} e {output / "little.csv"}')
    if not result['repetitions']:
        parser.exit(1, 'No per-stage pool measurements found.\n')


if __name__ == '__main__':
    main()
