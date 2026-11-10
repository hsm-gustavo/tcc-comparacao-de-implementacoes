#!/usr/bin/env python3
"""Write an isolated Compose model; never start containers or alter old volumes."""
import argparse
import hashlib
import json
import os
from pathlib import Path

ARCHITECTURES = ('distributed-baseline', 'tcc', 'two-phase-commit',
                 'orchestrated-saga', 'choreographed-saga')
ROOT = Path(__file__).resolve().parents[2]


def generate(architecture, run_id, output, root=ROOT):
    if architecture not in ARCHITECTURES:
        raise ValueError(f'Unsupported architecture: {architecture}')
    project = 'pilot-' + architecture + '-' + hashlib.sha256(run_id.encode()).hexdigest()[:10]
    output.mkdir(exist_ok=True)
    nginx = (root / 'environment/nginx/distributed.conf').read_text()
    legacy = architecture in ('tcc', 'two-phase-commit', 'choreographed-saga')
    if legacy:
        # Legacy applications expose GET /, rather than GET /health. Preserve
        # all workload routes and every proxy/socket/timeout setting.
        for service in ('order', 'warehouse', 'billing', 'shipping'):
            nginx = nginx.replace(f'proxy_pass http://{service}_cluster/health;',
                                  f'proxy_pass http://{service}_cluster/;')
    (output / 'nginx.conf').write_text(nginx)
    services, volumes = {}, {}
    for index, service in enumerate(('order', 'warehouse', 'billing', 'shipping'), 3001):
        db = service + '-db'
        volumes[service + '-data'] = {}
        services[db] = {
            'image': 'postgres:15-alpine', 'restart': 'unless-stopped',
            'environment': {'POSTGRES_DB': service, 'POSTGRES_USER': 'user', 'POSTGRES_PASSWORD': 'password'},
            'volumes': [f'{service}-data:/var/lib/postgresql/data'],
            'healthcheck': {'test': ['CMD-SHELL', f'pg_isready -U user -d {service}'],
                            'interval': '3s', 'timeout': '5s', 'retries': 10, 'start_period': '15s'}}
        env = {'POSTGRES_HOST': db, 'POSTGRES_PORT': '5432', 'PORT': str(index),
               'RUN_ID': run_id, 'DB_DIAGNOSTICS': os.environ.get('DB_DIAGNOSTICS', '1'),
               'PILOT_INJECT_DIAGNOSTICS': '1' if service == 'order' and architecture != 'distributed-baseline' else '0'}
        if os.environ.get('DB_POOL_MAX'):
            env['DB_POOL_MAX'] = os.environ['DB_POOL_MAX']
        if service == 'order':
            env.update(WAREHOUSE_URL='http://distributed-lb/warehouse',
                       BILLING_URL='http://distributed-lb/billing', SHIPPING_URL='http://distributed-lb/shipping')
        dependencies = {db: {'condition': 'service_healthy'}}
        if architecture == 'choreographed-saga':
            env['RABBITMQ_URL'] = 'amqp://pilot:pilot@rabbitmq:5672'
            dependencies['rabbitmq'] = {'condition': 'service_healthy'}
        services[service + '-service'] = {
            'build': {'context': str(root), 'dockerfile': 'environment/pilot/Dockerfile',
                      'args': {'ARCHITECTURE': architecture, 'SERVICE': service}},
            'restart': 'unless-stopped', 'environment': env, 'depends_on': dependencies}
    if architecture == 'choreographed-saga':
        services['rabbitmq'] = {'image': 'rabbitmq:4-management', 'restart': 'unless-stopped',
                               'environment': {'RABBITMQ_DEFAULT_USER': 'pilot', 'RABBITMQ_DEFAULT_PASS': 'pilot'},
                               'volumes': ['rabbitmq-data:/var/lib/rabbitmq'],
                               'healthcheck': {'test': ['CMD', 'rabbitmq-diagnostics', '-q', 'ping'],
                                               'interval': '5s', 'timeout': '5s', 'retries': 30}}
        volumes['rabbitmq-data'] = {}
    services['distributed-lb'] = {
        'image': 'nginx:alpine', 'restart': 'unless-stopped', 'ports': ['8000:80'],
        'ulimits': {'nofile': {'soft': 65536, 'hard': 65536}},
        'volumes': [f'{output / "nginx.conf"}:/etc/nginx/nginx.conf:ro'],
        'depends_on': [service + '-service' for service in ('order', 'warehouse', 'billing', 'shipping')]}
    model = {'name': project, 'services': services, 'volumes': volumes}
    (output / 'compose.json').write_text(json.dumps(model, indent=2) + '\n')
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--architecture', choices=ARCHITECTURES, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(generate(args.architecture, args.run_id, args.output.resolve())['name'])
