#!/usr/bin/env python3
"""Retrospective alignment of recorded pilot artifacts. Reads inputs only."""
import argparse
import bisect
from collections import defaultdict, Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics

ARCHS=('distributed-baseline','tcc','two-phase-commit','orchestrated-saga','choreographed-saga')
HISTORY='historico-60a90-20261102T033615Z'
CHOREO='coreografada-historico-20261102T143129Z'


def epoch(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def num(value):
    return float(str(value).replace(',','.'))


def csvwrite(path,rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n');w.writeheader();w.writerows(rows)


def memory(value):
    m=re.fullmatch(r'([0-9.]+)([A-Za-z]+)',value.strip())
    if not m:raise ValueError('Unrecognized Docker memory unit')
    unit={'B':1,'kB':1000,'KB':1000,'MB':1e6,'GB':1e9,'KiB':1024,'MiB':1024**2,'GiB':1024**3}[m[2]]
    return float(m[1])*unit/1024**2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2])
    p.add_argument('--output',required=True,type=Path)
    args=p.parse_args();repo=args.repo.resolve();out=args.output
    if out.exists() and any(out.iterdir()):p.error('Use a new output directory')
    out.mkdir(parents=True,exist_ok=True)
    audit=[];metrics=[];sources=[];errors=[];weighted=[];cohorts=[]
    for family,arch in [(HISTORY,a) for a in ARCHS]+[(CHOREO,'choreographed-saga')]:
        root=repo/'experiments/datasets/pilot'/family/arch
        dataset=family+'/'+arch
        print('Aligning',dataset,flush=True)
        meta=json.loads((root/'metadata.json').read_text());environment=meta['environment']
        with (root/'analysis/levels.csv').open() as stream:
            rows=[r for r in csv.DictReader(stream) if r['phase']=='benchmark']
        rows.sort(key=lambda r:float(r['start_epoch']))
        starts=[float(r['start_epoch']) for r in rows];ends=[]
        for row in rows:
            stage=row['stage'];summary=json.loads((root/(stage+'.json')).read_text())
            snapshot=json.loads((root/'business'/(stage+'.json')).read_text())
            snapshot_time=epoch(snapshot.get('sampledAt') or snapshot['collectedAt'])
            ends.append(snapshot_time)
            expected_prefix='customer-k6-'+arch+'-'+summary['run_id']+'-'
            counts=snapshot.get('counts');statuses=json.loads(row['http_status_counts'])
            record=dict(dataset=dataset,architecture=arch,stage=stage,rate=int(row['rate']),repetition=int(row['repetition']),
                start_utc=datetime.fromtimestamp(float(row['start_epoch']),timezone.utc).isoformat(),
                load_end_utc=datetime.fromtimestamp(float(row['start_epoch'])+float(row['duration_s']),timezone.utc).isoformat(),
                snapshot_utc=datetime.fromtimestamp(snapshot_time,timezone.utc).isoformat() if counts is not None else '',
                snapshot_lag_after_load_s=snapshot_time-float(row['start_epoch'])-float(row['duration_s']) if counts is not None else '',
                post_load_window_end_utc=datetime.fromtimestamp(snapshot_time,timezone.utc).isoformat(),
                planned_iterations=summary['expected_iterations'],iterations=summary['iterations'],started=int(row['started']),
                responses=int(row['responses']),accepted=int(row['accepted']),failed=int(row['failed']),timeouts=int(row['timeouts']),
                dropped=int(row['dropped']),vus_configured=summary['max_vus'],vus_peak=summary['observed_vus_max'],
                started_rps=float(row['started_rps']),accepted_rps=float(row['accepted_rps']),p95_ms=float(row['p95_ms']),
                p50_ms=float(row['p50_ms']),p99_ms=float(row['p99_ms']),mean_ms=float(row['latency_mean_ms']),
                http_status_counts=json.dumps(statuses,sort_keys=True),
                collection_status=snapshot['collectionStatus'],prefix_match=snapshot['customerPrefix']==expected_prefix,
                summary_run_id_match=summary['run_id']==environment['RUN_ID']+'-'+stage,
                confirmed='' if counts is None else counts.get('confirmed',0),
                pending='' if counts is None else counts.get('pending',0),
                cancelled='' if counts is None else counts.get('cancelled',0),
                confirmed_minus_201='' if counts is None else counts.get('confirmed',0)-int(row['accepted']),
                persisted_orders='' if counts is None else sum(counts.values()),
                snapshot_prefix=snapshot['customerPrefix'],queue_recovery=json.dumps(snapshot.get('queueRecovery',{}),sort_keys=True),
                complete=row['complete'])
            if sum(statuses.values())!=record['responses'] or statuses.get('201',0)!=record['accepted']:
                errors.append(dict(dataset=dataset,stage=stage,issue='HTTP status counts disagree'))
            if record['started']!=summary['started_requests'] or record['iterations']!=record['started']:
                errors.append(dict(dataset=dataset,stage=stage,issue='Starts/iterations disagree'))
            if not record['prefix_match'] or not record['summary_run_id_match']:
                errors.append(dict(dataset=dataset,stage=stage,issue='Cohort prefix/run identity mismatch'))
            audit.append(record)
        samples=defaultdict(list);counter=Counter();sums=defaultdict(lambda:[0,0.]);pg=Counter()

        def locate(t):
            i=bisect.bisect_right(starts,t)-1
            if i<0 or t>ends[i]:return None
            return rows[i]['stage'], 'load' if t<starts[i]+float(rows[i]['duration_s']) else 'post_load'

        def add(t,key,value,line):
            w=locate(t)
            if w is not None:samples[(*w,key)].append((value,line,t))

        def count(t,key,value=1):
            w=locate(t)
            if w is not None:counter[(*w,key)]+=value

        def duration(t,key,stats):
            w=locate(t)
            if w is not None and stats.get('count'):
                s=sums[(*w,key)];s[0]+=stats['count'];s[1]+=stats['count']*(stats.get('avgMs') or 0)

        def lines(name):
            path=root/name;h=hashlib.sha256();n=0
            with path.open('rb') as stream:
                for n,raw in enumerate(stream,1):
                    h.update(raw);yield n,raw.decode(errors='replace')
            sources.append(dict(dataset=dataset,file=name,source_path=str(path.relative_to(repo)),bytes=path.stat().st_size,lines=n,sha256=h.hexdigest()))

        for line,text in lines('application.log'):
            if 'db_diagnostic_sample' not in text:continue
            pos=text.find('{"timestamp"')
            if pos<0:continue
            try:d=json.loads(text[pos:])
            except ValueError:
                errors.append(dict(dataset=dataset,stage='',issue=f'application.log:{line} invalid diagnostic JSON'));continue
            if d.get('type')!='db_diagnostic_sample':continue
            t=epoch(d['timestamp'])
            for key in ['inUse','waiting','total','idle']:add(t,'pool.'+key,d['pool'][key],line)
            count(t,'pool.samples');count(t,'pool.full_samples',int(d['pool']['inUse']>=int(environment['DB_POOL_MAX'])))
            count(t,'acquire.failures',d['connectionAcquire']['failures'])
            for key in ['connectionAcquire','connectionHold','queryDuration']:
                duration(t,key,d[key])
                if d[key]['maxMs'] is not None:add(t,key+'.window_max_ms',d[key]['maxMs'],line)
            for key in ['COMMIT','UPDATE order','INSERT order','INSERT order_item','SELECT','START TRANSACTION']:
                s=d.get('queryDurations',{}).get(key,{})
                duration(t,'sql.'+key,s)
                if s.get('maxMs') is not None:add(t,'sql.'+key+'.window_max_ms',s['maxMs'],line)
            add(t,'event_loop.p99_ms',d['eventLoop']['p99Ms'],line)

        for line,text in lines('postgres-activity.jsonl'):
            d=json.loads(text);t=epoch(d['sampled_at']);w=locate(t)
            if w is None:continue
            count(t,'pg.snapshots')
            for s in d['data']:
                state=s['state'];event=s['wait_event_type'] or 'none';wait=s['wait_event'] or 'none'
                pg[(*w,state,event,wait)]+=1
                if s['blocking_pids']:count(t,'pg.blocked_session_samples')
                if state=='active':count(t,'pg.active_session_samples')

        for line,text in lines('containers.jsonl'):
            d=json.loads(text);t=epoch(d['timestamp']);c=d['container'];name=c['Name']
            # Preserve individual identities; Docker memory is not summed RSS.
            try:add(t,'container.'+name+'.cpu_pct',float(c['CPUPerc'].rstrip('%')),line)
            except ValueError:count(t,'container.'+name+'.cpu_missing')
            try:add(t,'container.'+name+'.memory_mib',memory(c['MemUsage'].split('/')[0]),line)
            except (ValueError,KeyError):count(t,'container.'+name+'.memory_missing')

        for line,text in lines('process-pidstat.log'):
            f=text.split()
            if len(f)!=19 or not f[0].isdigit():continue
            t=float(f[0]);command=f[-1]
            if command in ('k6','dockerd','containerd','node','postgres'):
                add(t,'pidstat.'+command+'.individual_cpu_pct',num(f[7]),line)
                add(t,'pidstat.'+command+'.individual_rss_mib',num(f[12])/1024,line)
                if command=='k6':add(t,'pidstat.k6.pid',int(f[2]),line)

        for line,text in lines('host-vmstat.log'):
            f=text.split()
            if len(f)<20 or not f[-2].startswith('2026-'):continue
            t=epoch(f[-2]+'T'+f[-1]+'+00:00')
            for key,index in [('run_queue',0),('blocked',1),('swap_kib',2),('free_kib',3),('swap_in_kib_s',6),('swap_out_kib_s',7),('cpu_user_pct',12),('cpu_system_pct',13),('cpu_idle_pct',14),('cpu_iowait_pct',15)]:
                add(t,'host.'+key,num(f[index]),line)

        t=None;headers=[];cpu_next=False
        for line,text in lines('iostat.log'):
            f=text.split()
            if not f:continue
            if f[0].startswith('2026-'):t=epoch(f[0])
            elif f[0]=='avg-cpu:':cpu_next=True
            elif cpu_next:
                cpu_next=False
            elif f[0]=='Device':headers=f[1:]
            elif f[0] in ('sdb','dm-0') and t is not None:
                values=dict(zip(headers,map(num,f[1:])))
                for key in ['w_await','r_await','f_await','aqu-sz','%util','wkB/s','rkB/s','w/s','f/s']:
                    if key in values:add(t,'disk.'+f[0]+'.'+key,values[key],line)
        for (stage,window,key),v in samples.items():
            maximum=max(v,key=lambda x:x[0]);minimum=min(v,key=lambda x:x[0]);values=[x[0] for x in v]
            metrics.append(dict(dataset=dataset,architecture=arch,stage=stage,window=window,metric=key,n=len(v),mean=statistics.mean(values),median=statistics.median(values),minimum=minimum[0],maximum=maximum[0],max_source_line=maximum[1],max_utc=datetime.fromtimestamp(maximum[2],timezone.utc).isoformat()))
        for (stage,window,key),value in counter.items():
            metrics.append(dict(dataset=dataset,architecture=arch,stage=stage,window=window,metric=key,n='',mean='',median='',minimum='',maximum='',total=value,max_source_line='',max_utc=''))
        for (stage,window,key),(n,total) in sums.items():
            weighted.append(dict(dataset=dataset,architecture=arch,stage=stage,window=window,metric=key,operations=n,weighted_mean_ms=total/n,sum_ms_approx=total))
        for (stage,window,state,kind,event),value in pg.items():
            cohorts.append(dict(dataset=dataset,architecture=arch,stage=stage,window=window,state=state,wait_event_type=kind,wait_event=event,session_samples=value))

    csvwrite(out/'audit.csv',audit);csvwrite(out/'operational.csv',metrics)
    csvwrite(out/'durations.csv',weighted);csvwrite(out/'postgres_waits.csv',cohorts);csvwrite(out/'sources.csv',sources)
    (out/'validation.json').write_text(json.dumps(dict(benchmark_stages=len(audit),sources=len(sources),issues=errors,
        source_hashes='Full selected log files hashed during streaming',
        analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        semantics='Unweighted sample statistics except durations weighted by completed operation counts. Missing metrics never zero-filled. PIDstat individual process samples, not group sums. All clocks normalized to UTC.'),indent=2)+'\n')
    worst=[]
    groups=defaultdict(list)
    for r in audit:groups[(r['dataset'],r['rate'])].append(r)
    for (dataset,rate),rs in groups.items():
        for criterion,row in [('highest_p95',max(rs,key=lambda r:r['p95_ms'])),('lowest_201_rps',min(rs,key=lambda r:r['accepted_rps']))]:
            worst.append(dict(criterion=criterion,**row))
    csvwrite(out/'worst_repetitions.csv',worst)
    print(f'Validated {len(audit)} stages; issues={len(errors)}; outputs={out}',flush=True)


if __name__=='__main__':main()
