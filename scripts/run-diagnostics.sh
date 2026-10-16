#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == --help ]]; then
  echo 'Usage: RUN_ID=baseline-40rps-rep01 RATE=40 [SEED_DATA=1] bash scripts/run-diagnostics.sh'
  echo 'Starts the baseline with diagnostic overlay, collectors, existing warmup and arrival-rate.js.'
  echo 'Existing volumes are preserved. SEED_DATA=1 requires an empty product catalog.'
  exit 0
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="$ROOT_DIR/environment/compose/compose.base.yaml"
OVERLAY="$ROOT_DIR/environment/compose/compose.diagnostics.yaml"
export RATE="${RATE:-20}" DURATION="${DURATION:-2m}"
export PRE_ALLOCATED_VUS="${PRE_ALLOCATED_VUS:-100}" MAX_VUS="${MAX_VUS:-300}"
export BASE_URL="${BASE_URL:-http://127.0.0.1:8000}" WAREHOUSE_PREFIX=/warehouse
export RUN_ID="${RUN_ID:-baseline-${RATE}rps-$(date -u +%Y%m%dT%H%M%SZ)}"
export DB_DIAGNOSTICS="${DB_DIAGNOSTICS:-1}"
OUT="$ROOT_DIR/experiments/datasets/diagnostics/$RUN_ID"
DIAGNOSTICS_DIR="$(dirname "$OUT")"
COMPOSE=(docker compose -f "$BASE" -f "$OVERLAY")
[[ "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ ]] || { echo 'Invalid RUN_ID' >&2; exit 1; }
for command in docker python3 curl k6; do
  command -v "$command" >/dev/null || { echo "Missing dependency: $command" >&2; exit 1; }
done
[[ ! -e "$OUT" ]] || { echo "Refusing to overwrite $OUT" >&2; exit 1; }
docker info >/dev/null
umask 077
mkdir -p "$DIAGNOSTICS_DIR"
mkdir "$OUT"
children=()
phase=initialization
phase_log=''
cleanup() {
  local status=$? pid result=failed
  local -a container_ids=()
  trap - EXIT INT TERM
  set +e
  for pid in "${children[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${children[@]}"; do wait "$pid" 2>/dev/null || true; done
  # Never stop containers or delete volumes; collectors use dedicated processes.
  if (( status != 0 )) && [[ "$phase" == startup ]]; then
    echo "[diagnostics] Capturing startup failure logs and container health (read-only)." >&2
    "${COMPOSE[@]}" ps --all >"$OUT/startup-containers.txt" 2>"$OUT/startup-inspection.log" || true
    "${COMPOSE[@]}" logs --no-color --timestamps --tail 200 \
      >"$OUT/startup-containers.log" 2>>"$OUT/startup-inspection.log" || true
    mapfile -t container_ids < <("${COMPOSE[@]}" ps --all --quiet 2>>"$OUT/startup-inspection.log")
    if (( ${#container_ids[@]} )); then
      docker inspect --format '{"id":{{json .Id}},"name":{{json .Name}},"state":{{json .State}}}' \
        "${container_ids[@]}" >"$OUT/startup-state.jsonl" 2>>"$OUT/startup-inspection.log" || true
    fi
  fi
  : >"$OUT/status.json"
  # Keep private logs readable by the user who invoked sudo, including on failure.
  if (( EUID == 0 )) && [[ "${SUDO_UID:-}" =~ ^[0-9]+$ && "${SUDO_GID:-}" =~ ^[0-9]+$ ]]; then
    if ! chown -- "$SUDO_UID:$SUDO_GID" "$DIAGNOSTICS_DIR" || \
       ! chown -R -P --no-dereference -- "$SUDO_UID:$SUDO_GID" "$OUT"; then
      echo "[diagnostics] Could not return output ownership to uid=$SUDO_UID; inspect $OUT with sudo." >&2
      (( status == 0 )) && status=1
    fi
  fi
  (( status == 0 )) && result=completed
  printf '{"runId":"%s","status":"%s","phase":"%s","exitCode":%d,"timestamp":"%s"}\n' \
    "$RUN_ID" "$result" "$phase" "$status" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$OUT/status.json"
  if (( status != 0 )); then
    echo "[diagnostics] Failed in phase '$phase' (exit=$status). Files preserved in $OUT" >&2
    if [[ -n "$phase_log" && -f "$phase_log" ]]; then
      echo "[diagnostics] Last 60 lines of $phase_log:" >&2
      tail -n 60 -- "$phase_log" >&2
    fi
  else
    echo "[diagnostics] Diagnostics saved to $OUT (k6 exit=0). Containers and volumes preserved."
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
mark_phase() {
  phase="$1"
  phase_log="${2:-}"
  printf '{"phase":"%s","timestamp":"%s"}\n' "$phase" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/phases.jsonl"
  echo "[diagnostics] $phase${phase_log:+ (log: $phase_log)}" >&2
}
echo "[diagnostics] Output: $OUT" >&2
mark_phase metadata
STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export STARTED_AT
python3 - "$OUT/metadata.json" <<'PY'
import json, os, platform, sys
keys = ['RUN_ID', 'DB_DIAGNOSTICS', 'RATE', 'DURATION', 'PRE_ALLOCATED_VUS', 'MAX_VUS', 'BASE_URL', 'STARTED_AT']
with open(sys.argv[1], 'x') as output:
    json.dump({'environment': {k: os.environ[k] for k in keys}, 'collectorHost': platform.node(),
               'note': 'No volume reset; dataset preparation must be controlled separately.'}, output, indent=2)
PY
git -C "$ROOT_DIR" rev-parse HEAD >"$OUT/git-head.txt"
git -C "$ROOT_DIR" status --short >"$OUT/git-status.txt"
mark_phase startup "$OUT/startup.log"
"${COMPOSE[@]}" up --detach --build >"$OUT/startup.log" 2>&1
"${COMPOSE[@]}" logs --follow --no-color --timestamps --since "$STARTED_AT" \
  >"$OUT/application.log" 2>&1 &
children+=("$!")

mark_phase readiness "$OUT/application.log"
deadline=$((SECONDS + 180))
for service in order warehouse billing shipping; do
  until curl -fsS --connect-timeout 2 --max-time 5 "$BASE_URL/$service/health" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || { echo 'Readiness timeout; inspect application.log' >&2; exit 1; }
    sleep 1
  done
done
if [[ "${SEED_DATA:-0}" == 1 ]]; then
  mark_phase seed "$OUT/seed.log"
  [[ "$BASE_URL" == http://127.0.0.1:8000 ]] || { echo 'seed.sh uses localhost:8000; prepare custom targets separately' >&2; exit 1; }
  curl -fsS --max-time 10 "$BASE_URL/warehouse/products" | python3 -c \
    'import json,sys; sys.exit(0 if json.load(sys.stdin) == [] else "Catalog is not empty; refusing duplicate seed")'
  bash "$ROOT_DIR/scripts/seed.sh" distributed >"$OUT/seed.log" 2>&1
fi
mark_phase collectors "$OUT/postgres-collector.log"
python3 "$ROOT_DIR/scripts/diagnostics/collect-postgres.py" --output-dir "$OUT" \
  --run-id "$RUN_ID" --compose "$BASE" >"$OUT/postgres-collector.log" 2>&1 &
postgres_pid=$!
children+=("$postgres_pid")
if [[ "${COLLECT_HOST:-1}" == 1 ]]; then
  python3 "$ROOT_DIR/scripts/diagnostics/collect-host.py" --output-dir "$OUT" \
    --run-id "$RUN_ID" --compose "$BASE" >"$OUT/host-collector.log" 2>&1 &
  children+=("$!")
fi
# Prove the observer connected before spending an experiment without PG evidence.
for ((attempt=0; attempt<15; attempt++)); do
  [[ -s "$OUT/postgres-session.json" && -s "$OUT/postgres-activity.jsonl" ]] && break
  kill -0 "$postgres_pid" 2>/dev/null || { echo 'PostgreSQL collector failed; inspect its log' >&2; exit 1; }
  sleep 1
done
[[ -s "$OUT/postgres-activity.jsonl" ]] || { echo 'PostgreSQL collector did not produce a sample' >&2; exit 1; }

mark_phase warmup "$OUT/warmup.log"
k6 run --quiet "$ROOT_DIR/experiments/k6/warmup.js" >"$OUT/warmup.log" 2>&1
mark_phase benchmark "$OUT/k6.log"
k6 run --out "csv=$OUT/k6.csv" "$ROOT_DIR/experiments/k6/arrival-rate.js" >"$OUT/k6.log" 2>&1 &
k6_pid=$!
children+=("$k6_pid")
exit_code=0
wait "$k6_pid" || exit_code=$?
printf '%s\n' "$exit_code" >"$OUT/k6-exit-code.txt"
mark_phase collection_tail "$OUT/postgres-collector.log"
sleep 2
kill -0 "$postgres_pid" 2>/dev/null || { echo 'PostgreSQL collector stopped during the experiment' >&2; exit 1; }
if (( exit_code != 0 )); then
  phase=benchmark
  phase_log="$OUT/k6.log"
fi
exit "$exit_code"
