#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHITECTURE="${1:-${ARCHITECTURE:-monolith}}"
START_RATE="${START_RATE:-10}"
MAX_RATE="${MAX_RATE:-60}"
STEP_RATE="${STEP_RATE:-10}"
DURATION="${DURATION:-1m}"
REPETITIONS="${REPETITIONS:-3}"
PRE_ALLOCATED_VUS="${PRE_ALLOCATED_VUS:-100}"
MAX_VUS="${MAX_VUS:-500}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-10}"
HEALTH_TIMEOUT_SECONDS="${HEALTH_TIMEOUT_SECONDS:-120}"
SEED_TIMEOUT_SECONDS="${SEED_TIMEOUT_SECONDS:-120}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT_DIR/experiments/datasets/pilot/$ARCHITECTURE}"
ACTIVE_ENVIRONMENT=0
RESULTS_RESERVED=0
CURRENT_LOG=""

fail() { echo "Erro: $*" >&2; exit 1; }

select_architecture() {
  case "$ARCHITECTURE" in
    monolith)
      COMPOSE_FILES=(-f "$ROOT_DIR/environment/compose/compose.monolith.yaml")
      BASE_URL="http://127.0.0.1:3000"
      SEED_MODE=monolith
      HEALTH_PATHS=(/health)
      ;;
    distributed-baseline|orchestrated-saga)
      export PILOT_IMPLEMENTATION="$ARCHITECTURE"
      COMPOSE_FILES=(
        -f "$ROOT_DIR/environment/compose/compose.base.yaml"
        -f "$ROOT_DIR/environment/compose/compose.pilot.yaml"
      )
      BASE_URL="http://127.0.0.1:8000"
      SEED_MODE=distributed
      HEALTH_PATHS=(/order/health /warehouse/health /billing/health /shipping/health)
      ;;
    tcc|two-phase-commit|choreographed-saga|outbox)
      fail "$ARCHITECTURE tem Compose apenas para infraestrutura e serviços com localhost fixo. O piloto automatizado suporta monolith, distributed-baseline e orchestrated-saga."
      ;;
    *) fail "Arquitetura desconhecida: $ARCHITECTURE" ;;
  esac
  COMPOSE=(docker compose -p "pilot-$ARCHITECTURE" "${COMPOSE_FILES[@]}")
}

validate_configuration() {
  (( $# <= 1 )) || fail "Uso: bash scripts/run-pilot.sh [arquitetura]"
  local name dependency
  for name in START_RATE MAX_RATE STEP_RATE REPETITIONS PRE_ALLOCATED_VUS MAX_VUS HEALTH_TIMEOUT_SECONDS SEED_TIMEOUT_SECONDS; do
    [[ "${!name}" =~ ^[1-9][0-9]{0,8}$ ]] || fail "$name deve ser um inteiro positivo (até 9 dígitos)."
  done
  [[ "$COOLDOWN_SECONDS" =~ ^(0|[1-9][0-9]{0,8})$ ]] || fail "COOLDOWN_SECONDS deve ser um inteiro >= 0."
  (( MAX_RATE >= START_RATE )) || fail "MAX_RATE deve ser >= START_RATE."
  (( MAX_VUS >= PRE_ALLOCATED_VUS )) || fail "MAX_VUS deve ser >= PRE_ALLOCATED_VUS."
  for dependency in docker k6 curl python3 timeout tee; do
    command -v "$dependency" >/dev/null || fail "Dependência ausente: $dependency"
  done
  python3 - "$DURATION" <<'PY'
import math
import re
import sys
value = sys.argv[1]
parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
units = {"ms": .001, "s": 1, "m": 60, "h": 3600}
seconds = sum(float(amount) * units[unit] for amount, unit in parts)
if "".join(amount + unit for amount, unit in parts) != value or not math.isfinite(seconds) or seconds <= 0:
    sys.exit("DURATION inválida: use duração positiva com unidades ms, s, m ou h.")
PY
  "${COMPOSE[@]}" version >/dev/null
  "${COMPOSE[@]}" config --quiet
  local compose_help
  compose_help="$("${COMPOSE[@]}" up --help)"
  [[ "$compose_help" == *--wait-timeout* ]] || fail "Docker Compose precisa suportar up --wait --wait-timeout."
  [[ ! -e "$RESULTS_DIR" ]] || fail "Diretório já existe: $RESULTS_DIR. Use RESULTS_DIR para uma nova série; resultados não serão sobrescritos."
}

cleanup() {
  "${COMPOSE[@]}" down --volumes --remove-orphans --timeout 10
}

finish() {
  local status=$?
  trap - EXIT
  if (( ACTIVE_ENVIRONMENT )); then
    if [[ -n "$CURRENT_LOG" ]]; then
      "${COMPOSE[@]}" logs --no-color >"$CURRENT_LOG" 2>&1 || true
    fi
    cleanup || { (( status != 0 )) || status=1; }
  fi
  if (( RESULTS_RESERVED )); then
    python3 "$ROOT_DIR/scripts/summarize-pilot.py" "$RESULTS_DIR" || { (( status != 0 )) || status=1; }
    echo "Resultados do piloto: $RESULTS_DIR"
  fi
  exit "$status"
}

wait_for_http() {
  local deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS)) path remaining
  for path in "${HEALTH_PATHS[@]}"; do
    while true; do
      remaining=$((deadline - SECONDS))
      (( remaining > 0 )) || fail "Timeout de saúde em $BASE_URL$path"
      if curl --fail --silent --show-error --max-time "$((remaining < 3 ? remaining : 3))" "$BASE_URL$path" >/dev/null 2>&1; then
        break
      fi
      sleep 1
    done
  done
}

run_trial() {
  local rate="$1" repetition="$2" prefix="rate${1}_run${2}"
  local console_file="$RESULTS_DIR/${prefix}_console.log"
  CURRENT_LOG="$RESULTS_DIR/logs/${prefix}_docker.log"
  echo "Arquitetura: $ARCHITECTURE; taxa: $rate pedidos/s; repetição: $repetition/$REPETITIONS"
  cleanup
  ACTIVE_ENVIRONMENT=1
  "${COMPOSE[@]}" up --detach --no-build --wait --wait-timeout "$HEALTH_TIMEOUT_SECONDS"
  wait_for_http
  timeout --foreground "${SEED_TIMEOUT_SECONDS}s" bash "$ROOT_DIR/scripts/seed.sh" "$SEED_MODE" \
    >"$RESULTS_DIR/logs/${prefix}_seed.log" 2>&1
  BASE_URL="$BASE_URL" WAREHOUSE_PREFIX=/warehouse \
    k6 run --quiet "$ROOT_DIR/experiments/k6/warmup.js" \
    >"$RESULTS_DIR/logs/${prefix}_warmup.log" 2>&1

  local pipeline_status
  set +e
  ARCHITECTURE="$ARCHITECTURE" RATE="$rate" REPETITION="$repetition" \
  RUN_ID="$SUITE_ID-$prefix" DURATION="$DURATION" \
  PRE_ALLOCATED_VUS="$PRE_ALLOCATED_VUS" MAX_VUS="$MAX_VUS" \
  BASE_URL="$BASE_URL" WAREHOUSE_PREFIX=/warehouse LOG_ERRORS="${LOG_ERRORS:-false}" \
  SUMMARY_JSON="$RESULTS_DIR/$prefix.json" \
    k6 run --out "csv=$RESULTS_DIR/$prefix.csv" "$ROOT_DIR/experiments/k6/pilot.js" 2>&1 | tee "$console_file"
  pipeline_status=("${PIPESTATUS[@]}")
  set -e
  echo "k6_exit_code=${pipeline_status[0]}" >>"$console_file"
  if (( pipeline_status[0] != 0 )); then
    echo "k6 falhou com código ${pipeline_status[0]}; série interrompida." >&2
    # O trap coleta logs e limpa o ambiente preservando este código,
    # inclusive se a coleta ou a limpeza também falhar.
    return "${pipeline_status[0]}"
  fi
  (( pipeline_status[1] == 0 )) || fail "Falha ao salvar a saída do k6 com tee."
  "${COMPOSE[@]}" logs --no-color >"$CURRENT_LOG" 2>&1
  cleanup
  ACTIVE_ENVIRONMENT=0
  [[ -s "$RESULTS_DIR/$prefix.json" ]] || fail "k6 não gerou o resumo JSON esperado."
  sleep "$COOLDOWN_SECONDS"
}

main() {
  select_architecture
  validate_configuration "$@"
  # mkdir exclusivo também protege contra duas séries no mesmo diretório.
  mkdir -p "$(dirname "$RESULTS_DIR")"
  mkdir "$RESULTS_DIR"
  RESULTS_DIR="$(cd "$RESULTS_DIR" && pwd)"
  RESULTS_RESERVED=1
  mkdir "$RESULTS_DIR/logs"
  SUITE_ID="$(date -u +%Y%m%dT%H%M%S)-$$"
  trap finish EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  k6 version >"$RESULTS_DIR/logs/k6-version.log"
  "${COMPOSE[@]}" config >"$RESULTS_DIR/logs/compose.yaml"
  echo "Construindo imagens uma única vez..."
  "${COMPOSE[@]}" build >"$RESULTS_DIR/logs/build.log" 2>&1
  local rate repetition
  for ((rate=START_RATE; rate<=MAX_RATE; rate+=STEP_RATE)); do
    for ((repetition=1; repetition<=REPETITIONS; repetition++)); do
      run_trial "$rate" "$repetition"
    done
  done
}

main "$@"
