#!/usr/bin/env bash
# Exploratory PostgreSQL capacity pilot; baseline remains the default.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: [RUN_ID=vm-pilot-01] [RATES=1,2,5,10,15,20,30,40] bash run.sh
       ARCHITECTURE=tcc RUN_ID=tcc-01 bash run.sh
Example: RUN_ID=vm-pilot-5reps REPETITIONS=5 bash run.sh
Starts the PostgreSQL distributed baseline, seeds an empty catalog, warms up,
and measures ascending constant-arrival-rate plateaus without resetting volumes.
Defaults: DURATION=3m WARMUP_DURATION=1m WARMUP_RATE=1 REPETITIONS=1
          SETTLING_SECONDS=30 COOLDOWN_SECONDS=30 RECOVERY_TIMEOUT_SECONDS=60
          DB_DIAGNOSTICS=1 COLLECT_HOST=1 AUTO_PLOT=1 SEED_DATA=auto
          K6_CSV_STORAGE=disk K6_CSV_TMPFS_ROOT=/dev/shm
REPETITIONS repeats each rate before advancing, e.g. rate1_run1, rate1_run2,
then rate2_run1, rate2_run2. DURATION applies to each repetition; warmup runs once.
Cooldown and pool recovery run between repetitions. Each has its own CSV/JSON.
Incomplete choreography business recovery is recorded as a warning and does not
abort subsequent repetitions. Messages and pending orders remain preserved.
PRE_ALLOCATED_VUS defaults to 25 * highest RATE (20 s HTTP timeout + headroom).
MAX_VUS defaults to PRE_ALLOCATED_VUS, avoiding runtime VU allocation by default.
PLOT_PYTHON can select a Python environment with matplotlib. Otherwise summaries
are still generated and charts can be generated manually later.
K6_CSV_STORAGE=tmpfs stages ALL k6 CSVs in memory until load and recovery end,
then copies them to the usual results directory before analysis/charts.
K6_CSV_TMPFS_ROOT must be a writable tmpfs mount (findmnt is required).
CSV files accumulate for the entire run: reserve enough free tmpfs/RAM space.
PRELOAD_ORDERS=249120 optionally creates coherent successful historical orders before warmup.
Requires an isolated stack and empty order/payment/shipment/reservation tables. Default=0.
History is setup only; warmup and all measured repetitions continue accumulating data.
Containers are stopped on exit, including failures and handled interruptions.
Isolated stacks also remove containers/networks on exit; named volumes remain preserved.
Existing containers/volumes and previous results are never deleted.
ARCHITECTURE: distributed-baseline (default), tcc, two-phase-commit,
              orchestrated-saga, choreographed-saga.
Other architectures use isolated stacks/volumes and the same collectors/charts.
DB_POOL_MAX optionally overrides pool max; omitted preserves each implementation.
DB_MODE=mock selects volatile storage on isolated stacks; requires PRELOAD_ORDERS=0.
Use run-mock.sh for all five architectures without PostgreSQL or pool diagnostics.
Startup budgets (seconds): DB_START_TIMEOUT_SECONDS=1800, BROKER_START_TIMEOUT_SECONDS=1800,
HTTP_START_TIMEOUT_SECONDS=600, SEED_TIMEOUT_SECONDS=600,
COLLECTOR_START_TIMEOUT_SECONDS=120, SETUP_HTTP_TIMEOUT_SECONDS=60.
These do not change workload HTTP, pool acquisition or recovery timeouts.
run-suite.sh runs architectures sequentially, with separate result directories.
HELP
  exit 0
fi
(( $# == 0 )) || { echo 'Use bash run.sh --help' >&2; exit 1; }
export ARCHITECTURE="${ARCHITECTURE:-distributed-baseline}"
export PILOT_ISOLATED="${PILOT_ISOLATED:-$([[ "$ARCHITECTURE" == distributed-baseline ]] && echo 0 || echo 1)}"
export RUN_ID="${RUN_ID:-${ARCHITECTURE}-pilot-$(date -u +%Y%m%dT%H%M%SZ)}"
export RATES="${RATES:-1,2,5,10,15,20,30,40}"
export DURATION="${DURATION:-3m}" WARMUP_DURATION="${WARMUP_DURATION:-1m}"
export WARMUP_RATE="${WARMUP_RATE:-1}" REPETITIONS="${REPETITIONS:-1}"
export SETTLING_SECONDS="${SETTLING_SECONDS:-30}"
export COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-30}"
export RECOVERY_TIMEOUT_SECONDS="${RECOVERY_TIMEOUT_SECONDS:-60}"
export DB_START_TIMEOUT_SECONDS="${DB_START_TIMEOUT_SECONDS:-1800}"
export BROKER_START_TIMEOUT_SECONDS="${BROKER_START_TIMEOUT_SECONDS:-1800}"
export HTTP_START_TIMEOUT_SECONDS="${HTTP_START_TIMEOUT_SECONDS:-600}"
export SEED_TIMEOUT_SECONDS="${SEED_TIMEOUT_SECONDS:-600}"
export COLLECTOR_START_TIMEOUT_SECONDS="${COLLECTOR_START_TIMEOUT_SECONDS:-120}"
export SETUP_HTTP_TIMEOUT_SECONDS="${SETUP_HTTP_TIMEOUT_SECONDS:-60}"
export DB_DIAGNOSTICS="${DB_DIAGNOSTICS:-1}" COLLECT_HOST="${COLLECT_HOST:-1}"
export K6_CSV_STORAGE="${K6_CSV_STORAGE:-disk}"
export K6_CSV_TMPFS_ROOT="${K6_CSV_TMPFS_ROOT:-/dev/shm}"
export SEED_DATA="${SEED_DATA:-auto}" BASE_URL=http://127.0.0.1:8000 WAREHOUSE_PREFIX=/warehouse
export PRELOAD_ORDERS="${PRELOAD_ORDERS:-0}"
export DB_MODE="${DB_MODE:-postgres}"
[[ "$DB_MODE" == postgres || "$DB_MODE" == mock ]] || { echo 'Invalid DB_MODE' >&2; exit 1; }
if [[ "$DB_MODE" == mock ]]; then
  [[ "$PILOT_ISOLATED" == 1 && "$PRELOAD_ORDERS" == 0 ]] || { echo 'Mock requires isolated stack and PRELOAD_ORDERS=0' >&2; exit 1; }
  export DB_DIAGNOSTICS=0
fi
export TZ=UTC
export STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
AUTO_PLOT="${AUTO_PLOT:-1}"
PLOT_PYTHON="${PLOT_PYTHON:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PLOT_PYTHON" ]] || PLOT_PYTHON=python3
OUT="$ROOT_DIR/experiments/datasets/pilot/$RUN_ID"
if [[ -n "${PILOT_SUITE_ID:-}" ]]; then
  [[ "$PILOT_SUITE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,59}$ ]] || { echo 'Invalid PILOT_SUITE_ID' >&2; exit 1; }
  OUT="$ROOT_DIR/experiments/datasets/pilot/$PILOT_SUITE_ID/$ARCHITECTURE"
fi
if [[ -n "${PILOT_TRIAL_ID:-}" ]]; then
  [[ "$PILOT_ISOLATED" == 1 && -n "${PILOT_SUITE_ID:-}" && "$PILOT_TRIAL_ID" =~ ^rate[1-9][0-9]*_run[1-9][0-9]*$ ]] || { echo 'Invalid isolated trial ID' >&2; exit 1; }
  OUT="$OUT/trials/$PILOT_TRIAL_ID"
fi
export PILOT_REPETITION_START="${PILOT_REPETITION_START:-1}"
[[ "$PILOT_REPETITION_START" =~ ^[1-9][0-9]{0,6}$ ]] || { echo 'Invalid repetition start' >&2; exit 1; }
[[ "${PILOT_SKIP_BUILD:-0}" =~ ^[01]$ ]] || { echo 'Invalid skip-build flag' >&2; exit 1; }
BASE="$ROOT_DIR/environment/compose/compose.base.yaml"
COMPOSE=(docker compose -f "$BASE" -f "$ROOT_DIR/environment/compose/compose.diagnostics.yaml")
fail() { echo "[pilot] $*" >&2; exit 1; }
[[ "$ARCHITECTURE" =~ ^(distributed-baseline|tcc|two-phase-commit|orchestrated-saga|choreographed-saga)$ ]] || fail 'Unsupported ARCHITECTURE'
[[ "$PILOT_ISOLATED" =~ ^[01]$ ]] || fail 'PILOT_ISOLATED must be 0 or 1'
[[ "$ARCHITECTURE" == distributed-baseline || "$PILOT_ISOLATED" == 1 ]] || fail 'Other architectures require isolated stacks'
[[ -z "${DB_POOL_MAX:-}" || "$DB_POOL_MAX" =~ ^[1-9][0-9]{0,5}$ ]] || fail 'DB_POOL_MAX must be a positive integer'
if [[ "$PILOT_ISOLATED" == 1 ]]; then
  BASE="$OUT/runtime/compose.json"
  COMPOSE=(docker compose -f "$BASE")
fi
[[ "$PRELOAD_ORDERS" =~ ^(0|[1-9][0-9]{0,7})$ ]] || fail 'PRELOAD_ORDERS must be a nonnegative integer'
(( PRELOAD_ORDERS <= 10000000 )) || fail 'PRELOAD_ORDERS maximum is 10000000'
if (( PRELOAD_ORDERS > 0 )); then
  [[ "$PILOT_ISOLATED" == 1 ]] || fail 'PRELOAD_ORDERS requires PILOT_ISOLATED=1 (use run-suite.sh)'
fi
[[ "$RUN_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$ ]] || fail 'Invalid RUN_ID'
[[ ! -e "$OUT" ]] || fail "Refusing to overwrite $OUT"
[[ "$RATES" =~ ^[1-9][0-9]*(,[1-9][0-9]*)*$ ]] || fail 'RATES must be ascending comma-separated positive integers'
IFS=, read -r -a rates <<<"$RATES"
previous=0
for rate in "${rates[@]}"; do
  (( rate > previous && rate <= 100000 )) || fail 'RATES must be strictly increasing (maximum 100000)'
  previous=$rate
done
export PRE_ALLOCATED_VUS="${PRE_ALLOCATED_VUS:-$((previous * 25))}"
export MAX_VUS="${MAX_VUS:-$PRE_ALLOCATED_VUS}"
for name in WARMUP_RATE REPETITIONS PRE_ALLOCATED_VUS MAX_VUS RECOVERY_TIMEOUT_SECONDS DB_START_TIMEOUT_SECONDS BROKER_START_TIMEOUT_SECONDS HTTP_START_TIMEOUT_SECONDS SEED_TIMEOUT_SECONDS COLLECTOR_START_TIMEOUT_SECONDS SETUP_HTTP_TIMEOUT_SECONDS; do
  [[ "${!name}" =~ ^[1-9][0-9]{0,6}$ ]] || fail "$name must be a positive integer"
done
if [[ -n "${PILOT_TRIAL_ID:-}" ]]; then
  [[ "$REPETITIONS" == 1 && "$PRELOAD_ORDERS" == 0 && "$PILOT_TRIAL_ID" == "rate${RATES}_run${PILOT_REPETITION_START}" ]] || fail 'Independent child must contain exactly its declared trial and no preload'
fi
for name in SETTLING_SECONDS COOLDOWN_SECONDS; do
  [[ "${!name}" =~ ^(0|[1-9][0-9]{0,6})$ ]] || fail "$name must be a nonnegative integer"
done
(( MAX_VUS >= PRE_ALLOCATED_VUS )) || fail 'MAX_VUS must be >= PRE_ALLOCATED_VUS'
for name in DB_DIAGNOSTICS COLLECT_HOST AUTO_PLOT; do
  [[ "${!name}" =~ ^[01]$ ]] || fail "$name must be 0 or 1"
done
[[ "$SEED_DATA" =~ ^(auto|0|1)$ ]] || fail 'SEED_DATA must be auto, 0 or 1'
[[ "$K6_CSV_STORAGE" =~ ^(disk|tmpfs)$ ]] || fail 'K6_CSV_STORAGE must be disk or tmpfs'
if [[ "$K6_CSV_STORAGE" == tmpfs ]]; then
  for tool in findmnt mktemp cp rm rmdir; do
    command -v "$tool" >/dev/null || fail "Missing tmpfs dependency: $tool"
  done
  [[ -d "$K6_CSV_TMPFS_ROOT" && -w "$K6_CSV_TMPFS_ROOT" && -x "$K6_CSV_TMPFS_ROOT" ]] \
    || fail "K6_CSV_TMPFS_ROOT is not a writable directory: $K6_CSV_TMPFS_ROOT"
  K6_CSV_TMPFS_ROOT="$(cd "$K6_CSV_TMPFS_ROOT" && pwd -P)"
  [[ "$(findmnt -n -T "$K6_CSV_TMPFS_ROOT" -o FSTYPE)" == tmpfs ]] \
    || fail "K6_CSV_TMPFS_ROOT must be on tmpfs: $K6_CSV_TMPFS_ROOT"
fi
python3 - "$DURATION" "$WARMUP_DURATION" "$SETTLING_SECONDS" <<'PY'
import re, sys
units = {'s': 1, 'm': 60, 'h': 3600}
seconds = []
for value in sys.argv[1:3]:
    match = re.fullmatch(r'([1-9][0-9]*)(s|m|h)', value)
    if not match:
        sys.exit('Durations must use positive integer s, m or h, e.g. 3m')
    seconds.append(int(match[1]) * units[match[2]])
if int(sys.argv[3]) >= seconds[0]:
    sys.exit('SETTLING_SECONDS must be smaller than DURATION')
PY
for tool in docker k6 curl python3 tail timeout; do
  command -v "$tool" >/dev/null || fail "Missing dependency: $tool"
done
docker info >/dev/null
if [[ "$PILOT_ISOLATED" == 0 ]]; then "${COMPOSE[@]}" config --quiet; fi
umask 077
source "$ROOT_DIR/scripts/diagnostics/dataset-permissions.sh"
dataset_prepare_parent "$ROOT_DIR" "$OUT"
mkdir "$OUT" "$OUT/logs"
children=()
CSV_DIR="$OUT"
csv_staging=''
csv_export_status=not_needed
phase=metadata
current_log=''
plots_status=not_requested
export_csv() {
  local file
  echo "[pilot] Exporting CSVs after load/recovery: $csv_staging -> $OUT"
  # Keep every RAM original if any copy fails. No fallback to disk during load.
  for file in "$csv_staging"/*.csv; do
    [[ -f "$file" ]] || continue
    cp -- "$file" "$OUT/${file##*/}" || return 1
  done
  for file in "$csv_staging"/*.csv; do
    [[ -f "$file" ]] || continue
    rm -- "$file" || return 1
  done
  rmdir -- "$csv_staging" || return 1
  csv_staging=''
  csv_export_status=exported
}
finish() {
  local status=$? pid container_stop=stopped
  trap - EXIT INT TERM
  set +e
  for pid in "${children[@]}"; do kill -TERM "$pid" 2>/dev/null; done
  for pid in "${children[@]}"; do wait "$pid" 2>/dev/null; done
  # Only export once every k6/collector child has stopped, including on failure.
  if [[ -n "$csv_staging" ]] && ! export_csv; then
    csv_export_status=failed
    echo "[pilot] CSV export failed; remaining originals preserved in RAM: $csv_staging" >&2
    if (( status == 0 )); then status=1; phase=csv_export; fi
  fi
  if (( status != 0 )); then
    "${COMPOSE[@]}" ps --all >"$OUT/logs/final-containers.txt" 2>&1
    "${COMPOSE[@]}" logs --no-color --tail 100 >"$OUT/logs/failure.log" 2>&1
  fi
  # All measurements/collectors have ended. Isolated runs have unique networks:
  # remove those stacks without -v so subnet allocations do not accumulate.
  # Keep the shared baseline's existing stop-only behavior.
  local shutdown=(stop --timeout 60)
  if [[ "$PILOT_ISOLATED" == 1 && -f "$OUT/runtime/compose.json" ]]; then
    "${COMPOSE[@]}" ps --all >"$OUT/logs/final-containers.txt" 2>&1
    "${COMPOSE[@]}" logs --no-color --tail 100 >"$OUT/logs/final-container-logs.txt" 2>&1
    shutdown=(down --timeout 60)
  fi
  echo '[pilot] Stopping experiment containers (volumes preserved)...'
  if ! "${COMPOSE[@]}" "${shutdown[@]}" >"$OUT/logs/shutdown.log" 2>&1; then
    container_stop=failed
    echo '[pilot] Could not stop experiment containers; see logs/shutdown.log' >&2
    tail -n 20 "$OUT/logs/shutdown.log" >&2
    if (( status == 0 )); then status=1; phase=container_stop; fi
  fi
  if [[ "$csv_export_status" != failed ]] && compgen -G "$OUT/rate*_run*.json" >/dev/null; then
    plots_status=summary_only
    python3 "$ROOT_DIR/experiments/analyze_pilot.py" "$OUT" --no-plots \
      >"$OUT/analysis.log" 2>&1 || plots_status=analysis_failed
    if [[ "$plots_status" == analysis_failed ]]; then
      echo '[pilot] Analysis failed; see analysis.log' >&2
      tail -n 20 "$OUT/analysis.log" >&2
      if (( status == 0 )); then status=1; phase=analysis; fi
    fi
    if [[ "$AUTO_PLOT" == 1 && "$plots_status" != analysis_failed ]]; then
      MPLCONFIGDIR="$OUT/analysis/.matplotlib" "$PLOT_PYTHON" \
        "$ROOT_DIR/experiments/analyze_pilot.py" "$OUT" >>"$OUT/analysis.log" 2>&1
      if (( $? == 0 )); then plots_status=generated; else plots_status=plots_pending; fi
    fi
  fi
  : >"$OUT/status.json"
  if ! dataset_return_ownership "$OUT" "$csv_staging"; then
    echo "[pilot] Could not return dataset ownership to sudo's caller: $OUT" >&2
    if (( status == 0 )); then status=1; phase=permissions; fi
  fi
  printf '{"runId":"%s","exitCode":%d,"phase":"%s","plots":"%s","csvStorage":"%s","csvExport":"%s","containerStop":"%s"}\n' \
    "$RUN_ID" "$status" "$phase" "$plots_status" "$K6_CSV_STORAGE" "$csv_export_status" "$container_stop" >"$OUT/status.json"
  echo "[pilot] Results: $OUT (exit=$status, phase=$phase, plots=$plots_status)"
  echo "[pilot] Container stop: $container_stop; volumes preserved. HTTP success must be assessed in the results."
  if (( status != 0 )) && [[ -f "$current_log" ]]; then tail -n 40 "$current_log" >&2; fi
  if [[ "$plots_status" == plots_pending ]]; then
    echo "[pilot] Charts pending; see analysis.log or run: python3 experiments/analyze_pilot.py '$OUT'"
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ "$PILOT_ISOLATED" == 1 ]]; then
  mkdir "$OUT/runtime" "$OUT/business"
  export COMPOSE_PROJECT_NAME
  COMPOSE_PROJECT_NAME="$(python3 "$ROOT_DIR/scripts/pilot/compose.py" \
    --architecture "$ARCHITECTURE" --run-id "$RUN_ID" --output "$OUT/runtime")"
  "${COMPOSE[@]}" config --quiet
fi
if [[ "$K6_CSV_STORAGE" == tmpfs ]]; then
  csv_staging="$(mktemp -d "$K6_CSV_TMPFS_ROOT/k6-pilot.$RUN_ID.XXXXXX")"
  CSV_DIR="$csv_staging"
  csv_export_status=pending
  python3 - "$CSV_DIR" <<'PY'
import os, sys
stats = os.statvfs(sys.argv[1])
free_bytes = stats.f_bavail * stats.f_frsize
if free_bytes <= 0:
    sys.exit('No free space on CSV tmpfs')
print(f'[pilot] CSV tmpfs: {sys.argv[1]}; available={free_bytes / 1024**2:.0f} MiB (not reserved)')
PY
fi
mark() {
  phase="$1"
  printf '{"phase":"%s","timestamp":"%s"}\n' "$phase" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/phases.jsonl"
  echo "[pilot] $phase"
}
echo "[pilot] Architecture: $ARCHITECTURE; output: $OUT; rates=$RATES; repetitions=$REPETITIONS per rate; duration=$DURATION per repetition; VUs=$PRE_ALLOCATED_VUS/$MAX_VUS"
echo "[pilot] CSV storage during load: $K6_CSV_STORAGE ($CSV_DIR)"
mark metadata
git -C "$ROOT_DIR" rev-parse HEAD >"$OUT/git-head.txt" 2>/dev/null || true
"${COMPOSE[@]}" config >"$OUT/compose-resolved.yaml"
k6 version >"$OUT/logs/k6-version.txt"
{ uname -a; command -v lscpu >/dev/null && lscpu; free -m; df -hT /; } >"$OUT/host-info.txt" 2>&1 || true
python3 - "$ROOT_DIR" "$OUT" "$CSV_DIR" <<'PY'
import hashlib, json, os, pathlib, sys
root, out, csv_dir = map(pathlib.Path, sys.argv[1:])
keys = ['RUN_ID','STARTED_AT','RATES','DURATION','WARMUP_DURATION','WARMUP_RATE','REPETITIONS',
        'SETTLING_SECONDS','COOLDOWN_SECONDS','RECOVERY_TIMEOUT_SECONDS',
        'PRE_ALLOCATED_VUS','MAX_VUS','DB_DIAGNOSTICS','COLLECT_HOST','SEED_DATA',
        'K6_CSV_STORAGE','K6_CSV_TMPFS_ROOT','ARCHITECTURE','PILOT_ISOLATED','DB_POOL_MAX','COMPOSE_PROJECT_NAME','PRELOAD_ORDERS','DB_MODE',
        'PILOT_TRIAL_ID','PILOT_REPETITION_START','PILOT_SKIP_BUILD','PILOT_IMAGE_PREFIX',
        'DB_START_TIMEOUT_SECONDS','BROKER_START_TIMEOUT_SECONDS','HTTP_START_TIMEOUT_SECONDS',
        'SEED_TIMEOUT_SECONDS','COLLECTOR_START_TIMEOUT_SECONDS','SETUP_HTTP_TIMEOUT_SECONDS']
data = {'architecture': os.environ['ARCHITECTURE'], 'database': os.environ['DB_MODE'],
        'businessRecoveryPolicy': 'Record incomplete business recovery and continue cumulatively; pool recovery and asynchronous observation failures remain fatal.',
        'responseSemantics': 'HTTP acceptance; completion is asynchronous' if os.environ['ARCHITECTURE'] == 'choreographed-saga' else 'HTTP response after synchronous coordination',
        'environment': {k: os.environ.get(k) for k in keys},
        'csvOutput': {'storage': os.environ['K6_CSV_STORAGE'], 'pathDuringLoad': str(csv_dir),
                      'availableBytesBeforeRun': os.statvfs(csv_dir).f_bavail * os.statvfs(csv_dir).f_frsize,
                      'exportPolicy': 'Copy tmpfs CSVs only after all load/recovery and child shutdown, before analysis.'},
        'datasetPolicy': 'Optional synthetic history, then cumulative warmup/benchmarks without resets' if int(os.environ['PRELOAD_ORDERS']) else 'Reuse volumes; seed only an empty catalog; ascending exploratory sweep without resets.'}
if os.environ['DB_MODE'] == 'mock':
    data['businessRecoveryPolicy'] = 'Bounded observation of order cohort in memory; no PostgreSQL pool or cross-service/broker invariant audit.'
    data['datasetPolicy'] = 'Fresh volatile state per service process; HTTP seed; cumulative warmup/benchmarks; no SQL preload.'
(out/'metadata.json').write_text(json.dumps(data, indent=2)+'\n')
files = [root/'run.sh', root/'experiments/k6/pilot.js', root/'experiments/analyze_pilot.py',
         root/'scripts/seed.sh', root/'scripts/diagnostics/collect-postgres.py',
         root/'scripts/diagnostics/collect-host.py', root/'scripts/diagnostics/dataset-permissions.sh']
for service in ['order','warehouse','billing','shipping']:
    base = root/f'implementation/{os.environ["ARCHITECTURE"]}/{service}-service'
    files += list((base/'src').rglob('*.ts'))
    files += [p for p in [base/'package.json', base/'pnpm-lock.yaml', base/'Dockerfile'] if p.exists()]
if os.environ['PILOT_ISOLATED'] == '1':
    files += list((root/'implementation/distributed-baseline/order-service/src/diagnostics').glob('*.ts'))
    files += list((root/'environment/pilot').glob('*')) + list((root/'environment/mock').glob('*')) + list((root/'scripts/pilot').glob('*.py'))
    if (root/'.dockerignore').exists(): files.append(root/'.dockerignore')
files += list((root/'environment/compose').glob('*.yaml'))
(out/'source-sha256.json').write_text(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in files}, indent=2)+'\n')
PY
mark startup
current_log="$OUT/logs/startup.log"
wait_service_ready() {
  local service="$1" deadline="$2" database="${3:-}" health id probe next_progress=$SECONDS
  local log="$OUT/logs/readiness-$service.log"
  while (( SECONDS < deadline )); do
    id="$(timeout --foreground 15 "${COMPOSE[@]}" ps --all --quiet "$service" 2>>"$log")" || id=''
    health='unavailable'
    if [[ -n "$id" ]]; then
      health="$(timeout --foreground 15 docker inspect --format '{{.State.Health.Status}}' "$id" 2>>"$log")" || health='unavailable'
    fi
    if [[ "$health" == healthy ]]; then
      if [[ -z "$database" ]]; then return 0; fi
      # A Unix socket can belong to initdb's temporary server. Require TCP and
      # the actual target database, with limits only on this disposable probe.
      probe="$(timeout --foreground 15 "${COMPOSE[@]}" exec -T \
        -e PGPASSWORD=password -e PGCONNECT_TIMEOUT=5 -e 'PGOPTIONS=-c statement_timeout=5000' \
        "$service" psql -X -w -h 127.0.0.1 -U user -d "$database" -Atqc 'SELECT 1' 2>>"$log")" || probe=''
      if [[ "$probe" == 1 ]]; then return 0; fi
    fi
    if (( SECONDS >= next_progress )); then
      echo "[pilot] Waiting for $service (health=$health; remaining=$((deadline-SECONDS))s); log=$log"
      next_progress=$((SECONDS + 15))
    fi
    sleep 2
  done
  timeout --foreground 15 "${COMPOSE[@]}" ps --all >>"$log" 2>&1 || true
  timeout --foreground 15 "${COMPOSE[@]}" logs --no-color --tail 100 "$service" >>"$log" 2>&1 || true
  fail "Startup timeout: $service; see $log"
}
if [[ "${PILOT_SKIP_BUILD:-0}" == 0 ]]; then
  "${COMPOSE[@]}" build >"$current_log" 2>&1
  printf '{"built":true}\n' >"$OUT/build-completed.json"
else
  echo '[pilot] Reusing suite images; build skipped' >"$current_log"
fi
if [[ "$DB_MODE" != mock ]]; then
deadline=$((SECONDS + DB_START_TIMEOUT_SECONDS))
timeout --foreground "$DB_START_TIMEOUT_SECONDS" "${COMPOSE[@]}" up --detach order-db warehouse-db billing-db shipping-db >>"$current_log" 2>&1
for service in order warehouse billing shipping; do
  wait_service_ready "$service-db" "$deadline" "$service"
done
fi
if [[ "$ARCHITECTURE" == choreographed-saga ]]; then
  deadline=$((SECONDS + BROKER_START_TIMEOUT_SECONDS))
  timeout --foreground "$BROKER_START_TIMEOUT_SECONDS" "${COMPOSE[@]}" up --detach rabbitmq >>"$current_log" 2>&1
  wait_service_ready rabbitmq "$deadline"
fi
deadline=$((SECONDS + HTTP_START_TIMEOUT_SECONDS))
timeout --foreground "$HTTP_START_TIMEOUT_SECONDS" "${COMPOSE[@]}" up --detach --no-build >>"$current_log" 2>&1
"${COMPOSE[@]}" logs --follow --no-color --timestamps --since "$STARTED_AT" \
  >"$OUT/application.log" 2>&1 &
children+=("$!")
mark readiness
for service in order warehouse billing shipping; do
  until curl -fsS --connect-timeout 2 --max-time 5 "$BASE_URL/$service/health" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || fail "HTTP readiness timeout: $service; see logs/startup.log and application.log"
    sleep 1
  done
done
mark dataset
curl -fsS --max-time "$SETUP_HTTP_TIMEOUT_SECONDS" "$BASE_URL/warehouse/products" >"$OUT/initial-products.json"
catalog_size="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$OUT/initial-products.json")"
if [[ "$catalog_size" == 0 && "$SEED_DATA" != 0 ]]; then
  current_log="$OUT/logs/seed.log"
  timeout --foreground "$SEED_TIMEOUT_SECONDS" bash "$ROOT_DIR/scripts/seed.sh" distributed >"$current_log" 2>&1
elif [[ "$catalog_size" != 0 && "$SEED_DATA" == 1 ]]; then
  fail 'Catalog is not empty; refusing duplicate seed. Use SEED_DATA=auto or 0.'
elif [[ "$catalog_size" == 0 ]]; then
  fail 'Catalog is empty and SEED_DATA=0'
fi
if [[ "$PILOT_ISOLATED" == 1 ]]; then
  curl -fsS --max-time "$SETUP_HTTP_TIMEOUT_SECONDS" "$BASE_URL/warehouse/products" >"$OUT/catalog-used.json"
fi
if (( PRELOAD_ORDERS > 0 )); then
  mark preload
  current_log="$OUT/logs/preload.log"
  python3 "$ROOT_DIR/scripts/pilot/preload.py" --compose "$BASE" \
    --architecture "$ARCHITECTURE" --orders "$PRELOAD_ORDERS" \
    --catalog "$OUT/catalog-used.json" --output "$OUT/preload" >"$current_log" 2>&1
fi
mark collectors
current_log="$OUT/postgres-collector.log"
if [[ "$DB_MODE" != mock ]]; then
python3 "$ROOT_DIR/scripts/diagnostics/collect-postgres.py" --output-dir "$OUT" \
  --run-id "$RUN_ID" --compose "$BASE" >"$current_log" 2>&1 &
postgres_pid=$!; children+=("$postgres_pid")
fi
if [[ "$COLLECT_HOST" == 1 ]]; then
  python3 "$ROOT_DIR/scripts/diagnostics/collect-host.py" --output-dir "$OUT" \
    --run-id "$RUN_ID" --compose "$BASE" >"$OUT/host-collector.log" 2>&1 &
  children+=("$!")
  if command -v iostat >/dev/null; then
    LC_ALL=C S_TIME_FORMAT=ISO iostat -xz -y -t 1 >"$OUT/iostat.log" 2>&1 &
    children+=("$!")
  else echo 'iostat unavailable (sysstat package)' >"$OUT/iostat-unavailable.txt"; fi
fi
if [[ "$DB_MODE" != mock ]]; then
deadline=$((SECONDS + COLLECTOR_START_TIMEOUT_SECONDS))
while (( SECONDS < deadline )); do
  [[ -s "$OUT/postgres-activity.jsonl" ]] && break
  kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector exited'
  sleep 1
done
[[ -s "$OUT/postgres-activity.jsonl" ]] || fail 'PostgreSQL collector produced no samples'
fi
run_stage() {
  local prefix="$1" rate="$2" duration="$3" repetition="$4" kind="$5" code=0 began=$SECONDS child
  mark "$prefix"
  current_log="$OUT/logs/${prefix}_k6.log"
  ARCHITECTURE="$ARCHITECTURE" RATE="$rate" DURATION="$duration" REPETITION="$repetition" \
  RUN_ID="$RUN_ID-$prefix" PILOT_PHASE="$kind" LOG_ERRORS=false SUMMARY_JSON="$OUT/$prefix.json" \
    k6 run --out "csv=$CSV_DIR/$prefix.csv" "$ROOT_DIR/experiments/k6/pilot.js" >"$current_log" 2>&1 &
  local load_pid=$!; children+=("$load_pid")
  while kill -0 "$load_pid" 2>/dev/null; do
    sleep 5
    if [[ "$DB_MODE" != mock ]]; then kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector stopped during stage'; fi
    if (( (SECONDS - began) / 5 % 3 == 0 )); then
      echo "[pilot] $prefix: $rate pedidos/s, elapsed=$((SECONDS - began))s; log=$current_log"
    fi
  done
  wait "$load_pid" || code=$?
  local -a remaining=()
  for child in "${children[@]}"; do
    [[ "$child" == "$load_pid" ]] || remaining+=("$child")
  done
  children=("${remaining[@]}")
  printf '%d\n' "$code" >"$OUT/${prefix}.exit-code.txt"
  (( code == 0 )) || return "$code"
  [[ -s "$OUT/$prefix.json" ]] || fail "Missing k6 summary: $prefix"
  if [[ "$DB_MODE" != mock ]]; then kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector stopped during stage'; fi
}
previous_business_snapshot=""
recover() {
  local stage="$1"
  mark recovery
  sleep "$COOLDOWN_SECONDS"
  if [[ "$DB_DIAGNOSTICS" == 1 ]]; then
  local deadline=$((SECONDS + RECOVERY_TIMEOUT_SECONDS))
  while ! tail -n 200 "$OUT/application.log" | python3 -c '
import datetime, json, sys
last = None
for line in sys.stdin:
    i = line.find("{\"timestamp\"")
    if i < 0: continue
    try: record = json.loads(line[i:])
    except ValueError: continue
    if record.get("type") == "db_diagnostic_sample": last = record
if last is None: sys.exit(1)
age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))).total_seconds()
sys.exit(0 if age < 5 and last["pool"]["inUse"] == 0 and last["pool"]["waiting"] == 0 else 1)
'; do
    (( SECONDS < deadline )) || fail 'Pool did not recover; stopping to avoid mixing plateaus. Inspect application.log.'
    sleep 2
  done
  fi
  if [[ "$PILOT_ISOLATED" == 1 ]]; then
    local -a extra=()
    if [[ "$ARCHITECTURE" == choreographed-saga ]]; then
      extra=(--wait-seconds "$RECOVERY_TIMEOUT_SECONDS" --broker)
      if [[ -n "$previous_business_snapshot" ]]; then extra+=(--previous "$previous_business_snapshot"); fi
    fi
    if [[ "$DB_MODE" == mock ]]; then
      python3 "$ROOT_DIR/scripts/pilot/mock_state.py" --compose "$BASE" \
        --customer-prefix "customer-k6-$ARCHITECTURE-$RUN_ID-$stage-" \
        --output "$OUT/business/$stage.json" --wait-seconds "$RECOVERY_TIMEOUT_SECONDS"
    else
    python3 "$ROOT_DIR/scripts/pilot/state.py" --compose "$BASE" \
      --customer-prefix "customer-k6-$ARCHITECTURE-$RUN_ID-$stage-" \
      --output "$OUT/business/$stage.json" "${extra[@]}"
    fi
    previous_business_snapshot="$OUT/business/$stage.json"
  fi
}
run_stage warmup "$WARMUP_RATE" "$WARMUP_DURATION" 0 warmup
recover warmup
for rate in "${rates[@]}"; do
  for ((repetition=PILOT_REPETITION_START; repetition<PILOT_REPETITION_START+REPETITIONS; repetition++)); do
    echo "[pilot] Rate=$rate requests/s; repetition=$repetition/$REPETITIONS"
    run_stage "rate${rate}_run${repetition}" "$rate" "$DURATION" "$repetition" benchmark
    recover "rate${rate}_run${repetition}"
  done
done
mark completed
