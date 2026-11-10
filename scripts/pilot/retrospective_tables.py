#!/usr/bin/env python3
"""Render tables from retrospective CSVs. Does not read or modify original datasets."""
import argparse
import csv
from collections import defaultdict
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('directory',type=Path);args=p.parse_args();root=args.directory
    read=lambda name:list(csv.DictReader((root/name).open()))
    audit=read('audit.csv');op=read('operational.csv');dur=read('durations.csv')
    oi={(r['dataset'],r['stage'],r['window'],r['metric']):r for r in op}
    di={(r['dataset'],r['stage'],r['window'],r['metric']):r for r in dur}
    label=lambda r:('C2/coreografada' if r['dataset'].startswith('coreografada') else 'C1/'+r['architecture'])
    def metric(r,key,field='mean'):
        value=oi.get((r['dataset'],r['stage'],'load',key),{}).get(field)
        return float(value) if value not in (None,'') else None
    def fmt(value):return 'ND' if value is None else f'{value:.2f}'
    groups=defaultdict(list)
    for r in audit:groups[(r['dataset'],int(r['rate']))].append(r)
    lines=['## Tabela A — carga entregue e confirmação por nível','',
           'C1 = histórico de 03:36; C2 = coreografada de 14:31. HTTP usa todas as etapas; 201 pareados e confirmação usam somente coortes com snapshot. Percentuais de contagens somadas dentro do mesmo dataset/nível.', '',
           '| Conjunto/implementação | req/s | N | Dropped | Pico VUs | 201 / disparos % | Snapshots | 201 pareados % | Confirmados pareados % | Pendentes¹ |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for (ds,rate),rs in groups.items():
        total=lambda k:sum(int(r[k]) for r in rs);valid=[r for r in rs if r['confirmed']!=''];den=sum(int(r['started']) for r in valid)
        lines.append(f"| {label(rs[0])} | {rate} | {len(rs)} | {total('dropped')} | {max(int(r['vus_peak']) for r in rs)} | {100*total('accepted')/total('started'):.2f} | {len(valid)}/{len(rs)} | {100*sum(int(r['accepted']) for r in valid)/den:.2f} | {100*sum(int(r['confirmed']) for r in valid)/den:.2f} | {sum(int(r['pending']) for r in valid)} |")
    lines+=['','¹ Soma de snapshots de coortes distintas, em momentos diferentes; não é uma auditoria simultânea final. ND significa ausência, nunca zero imputado.','',
            '## Tabela B — contraste entre repetições na mesma taxa','',
            'Para cada conjunto, seleciona-se a pior fração 201/disparos e a melhor repetição na mesma taxa. Métricas operacionais usam apenas a janela nominal de chegada de 180 s. COMMIT é média ponderada pelos counts das janelas diagnósticas; não duração SQL medida diretamente no servidor.', '',
            '| Conjunto | Etapa | 201 % | 201/s² | p95 HTTP ms | COMMIT médio ms | w_await sdb ms | Máx. waiting | Falhas aquisição | CPU host ociosa média % |',
            '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    selected=[]
    for ds in dict.fromkeys(r['dataset'] for r in audit):
        rs=[r for r in audit if r['dataset']==ds];worst=min(rs,key=lambda r:int(r['accepted'])/int(r['started']))
        best=max([r for r in rs if r['rate']==worst['rate']],key=lambda r:int(r['accepted'])/int(r['started']))
        for r in [worst,best]:
            commit=float(di[(ds,r['stage'],'load','sql.COMMIT')]['weighted_mean_ms'])
            failures=oi.get((ds,r['stage'],'load','acquire.failures'),{}).get('total','ND')
            lines.append(f"| {label(r)} | {r['stage']} | {100*int(r['accepted'])/int(r['started']):.2f} | {float(r['accepted_rps']):.2f} | {float(r['p95_ms']):.2f} | {commit:.2f} | {fmt(metric(r,'disk.sdb.w_await'))} | {fmt(metric(r,'pool.waiting','maximum'))} | {failures} | {fmt(metric(r,'host.cpu_idle_pct'))} |")
        selected.append(worst)
    lines+=['','² Throughput 201 do ensaio usa o denominador com drenagem do k6; demais indicadores nesta tabela usam janela de chegada. As duas janelas estão documentadas, não são idênticas.','',
            '## Tabela C — recursos nas repetições ruins da tabela B','',
            'CPU é percentual de um processador lógico; 100% não corresponde à máquina inteira (12 threads). RSS do k6 e memória Docker não têm exatamente a mesma semântica.', '',
            '| Conjunto / etapa | k6 CPU média observada % | k6 CPU pico % | k6 RSS pico MiB | order CPU média % | order CPU pico % | Soma médias CPU dos 4 bancos % | order memória pico MiB | Maior memória de um banco MiB |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in selected:
        ms=[x for x in op if x['dataset']==r['dataset'] and x['stage']==r['stage'] and x['window']=='load']
        ordercpu=next(x for x in ms if x['metric'].endswith('-order-service-1.cpu_pct'))
        ordermem=next(x for x in ms if x['metric'].endswith('-order-service-1.memory_mib'))
        banks=[float(x['maximum']) for x in ms if x['metric'].endswith('-db-1.memory_mib')]
        bank_cpu=sum(float(x['mean']) for x in ms if x['metric'].endswith('-db-1.cpu_pct'))
        lines.append(f"| {label(r)} / {r['stage']} | {fmt(metric(r,'pidstat.k6.individual_cpu_pct'))} | {fmt(metric(r,'pidstat.k6.individual_cpu_pct','maximum'))} | {fmt(metric(r,'pidstat.k6.individual_rss_mib','maximum'))} | {float(ordercpu['mean']):.2f} | {float(ordercpu['maximum']):.2f} | {bank_cpu:.2f} | {float(ordermem['maximum']):.2f} | {max(banks):.2f} |")
    (root/'tables.md').write_text('\n'.join(lines)+'\n')
    appendix=['# Auditoria individual — 174 repetições','',
              'Mesmas definições de audit.csv. C1/C2 definidos no relatório. CF−201 é diferença de totais, não correspondência individual de pedidos.','',
              '| Conjunto | Etapa | Iterações | Dropped | VUs pico | HTTP 201 | Timeouts | p95 ms | 201/s | CF−201 | Atraso snapshot s |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in audit:
        lag=fmt(float(r['snapshot_lag_after_load_s'])) if r['snapshot_lag_after_load_s'] else 'ND'
        appendix.append(f"| {label(r)} | {r['stage']} | {r['iterations']} | {r['dropped']} | {r['vus_peak']} | {r['accepted']} | {r['timeouts']} | {float(r['p95_ms']):.2f} | {float(r['accepted_rps']):.2f} | {r['confirmed_minus_201'] or 'ND'} | {lag} |")
    (root/'repetitions.md').write_text('\n'.join(appendix)+'\n')


if __name__=='__main__':main()
