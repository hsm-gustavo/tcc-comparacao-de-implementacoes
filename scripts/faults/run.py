#!/usr/bin/env python3
"""Independent one-order payment-fault trials. No monolith or manual recovery."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import signal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import csv

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('pilot_compose', ROOT/'scripts/pilot/compose.py')
compose = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compose)
STOCK = 100


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def http(url, payload=None):
    start = time.monotonic()
    request = Request(url, data=None if payload is None else json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            status, body = response.status, response.read().decode()
    except HTTPError as error:
        status, body = error.code, error.read().decode()
    except (URLError, TimeoutError, OSError):
        status, body = 0, 'network/timeout failure'
    try:
        body = json.loads(body)
    except ValueError:
        pass
    return dict(status=status, body=body, durationMs=(time.monotonic()-start)*1000,
                completedAt=utc())


def sql(command, service, query):
    result = subprocess.run(command + ['exec', '-T', '-e',
        'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000 -c application_name=fault-audit',
        service+'-db', 'psql', '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-U', 'user', '-d', service],
        input=query, capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(f'{service} audit failed: {result.stderr[-1000:]}')
    return json.loads(result.stdout)


def audit(command, customer, product, architecture):
    started_at=utc()
    # Identifiers originate in our runner/API, never interpolated arbitrary SQL.
    if not re.fullmatch(r'[a-zA-Z0-9._-]+', customer) or not re.fullmatch(r'[a-fA-F0-9-]{36}', product):
        raise ValueError('Invalid audit identifier')
    orders = sql(command, 'order', f"SELECT COALESCE(json_agg(t),'[]') FROM orders t WHERE \"customerId\"='{customer}';")
    ids = [r['id'] for r in orders]
    if any(not re.fullmatch(r'[a-fA-F0-9-]{36}', x) for x in ids):
        raise ValueError('Invalid order identifier')
    predicate = "IN ("+','.join("'"+x+"'" for x in ids)+")" if ids else "IN (NULL)"
    fetch = lambda service, table, column: sql(command, service,
        f'SELECT COALESCE(json_agg(t),\'[]\') FROM {table} t WHERE "{column}" {predicate};')
    result = dict(sampledAt=utc(), architecture=architecture, orders=orders, items=fetch('order','order_items','orderId'),
                  payments=fetch('billing','payments','orderId'), shipments=fetch('shipping','shipments','orderId'),
                  products=sql(command,'warehouse',f"SELECT COALESCE(json_agg(t),'[]') FROM products t WHERE id='{product}';"),
                  reservations=[], queues=[])
    reservation_table = 'stock_reservations' if architecture in ('tcc','two-phase-commit') else 'stock_reservations_saga'
    if architecture in ('tcc','two-phase-commit','choreographed-saga'):
        result['reservations'] = fetch('warehouse',reservation_table,'orderId')
    if architecture == 'choreographed-saga':
        output = subprocess.check_output(command + ['exec','-T','rabbitmq','rabbitmqctl','list_queues','--silent',
                                                   'name','messages_ready','messages_unacknowledged'], text=True, timeout=20)
        for line in output.splitlines():
            f=line.split()
            if len(f)==3 and f[1].isdigit() and f[2].isdigit():
                result['queues'].append(dict(name=f[0],ready=int(f[1]),unacknowledged=int(f[2])))
        expected={s+'-saga-queue' for s in ('order','warehouse','billing','shipping')}
        if not expected.issubset({q['name'] for q in result['queues']}):
            raise RuntimeError('Broker audit omitted expected queues')
    result['auditStartedAt']=started_at
    result['sampledAt']=utc()  # End of multi-database observation, not atomic snapshot.
    return result


def classify(s):
    if len(s['orders']) != 1 or len(s['products']) != 1:
        return 'inconclusive'
    order=s['orders'][0];stock=s['products'][0]['stockQuantity']
    payments=s['payments'];shipments=s['shipments'];reservations=s['reservations']
    # Exact one-order/product/amount invariants, not just terminal status counts.
    paid=len(payments)==1 and payments[0]['status']=='completed' and float(payments[0]['amount'])==10
    shipped=len(shipments)==1 and shipments[0]['status']=='shipped'
    item_ok=len(s['items'])==1 and s['items'][0]['quantity']==1 and s['items'][0]['productId']==s['products'][0]['id']
    no_active=(all(p['status'] in ('failed','refunded') for p in payments)
               and all(p['status']=='cancelled' for p in shipments)
               and all(r['status'] in ('cancelled','released') for r in reservations))
    reservation_required=s.get('architecture') in ('tcc','two-phase-commit','choreographed-saga')
    reservation_ok=(not reservation_required and not reservations) or (len(reservations)==1 and reservations[0]['status'] in ('committed','reserved')
                    and reservations[0]['quantity']==1 and reservations[0]['productId']==s['products'][0]['id'])
    if order['status']=='confirmed':
        return 'completed_valid' if item_ok and paid and shipped and stock==STOCK-1 and reservation_ok and float(order['totalAmount'])==10 else 'terminal_inconsistent'
    if order['status']=='cancelled':
        return 'aborted_valid' if item_ok and no_active and stock==STOCK and len(payments)<=1 and len(shipments)<=1 and len(reservations)<=1 else 'terminal_inconsistent'
    return 'pending' if order['status']=='pending' else 'inconclusive'


def events(command):
    text=subprocess.check_output(command+['logs','--no-color','billing-service'],text=True,timeout=30)
    rows=[]
    for line in text.splitlines():
        pos=line.find('{"type":"payment_fault_test"')
        if pos>=0:
            rows.append(json.loads(line[pos:]))
    return rows


def positive(value):
    v=int(value)
    if v<=0:raise argparse.ArgumentTypeError('must be positive')
    return v


def start_stack(command, log, timeout=300):
    databases=[s+'-db' for s in ('order','warehouse','billing','shipping')]
    subprocess.run(command+['up','-d','--no-build',*databases],stdout=log,stderr=subprocess.STDOUT,check=True)
    deadline=time.monotonic()+timeout
    for db in databases:
        while True:
            ids=subprocess.run(command+['ps','--all','--quiet',db],capture_output=True,text=True,check=True,timeout=20).stdout.strip().splitlines()
            health=''
            if len(ids)==1:
                health=subprocess.run(['docker','inspect','--format','{{.State.Status}} {{.State.Health.Status}}',ids[0]],
                    capture_output=True,text=True,check=True,timeout=20).stdout.strip()
            ready=subprocess.run(command+['exec','-T',db,'pg_isready','-h','127.0.0.1','-U','user','-d',db[:-3]],
                                 stdout=log,stderr=subprocess.STDOUT,timeout=15)
            # initdb's temporary server uses Unix sockets: TCP excludes that phase.
            if health=='running healthy' and ready.returncode==0:break
            if time.monotonic()>=deadline:raise RuntimeError(f'{db} final TCP/health readiness timeout')
            time.sleep(2)
    subprocess.run(command+['up','-d','--no-build'],stdout=log,stderr=subprocess.STDOUT,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--prepare-only',action='store_true',help='Generate trial Compose plans only; no Docker or HTTP')
    parser.add_argument('--repetitions',type=positive,default=int(os.getenv('REPETITIONS','20')))
    parser.add_argument('--fault-seconds',type=positive,default=int(os.getenv('FAULT_SECONDS','5')))
    parser.add_argument('--observe-seconds',type=positive,default=int(os.getenv('OBSERVE_SECONDS','60')))
    parser.add_argument('--cleanup-timeout',type=positive,default=int(os.getenv('CLEANUP_TIMEOUT_SECONDS','300')),
                        help='Maximum Docker teardown time; does not change application timeouts')
    parser.add_argument('--architectures',default=os.getenv('ARCHITECTURES',','.join(compose.ARCHITECTURES)))
    args=parser.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    architectures=args.architectures.split(',')
    if len(set(architectures))!=len(architectures) or any(a not in compose.ARCHITECTURES for a in architectures):
        parser.error('Select distinct distributed architectures; monolith/outbox are not supported')
    if args.output.exists():parser.error('Output already exists; choose a new RUN_ID')
    args.output.mkdir(parents=True)
    suite=args.output.name
    if not re.fullmatch(r'[a-zA-Z0-9._-]+',suite):parser.error('Invalid output identifier')
    source_hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [Path(__file__),ROOT/'environment/faults/register-payment-fault.cjs',ROOT/'scripts/pilot/compose.py',
                             ROOT/'environment/pilot/Dockerfile',ROOT/'environment/pilot/pnpm-workspace.yaml',
                             ROOT/'environment/nginx/distributed.conf',
                             *[p for a in architectures for p in (ROOT/'implementation'/a).glob('*-service/*')
                               if p.name in ('package.json','pnpm-lock.yaml','tsconfig.json','tsconfig.build.json')],
                             *[p for a in architectures for p in (ROOT/'implementation'/a).glob('*-service/src/**/*.ts') if not p.name.endswith('.spec.ts')]]}
    save(args.output/'metadata.json',dict(suite=suite,createdAt=utc(),architectures=architectures,repetitions=args.repetitions,
        faultSeconds=args.fault_seconds,observeSeconds=args.observe_seconds,cleanupTimeoutSeconds=args.cleanup_timeout,pollSeconds=2,initialStock=STOCK,
        requestQuantity=1,requestAmount=10,sourceSha256=source_hashes,prepareOnly=args.prepare_only,
        semantics='One target request per fresh trial; simulated fail-fast payment unavailability before persistence. No HTTP retry/manual recovery.'))
    results=[];built=set();exit_code=0
    try:
        for repetition in range(1,args.repetitions+1):
            # Rotate execution order; balance control/transient order across repetitions.
            order=architectures[(repetition-1)%len(architectures):]+architectures[:(repetition-1)%len(architectures)]
            for architecture in order:
                modes=['control','transient'] if repetition%2 else ['transient','control']
                for mode in modes:
                    trial=args.output/architecture/f'{mode}-rep{repetition:02}'
                    trial.mkdir(parents=True)
                    model=compose.generate(architecture,f'{suite}-{architecture}-{mode}-{repetition}',trial)
                    tag=hashlib.sha256((suite+architecture).encode()).hexdigest()[:12]
                    for name,service in model['services'].items():
                        if 'build' in service:service['image']=f'fault-test-{tag}-{name}'
                    billing=model['services']['billing-service']
                    billing['environment'].update(PAYMENT_FAULT_TEST='1',PAYMENT_FAULT_MODE=mode,
                        PAYMENT_FAULT_MS=str(args.fault_seconds*1000),ARCHITECTURE=architecture)
                    billing['volumes']=[str(ROOT/'environment/faults/register-payment-fault.cjs')+':/usr/src/app/fault-test.cjs:ro']
                    billing['command']=['node','--require','./fault-test.cjs','--require','./pilot/register-diagnostics.cjs','dist/main.js']
                    plan=trial/'compose.json';save(plan,model)
                    if args.prepare_only:continue
                    command=['docker','compose','-f',str(plan)]
                    result=dict(architecture=architecture,treatment=mode,repetition=repetition,trial=str(trial.relative_to(args.output)))
                    print(f'[faults] {architecture} {mode} {repetition}/{args.repetitions}',flush=True)
                    infrastructure_ok=False
                    try:
                        with (trial/'startup.log').open('w') as log:
                            if architecture not in built:
                                subprocess.run(command+['build'],stdout=log,stderr=subprocess.STDOUT,check=True)
                                built.add(architecture)
                            start_stack(command,log)
                        base='http://127.0.0.1:8000'
                        for service in ('order','warehouse','billing','shipping'):
                            deadline=time.monotonic()+180
                            while http(f'{base}/{service}/health')['status']!=200:
                                if time.monotonic()>=deadline:raise RuntimeError(f'{service} readiness timeout')
                                time.sleep(1)
                        # Warmup uses a different product/amount so it cannot trigger injection.
                        products=[]
                        for label,price in [('warmup',1),('target',10)]:
                            response=http(base+'/warehouse/products',dict(name=label,price=price,stockQuantity=STOCK))
                            if response['status']!=201 or not isinstance(response['body'],dict) or not response['body'].get('id'):
                                raise RuntimeError('Product preparation failed')
                            products.append(response['body']['id'])
                        customer='fault-'+model['name']
                        warm=http(base+'/orders',dict(customerId=customer+'-warmup',productId=products[0],quantity=1,amount=1))
                        save(trial/'warmup-http.json',warm)
                        if warm['status']!=201:raise RuntimeError('Warmup HTTP failed')
                        deadline=time.monotonic()+60
                        while True:
                            s=audit(command,customer+'-warmup',products[0],architecture)
                            if len(s['orders'])==1 and s['orders'][0]['status']=='confirmed':break
                            if time.monotonic()>=deadline:raise RuntimeError('Warmup did not confirm')
                            time.sleep(2)
                        save(trial/'warmup-state.json',s)
                        before=audit(command,customer,products[1],architecture)
                        save(trial/'before.json',before)
                        if before['orders'] or len(before['products'])!=1 or before['products'][0]['stockQuantity']!=STOCK or any(q['ready']+q['unacknowledged'] for q in before['queues']):
                            raise RuntimeError('Initial state not clean')
                        response=http(base+'/orders',dict(customerId=customer,productId=products[1],quantity=1,amount=10))
                        save(trial/'http.json',response);result['http_status']=response['status'];result['http_ms']=response['durationMs']
                        deadline=time.monotonic()+(args.fault_seconds if mode=='transient' else 0)+args.observe_seconds
                        first_valid=None;stable=0
                        restoration_deadline_set=False
                        with (trial/'observations.jsonl').open('w') as observations:
                            while True:
                                s=audit(command,customer,products[1],architecture);label=classify(s)
                                s['classification']=label;observations.write(json.dumps(s)+'\n');observations.flush()
                                e=events(command);save(trial/'injection-events.json',e)
                                restored=next((x['timestamp'] for x in e if x['event']=='fault_restored'),None)
                                if restored and not restoration_deadline_set:
                                    since=(datetime.now(timezone.utc)-datetime.fromisoformat(restored)).total_seconds()
                                    deadline=time.monotonic()+max(0,args.observe_seconds-since)
                                    restoration_deadline_set=True
                                boundary=next((x for x in e if x['event']=='boundary_reached'),None)
                                valid=label in ('completed_valid','aborted_valid')
                                if valid and first_valid is None:first_valid=s['sampledAt']
                                quiet=not any(q['ready']+q['unacknowledged'] for q in s['queues'])
                                stable=stable+1 if valid and quiet and (mode=='control' or restored) else 0
                                if stable>=3 or time.monotonic()>=deadline:break
                                time.sleep(2)
                        save(trial/'final-state.json',s)
                        triggered=any(x['event']=='payment_rejected' for x in e)
                        result.update(classification=label,boundary_reached=bool(boundary),fault_triggered=triggered,
                                      first_valid_observed_at=first_valid or '',restored_at=restored or '',
                                      observation_end=s['sampledAt'],recovery_ms='',resolution_timing='not_observed')
                        result.update(duplicate_payments=max(0,len(s['payments'])-1),
                                      duplicate_shipments=max(0,len(s['shipments'])-1),
                                      duplicate_reservations=max(0,len(s['reservations'])-1))
                        if first_valid and label in ('completed_valid','aborted_valid'):
                            if mode=='control':result['resolution_timing']='control'
                            elif restored and first_valid>=restored:
                                result['resolution_timing']='after_restoration'
                                result['recovery_ms']=(datetime.fromisoformat(first_valid)-datetime.fromisoformat(restored)).total_seconds()*1000
                            else:result['resolution_timing']='before_restoration'
                        if not boundary or boundary['orderId'] not in [o['id'] for o in s['orders']] or mode=='transient' and (not triggered or not restored):
                            result['classification']='inconclusive'
                            result['invalid_reason']='Expected boundary/injection/restoration was not observed'
                        infrastructure_ok=True
                        # Persist audited business outcome BEFORE potentially slow Docker teardown.
                        results.append(result);save(trial/'result.json',result)
                    except BaseException as error:
                        result.update(classification='inconclusive',invalid_reason=f'{type(error).__name__}: {error}')
                        if not any(r is result for r in results):results.append(result)
                        save(trial/'result.json',result)
                        raise
                    finally:
                        try:
                            with (trial/'application.log').open('w') as log:
                                subprocess.run(command+['logs','--no-color'],stdout=log,stderr=subprocess.STDOUT,timeout=40)
                        finally:
                            with (trial/'cleanup.log').open('w') as log:
                                # Only our unique trial project. Failed infrastructure keeps volumes for diagnosis.
                                cleanup=command+(['down','--volumes','--remove-orphans','--timeout','30'] if infrastructure_ok else ['stop','--timeout','30'])
                                try:
                                    subprocess.run(cleanup,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=args.cleanup_timeout)
                                    result['cleanup_status']='completed'
                                except (subprocess.SubprocessError,OSError) as error:
                                    result['cleanup_status']='failed'
                                    save(trial/'cleanup-error.json',dict(type=type(error).__name__,message=str(error)))
                                    raise
                                finally:
                                    if result in results:save(trial/'result.json',result)
    except (Exception,KeyboardInterrupt) as error:
        exit_code=130 if isinstance(error,KeyboardInterrupt) else 1
        save(args.output/'runner-error.json',dict(timestamp=utc(),type=type(error).__name__,message=str(error)))
        print('[faults] Infrastructure/runner failure; inspect runner-error.json and trial logs.',file=sys.stderr)
    finally:
        if results:
            fields=list(dict.fromkeys(k for r in results for k in r))
            with (args.output/'results.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(results)
            summaries=[]
            for architecture in architectures:
                for mode in ('control','transient'):
                    rs=[r for r in results if r['architecture']==architecture and r['treatment']==mode]
                    valid=[r for r in rs if r['classification']!='inconclusive']
                    counts={k:sum(r['classification']==k for r in rs) for k in ('completed_valid','aborted_valid','pending','terminal_inconsistent','inconclusive')}
                    summaries.append(dict(architecture=architecture,treatment=mode,attempted=len(rs),protocol_valid=len(valid),
                         **counts,valid_business_fraction=(counts['completed_valid']+counts['aborted_valid'])/len(valid) if valid else ''))
            with (args.output/'summary.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(summaries[0]));writer.writeheader();writer.writerows(summaries)
        save(args.output/'status.json',dict(exitCode=exit_code,prepareOnly=args.prepare_only,completedTrials=len(results),
             expectedTrials=len(architectures)*args.repetitions*2,note='Completion is not business success; interpret classifications and snapshots.'))
    return exit_code


if __name__=='__main__':sys.exit(main())
