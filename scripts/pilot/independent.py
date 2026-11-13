#!/usr/bin/env python3
"""Drive existing run.sh once per trial, with fresh, exclusively owned Docker volumes."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
ARCHS = ('distributed-baseline','tcc','two-phase-commit','orchestrated-saga','choreographed-saga')


def command(args, **kwargs):
    kwargs.setdefault('timeout',60)
    return subprocess.run(args, check=True, text=True, **kwargs)


def owned_project(architecture, run_id):
    return 'pilot-' + architecture + '-' + hashlib.sha256(run_id.encode()).hexdigest()[:10]


def assert_unused(project):
    # Include stopped containers and unused volumes/networks, not just running ones.
    for resource, verb in [('container','ls'),('volume','ls'),('network','ls')]:
        result = command(['docker', resource, verb]+(['-a'] if resource=='container' else [])+['-q', '--filter', 'label=com.docker.compose.project='+project], capture_output=True)
        if result.stdout.strip():
            raise RuntimeError('Refusing existing Docker project: '+project)


def cleanup(trial, architecture, run_id):
    model_path = trial/'runtime/compose.json'
    status = json.loads((trial/'status.json').read_text())
    model = json.loads(model_path.read_text())
    if status.get('containerStop') != 'stopped' or model.get('name') != owned_project(architecture, run_id):
        raise RuntimeError('Shutdown/ownership unconfirmed; preserving resources: '+str(trial))
    if model.get('networks') or any(v.get('external') or v.get('name') for v in model['volumes'].values()):
        raise RuntimeError('Refusing cleanup of external/named volume or custom network model')
    # run.sh saved logs/snapshots and exported tmpfs before reaching this point.
    with (trial/'logs/volume-cleanup.log').open('w') as log:
        command(['docker','compose','-f',str(model_path),'down','--volumes','--timeout','60'], stdout=log, stderr=subprocess.STDOUT, timeout=180)
    for resource in ['container','volume','network']:
        result = command(['docker',resource,'ls']+(['-a'] if resource=='container' else [])+['-q','--filter','label=com.docker.compose.project='+model['name']],capture_output=True)
        if result.stdout.strip():raise RuntimeError('Project resources remain after cleanup: '+model['name'])
    status['volumeCleanup'] = 'removed_only_trial_project'
    (trial/'status.json').write_text(json.dumps(status,indent=2)+'\n')


def link_trial(architecture_root, trial, stage):
    # No copies of giant k6 CSVs: canonical views point into the preserved trial folder.
    if not (trial/(stage+'.json')).exists():return
    for suffix in ['.json','.csv','.exit-code.txt']:
        source = trial/(stage+suffix)
        if source.exists():
            (architecture_root/source.name).symlink_to(source.relative_to(architecture_root))
    source = trial/'business'/(stage+'.json')
    if source.exists():
        (architecture_root/'business'/source.name).symlink_to(Path('..')/source.relative_to(architecture_root))


def analyze(output, roots, plots):
    python = os.environ.get('PLOT_PYTHON', str(ROOT/'.venv/bin/python'))
    if not Path(python).is_file():python=sys.executable
    available=[]
    for root in roots:
        if not list(root.glob('rate*_run*.json')):continue
        with (root/'analysis.log').open('w') as log:
            command([python,str(ROOT/'experiments/analyze_pilot.py'),str(root)]+([] if plots else ['--no-plots']),stdout=log,stderr=subprocess.STDOUT,timeout=3600)
        available.append(str(root))
    if available:
        with (output/'comparison.log').open('w') as log:
            command([python,str(ROOT/'experiments/compare_pilots.py'),'--output',str(output/'comparison')]+([] if plots else ['--no-plots'])+available,stdout=log,stderr=subprocess.STDOUT,timeout=3600)


def run(output):
    if output.exists():raise ValueError('Output already exists')
    suite=os.environ['SUITE_ID']
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]{0,39}',suite):raise ValueError('Invalid SUITE_ID')
    if output != ROOT/'experiments/datasets/pilot'/suite:raise ValueError('Output must be the new suite directory')
    architectures=os.environ.get('ARCHITECTURES',','.join(ARCHS)).split(',')
    if len(set(architectures))!=len(architectures) or any(a not in ARCHS for a in architectures):raise ValueError('Invalid ARCHITECTURES')
    rates=[int(x) for x in os.environ.get('RATES','1,2,5,10,15,20,30,40,50,60,70,80,90,100').split(',')]
    if not rates or sorted(set(rates))!=rates or min(rates)<1 or max(rates)>100000:raise ValueError('Invalid RATES')
    repetitions=int(os.environ.get('REPETITIONS','8'))
    if not 1<=repetitions<=9999:raise ValueError('Invalid REPETITIONS')
    if os.environ.get('PRELOAD_ORDERS','0')!='0' or os.environ.get('DB_MODE','postgres')!='postgres':raise ValueError('Independent trials require PostgreSQL and PRELOAD_ORDERS=0')
    if os.environ.get('BASELINE_RUN'):raise ValueError('Do not mix cumulative BASELINE_RUN into independent trials')
    running=command(['docker','ps','--format','{{.Label "com.docker.compose.project"}}'],capture_output=True).stdout.splitlines()
    if any(p.startswith('pilot-') or p in ['distributed-lab','distributed-lab-base','distributed-lab-mq'] for p in running):raise RuntimeError('Stop active experimental stacks before starting')
    output.mkdir(parents=True)
    sources={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'scripts/pilot/independent.py',ROOT/'run-independent-suite.sh',ROOT/'run.sh'] if p.is_file()}
    (output/'metadata.json').write_text(json.dumps(dict(protocol='independent-fresh-volumes',architectures=architectures,rates=rates,repetitions=repetitions,warmup='before_every_trial',preload=0,volumePolicy='remove_only_new_trial_project_after_observation',sourceHashes=sources,startedAt=datetime.now(timezone.utc).isoformat()),indent=2)+'\n')
    active=None;code=0;completed=[];roots=[];trial_rows=[]
    previous_signals={s:signal.getsignal(s) for s in [signal.SIGTERM,signal.SIGINT]}
    cancelling=False
    def interrupted(signum, frame):
        nonlocal cancelling
        if not cancelling:
            cancelling=True
            raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    try:
        for arch in architectures:
            arch_root=output/arch;arch_root.mkdir();(arch_root/'trials').mkdir();(arch_root/'business').mkdir();roots.append(arch_root)
            built=False
            image='independent-'+hashlib.sha256((suite+arch).encode()).hexdigest()[:16]
            for rate in rates:
                for rep in range(1,repetitions+1):
                    stage=f'rate{rate}_run{rep}';run_id=f'{suite}-{arch}-{stage}';trial=arch_root/'trials'/stage
                    assert_unused(owned_project(arch,run_id))
                    env=dict(os.environ,ARCHITECTURE=arch,PILOT_ISOLATED='1',PILOT_SUITE_ID=suite,
                        PILOT_TRIAL_ID=stage,PILOT_REPETITION_START=str(rep),RUN_ID=run_id,RATES=str(rate),REPETITIONS='1',
                        PRELOAD_ORDERS='0',DB_MODE='postgres',PILOT_IMAGE_PREFIX=image,PILOT_SKIP_BUILD='1' if built else '0',AUTO_PLOT='0',
                        PRE_ALLOCATED_VUS=os.environ.get('PRE_ALLOCATED_VUS',str(max(rates)*25)),MAX_VUS=os.environ.get('MAX_VUS',os.environ.get('PRE_ALLOCATED_VUS',str(max(rates)*25))))
                    print(f'[independent] {arch} {stage}: fresh databases/broker, seed, warmup, load, observation',flush=True)
                    with (arch_root/(stage+'.log')).open('w') as log:
                        active=subprocess.Popen(['bash',str(ROOT/'run.sh')],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                        began=time.monotonic()
                        while True:
                            try:result=active.wait(timeout=15);break
                            except subprocess.TimeoutExpired:
                                phases=trial/'phases.jsonl'
                                phase=json.loads(phases.read_text().splitlines()[-1])['phase'] if phases.exists() and phases.stat().st_size else 'initialization'
                                print(f'[independent] {arch} {stage}: elapsed={int(time.monotonic()-began)}s; phase={phase}; log={arch_root/(stage+".log")}',flush=True)
                        active=None
                    if (trial/'metadata.json').exists() and not (arch_root/'metadata.json').exists():
                        meta=json.loads((trial/'metadata.json').read_text());meta.update(experimentProtocol='independent-fresh-volumes',datasetPolicy='Fresh PostgreSQL and broker volumes, seed and warmup before each measured repetition.',runIdSemantics='Group ID; actual RUN_ID and source hashes in trials/<stage>/')
                        meta['environment'].update(RUN_ID=f'{suite}-{arch}',RATES=','.join(map(str,rates)),REPETITIONS=str(repetitions))
                        for key in ['PILOT_TRIAL_ID','PILOT_REPETITION_START','PILOT_SKIP_BUILD','COMPOSE_PROJECT_NAME']:
                            meta['environment'].pop(key,None)
                        (arch_root/'metadata.json').write_text(json.dumps(meta,indent=2)+'\n')
                    built=built or (trial/'build-completed.json').exists()
                    link_trial(arch_root,trial,stage)
                    trial_rows.append(dict(architecture=arch,rate=rate,repetition=rep,stage=stage,run_id=run_id,trial_path=str(trial.relative_to(output)),exit_code=result))
                    cleanup(trial,arch,run_id)
                    if result:raise RuntimeError(f'Trial failed ({result}); see {trial}. Stopping independent suite.')
                    completed.append((arch,stage))
            (arch_root/'status.json').write_text(json.dumps(dict(exitCode=0,phase='completed',experimentProtocol='independent-fresh-volumes',containerStop='stopped'))+'\n')
    except KeyboardInterrupt:
        code=130
    except Exception as error:
        code=1;print('[independent] '+str(error),file=sys.stderr,flush=True)
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        if active is not None:
            os.killpg(active.pid,signal.SIGTERM)
            try:
                active.wait(timeout=180)
                if (trial/'status.json').exists():
                    link_trial(arch_root,trial,stage)
                    cleanup(trial,arch,run_id)
            except Exception as error:print('[independent] Interrupted cleanup unconfirmed; resources preserved: '+str(error),file=sys.stderr)
        if trial_rows:
            with (output/'trials.csv').open('w',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=list(trial_rows[0]));writer.writeheader();writer.writerows(trial_rows)
        for root in roots:
            if not (root/'status.json').exists():
                (root/'status.json').write_text(json.dumps(dict(exitCode=code or 1,phase='partial',experimentProtocol='independent-fresh-volumes'))+'\n')
        try:analyze(output,roots,os.environ.get('AUTO_PLOT','1')=='1')
        except Exception as error:code=code or 1;print('[independent] Analysis failed: '+str(error),file=sys.stderr)
        (output/'status.json').write_text(json.dumps(dict(exitCode=code,protocol='independent-fresh-volumes',completedTrials=len(completed),plannedTrials=len(architectures)*len(rates)*repetitions))+'\n')
        print(f'[independent] Results: {output}; complete trials={len(completed)}; exit={code}',flush=True)
        for signum,handler in previous_signals.items():signal.signal(signum,handler)
    return code

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:sys.exit(run(args.output.resolve()))
    except (ValueError,RuntimeError,subprocess.SubprocessError) as error:parser.exit(1,str(error)+'\n')
