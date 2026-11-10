#!/usr/bin/env python3
"""Validate an existing fault campaign and export descriptive statistics; no services."""
import argparse
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, rows):
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n')
        writer.writeheader();writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('Choose a new output directory')
    meta=json.loads((args.run/'metadata.json').read_text())
    changed=[name for name,value in meta['sourceSha256'].items() if not (ROOT/name).exists() or digest(ROOT/name)!=value]
    if changed:raise RuntimeError('Sources differ from campaign; cannot validate with current classifier: '+', '.join(changed))
    spec=importlib.util.spec_from_file_location('validated_fault_runner',Path(__file__).with_name('run.py'))
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    with (args.run/'results.csv').open() as stream:rows=list(csv.DictReader(stream))
    checked=[];hashes={};groups={}
    methods={'distributed-baseline':'processPayment','orchestrated-saga':'processPayment',
             'tcc':'preparePayment','two-phase-commit':'preparePayment','choreographed-saga':'processSagaPayment'}
    delta=lambda a,b:(datetime.fromisoformat(a)-datetime.fromisoformat(b)).total_seconds()*1000
    seen=set()
    for row in rows:
        key=(row['architecture'],row['treatment'],row['repetition'])
        if key in seen:raise ValueError('Duplicated trial')
        seen.add(key)
        directory=args.run/row['trial']
        load=lambda name:json.loads((directory/name).read_text())
        state=load('final-state.json');events=load('injection-events.json');before=load('before.json')
        assert runner.classify(state)==row['classification']==state['classification']
        assert before['orders']==[] and len(before['products'])==1 and before['products'][0]['stockQuantity']==meta['initialStock']
        assert not any(q['ready']+q['unacknowledged'] for q in before['queues'])
        assert load('warmup-http.json')['status']==201
        boundary=[e for e in events if e['event']=='boundary_reached']
        assert len(boundary)==1 and boundary[0]['method']==methods[row['architecture']]
        order=state['orders'][0]['id']
        assert all(e.get('orderId')==order for e in events)
        start=[e for e in events if e['event']=='fault_started'];restore=[e for e in events if e['event']=='fault_restored']
        rejected=[e for e in events if e['event']=='payment_rejected']
        if row['treatment']=='transient':
            assert len(start)==len(restore)==1 and rejected
        else:assert not start and not restore and not rejected
        result=dict(architecture=row['architecture'],treatment=row['treatment'],repetition=int(row['repetition']),
                    classification=row['classification'],http_status=int(row['http_status']),http_ms=float(row['http_ms']),
                    fault_window_ms=delta(restore[0]['timestamp'],start[0]['timestamp']) if restore else '',
                    rejection_count=len(rejected),recovery_observed_ms=float(row['recovery_ms']) if row['recovery_ms'] else '',
                    post_restoration_observation_ms=delta(state['sampledAt'],restore[0]['timestamp']) if restore else '',
                    stock_final=state['products'][0]['stockQuantity'],order_status=state['orders'][0]['status'],
                    payment_statuses=','.join(p['status'] for p in state['payments']),
                    shipment_statuses=','.join(p['status'] for p in state['shipments']),
                    reservation_statuses=','.join(p['status'] for p in state['reservations']),
                    queues_final_messages=sum(q['ready']+q['unacknowledged'] for q in state['queues']),
                    source_trial=row['trial'])
        checked.append(result);groups.setdefault(key[:2],[]).append(result)
        for name in ['final-state.json','before.json','http.json','injection-events.json','observations.jsonl','result.json']:
            file=directory/name;hashes[str(file.relative_to(args.run))]=digest(file)
    expected={(a,m,str(r)) for a in meta['architectures'] for m in ('control','transient') for r in range(1,meta['repetitions']+1)}
    assert seen==expected, 'Campaign is incomplete'
    statistics_rows=[]
    for (arch,mode),rs in groups.items():
        for metric in ['http_ms','fault_window_ms','recovery_observed_ms','post_restoration_observation_ms']:
            values=[r[metric] for r in rs if r[metric]!='']
            statistics_rows.append(dict(architecture=arch,treatment=mode,metric=metric,n=len(values),
                mean=statistics.mean(values) if values else '',median=statistics.median(values) if values else '',
                minimum=min(values) if values else '',maximum=max(values) if values else '',
                sample_stddev=statistics.stdev(values) if len(values)>1 else ''))
    args.output.mkdir(parents=True)
    write(args.output/'trials_checked.csv',checked);write(args.output/'timing_statistics.csv',statistics_rows)
    for name in ['metadata.json','results.csv','summary.csv','status.json']:hashes[name]=digest(args.run/name)
    (args.output/'validation.json').write_text(json.dumps(dict(run=str(args.run),checked_trials=len(checked),
        source_hashes_match=True,input_sha256=hashes,analyzer_sha256=digest(Path(__file__))),indent=2)+'\n')
    print(f'Validated {len(checked)} trials; statistics saved in {args.output}')


if __name__=='__main__':main()
