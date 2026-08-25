#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COMPOSE="$ROOT_DIR/environment/compose/compose.base.yaml"
BENCHMARK_DIR="$ROOT_DIR/experiments/k6"
RESULTS_DIR="$ROOT_DIR/experiments/datasets/baseline"
LOGS_DIR="$RESULTS_DIR/logs"

REPETITIONS="${1:-10}"
RANDOM_SEED="${RANDOM_SEED:-20260803}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-15}"

SCHEDULE_FILE="$RESULTS_DIR/execution_schedule.csv"

LOADS=(
  "low"
  "medium"
  "high"
)

mkdir -p "$RESULTS_DIR"
mkdir -p "$LOGS_DIR"

cleanup() {
  echo "Removing containers and volumes..."

  docker compose \
    -f "$COMPOSE" \
    down \
    --volumes \
    --remove-orphans \
    >/dev/null 2>&1 || true
}

handle_interrupt() {
  echo
  echo "Execution interrupted."

  cleanup

  exit 130
}

trap handle_interrupt INT TERM
trap cleanup EXIT

wait_for_services() {
  echo "Waiting for all services to become healthy..."

  local max_attempts=120
  local attempt=1

  while (( attempt <= max_attempts )); do
    if curl -fs \
      "http://127.0.0.1:8000/warehouse/health" \
      >/dev/null 2>&1 &&
      curl -fs \
        "http://127.0.0.1:8000/shipping/health" \
        >/dev/null 2>&1 &&
      curl -fs \
        "http://127.0.0.1:8000/billing/health" \
        >/dev/null 2>&1 &&
      curl -fs \
        "http://127.0.0.1:8000/order/health" \
        >/dev/null 2>&1; then

      echo "All services are healthy."

      return 0
    fi

    if (( attempt == max_attempts )); then
      echo "Error: services did not become healthy in time."

      docker compose \
        -f "$COMPOSE" \
        logs \
        --no-color

      return 1
    fi

    sleep 1

    ((attempt++))
  done
}

start_environment() {
  cleanup

  echo "Starting clean environment..."

  docker compose \
    -f "$COMPOSE" \
    up \
    --detach \
    >/dev/null

  wait_for_services
}

seed_environment() {
  echo "Seeding databases..."

  bash "$ROOT_DIR/scripts/seed.sh" distributed \
    >/dev/null
}

shuffle_loads() {
  local block="$1"

  # Gera uma ordem reproduzível para cada bloco.
  # A mesma RANDOM_SEED produz sempre o mesmo cronograma.
  printf '%s\n' "${LOADS[@]}" |
    shuf \
      --random-source=<(
        printf '%s-%s\n' "$RANDOM_SEED" "$block" |
          sha256sum |
          awk '{ print $1 }' |
          xxd -r -p
      )
}

run_benchmark() {
  local block="$1"
  local position="$2"
  local load="$3"

  local script="$BENCHMARK_DIR/${load}.js"
  local result_prefix="${load}_block${block}_position${position}"

  local csv_file="$RESULTS_DIR/${result_prefix}.csv"
  local console_file="$RESULTS_DIR/${result_prefix}_console.log"
  local docker_log_file="$LOGS_DIR/${result_prefix}_docker.log"

  if [[ ! -f "$script" ]]; then
    echo "Error: k6 script not found: $script"

    return 1
  fi

  echo
  echo "=================================================="
  echo "Block:       $block/$REPETITIONS"
  echo "Position:    $position/3"
  echo "Load:        $load"
  echo "Script:      $script"
  echo "CSV output:  $csv_file"
  echo "=================================================="

  start_environment
  seed_environment

  echo "Running benchmark..."

  local k6_exit_code=0

  k6 run \
    --out "csv=$csv_file" \
    "$script" \
    2>&1 |
    tee "$console_file" ||
    k6_exit_code="${PIPESTATUS[0]}"

  echo "Saving Docker logs..."

  docker compose \
    -f "$COMPOSE" \
    logs \
    --no-color \
    >"$docker_log_file" 2>&1 || true

  {
    echo
    echo "k6_exit_code=$k6_exit_code"
    echo "block=$block"
    echo "position=$position"
    echo "load=$load"
    echo "random_seed=$RANDOM_SEED"
    echo "finished_at=$(date --iso-8601=seconds)"
  } >>"$console_file"

  if (( k6_exit_code != 0 )); then
    echo "Warning: benchmark finished with exit code $k6_exit_code."
    echo "The experiment will continue so all results are preserved."
  else
    echo "Benchmark finished successfully."
  fi

  cleanup

  echo "Cooling down for ${COOLDOWN_SECONDS}s..."

  sleep "$COOLDOWN_SECONDS"
}

validate_configuration() {
  if ! [[ "$REPETITIONS" =~ ^[1-9][0-9]*$ ]]; then
    echo "Error: repetitions must be a positive integer."

    exit 1
  fi

  local required_files=(
    "$COMPOSE"
    "$ROOT_DIR/scripts/seed.sh"
    "$BENCHMARK_DIR/low.js"
    "$BENCHMARK_DIR/medium.js"
    "$BENCHMARK_DIR/high.js"
  )

  for file in "${required_files[@]}"; do
    if [[ ! -f "$file" ]]; then
      echo "Error: required file not found: $file"

      exit 1
    fi
  done

  local required_commands=(
    docker
    curl
    k6
    shuf
    sha256sum
    xxd
  )

  for command in "${required_commands[@]}"; do
    if ! command -v "$command" >/dev/null 2>&1; then
      echo "Error: required command not found: $command"

      exit 1
    fi
  done
}

create_schedule() {
  echo "block,position,load,random_seed" >"$SCHEDULE_FILE"

  for block in $(seq 1 "$REPETITIONS"); do
    local position=1

    while IFS= read -r load; do
      printf '%s,%s,%s,%s\n' \
        "$block" \
        "$position" \
        "$load" \
        "$RANDOM_SEED" \
        >>"$SCHEDULE_FILE"

      ((position++))
    done < <(shuffle_loads "$block")
  done
}

main() {
  validate_configuration

  echo "Pre-building Docker images..."

  docker compose \
    -f "$COMPOSE" \
    build \
    --quiet

  create_schedule

  echo
  echo "Starting benchmark suite."
  echo "Blocks:          $REPETITIONS"
  echo "Treatments:      $((REPETITIONS * 3))"
  echo "Random seed:     $RANDOM_SEED"
  echo "Schedule file:   $SCHEDULE_FILE"
  echo "Results:         $RESULTS_DIR"
  echo

  tail -n +2 "$SCHEDULE_FILE" |
    while IFS=',' read -r block position load seed; do
      run_benchmark \
        "$block" \
        "$position" \
        "$load"
    done

  echo
  echo "=================================================="
  echo "All experiments finished."
  echo "Total executions: $((REPETITIONS * 3))"
  echo "Results directory: $RESULTS_DIR"
  echo "Schedule: $SCHEDULE_FILE"
  echo "=================================================="

  # Evita executar cleanup novamente com o ambiente já removido.
  trap - EXIT
}

main "$@"
