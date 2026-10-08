# Piloto de capacidade

O piloto inicia pedidos com `constant-arrival-rate`, sem espera artificial entre
requisições. Ele fornece dados para escolher posteriormente cargas baixa, média e
alta, usando as mesmas taxas absolutas entre arquiteturas. Não seleciona níveis
automaticamente e não mede a conclusão de transações assíncronas.

## Execução

Dependências: Bash, Docker com Compose (`include`, `up --wait --wait-timeout`),
k6, curl, Python 3, GNU timeout e tee. Não são necessárias bibliotecas Python.

```bash
bash scripts/run-pilot.sh monolith
bash scripts/run-pilot.sh distributed-baseline
bash scripts/run-pilot.sh orchestrated-saga

START_RATE=10 MAX_RATE=60 STEP_RATE=10 DURATION=1m REPETITIONS=3 \
PRE_ALLOCATED_VUS=100 MAX_VUS=500 COOLDOWN_SECONDS=10 \
bash scripts/run-pilot.sh monolith
```

A seleção também aceita `ARCHITECTURE`. O monólito usa o Compose existente e a
porta 3000; as duas versões distribuídas usam `compose.base.yaml` mais o overlay
`compose.pilot.yaml`, na porta 8000. O overlay seleciona o contexto de build e
acrescenta verificações de saúde nos quatro serviços. Todas as aplicações e bancos
devem estar saudáveis antes de validar os endpoints públicos. Cada serviço tem uma
instância. O proxy segue a configuração existente.

Os Compose em `implementation/{tcc,two-phase-commit,choreographed-saga,outbox}`
contêm apenas bancos/broker. Essas aplicações ainda têm endereços `localhost`
fixos e não oferecem a mesma configuração de Docker/health checks. O executor
rejeita essas seleções com uma mensagem explícita. O script k6 pode ser usado
diretamente quando uma dessas aplicações estiver disponível:

```bash
ARCHITECTURE=outbox RUN_ID=ensaio-unico RATE=10 DURATION=1m \
BASE_URL=http://127.0.0.1:3000 WAREHOUSE_PREFIX=/warehouse \
SUMMARY_JSON=/tmp/pilot-outbox-ensaio-unico.json \
k6 run experiments/k6/pilot.js
```

Na execução direta, escolha um `SUMMARY_JSON` novo: o k6 sobrescreve o arquivo
informado. Sem essa variável, o resumo é emitido somente no stdout. O executor
shell protege toda a série contra sobrescrita.

As imagens são construídas uma vez. Cada ensaio remove os volumes do projeto
Compose do piloto, inicia um estado limpo, carrega o seed existente (100 produtos
com 100.000 unidades por padrão), executa o warm-up existente e só então mede a
carga. O warm-up possui saída separada e não contribui para as métricas do piloto.
Uma falha no k6 interrompe a série com o código de saída original e preserva logs
e os resumos disponíveis. `HEALTH_TIMEOUT_SECONDS` e `SEED_TIMEOUT_SECONDS`
valem 120 por padrão. As portas e os nomes fixos de containers existentes impedem
executar simultaneamente ambientes que disputem esses recursos.

## Resultados

Por padrão, `experiments/datasets/pilot/<arquitetura>/` contém
`rate10_run1.json`, `rate10_run1.csv`, `rate10_run1_console.log`, os demais ensaios,
`consolidated.csv` e `logs/` com Docker, seed, warm-up, build, versão do k6 e Compose
resolvido. Um diretório existente faz o executor parar antes do build. Para outra
série, indique um diretório novo:

```bash
RESULTS_DIR=experiments/datasets/pilot/monolith/segunda-serie \
bash scripts/run-pilot.sh monolith
```

## Interpretação

- `rate`: taxa oferecida em pedidos/s durante `duration_seconds`.
- `started_requests_per_second`: requisições efetivamente iniciadas divididas
  pela duração configurada; `started_requests` é registrado antes do POST.
- `throughput`: respostas HTTP observadas, inclusive erros, divididas pela
  janela de observação. `requests` exclui o setup.
- `accepted_orders_per_second`: pedidos com HTTP 201 divididos pela mesma janela.
  `accepted_orders` conta aceitação, sem presumir confirmação final da Saga.
- `observation_seconds`: duração configurada mais eventual drenagem das últimas
  requisições. Exclui setup, seed e warm-up. Portanto, throughput pode ficar abaixo
  da taxa iniciada mesmo sem descarte, especialmente em ensaios curtos.
- `p50`, `p95`, `p99` e `latency_mean_ms`: latência HTTP em milissegundos,
  calculada sobre todas as respostas do workload. O tempo de espera e o tempo de
  espera das respostas 201 também ficam no JSON em `metrics`.
- `error_rate`: proporção das respostas diferentes de 201; `http_error_rate`
  usa a mesma expectativa. `timeouts` conta timeouts do cliente; `server_errors`
  conta HTTP 5xx, incluindo 504. Sem respostas, taxas de erro e percentis são nulos.
- `iterations`: iterações concluídas; `started_requests` também inclui aquelas
  que chegaram a iniciar e podem ter sido interrompidas.
- `dropped_iterations`: iterações que o gerador não iniciou, separadas dos erros
  HTTP. Inspecione também os limites de VUs e os avisos do k6. CPU, memória e rede
  do gerador podem saturar; aumentar `MAX_VUS` não garante corrigir o problema.

Os CSVs brutos identificam `phase=setup` e `phase=benchmark`. Os percentis do JSON
usam submétricas `phase:benchmark`, sem misturar a busca de produtos. O consolidado
contém uma linha por resumo disponível, ordenada por taxa e repetição; uma falha
anterior à geração do resumo permanece registrada nos logs, sem uma linha fictícia.

Compare as repetições para encontrar estabilidade em baixa carga, comportamento
intermediário e a região onde p95/p99 aumentam, a aceitação deixa de acompanhar a
taxa oferecida ou surgem erros e descartes. Registre as condições do host/gerador
para distinguir limitação do sistema e do gerador. Escolha os três níveis após
examinar os resultados de todas as arquiteturas.
