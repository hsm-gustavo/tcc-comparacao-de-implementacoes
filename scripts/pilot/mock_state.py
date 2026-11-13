"""Read the in-process order mock after load; never query PostgreSQL."""
import argparse
import json
import subprocess
import time
from pathlib import Path


def collect(compose, prefix, output, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    command = ['docker', 'compose', '-f', str(compose), 'exec', '-T', 'order-service',
               'node', '-e',
               "fetch('http://127.0.0.1:3999/?prefix='+encodeURIComponent(process.argv[1]))"
               ".then(async r=>{if(!r.ok)throw Error(await r.text());console.log(await r.text())})"
               ".catch(e=>{console.error(e);process.exitCode=1})", prefix]
    while True:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=True)
        record = json.loads(result.stdout)
        record.update(customerPrefix=prefix, collectionStatus='ok',
                      settled=not record['counts'].get('pending', 0),
                      recoveryStatus='incomplete' if record['counts'].get('pending', 0) else 'complete',
                      limitation='Order cohort snapshot only; no cross-service invariant or broker backlog audit')
        if not record['counts'].get('pending', 0) or time.monotonic() >= deadline:
            output.write_text(json.dumps(record, indent=2) + '\n')
            return
        time.sleep(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compose', type=Path, required=True)
    parser.add_argument('--customer-prefix', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wait-seconds', type=int, default=60)
    args = parser.parse_args()
    collect(args.compose, args.customer_prefix, args.output, args.wait_seconds)
