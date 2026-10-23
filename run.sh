#!/usr/bin/env bash
# Exploratory PostgreSQL capacity pilot for the distributed baseline.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: [RUN_ID=vm-pilot-01] [RATES=1,2,5,10,15,20,30,40] bash run.sh
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
PRE_ALLOCATED_VUS defaults to 25 * highest RATE (20 s HTTP timeout + headroom).
MAX_VUS defaults to PRE_ALLOCATED_VUS, avoiding runtime VU allocation by default.
PLOT_PYTHON can select a Python environment with matplotlib. Otherwise summaries
are still generated and charts can be generated manually later.
K6_CSV_STORAGE=tmpfs stages ALL k6 CSVs in memory until load and recovery end,
then copies them to the usual results directory before analysis/charts.
K6_CSV_TMPFS_ROOT must be a writable tmpfs mount (findmnt is required).
CSV files accumulate for the entire run: reserve enough free tmpfs/RAM space.
Existing containers/volumes and previous results are never deleted.
HELP
  exit 0
fi
(( $# == 0 )) || { echo 'Use bash run.sh --help' >&2; exit 1; }
export RUN_ID="${RUN_ID:-vm-pg-pilot-$(date -u +%Y%m%dT%H%M%SZ)}"
export RATES="${RATES:-1,2,5,10,15,20,30,40}"
export DURATION="${DURATION:-3m}" WARMUP_DURATION="${WARMUP_DURATION:-1m}"
export WARMUP_RATE="${WARMUP_RATE:-1}" REPETITIONS="${REPETITIONS:-1}"
export SETTLING_SECONDS="${SETTLING_SECONDS:-30}"
export COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-30}"
export RECOVERY_TIMEOUT_SECONDS="${RECOVERY_TIMEOUT_SECONDS:-60}"
export DB_DIAGNOSTICS="${DB_DIAGNOSTICS:-1}" COLLECT_HOST="${COLLECT_HOST:-1}"
export K6_CSV_STORAGE="${K6_CSV_STORAGE:-disk}"
export K6_CSV_TMPFS_ROOT="${K6_CSV_TMPFS_ROOT:-/dev/shm}"
export SEED_DATA="${SEED_DATA:-auto}" BASE_URL=http://127.0.0.1:8000 WAREHOUSE_PREFIX=/warehouse
export TZ=UTC
export STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
AUTO_PLOT="${AUTO_PLOT:-1}"
PLOT_PYTHON="${PLOT_PYTHON:-$ROOT_DIR/.venv/bin/python}"
[[ -x "$PLOT_PYTHON" ]] || PLOT_PYTHON=python3
OUT="$ROOT_DIR/experiments/datasets/pilot/$RUN_ID"
BASE="$ROOT_DIR/environment/compose/compose.base.yaml"
COMPOSE=(docker compose -f "$BASE" -f "$ROOT_DIR/environment/compose/compose.diagnostics.yaml")
fail() { echo "[pilot] $*" >&2; exit 1; }
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
for name in WARMUP_RATE REPETITIONS PRE_ALLOCATED_VUS MAX_VUS RECOVERY_TIMEOUT_SECONDS; do
  [[ "${!name}" =~ ^[1-9][0-9]{0,6}$ ]] || fail "$name must be a positive integer"
done
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
"${COMPOSE[@]}" config --quiet
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
  local status=$? pid
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
  printf '{"runId":"%s","exitCode":%d,"phase":"%s","plots":"%s","csvStorage":"%s","csvExport":"%s"}\n' \
    "$RUN_ID" "$status" "$phase" "$plots_status" "$K6_CSV_STORAGE" "$csv_export_status" >"$OUT/status.json"
  echo "[pilot] Results: $OUT (exit=$status, phase=$phase, plots=$plots_status)"
  echo '[pilot] Containers and volumes preserved. HTTP success must be assessed in the results.'
  if (( status != 0 )) && [[ -f "$current_log" ]]; then tail -n 40 "$current_log" >&2; fi
  if [[ "$plots_status" == plots_pending ]]; then
    echo "[pilot] Charts pending; see analysis.log or run: python3 experiments/analyze_pilot.py '$OUT'"
  fi
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
echo "[pilot] Output: $OUT; rates=$RATES; repetitions=$REPETITIONS per rate; duration=$DURATION per repetition; VUs=$PRE_ALLOCATED_VUS/$MAX_VUS"
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
        'K6_CSV_STORAGE','K6_CSV_TMPFS_ROOT']
data = {'architecture': 'distributed-baseline', 'database': 'postgres',
        'environment': {k: os.environ[k] for k in keys},
        'csvOutput': {'storage': os.environ['K6_CSV_STORAGE'], 'pathDuringLoad': str(csv_dir),
                      'availableBytesBeforeRun': os.statvfs(csv_dir).f_bavail * os.statvfs(csv_dir).f_frsize,
                      'exportPolicy': 'Copy tmpfs CSVs only after all load/recovery and child shutdown, before analysis.'},
        'datasetPolicy': 'Reuse volumes; seed only an empty catalog; ascending exploratory sweep without resets.'}
(out/'metadata.json').write_text(json.dumps(data, indent=2)+'\n')
files = [root/'run.sh', root/'experiments/k6/pilot.js', root/'experiments/analyze_pilot.py',
         root/'scripts/seed.sh', root/'scripts/diagnostics/collect-postgres.py',
         root/'scripts/diagnostics/collect-host.py', root/'scripts/diagnostics/dataset-permissions.sh']
for service in ['order','warehouse','billing','shipping']:
    base = root/f'implementation/distributed-baseline/{service}-service'
    files += list((base/'src').rglob('*.ts'))
    files += [base/'package.json', base/'pnpm-lock.yaml', base/'Dockerfile']
files += list((root/'environment/compose').glob('*.yaml'))
(out/'source-sha256.json').write_text(json.dumps({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                              for p in files}, indent=2)+'\n')
PY
mark startup
current_log="$OUT/logs/startup.log"
"${COMPOSE[@]}" build >"$current_log" 2>&1
"${COMPOSE[@]}" up --detach order-db warehouse-db billing-db shipping-db >>"$current_log" 2>&1
deadline=$((SECONDS + 600))
for service in order warehouse billing shipping; do
  until [[ "$(docker inspect --format '{{.State.Health.Status}}' "$service-db")" == healthy ]]; do
    (( SECONDS < deadline )) || fail "Database initialization timeout: $service-db"
    sleep 2
  done
done
"${COMPOSE[@]}" up --detach --no-build >>"$current_log" 2>&1
"${COMPOSE[@]}" logs --follow --no-color --timestamps --since "$STARTED_AT" \
  >"$OUT/application.log" 2>&1 &
children+=("$!")
mark readiness
deadline=$((SECONDS + 180))
for service in order warehouse billing shipping; do
  until curl -fsS --connect-timeout 2 --max-time 5 "$BASE_URL/$service/health" >/dev/null 2>&1; do
    (( SECONDS < deadline )) || fail "HTTP readiness timeout: $service"
    sleep 1
  done
done
mark dataset
curl -fsS --max-time 10 "$BASE_URL/warehouse/products" >"$OUT/initial-products.json"
catalog_size="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$OUT/initial-products.json")"
if [[ "$catalog_size" == 0 && "$SEED_DATA" != 0 ]]; then
  current_log="$OUT/logs/seed.log"
  timeout --foreground 180 bash "$ROOT_DIR/scripts/seed.sh" distributed >"$current_log" 2>&1
elif [[ "$catalog_size" != 0 && "$SEED_DATA" == 1 ]]; then
  fail 'Catalog is not empty; refusing duplicate seed. Use SEED_DATA=auto or 0.'
elif [[ "$catalog_size" == 0 ]]; then
  fail 'Catalog is empty and SEED_DATA=0'
fi
mark collectors
current_log="$OUT/postgres-collector.log"
python3 "$ROOT_DIR/scripts/diagnostics/collect-postgres.py" --output-dir "$OUT" \
  --run-id "$RUN_ID" --compose "$BASE" >"$current_log" 2>&1 &
postgres_pid=$!; children+=("$postgres_pid")
if [[ "$COLLECT_HOST" == 1 ]]; then
  python3 "$ROOT_DIR/scripts/diagnostics/collect-host.py" --output-dir "$OUT" \
    --run-id "$RUN_ID" --compose "$BASE" >"$OUT/host-collector.log" 2>&1 &
  children+=("$!")
  if command -v iostat >/dev/null; then
    LC_ALL=C S_TIME_FORMAT=ISO iostat -xz -y -t 1 >"$OUT/iostat.log" 2>&1 &
    children+=("$!")
  else echo 'iostat unavailable (sysstat package)' >"$OUT/iostat-unavailable.txt"; fi
fi
for ((attempt=0; attempt<15; attempt++)); do
  [[ -s "$OUT/postgres-activity.jsonl" ]] && break
  kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector exited'
  sleep 1
done
[[ -s "$OUT/postgres-activity.jsonl" ]] || fail 'PostgreSQL collector produced no samples'
run_stage() {
  local prefix="$1" rate="$2" duration="$3" repetition="$4" kind="$5" code=0 began=$SECONDS child
  mark "$prefix"
  current_log="$OUT/logs/${prefix}_k6.log"
  ARCHITECTURE=distributed-baseline RATE="$rate" DURATION="$duration" REPETITION="$repetition" \
  RUN_ID="$RUN_ID-$prefix" PILOT_PHASE="$kind" LOG_ERRORS=false SUMMARY_JSON="$OUT/$prefix.json" \
    k6 run --out "csv=$CSV_DIR/$prefix.csv" "$ROOT_DIR/experiments/k6/pilot.js" >"$current_log" 2>&1 &
  local load_pid=$!; children+=("$load_pid")
  while kill -0 "$load_pid" 2>/dev/null; do
    sleep 5
    kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector stopped during stage'
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
  kill -0 "$postgres_pid" 2>/dev/null || fail 'PostgreSQL collector stopped during stage'
}
recover() {
  mark recovery
  sleep "$COOLDOWN_SECONDS"
  [[ "$DB_DIAGNOSTICS" == 1 ]] || return 0
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
}
run_stage warmup "$WARMUP_RATE" "$WARMUP_DURATION" 0 warmup
recover
for rate in "${rates[@]}"; do
  for ((repetition=1; repetition<=REPETITIONS; repetition++)); do
    echo "[pilot] Rate=$rate requests/s; repetition=$repetition/$REPETITIONS"
    run_stage "rate${rate}_run${repetition}" "$rate" "$DURATION" "$repetition" benchmark
    recover
  done
done
mark completed
