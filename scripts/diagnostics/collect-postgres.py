#!/usr/bin/env python3
"""Read PostgreSQL snapshots over ONE independent, read-only psql session."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
SQL = """
SELECT json_build_object(
 'sampled_at', now(),
 'activity', (SELECT coalesce(json_agg(a), '[]'::json) FROM (
   SELECT pid, application_name, state, now() - query_start AS query_age,
          wait_event_type, wait_event, pg_blocking_pids(pid) AS blocking_pids,
          left(query, 200) AS query
   FROM pg_stat_activity
   WHERE datname = current_database() AND pid <> pg_backend_pid()
 ) a),
 'database', (SELECT row_to_json(d) FROM pg_stat_database d WHERE datname = current_database()),
 'wal', (SELECT row_to_json(w) FROM pg_stat_wal w),
 'bgwriter', (SELECT row_to_json(b) FROM pg_stat_bgwriter b)
);
""".replace('\n', ' ')


def json_records(stream):
    """Read psql -0 records; JSON whitespace may contain physical newlines."""
    pending = b''
    while chunk := stream.read1(65536):
        records = (pending + chunk).split(b'\0')
        pending = records.pop()
        for record in records:
            yield json.loads(record)
    raise RuntimeError('psql output ended before the next complete NUL-delimited JSON record')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--run-id', default='manual')
    parser.add_argument('--compose', default=str(ROOT / 'environment/compose/compose.base.yaml'))
    parser.add_argument('--local', action='store_true', help='Use local psql and standard PG* environment variables')
    parser.add_argument('--interval', type=float, default=1)
    parser.add_argument('--samples', type=int, help='Stop after N snapshots (default: until interrupted)')
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error('--interval must be positive')
    if args.samples is not None and args.samples <= 0:
        parser.error('--samples must be positive')
    if not args.output_dir.is_dir():
        parser.error('--output-dir must already exist')
    if not shutil.which('psql' if args.local else 'docker'):
        parser.error('Missing psql' if args.local else 'Missing docker')
    # These settings affect this observer session only, never the application.
    options = '-c default_transaction_read_only=on -c application_name=tcc-diagnostics -c statement_timeout=2000'
    psql = ['psql', '-X', '-q', '-A', '-t', '-0', '-v', 'ON_ERROR_STOP=1']
    env = os.environ.copy()
    if args.local:
        env['PGOPTIONS'] = options
        command = psql
    else:
        command = ['docker', 'compose', '-f', args.compose, 'exec', '-T',
                   '-e', f'PGOPTIONS={options}', 'order-db', *psql, '-U', 'user', '-d', 'order']
    paths = {name: args.output_dir / f'postgres-{name}.jsonl'
             for name in ['activity', 'database', 'wal', 'bgwriter']}
    files = {}
    child = None

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for name, path in paths.items():
            files[name] = path.open('x', buffering=1)
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 text=True, env=env, bufsize=1)
        # Read only stdout's binary buffer: -0 frames each complete row, even
        # when json_agg inserts newlines between session records. Stdin stays text.
        records = json_records(child.stdout.buffer)
        # Handshake also proves session read-only and server settings, without SET
        # statements, schema changes, extensions or the application's pool.
        child.stdin.write("SELECT json_build_object('readOnly', current_setting('transaction_read_only'),"
                          " 'version', version(), 'settings', json_build_object("
                          " 'max_connections', current_setting('max_connections'),"
                          " 'statement_timeout', current_setting('statement_timeout'),"
                          " 'lock_timeout', current_setting('lock_timeout'),"
                          " 'synchronous_commit', current_setting('synchronous_commit'),"
                          " 'fsync', current_setting('fsync')));\n")
        child.stdin.flush()
        handshake = next(records)
        if handshake['readOnly'] != 'on':
            raise RuntimeError('Observer session is not read-only')
        (args.output_dir / 'postgres-session.json').write_text(json.dumps(handshake, indent=2) + '\n')
        completed = 0
        while args.samples is None or completed < args.samples:
            started = time.monotonic()
            child.stdin.write(SQL + '\n')
            child.stdin.flush()
            sample = next(records)
            collected_at = datetime.now(timezone.utc).isoformat()
            for name, output in files.items():
                output.write(json.dumps({'runId': args.run_id, 'sampled_at': sample['sampled_at'],
                                         'collected_at': collected_at, 'data': sample[name]}) + '\n')
            # No overlap or catch-up burst when a sample takes longer than 1 s.
            completed += 1
            if args.samples is None or completed < args.samples:
                time.sleep(max(0, args.interval - (time.monotonic() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        if child is not None:
            try:
                child.stdin.close()
            except BrokenPipeError:
                pass
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for output in files.values():
            output.close()


if __name__ == '__main__':
    main()
