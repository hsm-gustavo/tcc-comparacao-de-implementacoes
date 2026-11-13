#!/usr/bin/env python3
"""Package existing evidence only. Never starts containers, runs load or moves inputs."""
import argparse,csv,hashlib,json,re,shutil,statistics,subprocess,zipfile
from datetime import datetime,timezone
from pathlib import Path

ARCHS=['distributed-baseline','tcc','two-phase-commit','orchestrated-saga','choreographed-saga']
RATES=[1,2,5,10,15,20,30,40,50,60,70,80,90,100]
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
def table(p):
 with p.open(newline='') as f:return list(csv.DictReader(f))
def text_safe(s):
 s=re.sub(r'(?i)([a-z]+://)[^\s/"\']+:[^\s/@"\']+@',r'\1REDACTED:REDACTED@',s)
 s=re.sub(r'\bsqu_[A-Za-z0-9]+\b','REDACTED',s)
 s=re.sub(r'/home/[^/\s"\']+/', '/home/REDACTED/',s)
 return re.sub(r'(?im)(["\']?(?:password|username|POSTGRES_PASSWORD|POSTGRES_USER|RABBITMQ_DEFAULT_PASS|RABBITMQ_DEFAULT_USER)["\']?\s*[:=]\s*)(["\']?)[^\s,}\n]+',r'\1"REDACTED"',s)
def clean_json(x):
 if isinstance(x,dict):return {k:('REDACTED' if re.search(r'(?i)(password|passwd|secret|token|username|POSTGRES_USER|RABBITMQ_DEFAULT_PASS|RABBITMQ_DEFAULT_USER)$',k) else clean_json(v)) for k,v in x.items()}
 if isinstance(x,list):return [clean_json(v) for v in x]
 if isinstance(x,str):
  return re.sub(r'/home/[^/\s"\']+/', '/home/REDACTED/',re.sub(r'(?i)([a-z]+://)[^\s/"\']+:[^\s/@"\']+@',r'\1REDACTED:REDACTED@',x))
 return x

def build(repo,out,kind):
 repo,out=repo.resolve(),out.resolve()
 if out.exists() or out.with_suffix('.zip').exists():raise ValueError('Refusing existing output or ZIP')
 out.mkdir(parents=True)
 manifest=[];copied={};omitted=[]
 def record(source,dest,category,transform,source_hash=''):
  manifest.append(dict(source_path=source,copied_path=str(dest.relative_to(out)),category=category,description=transform,sha256=sha(dest),source_sha256=source_hash,transformation=transform))
 def copy(src,dest,category='source_evidence'):
  src=src.resolve()
  if src in copied:return copied[src]
  dest=out/dest;dest.parent.mkdir(parents=True,exist_ok=True)
  original=sha(src)
  if src.suffix.lower()=='.png':shutil.copy2(src,dest)
  elif src.suffix.lower()=='.json':
   original_text=src.read_text();obj=json.loads(original_text);sanitized=clean_json(obj)
   if sanitized==obj:shutil.copy2(src,dest)
   else:dest.write_text(json.dumps(sanitized,indent=2)+'\n')
  else:
   original_text=src.read_text();clean=text_safe(original_text)
   if clean==original_text:shutil.copy2(src,dest)
   else:dest.write_text(clean)
  transform='copy2' if original==sha(dest) else 'sanitized_copy'
  record(str(src.relative_to(repo)),dest,category,transform,original)
  copied[src]=str(dest.relative_to(out));return copied[src]
 def generated(name,content,category='documentation'):
  dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(content)
  record('generated:'+name,dest,category,'generated')
 def csvout(name,rows):
  keys=list(dict.fromkeys(k for r in rows for k in r));dest=out/name;dest.parent.mkdir(parents=True,exist_ok=True)
  with dest.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
  record('generated:'+name,dest,'derived','generated')
 root=repo/'experiments/datasets/pilot'
 target=root/('mock-1a100-20261110T221441Z' if kind=='mock' else 'comparacao-1a100-atualizada-20261110')
 comparison=target/'comparison' if kind=='mock' else target
 for f in sorted(comparison.iterdir()):
  if f.is_file() and f.suffix in ['.csv','.json','.png','.md']:copy(f,'data/comparison/'+f.name)
 chosen={}
 if kind=='mock':
  for arch in ARCHS:
   chosen[(target/arch).resolve()]=[(f'rate{rate}_run{i}',rate,i) for rate in RATES for i in range(1,9)]
  generated('data/comparison/selection.json',json.dumps([dict(architecture=arch,dataset=target.name,rates=RATES,repetitions=8) for arch in ARCHS],indent=2)+'\n','selection')
 else:
  for r in table(target/'runs.csv'):
   if r['complete']!='True':continue
   source=Path(r['source_dataset'])
   if not source.exists():raise FileNotFoundError(source)
   chosen.setdefault(source.resolve(),[]).append((r['stage'],int(r['rate']),int(r['repetition'])))
 inventory=[];runs=[];outcomes=[];checks=[];mapping={};allkeys=set()
 for source,stages in chosen.items():
  meta=json.loads((source/'metadata.json').read_text());arch=meta['architecture'];env=meta['environment']
  if arch not in ARCHS:raise ValueError(arch)
  local=Path('data/source-runs')/source.parent.name/arch;mapping[str(source.relative_to(repo))]=str(local)
  status=json.loads((source/'status.json').read_text()) if (source/'status.json').exists() else {}
  inventory.append(dict(dataset=source.parent.name,architecture=arch,selected_trials=len(stages),recorded_database=meta.get('database'),
   rates=','.join(map(str,sorted({s[1] for s in stages}))),duration=env.get('DURATION'),repetitions=env.get('REPETITIONS'),
   warmup=env.get('WARMUP_DURATION'),preload_orders=env.get('PRELOAD_ORDERS'),csv_storage=env.get('K6_CSV_STORAGE'),
   vus=env.get('MAX_VUS'),diagnostics=env.get('DB_DIAGNOSTICS'),runner_exit=status.get('exitCode'),evidence_path=str(local)))
  for name in ['metadata.json','status.json','phases.jsonl','git-head.txt','source-sha256.json','host-info.txt','postgres-session.json','warmup.json','warmup.exit-code.txt','catalog-used.json','host-collector-status.json','compose-resolved.yaml']:
   if (source/name).is_file():copy(source/name,local/name)
  if (source/'runtime/compose.json').exists():copy(source/'runtime/compose.json',local/'runtime/compose.json')
  for name in ['levels.csv','business.csv','summary.json']:
   if (source/'analysis'/name).exists():copy(source/'analysis'/name,local/'analysis'/name)
  recorded=json.loads((source/'source-sha256.json').read_text()) if (source/'source-sha256.json').exists() else {}
  for rel,digest in recorded.items():
   current=repo/rel
   if current.is_file():checks.append(dict(dataset=source.parent.name,architecture=arch,path=rel,historical_sha256=digest,current_sha256=sha(current),matches=sha(current)==digest))
  for stage,rate,repetition in sorted(stages,key=lambda s:(s[1],s[2])):
   identity=(arch,rate,repetition)
   if identity in allkeys:raise ValueError(f'Duplicate selected trial: {identity}')
   allkeys.add(identity)
   summary=json.loads((source/(stage+'.json')).read_text())
   raw={k:v for k,v in summary.items() if not isinstance(v,(dict,list))}
   runs.append(dict(dataset=source.parent.name,architecture=arch,stage=stage,evidence_path=str(local/(stage+'.json')),**{k:v for k,v in raw.items() if k!='architecture'}))
   for suffix in ['.json','.exit-code.txt']:
    copy(source/(stage+suffix),local/(stage+suffix))
   b=source/'business'/(stage+'.json')
   if b.exists():
    snapshot=json.loads(b.read_text());counts=snapshot.get('counts')
    outcomes.append(dict(dataset=source.parent.name,architecture=arch,stage=stage,rate=rate,repetition=repetition,
     accepted=summary.get('accepted_orders'),started=summary.get('started_requests'),collection_status=snapshot.get('collectionStatus'),
     confirmed=counts.get('confirmed',0) if isinstance(counts,dict) else None,pending=counts.get('pending',0) if isinstance(counts,dict) else None,
     cancelled=counts.get('cancelled',0) if isinstance(counts,dict) else None,customer_prefix=snapshot.get('customerPrefix'),
     snapshot_utc=snapshot.get('sampledAt'),recovery_status=snapshot.get('recoveryStatus'),evidence_path=str(local/'business'/b.name)))
    copy(b,local/'business'/b.name)
  # Raw high-frequency files are indexed, not copied or hashed in this package.
  for f in sorted(source.iterdir()):
   if f.is_file() and (f.suffix=='.csv' or f.name in ['application.log','containers.jsonl','host-vmstat.log','process-pidstat.log','iostat.log','postgres-activity.jsonl','postgres-database.jsonl','postgres-wal.jsonl','postgres-bgwriter.jsonl']):
    omitted.append(dict(source_path=str(f.relative_to(repo)),bytes=f.stat().st_size,reason='Raw/high-frequency input omitted; per-trial summaries, business snapshots and derived analysis retained. Original not changed.'))
  if kind=='mock':
   for name in ['availability.png','throughput.png','latency.png','business.png']:
    if (source/'analysis'/name).exists():copy(source/'analysis'/name,local/'analysis'/name)
 if len(runs)!=560 or len(allkeys)!=560:raise ValueError('Expected 560 unique selected trials')
 csvout('data/performance/runs.csv',runs);csvout('data/performance/outcomes.csv',outcomes);csvout('data/inventory.csv',inventory)
 csvout('data/source_version_checks.csv',checks);csvout('data/large_files_omitted.csv',omitted)
 generated('data/source_mapping.json',json.dumps(mapping,indent=2)+'\n','provenance')
 if kind=='mock':
  diagnostic=repo/'docs/evidencias/mock-1a100-20261110T221441Z/final'
  for f in sorted(diagnostic.iterdir()):
   if f.is_file():copy(f,'data/diagnostic/'+f.name)
  original=repo/'docs/diagnostico-mock-1a100-20261110T221441Z.md'
  dest=out/'results/diagnosis.md';dest.parent.mkdir(parents=True,exist_ok=True)
  text=text_safe(original.read_text()).replace('evidencias/mock-1a100-20261110T221441Z/final','../data/diagnostic').replace('../experiments/datasets/pilot/mock-1a100-20261110T221441Z','../data/source-runs/mock-1a100-20261110T221441Z')
  dest.write_text(text);record(str(original.relative_to(repo)),dest,'prior_diagnosis','sanitized_copy_and_links_adapted',sha(original))
 # Present-day source snapshot, explicitly separated from historical sources.
 for arch in ARCHS:
  for f in sorted((repo/'implementation'/arch).glob('*-service/src/**/*.ts')):
   if not f.name.endswith('.spec.ts'):copy(f,Path('configurations/current-source')/f.relative_to(repo),'current_source_not_historical')
 for name in ['run.sh','run-suite.sh','run-mock.sh','experiments/k6/pilot.js','experiments/analyze_pilot.py','experiments/compare_pilots.py','experiments/combine_pilot_comparisons.py','scripts/pilot/compose.py','scripts/pilot/state.py','scripts/pilot/mock_state.py','scripts/pilot/preload.py','scripts/pilot/analyze_mock_run.py','scripts/seed.sh','environment/nginx/distributed.conf','environment/pilot/Dockerfile']:
  copy(repo/name,Path('configurations/current')/name,'current_configuration_not_proof_of_history')
 for f in sorted((repo/'environment/mock').iterdir()):
  if f.is_file():copy(f,Path('configurations/current/environment/mock')/f.name,'current_mock_source')
 copy(Path(__file__), 'tooling/package_campaign_evidence.py','collection_tool')
 generated('data/provenance.json',json.dumps(dict(collected_at=datetime.now(timezone.utc).isoformat(),kind=kind,
  git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
  git_status=subprocess.check_output(['git','status','--short'],cwd=repo,text=True),
  original_target=str(target.relative_to(repo)),selected_trials=len(runs),architectures=ARCHS,
  notes='HEAD does not identify uncommitted working tree; source checks compare historical hashes with collection snapshot.'),indent=2)+'\n','provenance')
 generated('methodology/implementations.md','''# Implementações e fronteiras

São avaliadas somente as cinco implementações distribuídas. Monólito e Outbox estão fora deste pacote.
Order coordena/reage ao fluxo, Warehouse controla estoque/reservas, Billing registra pagamentos,
Shipping registra entrega. Cada serviço possui armazenamento próprio; não há gateway financeiro real.

| Implementação | Coordenação e resposta HTTP |
|---|---|
| distributed-baseline | Chamadas HTTP sequenciais a warehouse, billing, shipping; 201 após fluxo síncrono. Sem compensação global equivalente às Sagas. |
| tcc | Try de reserva/pagamento/entrega, seguido de Confirm; Cancel quando aplicável. Resultado incerto pode permanecer pending. |
| two-phase-commit | Prepare e Commit/Rollback implementados na aplicação via HTTP. O nome não prova um 2PC clássico com log durável e recuperação integral. |
| orchestrated-saga | Coordenador faz etapas e compensações via HTTP; 201 após fluxo síncrono. Resultado incerto pode permanecer pending. |
| choreographed-saga | Eventos RabbitMQ entre participantes; 201 mede aceitação/publicação, não conclusão de negócio. |

Os fontes atuais dos 20 serviços estão em [current-source](../configurations/current-source/implementation).
Não são uma reconstrução garantida de cada versão histórica. Consulte
[source_version_checks.csv](../data/source_version_checks.csv) e os manifests históricos empacotados.
Regras de compensação, atomicidade, idempotência e configurações evoluíram entre campanhas.
O modo mock é descrito em [environment.md](environment.md). Não há nova alteração de arquitetura nesta coleta.
''')
 generated('methodology/environment.md','''# Ambiente e configuração: registrado versus atual

[inventory.csv](../data/inventory.csv) resume os parâmetros efetivamente registrados por dataset.
Cada pasta em [source-runs](../data/source-runs) contém metadata, host-info, status,
source-sha256 e, quando disponível, sessão PostgreSQL e Compose resolvido.
As versões de imagens/Node/PostgreSQL devem ser atribuídas ao respectivo registro, não inferidas
somente de tags mutáveis como node:22-alpine ou postgres:15-alpine.

Os fontes/configurações em [configurations/current](../configurations/current) representam o estado
na coleta. Não provam quais arquivos antigos foram usados; manifests históricos contêm hashes.
Credenciais e caminhos pessoais foram sanitizados nas cópias, com hashes original e copiado separados.

PostgreSQL: durabilidade, pool, timeouts, proxy e réplicas são evidências por ensaio; consultar Compose
resolvido e metadados. Não presumir equivalência de todos os extras entre as versões antigas.
Mock: dados voláteis em Maps, um mutex por serviço, rollback em memória, sem conexão PostgreSQL,
SQL ou WAL/fsync. RabbitMQ continua real na coreografada. Não existe medição de tempo real de pool
ou COMMIT nesse modo. Instrumentação de host/containers permanece; há inspeção de coortes de pedido.
''')
 generated('methodology/experiments.md','''# Procedimento e critérios de seleção

Os ensaios são pilotos de chegada constante do k6: taxa solicitada significa disparos por segundo,
não throughput útil. A matriz selecionada tem 14 cargas, 8 repetições e 5 implementações (560 ensaios).
Parâmetros reais estão em [inventory.csv](../data/inventory.csv); defaults dos scripts não substituem registros.
Warm-up e seed precedem as medições; dentro de cada campanha o estado acumula entre repetições.
Não são repetições independentes com o mesmo estado inicial. Há cooldown e observação após carga.

A comparação PostgreSQL de 1–100 é uma seleção de campanhas: dados antigos de 1–50, históricos
corrigidos de 60–90, reteste do baseline de 80–90 e nova campanha de 100. A seleção está em
[data/comparison/selection.json](../data/comparison/selection.json) quando aplicável.
As tabelas analysis/levels.csv nas pastas fonte podem conter ensaios NÃO selecionados; a seleção
oficial deste pacote é [performance/runs.csv](../data/performance/runs.csv), com 560 linhas.

No mock: 60 s por repetição, sem preload SQL e com CSV no tmpfs. Nos ensaios PostgreSQL selecionados,
consultar a duração, preload e armazenamento de CSV por segmento: não presumir parâmetros iguais.
Ajustes de código/configuração e histórico sintético impedem tratar a junção como execução contínua.
A exportação do tmpfs ocorre depois da carga/recuperação de cada arquitetura; logs e broker ainda
podem realizar I/O. Nenhum ensaio foi executado para construir este pacote.
''')
 generated('results/failures.md','''# Erros, pendências e recuperação

Este pacote contém erros sob carga e snapshots por coorte; não contém resultados de uma campanha
formal de injeção de falhas. Não foram encontrados, no escopo destes dois conjuntos, resultados
suficientes para uma análise experimental de resiliência e recuperação por injeção controlada.
Isso não afirma que tais campanhas não existam em outras pastas do repositório.

Consultar [outcomes.csv](../data/performance/outcomes.csv): HTTP 500 não comprova ausência de efeitos,
201 não garante conclusão assíncrona e pending não comprova sozinho inconsistência entre participantes.
Snapshots são observações em instantes distintos, não um censo simultâneo ao fim de toda a campanha.
Valores ausentes permanecem vazios; nenhum erro de coleta foi convertido em zero confirmado.
No mock, a inspeção só observa pedidos; não audita pagamento, entrega, estoque e broker integralmente.
''')
 generated('results/complexity.md','''# Tamanho e complexidade: limite deste pacote

Nenhuma nova contagem SLOC, análise SonarQube ou complexidade ciclomática foi executada.
As métricas de desempenho aqui não medem dificuldade de desenvolvimento. Os fontes atuais foram
incluídos para rastrear mecanismos e entidades; não são métricas de tamanho das versões históricas.
Há 20 serviços (quatro em cada implementação), com RabbitMQ adicional na coreografada.
Contagens SLOC históricas do pacote geral anterior não devem ser atribuídas automaticamente ao mock
ou a todas as revisões selecionadas. Para medir essas versões, falta uma coleta com escopo e hashes
próprios. Mais SLOC não equivale automaticamente a maior complexidade arquitetural.
''')
 # Render all treatment aggregates already calculated by the comparison tool.
 ag=table(comparison/'by_rate.csv')
 lines=['# Resultados por tratamento','',
 'São agregados por implementação e carga: mediana e P25–P75 entre repetições, não percentis globais.',
 'Abaixo: mediana de HTTP 201/s incluindo drenagem; mediana do p95 dos HTTP 201 em ms; sucesso HTTP em %.',
 'HTTP 201 na coreografada é aceitação. Confirmações são snapshots, não throughput temporal fim a fim.','',
 '| Implementação | Req/s | Mediana HTTP 201/s | Mediana p95 dos sucessos ms | Mediana sucesso HTTP % |','|---|---:|---:|---:|---:|']
 for arch in ARCHS:
  for rate in RATES:
   def value(metric):
    r=next((x for x in ag if x['architecture']==arch and int(x['rate'])==rate and x['metric']==metric),None)
    return f"{float(r['median']):.3f}" if r and r['median'] else 'ausente'
   lines.append(f'| {arch} | {rate} | {value("accepted_rps")} | {value("successful_p95_ms")} | {value("availability_pct")} |')
 lines+=['','## Limitações específicas','']
 if kind=='mock':lines+=['O colapso de TCC/2PC acompanha custo linear de unicidade no mock e CPU no warehouse, não PostgreSQL.',
  'Leia o [diagnóstico completo](diagnosis.md) e [dados operacionais](../data/diagnostic). Não usar como ranking isolado dos padrões.']
 else:lines+=['A matriz completa une campanhas/versões distintas e tem histórico/preload e CSV storage diferentes.',
  'Não permite atribuir diferenças exclusivamente à carga ou ao padrão. Os gráficos omitem a separação visual a pedido do pesquisador;',
  'a proveniência fica preservada em selection.json, runs.csv e manifests. Não há latência fim a fim completa para a coreografada.',
  'O reteste do baseline em 80–90 substitui os pontos anteriores nessa seleção, não apaga os ensaios anteriores.']
 generated('results/performance.md','\n'.join(lines)+'\n')
 generated('README.md',f'''# Evidências — {target.name}

Pacote independente, criado em {datetime.now(timezone.utc).isoformat()}. Cinco implementações distribuídas;
**560 ensaios selecionados**, 14 cargas, 8 repetições por tratamento. Não inclui monólito nem Outbox.
Objetivo: fornecer evidência verificável para análise acadêmica; não escrever capítulos do TCC.
Nenhum original foi alterado ou movido, nem Docker, benchmark ou banco iniciado.

## Índice

1. [Implementações](methodology/implementations.md)
2. [Ambiente registrado versus configuração atual](methodology/environment.md)
3. [Procedimentos e seleção](methodology/experiments.md)
4. [Desempenho e limitações](results/performance.md)
5. [Erros e recuperação](results/failures.md)
6. [Escopo de complexidade](results/complexity.md)

## Arquivos principais

- [Gráficos e agregados da comparação](data/comparison)
- [Ensaios individuais enriquecidos com resumos k6](data/performance/runs.csv)
- [Snapshots de negócio por coorte](data/performance/outcomes.csv)
- [Inventário de configurações efetivas](data/inventory.csv)
- [Evidências originais selecionadas por dataset](data/source-runs)
- [Verificação código atual versus hashes históricos](data/source_version_checks.csv)
- [Proveniência Git e coleta](data/provenance.json)
- [Inventário dos grandes arquivos omitidos](data/large_files_omitted.csv)
- [Manifesto com SHA-256](manifest.csv)

Cópias idênticas usam copy2; sanitizações de credenciais/caminhos e adaptação de links são declaradas
no manifesto, com hashes original e copiado. Dados sanitizados não são cópias byte a byte.
Arquivos de alta frequência não foram duplicados: resumos, snapshots e análises disponíveis permitem
análise descritiva, mas não reconstrução completa de percentis globais nem profiling não coletado.
Fontes atuais não são automaticamente fontes históricos. Consulte os checks de versão antes de
vincular uma linha de código atual a uma medição antiga. Ausência de evidência não foi preenchida por estimativa.

## Reprodução

`tooling/package_campaign_evidence.py --repo <repo-original> --kind {kind} --output <nova-pasta>`
recria o pacote a partir dos originais, recusando sobrescrever saída existente. Este pacote não precisa
ser executado para leitura; o ZIP ao lado é uma cópia completa. `manifest.csv` não inclui o próprio
hash recursivo; seu conteúdo é protegido pelo ZIP e SHA-256 externo.
''')
 # Check every local Markdown link we generated/adapted.
 for f in out.rglob('*.md'):
  if 'configurations' in f.parts or 'data/comparison' in str(f):continue
  for link in re.findall(r'\]\(([^)]+)\)',f.read_text()):
   if '://' in link:continue
   if link == 'manifest.csv' and f == out/'README.md':continue  # Written immediately below.
   if not (f.parent/link.split('#')[0]).exists():raise ValueError(f'Broken package link: {f}: {link}')
 dest=out/'manifest.csv'
 with dest.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(manifest[0]));w.writeheader();w.writerows(manifest)
 for r in manifest:
  if sha(out/r['copied_path'])!=r['sha256']:raise ValueError(r['copied_path'])
 for f in out.rglob('*.json'):json.loads(f.read_text())
 archive=out.with_suffix('.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
  for f in sorted(out.rglob('*')):
   if f.is_file():z.write(f,Path(out.name)/f.relative_to(out))
 with zipfile.ZipFile(archive) as z:
  if z.testzip():raise ValueError('ZIP validation failed')
 archive.with_suffix('.zip.sha256').write_text(sha(archive)+'  '+archive.name+'\n')
 print(json.dumps(dict(output=str(out),copied_sources=len(copied),manifest_entries=len(manifest),trials=len(runs),zip_bytes=archive.stat().st_size)))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--kind',choices=['mock','comparison'],required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();build(a.repo,a.output,a.kind)
