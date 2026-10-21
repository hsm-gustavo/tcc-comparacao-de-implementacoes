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

### Lei de Little: 100 requisições/s, 20 repetições de 3 minutos

Na raiz do checkout atualizado, com os mesmos pré-requisitos do piloto:

```bash
sudo RUN_ID=vm-pg-little-100rps-20reps-01 bash run-little.sh
```

O script usa `run.sh`, com `RATES=100`, `REPETITIONS=20`, `DURATION=3m` e
`DB_DIAGNOSTICS=1`. O aquecimento, cooldown, recuperação, coletores e gráficos
continuam sendo os do piloto. São 60 minutos de disparos medidos, aproximadamente
1h12 com aquecimento e cooldown, mais build, drenagem e esperas de recuperação.
Repetições/duração podem ser substituídas pelas mesmas variáveis do piloto.
O limite de VUs continua sendo calculado pelo runner: 2500 para 100 disparos/s.
Não há alteração de pool, timeouts, sockets, queries ou transações.

Durante a execução, `application.log` recebe `pool_hold_ms` em cada amostra
diagnóstica de aproximadamente um segundo: média de posse das conexões devolvidas
na janela, desde `pg.Pool.acquire` até `pg.Pool.release` do pool real do TypeORM.
É `null` quando nenhuma conexão foi devolvida. Exclui espera por aquisição e
inclui todas as queries, COMMIT e demais esperas enquanto o client está adquirido.
Os percentis existentes continuam em `connectionHold`; para Little usa-se média.

O header diagnóstico `X-Pilot-Stage` associa cada pedido à sua repetição. Os
contadores `poolHoldByStage` registram pedidos recebidos, aquisições entregues,
falhas de aquisição, devoluções e soma dos tempos de posse (`holdMsTotal`). São
deltas agregados, sem log por aquisição/query e sem manter objetos de requisição.
Se stdout estiver sob backpressure, esses deltas ficam acumulados até a próxima
publicação. Devoluções tardias continuam na repetição original, mesmo depois de
timeout HTTP. O overhead adicional é um header e contadores/somas em memória,
mais alguns campos por segundo; isso também deve ser mantido igual nas comparações.

Ao final, o terminal mostra cada repetição e o resultado agregado. Os relatórios
ficam em `experiments/datasets/pilot/<RUN_ID>/analysis/little.json` e `little.csv`.
Para recalcular sem executar carga:

```bash
python3 experiments/analyze_little.py \
  experiments/datasets/pilot/vm-pg-little-100rps-20reps-01 \
  --target-rps 100 --expected-repetitions 20
```

O relatório diferencia três cálculos:

- `C_single_checkout_assumption = 100 × pool_hold_ms / 1000`: expressão direta
  solicitada, válida para conexões ocupadas se houver exatamente um checkout por pedido.
- `C_at_target_request_rate = 100 × pool_hold_per_request_ms / 1000`, onde
  `pool_hold_per_request_ms = soma de TODOS os tempos de posse / pedidos recebidos`.
  É equivalente a `100 × checkouts/pedido × posse média por checkout / 1000`.
  Considera que um pedido pode adquirir/liberar a conexão várias vezes. Uma aquisição
  pode executar várias queries; o fator é por checkout, não por query.
- `C_observed = lambda_pool_observed_per_s × pool_hold_ms / 1000`: utiliza a taxa
  efetiva de checkouts devolvidos, considerando a duração observada e a drenagem,
  inclusive devoluções posteriores ao fim das respostas HTTP. Não substitui a taxa
  de disparos de requisições pela vazão de respostas bem-sucedidas.

Exemplo: posse média de 50 ms e três checkouts por pedido resultam em
`100 × 3 × 0,050 = 15` conexões ocupadas em média; usar só `100 × 0,050 = 5`
omitaria duas visitas ao pool. A média agregada é ponderada pelo número de
checkouts, não uma média das médias/percentis das janelas de um segundo.

Os cálculos incluem a acomodação inicial e a drenagem de cada repetição, excluindo
warmup, setup e os intervalos de recuperação. Não certificam regime estacionário.
Little requer taxa e tempo referentes ao mesmo sistema/população; veja
[Kim e Whitt, sobre aplicação e validação da Lei de Little](https://www.columbia.edu/~ww2040/Kim_Whitt_PEIS_2013.pdf).
`C` é ocupação **média**, não tamanho mínimo seguro do pool. Falhas e timeouts
podem reduzir o número de checkouts realizados por pedido; projetar esse perfil
para 100/s não comprova capacidade para 100 pedidos/s com sucesso. O relatório
sinaliza falhas, chegadas descartadas, divergência entre pedidos disparados e
recebidos, clientes não devolvidos/removidos e repetições incompletas. C ausente
não é substituído por zero quando faltam os contadores.

O coletor PostgreSQL mantém seu `statement_timeout` de 2s, exclusivo da sessão
observadora. Se falhar antes do benchmark, o runner interrompe e nenhum C é
calculado. Para falhas durante a carga, os resultados disponíveis são parciais e
o status de falha do piloto é preservado. Esses relatórios requerem a imagem
reconstruída com a nova instrumentação; logs antigos não têm contadores por coorte.

### Piloto com vários níveis

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

### Repetições por nível

`REPETITIONS` controla quantas vezes cada taxa é medida. O padrão é `1`.
Para executar cinco repetições de três minutos em cada nível:

```bash
sudo RUN_ID=vm-pg-pilot-5reps-01 REPETITIONS=5 \
  RATES=1,2,5,10,15,20,30,40 DURATION=3m WARMUP_DURATION=1m \
  bash run.sh
```

Na máquina local, use outro identificador, como `RUN_ID=local-pg-pilot-5reps-01`.
O valor pode ser qualquer inteiro positivo, por exemplo `REPETITIONS=3`, `5` ou
`20`. A sequência para duas repetições será:

```text
warmup (uma vez)
1/s:  repetição 1 → recuperação → repetição 2 → recuperação
2/s:  repetição 1 → recuperação → repetição 2 → recuperação
...
40/s: repetição 1 → recuperação → repetição 2 → recuperação
```

`DURATION` vale para **cada repetição**, e o cooldown/verificação do pool ocorre
também entre repetições da mesma taxa. O banco é reutilizado, sem reinício ou novo
seed entre elas. Cada medição gera arquivos separados, como `rate20_run1.csv`,
`rate20_run2.csv`, seus resumos JSON e logs. As tabelas preservam o número da
repetição. Os gráficos resumem sua distribuição e permitem inspecionar cada
execução nos mapas de calor e detalhes temporais por nível.

Com oito níveis, três minutos de medição e 30s de cooldown, cinco repetições
levam aproximadamente **2h22min**, mais build, readiness, drenagem e eventuais
esperas adicionais. Vinte repetições levam aproximadamente **9h22min**, mais
esses tempos. Aumentar repetições não multiplica a taxa de disparos por segundo.

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
    by_rate.csv                 # distribuição das métricas entre repetições de cada taxa
    summary.json                # advertências e semântica
    availability.png            # HTTP 201 / requisições observadas (%), por nível
    throughput.png              # disparos, respostas totais/s e respostas HTTP 201/s
    latency.png                 # média, p95 e p99; todas as respostas e somente HTTP 201
    capacity.png                # mediana, P25-P75 e pontos individuais, com legenda fixa
    repetitions.png             # mapa taxa × repetição: p95, falhas e vazão relativa
    timeline.png                # visão temporal geral, com um rótulo por nível
    timeline_by_rate/rate20.png  # detalhe das repetições de cada nível (um arquivo por taxa)
    pool.png                    # ocupação, fila, durações e event loop em painéis separados
    pool_by_rate/rate20.png      # detalhe do pool por nível (um arquivo por taxa)
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

# Refazer só a visualização de uma coleta já analisada, sem reler CSVs brutos do k6:
.venv/bin/python experiments/analyze_pilot.py \
  experiments/datasets/pilot/vm-pg-pilot-01 --plots-only
```

Para muitas repetições, comece por `capacity.png` e `repetitions.png`. O primeiro
mostra a **mediana das métricas por repetição**, com faixa entre P25 e P75 (IQR)
e pontos individuais levemente deslocados na horizontal. Essa faixa descreve a
variação observada; **não é intervalo de confiança**. A mediana dos p95 das
repetições **não é o p95 global das requisições**. Nenhuma repetição completa é
descartada por estar entre as primeiras ou por apresentar falhas.

Os gráficos individuais `availability.png`, `throughput.png` e `latency.png`
usam o mesmo resumo: mediana e P25–P75 entre repetições, com pontos individuais.
Disponibilidade aqui é **sucesso HTTP por requisição** (`HTTP 201 / responses ×
100`), como o `success.png` do gerador anterior. Inclui timeouts do cliente como
falhas, exclui iterações não iniciadas do denominador e não mede uptime nem a
conclusão das operações assíncronas de negócio. Se não houve requisições
observadas, a disponibilidade é indefinida, não 100%.

O throughput distingue disparos/s solicitados, disparos/s efetivos, respostas/s
totais (inclusive falhas) e HTTP 201/s. As respostas usam o tempo de observação
incluindo drenagem. O gerador antigo normalizava sucessos pela duração nominal;
por isso essa curva antiga pode diferir de `accepted_rps`. Para reproduzir o
valor nominal a partir das tabelas, use `accepted / duration_s`.

A latência possui painéis separados para todas as respostas e somente HTTP 201,
com média, p95 e p99 em segundos. A linha da média também é a mediana das médias
individuais das repetições; não é uma média global. Para tabelas antigas sem a
coluna de média, `--plots-only` recupera esses valores dos pequenos JSONs de cada
etapa. Se eles não estiverem disponíveis, a curva de média é omitida; ela nunca é
estimada a partir dos percentis.

O mapa de calor mantém os números de repetição em ordem numérica para verificar
se as primeiras execuções diferem das últimas. Células cinzas indicam dados
ausentes ou percentis indefinidos, por exemplo quando não houve HTTP 201. O
resumo `by_rate.csv` inclui número de valores válidos, mínimo, P25, mediana, P75 e
máximo de cada métrica por nível. Os detalhes em `timeline_by_rate/` e
`pool_by_rate/` evitam comprimir horas de coleta num único gráfico; `r1`, `r2`,
etc. identificam as repetições. Latências HTTP/SQL são exibidas em segundos; event
loop permanece em milissegundos. As tabelas mantêm as unidades dos seus cabeçalhos.

`--plots-only` preserva `levels.csv`, `windows.csv` e os resultados brutos; gera
`by_rate.csv` e regrava as figuras. Ainda lê `application.log` para os gráficos do
pool, guardando somente os campos necessários. Esse modo usa as janelas já
calculadas e não aceita alterações de `--window-seconds` ou `--settling-seconds`.

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
