#!/usr/bin/env python3
"""Optional local-host tools and streaming Docker stats; no installation needed."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

ANSI_CSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--run-id', default='manual')
    parser.add_argument('--compose', required=True)
    args = parser.parse_args()
    if not args.output_dir.is_dir():
        parser.error('--output-dir must already exist')
    children, outputs = [], []
    status = {}

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for tool, command, filename in [
            ('vmstat', ['vmstat', '-w', '-t', '1'], 'host-vmstat.log'),
            ('pidstat', ['pidstat', '-h', '-H', '-u', '-r', '-d', '1'], 'process-pidstat.log'),
        ]:
            status[tool] = 'unavailable'
            if shutil.which(tool):
                output = (args.output_dir / filename).open('x')
                outputs.append(output)
                children.append(subprocess.Popen(command, stdout=output, stderr=output))
                status[tool] = 'started'
        status['docker'] = 'unavailable'
        if shutil.which('docker'):
            result = subprocess.run(['docker', 'compose', '-f', args.compose, 'ps', '-q'],
                                    capture_output=True, text=True, timeout=15)
            ids = result.stdout.split() if result.returncode == 0 else []
            if result.returncode != 0:
                status['docker'] = 'unavailable: ' + result.stderr.strip()
            if ids:
                child = subprocess.Popen(['docker', 'stats', '--format', '{{json .}}', *ids],
                                         stdout=subprocess.PIPE, text=True, bufsize=1)
                children.append(child)
                status['docker'] = 'started; native streaming cadence (~2 s)'
        (args.output_dir / 'host-collector-status.json').write_text(json.dumps(status, indent=2) + '\n')
        print(json.dumps(status), flush=True)
        if status['docker'].startswith('started'):
            with (args.output_dir / 'containers.jsonl').open('x', buffering=1) as output:
                for line in child.stdout:
                    # Docker's streaming renderer can emit cursor/erase codes
                    # even with --format JSON and stdout redirected to a pipe.
                    line = ANSI_CSI.sub('', line).strip()
                    if not line:
                        continue
                    try:
                        container = json.loads(line)
                    except json.JSONDecodeError:
                        status['docker'] = 'failed: invalid JSON in Docker stats stream'
                        child.terminate()
                        break
                    output.write(json.dumps({'runId': args.run_id,
                                             'timestamp': datetime.now(timezone.utc).isoformat(),
                                             'container': container}) + '\n')
            if status['docker'].startswith('started'):
                status['docker'] = 'stopped: Docker stats stream ended'
            (args.output_dir / 'host-collector-status.json').write_text(json.dumps(status, indent=2) + '\n')
            print(json.dumps(status), flush=True)
        # Native tools keep collecting even if Docker is unavailable or its
        # stream ends/fails. An optional Docker failure must not stop vmstat/pidstat.
        while any(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for output in outputs:
            output.close()


if __name__ == '__main__':
    main()
