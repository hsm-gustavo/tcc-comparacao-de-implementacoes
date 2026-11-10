#!/usr/bin/env python3
"""Read a pilot cohort's business state AFTER load, using a separate PG session."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time


def snapshot(compose, prefix):
    if not re.fullmatch(r'[a-zA-Z0-9._-]{1,500}', prefix):
        raise ValueError('Invalid customer prefix')
    sql = ("SELECT json_build_object('sampledAt', now(), 'counts', COALESCE("
           "(SELECT json_object_agg(status, n) FROM (SELECT status, count(*) AS n "
           f"FROM orders WHERE left(\"customerId\", {len(prefix)}) = '{prefix}' "
           "GROUP BY status) s), '{}'::json));")
    command = ['docker', 'compose', '-f', str(compose), 'exec', '-T', '-e',
               'PGOPTIONS=-c default_transaction_read_only=on -c application_name=pilot-outcomes -c statement_timeout=10000',
               'order-db', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'user', '-d', 'order']
    result = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=15, check=True)
    return json.loads(result.stdout)


def queues(compose):
    result = subprocess.run(['docker', 'compose', '-f', str(compose), 'exec', '-T', 'rabbitmq',
                             'rabbitmqctl', 'list_queues', '--silent', 'name', 'messages_ready',
                             'messages_unacknowledged'], capture_output=True, text=True,
                            timeout=15, check=True)
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3 and fields[1].isdigit() and fields[2].isdigit():
            rows.append({'name': fields[0], 'ready': int(fields[1]), 'unacknowledged': int(fields[2])})
    # Four durable consumer queues are created by the choreography's bootstrap.
    expected = {name + '-saga-queue' for name in ('order', 'warehouse', 'billing', 'shipping')}
    if not expected.issubset({row['name'] for row in rows}):
        raise RuntimeError('RabbitMQ snapshot omitted expected saga queues')
    return rows


def collect(compose, prefix, output, wait_seconds=0, broker=False, previous=None):
    previous_errors = None
    if broker:
        previous_errors = 0
        if previous is not None:
            prior = json.loads(previous.read_text())
            previous_errors = sum(row['ready'] + row['unacknowledged']
                                  for row in prior['queues'] if row['name'].endswith('.errors'))
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            record = snapshot(compose, prefix)
            record['queues'] = queues(compose) if broker else []
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError) as error:
            detail = getattr(error, 'stderr', None)
            if isinstance(detail, bytes):
                detail = detail.decode(errors='replace')
            record = {
                'customerPrefix': prefix, 'collectedAt': datetime.now(timezone.utc).isoformat(),
                'collectionStatus': 'error', 'counts': None, 'queues': None, 'settled': None,
                'error': {'type': type(error).__name__, 'message': str(error),
                          'returnCode': getattr(error, 'returncode', None),
                          'stderr': (detail or '').strip()},
            }
            output.write_text(json.dumps(record, indent=2) + '\n')
            print(f'[pilot] Business snapshot unavailable: {output}: '
                  f'{detail.strip() if detail else error}', file=sys.stderr, flush=True)
            if broker:
                # Pending orders are part of the asynchronous drain check.
                raise RuntimeError('Cannot verify asynchronous drain; snapshot error saved in ' + str(output)) from error
            # A synchronous post-load observation must not abort the workload.
            return record
        record.update(customerPrefix=prefix, collectedAt=datetime.now(timezone.utc).isoformat(),
                      collectionStatus='ok',
                      note='Snapshot after load/recovery; HTTP 201 is acceptance, not necessarily business completion.')
        record['settled'] = record['counts'].get('pending', 0) == 0 and all(
            row['ready'] + row['unacknowledged'] == 0 for row in record['queues'])
        if broker:
            parked = sum(row['ready'] + row['unacknowledged'] for row in record['queues']
                         if row['name'].endswith('.errors'))
            active = sum(row['ready'] + row['unacknowledged'] for row in record['queues']
                         if not row['name'].endswith('.errors'))
            record['queueRecovery'] = {
                'activeMessages': active, 'parkedMessages': parked,
                'previousParkedMessages': previous_errors,
                'parkedNetChange': parked - previous_errors,
                'note': 'Global queue counts; net change between snapshots, not unique orders or exact arrivals.'}
            record['recoveryStatus'] = (
                'incomplete' if record['counts'].get('pending', 0) or active or parked > previous_errors
                else 'settled_with_previous_errors' if parked else 'settled')
        output.write_text(json.dumps(record, indent=2) + '\n')
        # Synchronous implementations can legitimately leave partial orders on
        # failure: report them instead of hiding them or changing business logic.
        # Parked messages are not automatically consumed. Old parked messages
        # alone must not spend the recovery budget again on every repetition.
        if not broker or (not record['counts'].get('pending', 0) and not active):
            if broker and record['recoveryStatus'] == 'incomplete':
                print(f'[pilot] WARNING: recovery incomplete; continuing with preserved state: {output}', flush=True)
            return record
        if time.monotonic() >= deadline:
            print(f'[pilot] WARNING: recovery incomplete; continuing with preserved state: {output}', flush=True)
            return record
        time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', type=Path, required=True)
    parser.add_argument('--customer-prefix', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=int, default=0)
    parser.add_argument('--broker', action='store_true')
    parser.add_argument('--previous', type=Path, help='Previous business snapshot for global parked-queue net change')
    args = parser.parse_args()
    collect(args.compose, args.customer_prefix, args.output, args.wait_seconds, args.broker, args.previous)
