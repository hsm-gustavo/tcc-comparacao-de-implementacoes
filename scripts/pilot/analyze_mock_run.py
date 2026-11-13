#!/usr/bin/env python3
"""Retrospective, read-only input analysis of mock pilot artifacts (no experiments)."""
import argparse,bisect,csv,hashlib,json,re,statistics
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path

ARCHS=['distributed-baseline','tcc','two-phase-commit','orchestrated-saga','choreographed-saga']
def write(path,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
def epoch(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def analyze(root,out):
    out.mkdir(parents=True,exist_ok=False)
    trials=[];aggregated=[];resources=[];endpoints=[];evidence=[];hashes=[];validation=[]
    for arch in ARCHS:
        p=root/arch
        rows=list(csv.DictReader((p/'analysis/levels.csv').open()))
        rows=[r for r in rows if r['phase']=='benchmark']
        rows=sorted(rows,key=lambda r:float(r['start_epoch']));starts=[float(r['start_epoch']) for r in rows]
        stats=defaultdict(list);http=defaultdict(list);statuses=Counter()
        def stage(t):
            i=bisect.bisect_right(starts,t)-1
            return rows[i]['stage'] if i>=0 and t<starts[i]+float(rows[i]['duration_s']) else None
        for r in rows:
            b=json.loads((p/'business'/(r['stage']+'.json')).read_text())
            summary=json.loads((p/(r['stage']+'.json')).read_text())
            # pilot JSON stores exact per-stage results; keep original fields as provenance.
            counts=b.get('counts',{})
            trials.append({'architecture':arch,**r,'iterations':summary['iterations'],
                'observed_vus_max':summary['observed_vus_max'],'expected_iterations':summary['expected_iterations'],
                'confirmed':counts.get('confirmed',0),
                'pending':counts.get('pending',0),'cancelled':counts.get('cancelled',0),
                'snapshot_utc':b.get('sampledAt'),'table_orders':b.get('tables',{}).get('Order'),
                'source':str(p/r['stage'])})
        for rate in sorted({int(r['rate']) for r in rows}):
            rr=[r for r in trials if r['architecture']==arch and int(r['rate'])==rate]
            aggregated.append({'architecture':arch,'rate':rate,'repetitions':len(rr),
                'started':sum(int(r['started']) for r in rr),'accepted':sum(int(r['accepted']) for r in rr),
                'failed':sum(int(r['failed']) for r in rr),'timeouts':sum(int(r['timeouts']) for r in rr),
                'dropped':sum(int(r['dropped']) for r in rr),'confirmed':sum(r['confirmed'] for r in rr),
                'pending':sum(r['pending'] for r in rr),'cancelled':sum(r['cancelled'] for r in rr),
                'median_accepted_rps':statistics.median(float(r['accepted_rps']) for r in rr),
                'median_successful_p95_ms':statistics.median(float(r['successful_p95_ms']) for r in rr),
                'median_p95_ms':statistics.median(float(r['p95_ms']) for r in rr)})
        for no,line in enumerate((p/'containers.jsonl').open(),1):
            x=json.loads(line);s=stage(epoch(x['timestamp']))
            if not s:continue
            c=x['container'];cpu=c['CPUPerc'].rstrip('%')
            if cpu=='--':continue
            # Keep complete names; no mixing processes or architectures.
            stats[(s,c['Name'],'cpu_pct')].append((float(cpu),no,x['timestamp']))
            m=re.match(r'([\d.]+)([KMG]?i?B)',c['MemUsage'])
            if m:
                factor={'B':1/1048576,'KiB':1/1024,'MiB':1,'GiB':1024,'kB':1000/1048576,'MB':1000000/1048576,'GB':1000000000/1048576}
                stats[(s,c['Name'],'memory_mib')].append((float(m[1])*factor[m[2]],no,x['timestamp']))
        for no,line in enumerate((p/'host-vmstat.log').open(),1):
            f=line.split()
            if len(f)<19 or not f[0].isdigit():continue
            try:t=epoch(f[-2]+'T'+f[-1]+'+00:00')
            except ValueError:continue
            s=stage(t)
            if s:
                for name,index in [('idle_pct',14),('iowait_pct',15),('runqueue',0),('swapin_kib_s',6),('swapout_kib_s',7)]:stats[(s,'host',name)].append((float(f[index]),no,f[-2]+'T'+f[-1]+'Z'))
        for no,line in enumerate((p/'process-pidstat.log').open(),1):
            f=line.split()
            if len(f)!=19 or not f[0].isdigit() or f[-1]!='k6':continue
            s=stage(float(f[0]));stamp=datetime.fromtimestamp(float(f[0]),timezone.utc).isoformat()
            if s:
                stats[(s,'k6','cpu_pct')].append((float(f[7].replace(',','.')),no,stamp))
                stats[(s,'k6','rss_mib')].append((float(f[12].replace(',','.'))/1024,no,stamp))
        t=None
        for no,line in enumerate((p/'iostat.log').open(),1):
            f=line.split()
            if not f:continue
            if re.match(r'^\d{4}-\d\d-\d\dT',f[0]):
                try:t=epoch(f[0])
                except ValueError:t=None
            if t is None or f[0] not in ['sdb','dm-0'] or len(f)<23:continue
            s=stage(t)
            if s:
                for name,index in [('write_await_ms',11),('util_pct',22)]:stats[(s,f[0],name)].append((float(f[index]),no,datetime.fromtimestamp(t,timezone.utc).isoformat()))
        # Stream large application logs; never load them all into memory.
        pattern=re.compile(r'"(?:POST|GET|PATCH|PUT|DELETE) ([^ ]+) HTTP/[^\"]+" status=(\d+) request_time=([\d.]+)')
        first_errors=0
        for no,line in enumerate((p/'application.log').open(errors='replace'),1):
            m=pattern.search(line)
            if m:
                stamp=line.split('|',1)[1].strip().split()[0]
                try:s=stage(epoch(stamp))
                except ValueError:continue
                if s:
                    http[(s,m[1])].append(float(m[3])*1000);statuses[(s,m[1],m[2])]+=1
            if ('timeout of 5000ms' in line or 'upstream timed out' in line) and first_errors<5:
                evidence.append({'architecture':arch,'source':str(p/'application.log'),'line':no,'text':re.sub(r'\x1b\[[0-9;]*m','',line.strip())});first_errors+=1
        for (s,name,metric),values in stats.items():
            peak=max(values)
            resources.append({'architecture':arch,'stage':s,'resource':name,'metric':metric,'samples':len(values),
                'mean':statistics.mean(v[0] for v in values),'min':min(v[0] for v in values),'max':peak[0],
                'max_source_line':peak[1],'max_utc':peak[2]})
        for (s,url),values in http.items():
            endpoints.append({'architecture':arch,'stage':s,'endpoint':url,'responses_during_load':len(values),
                'mean_request_ms':statistics.mean(values),'max_request_ms':max(values),
                'statuses':json.dumps({code:n for (ss,uu,code),n in statuses.items() if ss==s and uu==url})})
        recorded=json.loads((p/'source-sha256.json').read_text())
        for name in ['environment/mock/memory-database.cjs','environment/mock/register.cjs']:
            current=hashlib.sha256(Path(name).read_bytes()).hexdigest()
            validation.append({'architecture':arch,'source':name,'recorded_sha256':recorded.get(name),'current_sha256':current,'match':current==recorded.get(name)})
        print('Analyzed',arch,flush=True)
    write(out/'trials.csv',trials);write(out/'by_rate.csv',aggregated);write(out/'resources.csv',resources)
    write(out/'endpoints.csv',endpoints);write(out/'error_examples.csv',evidence);write(out/'source_validation.csv',validation)
    (out/'methodology.json').write_text(json.dumps({'input':str(root),'windows':'[scenario start, start + duration); resource samples are not operation timings',
        'http':'NGINX completion timestamps, only during load; includes internal and external endpoints separately',
        'business':'per-cohort snapshots at distinct times; no end-to-end latency or participant invariant inferred',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('run',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();analyze(a.run,a.output)
