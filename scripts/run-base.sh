#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COMPOSE="$ROOT_DIR/environment/compose/compose.base.yaml"
RESULTS="$ROOT_DIR/experiments/datasets/baseline"
LOGS_DIR="$RESULTS/logs"
BENCHMARK="$ROOT_DIR/experiments/k6"

LOAD="${1:-low}"
REPETITIONS="${2:-10}" # default to 10 repetitions

case "$LOAD" in
  low)
    SCRIPT="low.js"
    ;;
  medium)
    SCRIPT="medium.js"
    ;;
  high)
    SCRIPT="high.js"
    ;;
  *)
    echo "Carga inválida."
    exit 1
    ;;
esac

mkdir -p "$RESULTS"
mkdir -p "$LOGS_DIR"

cleanup() {
  docker compose -f "$COMPOSE" down -v >/dev/null 2>&1 || true
}

trap cleanup EXIT

echo "Pre-building Docker images..."
docker compose -f "$COMPOSE" build --quiet

echo "Starting benchmark suite: $REPETITIONS runs for '$LOAD' load."

for i in $(seq 1 $REPETITIONS); do
  echo "[$(date +'%Y-%m-%d %H:%M:%S')] TRIAL $i/$REPETITIONS ($LOAD load)"

  cleanup

  docker compose -f "$COMPOSE" up -d >/dev/null 2>&1

  echo "Waiting all services to be healthy..."

  until curl -fs http://localhost:3001/health >/dev/null 2>&1 && \
        curl -fs http://localhost:3002/health >/dev/null 2>&1 && \
        curl -fs http://localhost:3003/health >/dev/null 2>&1 && \
        curl -fs http://localhost:3004/health >/dev/null 2>&1; do
      sleep 1
  done

  bash "$ROOT_DIR/scripts/seed.sh" >/dev/null 2>&1

  k6 run --quiet "$BENCHMARK/warmup.js"

  echo "5. Running Benchmark - $LOAD load (run $i/$REPETITIONS)"
  k6 run \
    --quiet \
    --out csv="$RESULTS/${LOAD}_run${i}.csv" \
    "$BENCHMARK/$SCRIPT"

  docker compose -f "$COMPOSE" logs --no-color > "$LOGS_DIR/${LOAD}_run${i}_docker.log"

  cleanup

  sleep 5
done

echo "=================================================="
echo "All $REPETITIONS trials finished successfully."
