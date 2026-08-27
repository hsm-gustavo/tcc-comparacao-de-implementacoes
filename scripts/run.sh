#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COMPOSE="$ROOT_DIR/environment/compose/compose.monolith.yaml"

BENCHMARK="$ROOT_DIR/experiments/k6/concurrent-users.js"

RESULTS_DIR="$ROOT_DIR/experiments/datasets/monolith"
LOGS_DIR="$RESULTS_DIR/logs"

# config
START_USERS="${START_USERS:-100}"
MAX_USERS="${MAX_USERS:-1000}"
STEP_USERS="${STEP_USERS:-100}"

# reps per level
REPETITIONS="${REPETITIONS:-10}"

COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-10}"

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

wait_for_service() {
  echo "Waiting for monolith to become healthy..."

  local attempts=0

  until curl -fs "http://127.0.0.1:3000/health" >/dev/null 2>&1; do
    sleep 1

    ((attempts += 1))

    if (( attempts % 10 == 0 )); then
      echo "Still waiting... ${attempts}s"
    fi
  done

  echo "Monolith is healthy."
}

wait_for_all_monolith_replicas() {
  local expected=8

  echo "Waiting for all $expected monolith replicas to become healthy..."

  while true; do
    healthy_count=$(
      docker compose -f "$COMPOSE" ps --format json \
        | jq -s '[.[] | select(.Service == "monolith" and .Health == "healthy")] | length'
    )

    echo "Healthy replicas: $healthy_count/$expected"

    if [ "$healthy_count" -eq "$expected" ]; then
      echo "All monolith replicas are healthy."
      break
    fi

    sleep 1
  done

  echo "Stabilizing..."
  sleep 5
}

start_environment() {
  cleanup

  echo "Starting clean monolith environment..."

  docker compose \
    -f "$COMPOSE" \
    up \
    --detach \
    >/dev/null

  wait_for_all_monolith_replicas
}

seed_environment() {
  echo "Seeding monolith..."

  if ! bash "$ROOT_DIR/scripts/seed.sh" "monolith"; then
    echo "Failed to seed monolith. Saving logs..."
    docker compose -f "$COMPOSE" logs --no-color \
      > "$LOGS_DIR/users${users}_run${run}_seed-failure.log"

    echo "Logs saved."

    exit 1
  fi
}

run_benchmark() {
  local users="$1"
  local repetition="$2"

  local prefix="users${users}_run${repetition}"

  local csv_file="$RESULTS_DIR/${prefix}.csv"
  local console_file="$RESULTS_DIR/${prefix}_console.log"
  local docker_log_file="$LOGS_DIR/${prefix}_docker.log"

  echo
  echo "=================================================="
  echo "Concurrent users: $users"
  echo "Run:              $repetition/$REPETITIONS"
  echo "CSV:              $csv_file"
  echo "=================================================="

  start_environment

  seed_environment

  echo "Running warmup..."

  BASE_URL="http://127.0.0.1:3000" \
  WAREHOUSE_PREFIX="/warehouse" \
  k6 run \
    --quiet \
    "$ROOT_DIR/experiments/k6/warmup.js"

  echo "Warmup completed."

  echo "Running benchmark..."

  local k6_exit_code=0

  BASE_URL="http://127.0.0.1:3000" \
  WAREHOUSE_PREFIX="/warehouse" \
  USERS="$users" \
  k6 run \
    --out "csv=$csv_file" \
    "$BENCHMARK" \
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
    echo "users=$users"
    echo "repetition=$repetition"
    echo "k6_exit_code=$k6_exit_code"
    echo "finished_at=$(date --iso-8601=seconds)"
  } >>"$console_file"

  if (( k6_exit_code != 0 )); then
    echo "Warning: k6 finished with exit code $k6_exit_code."
  fi

  cleanup

  echo "Cooling down for ${COOLDOWN_SECONDS}s..."

  sleep "$COOLDOWN_SECONDS"
}

validate_configuration() {
  if [[ ! -f "$COMPOSE" ]]; then
    echo "Error: compose file not found:"
    echo "$COMPOSE"
    exit 1
  fi

  if [[ ! -f "$BENCHMARK" ]]; then
    echo "Error: benchmark not found:"
    echo "$BENCHMARK"
    exit 1
  fi

  if [[ ! -f "$ROOT_DIR/scripts/seed.sh" ]]; then
    echo "Error: seed.sh not found."
    exit 1
  fi

  if (( START_USERS <= 0 )); then
    echo "Error: START_USERS must be > 0."
    exit 1
  fi

  if (( MAX_USERS < START_USERS )); then
    echo "Error: MAX_USERS must be >= START_USERS."
    exit 1
  fi

  if (( STEP_USERS <= 0 )); then
    echo "Error: STEP_USERS must be > 0."
    exit 1
  fi

  if (( REPETITIONS <= 0 )); then
    echo "Error: REPETITIONS must be > 0."
    exit 1
  fi
}

main() {
  validate_configuration

  echo "Pre-building Docker images..."

  docker compose \
    -f "$COMPOSE" \
    build \
    --quiet

  echo
  echo "Starting monolith benchmark suite"
  echo
  echo "Start users:  $START_USERS"
  echo "Max users:    $MAX_USERS"
  echo "Step:         $STEP_USERS"
  echo "Repetitions:  $REPETITIONS"
  echo

  for users in $(seq \
    "$START_USERS" \
    "$STEP_USERS" \
    "$MAX_USERS"
  ); do

    echo
    echo "##################################################"
    echo "Testing $users concurrent users"
    echo "##################################################"

    for repetition in $(seq 1 "$REPETITIONS"); do
      run_benchmark \
        "$users" \
        "$repetition"
    done
  done

  echo
  echo "=================================================="
  echo "All monolith experiments finished."
  echo "Results: $RESULTS_DIR"
  echo "=================================================="

  trap - EXIT
}

main "$@"
