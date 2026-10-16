# Piloto PostgreSQL na VM

O `run.sh` da **raiz** executa o distributed baseline com PostgreSQL. O runner
anterior `scripts/run.sh` e o piloto para múltiplas arquiteturas
`scripts/run-pilot.sh` continuam separados. Este novo piloto não apaga volumes,
não troca configurações de banco/HTTP/NGINX e não modifica regras de negócio.
A variante MySQL foi removida; resultados históricos não foram apagados.

Caso a stack MySQL experimental ainda esteja ligada na máquina local, encerre
somente seus containers antes de voltar a executar PostgreSQL nessa máquina
(não é necessário na VM que usou apenas PostgreSQL):

```bash
sudo docker ps -q --filter label=com.docker.compose.project=distributed-lab-mysql \
  | xargs -r sudo docker stop
```

Isso preserva volumes e históricos. O runner PostgreSQL não interfere em outras
stacks automaticamente.

## Executar

Na raiz do checkout da VM, com Docker + Compose, k6, Python 3, curl e timeout
disponíveis. O `sysstat` é opcional para vmstat/pidstat/iostat. Prepare o ambiente
Python antes da medição para gerar gráficos automaticamente:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

sudo RUN_ID=vm-pg-pilot-01 RATES=1,2,5,10,15,20,30,40 \
  DURATION=3m WARMUP_DURATION=1m bash run.sh
```

Se Docker funcionar sem sudo, retire `sudo`. As saídas são devolvidas ao usuário
que invocou sudo, evitando arquivos ilegíveis. Use RUN_ID diferente a cada coleta;
uma pasta existente nunca é sobrescrita. Não execute outro benchmark na mesma VM
durante o piloto. Não é necessário instalar Node/pnpm no host: o build é no Docker.

O runner constrói as imagens antes da medição, inicia os quatro PostgreSQL,
aguarda sua saúde (até 10 minutos de inicialização), inicia os serviços e aguarda
os endpoints de saúde. Um catálogo vazio recebe o seed existente de 100 produtos
com estoque de 100000 por produto. Um catálogo existente é reutilizado; seus
produtos iniciais são arquivados. `SEED_DATA=1` exige catálogo vazio, `0` proíbe
seed e `auto` é o padrão. O teste cria pedidos e consome estoque; não é somente
leitura. Controle o estado inicial entre futuras repetições acadêmicas; não use
exclusão automática de volumes para isso.

Depois executa:

1. Aquecimento separado: 1 pedido/s durante 1 minuto.
2. Patamares constantes crescentes: 1, 2, 5, 10, 15, 20, 30 e 40 pedidos/s,
   cada um durante 3 minutos, um processo k6 por patamar.
3. Entre patamares, 30 segundos de acomodação e verificação de amostra recente
   com `inUse=0` e `waiting=0` no pool do order-service. Espera adicional máxima
   de 60 segundos; se não recuperar, interrompe e preserva resultados parciais.
4. Tabelas e gráficos após encerrar os coletores. Containers e volumes permanecem.

O tempo padrão é aproximadamente **29 minutos, mais build, readiness e drenagem**.
O cooldown não limpa caches nem reinicia serviços. A recuperação do pool do order
não comprova que todos os efeitos distribuídos tardios terminaram. Consulte os
logs quando houver timeout antes de reutilizar uma coleta parcial.

Todos os níveis usam `constant-arrival-rate`, não VUs como carga oferecida. O
timeout HTTP do piloto continua 20s e gracefulStop 30s, como no piloto existente.
Por padrão, `PRE_ALLOCATED_VUS=MAX_VUS=25 × maior taxa` (1000 para taxa máxima
40): orçamento para 20s de espera mais folga, fixo entre patamares. Isso evita
que a criação dinâmica de VUs vire parte da subida de carga, mas pode consumir
memória significativa. Configurável por essas duas variáveis; registre a mudança
como configuração do gerador. Não confunda `dropped_iterations` com resposta 500.

O coletor PostgreSQL usa uma sessão independente somente leitura, não o pool da
aplicação. O order publica diagnóstico agregado de pool/SQL/event loop a cada
segundo. A coleta do host é opcional (`COLLECT_HOST=0` desativa); ferramentas
ausentes são registradas. Nenhum pacote é instalado durante o piloto. Gerador,
Docker, bancos, serviços e coletores compartilham a VM: CPU/memória/I/O do gerador
também precisam ser avaliados. Logs e CSV têm overhead de observação; mantenha
essa configuração igual entre coletas que pretende comparar.

## Saídas e gráficos

```text
experiments/datasets/pilot/<RUN_ID>/
  metadata.json, source-sha256.json, compose-resolved.yaml, phases.jsonl
  initial-products.json, host-info.txt, status.json
  warmup.csv, warmup.json
  rate1_run1.csv, rate1_run1.json, rate1_run1.exit-code.txt, ...
  application.log, postgres-*.jsonl, postgres-session.json
  containers.jsonl, host-vmstat.log, process-pidstat.log, iostat.log (se disponíveis)
  logs/                         # startup, seed e k6 por etapa
  analysis/
    levels.csv                  # uma linha por patamar/repetição; aquecimento identificado
    windows.csv                 # janelas de 10s dentro dos patamares, inclusive janelas vazias
    summary.json                # advertências e semântica
    capacity.png                # taxa solicitada, entregue, sucesso, latência, erros e descartes
    timeline.png                # estabilidade dentro de cada patamar
    pool.png                    # pool, aquisição, posse, SQL e event loop
```

Sem matplotlib, a coleta continua válida e as tabelas são geradas com a biblioteca
padrão do Python. Os gráficos ficam pendentes em `status.json`. Para gerar depois,
inclusive em outra máquina com a pasta completa copiada:

```bash
.venv/bin/python experiments/analyze_pilot.py \
  experiments/datasets/pilot/vm-pg-pilot-01

# Só tabelas, sem dependências externas:
python3 experiments/analyze_pilot.py \
  experiments/datasets/pilot/vm-pg-pilot-01 --no-plots
```

`AUTO_PLOT=0` desativa os gráficos automáticos. `PLOT_PYTHON` seleciona outro
interpretador. O analisador regrava apenas `analysis/`; preserva os dados brutos.
`SETTLING_SECONDS=30` marca os primeiros 30s de cada patamar. `tail_*` considera
respostas concluídas depois dessa acomodação e antes do fim nominal, não inclui
a drenagem. Não é um grupo de requisições iniciadas nesse intervalo. O gráfico
temporal mantém todas as janelas para revelar a acomodação e eventuais filas.

`accepted_rps` divide HTTP 201 pelo tempo observado **incluindo drenagem**, sem
setup. `started_rps` divide inícios pela duração nominal de chegadas. Percentis
são calculados sobre amostras individuais do CSV; sucessos e todas as respostas
ficam separados. Não se faz média de percentis entre repetições. Sem respostas
ou sem sucessos, percentis são nulos, não zero. Timestamps do CSV têm resolução
de aproximadamente um segundo. HTTP 201 não comprova por si só consistência de
todo o estado de negócio. Exit 0 do k6 comprova execução, não ausência de falhas:
os thresholds são neutros para permitir observar a degradação.

## Escolher baixa, média e alta

Aquecimento é uma fase temporal; carga baixa também deve ser observada depois
que o sistema aqueceu. Examine `timeline.png`, não apenas a média do patamar:

- **Baixa:** taxa atendida com folga, sem fila crescente e erros, após acomodação.
- **Média:** região estável de operação, com vazão acompanhando chegadas e
  percentis aproximadamente estáveis entre janelas consecutivas.
- **Alta:** região onde a fila ou percentis crescem de forma sustentada, a vazão
  útil deixa de acompanhar chegadas ou começam erros/timeouts.

Não há classificação automática com limites arbitrários. Se 5/s estiver estável
e 10/s degradar, repita um piloto mais fino, por exemplo
`RATES=5,6,7,8,9,10`. Se o primeiro patamar já degradar, investigue antes de subir.
Se 40/s continuar estável, amplie a faixa e reavalie orçamento de VUs/host.
Com descartes, avalie VUs e recursos do gerador antes de atribuir o limite apenas
à aplicação. Respostas 500 rápidas podem fazer o p95 geral cair; confira a curva
de sucessos, a vazão útil e a contagem de falhas em conjunto.

O sweep é exploratório: o banco cresce, caches aquecem e a ordem é crescente.
`REPETITIONS` repete cada patamar sem reset, não cria repetições independentes.
Depois de escolher os níveis, faça o experimento formal com estado inicial
controlado, repetições independentes e mesmo procedimento entre arquiteturas.
