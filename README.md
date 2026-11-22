# Como rodar os testes

Execute os comandos na raiz do repositório, em Linux, com Bash, Docker com Compose, k6, Python 3 e curl. O Matplotlib é necessário para gerar gráficos.

## Desempenho com PostgreSQL

Para rodar a suíte independente com as cinco arquiteturas, oito repetições por taxa e bancos novos a cada repetição:

```bash
sudo SUITE_ID="independente-1a100-$(date -u +%Y%m%dT%H%M%SZ)" \
  RATES=1,2,5,10,15,20,30,40,50,60,70,80,90,100 \
  REPETITIONS=8 DURATION=3m WARMUP_DURATION=1m \
  K6_CSV_STORAGE=tmpfs bash run-independent-suite.sh
```

Para um teste rápido do fluxo:

```bash
sudo ARCHITECTURES=distributed-baseline RATES=1 REPETITIONS=1 \
  DURATION=30s WARMUP_DURATION=15s SETTLING_SECONDS=10 \
  COOLDOWN_SECONDS=5 bash run-independent-suite.sh
```

Cada repetição da suíte independente prepara o catálogo, faz o aquecimento e mede a carga. A coreografada também recebe um RabbitMQ novo. Os dados de cada repetição são coletados antes da remoção dos volumes daquele ensaio.

Outros comandos para os testes de desempenho:

- `sudo ARCHITECTURES=distributed-baseline,tcc RATES=1,5,10 REPETITIONS=2 bash run-suite.sh` executa uma suíte cumulativa, mantendo os dados entre repetições de cada arquitetura.
- `sudo RATES=1,5,10 REPETITIONS=2 bash run-mock.sh` executa as cinco arquiteturas com armazenamento em memória.
- `sudo ARCHITECTURE=distributed-baseline RATES=1,5,10 REPETITIONS=2 bash run.sh` executa um teste individual.

As suítes salvam os resultados em `experiments/datasets/pilot/<SUITE_ID>/`. Na suíte independente, os dados brutos ficam em `<arquitetura>/trials/<etapa>/` e os gráficos comparativos em `comparison/`. O teste individual salva em `experiments/datasets/pilot/<RUN_ID>/`.

### Variáveis de desempenho

| Variável | Padrão | Uso |
|---|---|---|
| `SUITE_ID` / `RUN_ID` | Gerado automaticamente | Identificador da suíte ou do teste individual. Use um ID novo a cada execução. |
| `ARCHITECTURES` / `ARCHITECTURE` | Cinco na suíte independente e no mock. Quatro na cumulativa. Baseline no teste individual. | Arquiteturas separadas por vírgula na suíte ou uma arquitetura no teste individual. |
| `RATES` | 1 a 100 nos 14 níveis do exemplo na suíte independente. `1,2,5,10,15,20,30,40` nos demais. | Taxas crescentes de requisições iniciadas por segundo. |
| `REPETITIONS` | `8` na independente, `1` nos demais | Repetições por taxa. |
| `DURATION` | `3m` | Duração de cada repetição medida. |
| `WARMUP_DURATION`, `WARMUP_RATE` | `1m`, `1` | Duração e taxa do aquecimento. |
| `SETTLING_SECONDS` | `30` | Janela inicial destacada na análise. Deve ser menor que a duração medida. |
| `COOLDOWN_SECONDS`, `RECOVERY_TIMEOUT_SECONDS` | `30`, `60` | Pausa após cada etapa e prazo de espera na recuperação. |
| `PRE_ALLOCATED_VUS`, `MAX_VUS` | 25 × maior taxa, mesmo valor para o máximo | Quantidade inicial e limite de usuários virtuais do k6. |
| `DB_MODE` | `postgres` | `postgres` ou `mock`. A suíte independente exige PostgreSQL e `run-mock.sh` seleciona o mock. |
| `DB_POOL_MAX` | Valor de cada serviço | Define o tamanho máximo do pool nos ambientes isolados PostgreSQL. |
| `PRELOAD_ORDERS` | `0` | Pedidos históricos sintéticos. Disponível no modo cumulativo PostgreSQL isolado. |
| `SEED_DATA` | `auto` | `auto` prepara um catálogo vazio, `1` exige catálogo vazio para prepará-lo, `0` exige catálogo já preenchido. |
| `PRODUCT_COUNT`, `INITIAL_STOCK` | `100`, `100000` | Produtos criados no seed e estoque inicial por produto. |
| `K6_CSV_STORAGE`, `K6_CSV_TMPFS_ROOT` | `disk`, `/dev/shm` | Use `tmpfs` para gravar os CSVs temporariamente em RAM. Os CSVs são copiados para a pasta de resultados ao final da coleta. |
| `DB_DIAGNOSTICS`, `COLLECT_HOST` | `1`, `1` | Ativam os diagnósticos da aplicação e a coleta do host. Use `0` para desativar. O mock desativa diagnósticos de banco. |
| `AUTO_PLOT`, `PLOT_PYTHON` | `1`, Python de `.venv` ou `python3` | Use `AUTO_PLOT=0` para gerar só as tabelas. `PLOT_PYTHON` aceita o caminho do interpretador para gráficos. |
| `PILOT_ISOLATED` | `1` nas suítes. No teste individual, `0` para o baseline e `1` para as demais. | Use `1` em `run.sh` para criar um projeto Docker isolado. |
| `BASELINE_RUN` | Vazio | Caminho de um baseline já analisado, aceito por `run-suite.sh` quando o baseline está fora de `ARCHITECTURES`. |

Identificadores de arquitetura disponíveis: `distributed-baseline`, `tcc`, `two-phase-commit`, `orchestrated-saga`, `choreographed-saga`. A suíte cumulativa padrão inclui as quatro últimas.

### Prazos de preparação

Valores em segundos. Os prazos das requisições do benchmark são definidos separadamente.

| Variável | Padrão | Etapa |
|---|---:|---|
| `DB_START_TIMEOUT_SECONDS` | 1800 | Inicialização dos bancos. |
| `BROKER_START_TIMEOUT_SECONDS` | 1800 | Inicialização do RabbitMQ. |
| `HTTP_START_TIMEOUT_SECONDS` | 600 | Inicialização e verificação dos serviços HTTP. |
| `SEED_TIMEOUT_SECONDS` | 600 | Criação do catálogo. |
| `COLLECTOR_START_TIMEOUT_SECONDS` | 120 | Espera pela primeira amostra do PostgreSQL. |
| `SETUP_HTTP_TIMEOUT_SECONDS` | 60 | Cada consulta HTTP de catálogo na preparação. |

## Injeção de falhas

```bash
sudo REPETITIONS=1 FAULT_SECONDS=5 OBSERVE_SECONDS=20 bash run-faults.sh
```

Os resultados ficam em `experiments/datasets/faults/<RUN_ID>/`.

| Variável | Padrão | Uso |
|---|---|---|
| `RUN_ID` | Gerado automaticamente | Nome da execução. |
| `ARCHITECTURES` | As cinco arquiteturas | Lista separada por vírgulas. |
| `REPETITIONS` | `20` | Repetições por cenário e arquitetura. |
| `FAULT_SECONDS` | `5` | Duração da falha transitória no pagamento. |
| `OBSERVE_SECONDS` | `60` | Tempo de observação. |
| `CLEANUP_TIMEOUT_SECONDS` | `300` | Prazo para encerrar o ambiente Docker. |

## Testes dos scripts

```bash
python3 -m unittest scripts/pilot/test_independent.py scripts/pilot/test_profiles.py \
  scripts/pilot/test_suite.py scripts/diagnostics/test_pilot_runner.py
```
