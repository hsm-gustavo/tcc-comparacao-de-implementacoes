#!/usr/bin/env python3
"""Refresh presentation-only comparison PNGs and their package integrity metadata."""
import argparse,csv,hashlib,json,shutil,zipfile
from datetime import datetime,timezone
from pathlib import Path


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def refresh(repo,package):
    manifest=package/'manifest.csv'
    with manifest.open(newline='') as stream:rows=list(csv.DictReader(stream))
    changes=[]
    prefixes=('experiments/datasets/pilot/mock-1a100-20261110T221441Z/comparison/',
              'experiments/datasets/pilot/comparacao-1a100-atualizada-20261110/')
    for row in rows:
        source=row['source_path']
        if not (source=='experiments/compare_pilots.py' or
                source.startswith(prefixes) and source.endswith('.png')):continue
        target=package/row['copied_path'];old=sha(target)
        shutil.copy2(repo/source,target)
        row.update(sha256=sha(target),source_sha256=sha(repo/source),transformation='copy2',
                   description='Presentation refresh: architecture display labels; metrics unchanged')
        changes.append(dict(path=row['copied_path'],before_sha256=old,after_sha256=sha(target)))
    if len([x for x in changes if x['path'].endswith('.png')])!=4:
        raise ValueError('Expected four comparison PNGs per package')
    update=package/'data/plot-refresh.json'
    update.write_text(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),
        purpose='Architecture labels aligned with thesis; numeric metrics unchanged',
        labels={'distributed-baseline':'Arquitetura de referência','tcc':'Try-Confirm/Cancel',
                'two-phase-commit':'Two-Phase Commit (2PC)','orchestrated-saga':'Saga orquestrada',
                'choreographed-saga':'Saga coreografada'},changes=changes),indent=2,ensure_ascii=False)+'\n')
    entry=dict(source_path='generated:data/plot-refresh.json',copied_path='data/plot-refresh.json',
        category='presentation_update',description='Graph presentation update; no metric recomputation',
        sha256=sha(update),source_sha256='',transformation='generated')
    rows=[r for r in rows if r['copied_path']!='data/plot-refresh.json']+[entry]
    readme=package/'README.md'
    if 'data/plot-refresh.json' not in readme.read_text():
        with readme.open('a') as stream:stream.write('\n## Atualização das legendas\n\nOs gráficos usam os nomes das arquiteturas adotados no texto do TCC.\nAs métricas não foram alteradas. Veja [registro da atualização](data/plot-refresh.json).\n')
    for row in rows:
        if row['copied_path']=='README.md':row['sha256']=sha(readme)
    with manifest.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    for row in rows:assert sha(package/row['copied_path'])==row['sha256'],row['copied_path']
    archive=package.with_suffix('.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted(package.rglob('*')):
            if path.is_file():z.write(path,Path(package.name)/path.relative_to(package))
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n')
    print('Updated',package)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('packages',type=Path,nargs='+')
    args=parser.parse_args()
    for package in args.packages:refresh(args.repo.resolve(),package.resolve())
