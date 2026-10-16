#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE="${COMPOSE:-$ROOT_DIR/environment/compose/compose.base.yaml}"
BENCHMARK="$ROOT_DIR/experiments/k6/arrival-rate.js"
WARMUP="$ROOT_DIR/experiments/k6/warmup.js"
SEED="$ROOT_DIR/scripts/seed.sh"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
WAREHOUSE_PREFIX="${WAREHOUSE_PREFIX:-/warehouse}"
START_RATE="${START_RATE:-10}"
MAX_RATE="${MAX_RATE:-60}"
STEP_RATE="${STEP_RATE:-10}"
REPETITIONS="${REPETITIONS:-1}"
DURATION="${DURATION:-2m}"
PRE_ALLOCATED_VUS="${PRE_ALLOCATED_VUS:-100}"
MAX_VUS="${MAX_VUS:-300}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-10}"
# A unique directory per pilot stage avoids mixing or overwriting old datasets.
RESULTS_DIR="${RESULTS_DIR:-$ROOT_DIR/experiments/datasets/distributed-baseline-arrival-pilot}"
LOGS_DIR="$RESULTS_DIR/logs"
mkdir -p "$RESULTS_DIR" "$LOGS_DIR"

cleanup() {
  echo 'Removing containers and volumes...'
  docker compose -f "$COMPOSE" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
interrupt() { echo 'Interrupted'; exit 130; }
trap interrupt INT TERM
trap cleanup EXIT

wait_for_service() {
  #echo 'Waiting for monolith /health (without a fixed timeout)...'
  #local attempts=0
  #until curl -fsS "$BASE_URL/health" >/dev/null 2>&1; do
  #  sleep 1
  #  ((attempts += 1))
  #  if (( attempts % 10 == 0 )); then echo "Still waiting... ${attempts}s"; fi
  #done
  #echo 'Monolith is healthy.'
  #sleep 5

  echo "Waiting for distributed services..."
  
  local endpoints=(
    "$BASE_URL/order/health"
    "$BASE_URL/warehouse/health"
    "$BASE_URL/billing/health"
    "$BASE_URL/shipping/health"
  )

  for endpoint in "${endpoints[@]}"; do
    echo "Waiting for $endpoint"

    until curl -fs "$endpoint" >/dev/null 2>&1; do
      sleep 1
    done
  done
}

check_positive() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[0-9]+$ ]] || (( value < 1 )); then
    echo "Error: $name must be a positive integer, got '$value'" >&2
    exit 1
  fi
}

validate_configuration() {
  for path in "$COMPOSE" "$BENCHMARK" "$WARMUP" "$SEED"; do
    if [[ ! -f "$path" ]]; then echo "Missing file: $path" >&2; exit 1; fi
  done
  for cmd in docker curl k6; do
    command -v "$cmd" >/dev/null || { echo "Missing dependency: $cmd" >&2; exit 1; }
  done
  for variable in START_RATE MAX_RATE STEP_RATE REPETITIONS PRE_ALLOCATED_VUS MAX_VUS; do
    check_positive "$variable" "${!variable}"
  done
  if (( MAX_RATE < START_RATE || MAX_VUS < PRE_ALLOCATED_VUS )); then
    echo 'Error: MAX_RATE >= START_RATE and MAX_VUS >= PRE_ALLOCATED_VUS are required.' >&2
    exit 1
  fi
  if ! [[ "$COOLDOWN_SECONDS" =~ ^[0-9]+$ ]]; then
    echo 'Error: COOLDOWN_SECONDS must be a nonnegative integer.' >&2
    exit 1
  fi
  # k6 duration values such as 30s, 2m, 1h; keep parser unambiguous for graphs.
  if ! [[ "$DURATION" =~ ^[1-9][0-9]*(s|m|h)$ ]]; then
    echo 'Error: DURATION must look like 30s, 2m or 1h.' >&2
    exit 1
  fi
  if compgen -G "$RESULTS_DIR/rate*_run*.csv" >/dev/null; then
    echo "Error: CSV files already exist under $RESULTS_DIR. Choose a fresh RESULTS_DIR." >&2
    exit 1
  fi
}

save_logs() {
  local prefix="$1"
  docker compose -f "$COMPOSE" logs --no-color >"$LOGS_DIR/${prefix}_docker.log" 2>&1 || true
}

run_benchmark() {
  local rate="$1" repetition="$2"
  local prefix="rate${rate}_run${repetition}"
  local csv_file="$RESULTS_DIR/${prefix}.csv"
  local console_file="$RESULTS_DIR/${prefix}_console.log"
  local metadata_file="$RESULTS_DIR/${prefix}.meta"
  echo "===== Rate: ${rate} req/s | Run: ${repetition}/${REPETITIONS} ====="
  cleanup
  docker compose -f "$COMPOSE" up --detach
  wait_for_service
  if ! bash "$SEED" distributed; then
    save_logs "${prefix}_seed-failure"
    echo 'Seed failed. Stopping experiment.' >&2
    exit 1
  fi
  echo 'Running warm-up...'
  if ! BASE_URL="$BASE_URL" WAREHOUSE_PREFIX="$WAREHOUSE_PREFIX" k6 run --quiet "$WARMUP"; then
    save_logs "${prefix}_warmup-failure"
    echo 'Warm-up failed. Stopping experiment.' >&2
    exit 1
  fi
  echo 'Running sustained-arrival-rate benchmark...'
  local k6_exit_code=0
  BASE_URL="$BASE_URL" WAREHOUSE_PREFIX="$WAREHOUSE_PREFIX" \
  RATE="$rate" DURATION="$DURATION" PRE_ALLOCATED_VUS="$PRE_ALLOCATED_VUS" \
  MAX_VUS="$MAX_VUS" k6 run --out "csv=$csv_file" "$BENCHMARK" \
    > >(tee "$console_file") 2>&1 || k6_exit_code=$?
  save_logs "$prefix"
  cat >"$metadata_file" <<META
rate=$rate
repetition=$repetition
duration=$DURATION
pre_allocated_vus=$PRE_ALLOCATED_VUS
max_vus=$MAX_VUS
k6_exit_code=$k6_exit_code
finished_at=$(date --iso-8601=seconds)
META
  if (( k6_exit_code != 0 )); then
    echo "WARNING: k6 exit code ${k6_exit_code}; inspect ${console_file} before accepting this run."
  fi
  cleanup
  echo "Cooling down for ${COOLDOWN_SECONDS}s..."
  sleep "$COOLDOWN_SECONDS"
}

main() {
  validate_configuration
  docker compose -f "$COMPOSE" build --quiet
  printf 'Rates: %s..%s step %s | repetitions=%s | duration=%s | results=%s\n' \
    "$START_RATE" "$MAX_RATE" "$STEP_RATE" "$REPETITIONS" "$DURATION" "$RESULTS_DIR"
  local rate repetition
  for (( rate=START_RATE; rate<=MAX_RATE; rate+=STEP_RATE )); do
    for (( repetition=1; repetition<=REPETITIONS; repetition++ )); do
      run_benchmark "$rate" "$repetition"
    done
  done
  echo "Pilot completed: $RESULTS_DIR"
}
main "$@"
