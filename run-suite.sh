#!/usr/bin/env bash
# Same run.sh protocol, one isolated architecture at a time. No volume reset.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  cat <<'HELP'
Usage: [SUITE_ID=comparison-01] [REPETITIONS=8] [RATES=1,2,5,10,15,20,30,40] bash run-suite.sh
Defaults: ARCHITECTURES=tcc,two-phase-commit,orchestrated-saga,choreographed-saga
Add distributed-baseline to ARCHITECTURES to run it as well.
BASELINE_RUN=/path/to/existing/baseline optionally adds it to comparison charts
without running it again. Cannot combine with distributed-baseline in the list.
All workload, collection and CSV storage variables are inherited by run.sh.
PRELOAD_ORDERS optionally prepares synthetic historical orders once per architecture.
DB_POOL_MAX=80 optionally standardizes max; omitted preserves native settings.
Each architecture gets its own folder, containers, network and persistent volumes.
Failed architectures are recorded and execution continues after their containers stop.
The suite exits nonzero at the end if any architecture failed. Interruptions and
unconfirmed container shutdown stop the sequence immediately.
HELP
  exit 0
fi
(( $# == 0 )) || { echo 'Use bash run-suite.sh --help' >&2; exit 1; }
export SUITE_ID="${SUITE_ID:-comparison-$(date -u +%Y%m%dT%H%M%SZ)}"
export ARCHITECTURES="${ARCHITECTURES:-tcc,two-phase-commit,orchestrated-saga,choreographed-saga}"
[[ "$SUITE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,59}$ ]] || { echo 'Invalid SUITE_ID' >&2; exit 1; }
[[ "$ARCHITECTURES" =~ ^[a-z-]+(,[a-z-]+)*$ ]] || { echo 'Invalid ARCHITECTURES list' >&2; exit 1; }
IFS=, read -r -a architectures <<<"$ARCHITECTURES"
declare -A selected=()
for architecture in "${architectures[@]}"; do
  [[ "$architecture" =~ ^(distributed-baseline|tcc|two-phase-commit|orchestrated-saga|choreographed-saga)$ ]] \
    || { echo "Unsupported architecture: $architecture" >&2; exit 1; }
  [[ -z "${selected[$architecture]:-}" ]] || { echo "Duplicate architecture: $architecture" >&2; exit 1; }
  selected[$architecture]=1
done
OUT="$ROOT_DIR/experiments/datasets/pilot/$SUITE_ID"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { echo "Refusing to overwrite $OUT" >&2; exit 1; }
BASELINE_RUN="${BASELINE_RUN:-}"
if [[ -n "$BASELINE_RUN" ]]; then
  [[ -z "${selected[distributed-baseline]:-}" ]] || { echo 'Choose BASELINE_RUN or a new baseline run, not both' >&2; exit 1; }
  BASELINE_RUN="$(cd "$BASELINE_RUN" && pwd -P)"
  python3 - "$BASELINE_RUN" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
if json.loads((p/'metadata.json').read_text()).get('architecture') != 'distributed-baseline':
    sys.exit('BASELINE_RUN must point to a distributed-baseline dataset')
if not (p/'analysis/levels.csv').is_file():
    sys.exit('BASELINE_RUN must already have analysis/levels.csv')
PY
fi
running="$(docker ps --format '{{.Names}} {{.Label "com.docker.compose.project"}}')"
printf '%s\n' "$running" | python3 -c '
import sys
projects = ("distributed-lab", "distributed-lab-base", "distributed-lab-mq")
prefixes = tuple("pilot-" + a for a in ("distributed-baseline", "tcc", "two-phase-commit", "orchestrated-saga", "choreographed-saga"))
active = [line.strip() for line in sys.stdin if len(line.split()) >= 2 and
          (line.split()[1] in projects or line.split()[1].startswith(prefixes))]
if active:
    sys.exit("Stop the existing experimental stacks before this suite:\n" + "\n".join(active))
'
umask 077
source "$ROOT_DIR/scripts/diagnostics/dataset-permissions.sh"
dataset_prepare_parent "$ROOT_DIR" "$OUT"
mkdir "$OUT"
active_pid=''
active_architecture=''
progress_pid=''
suite_code=0
failed_architectures=()
runs=()
[[ -z "$BASELINE_RUN" ]] || runs+=("$BASELINE_RUN")
finish() {
  local status=$? plots=not_requested
  trap - EXIT INT TERM
  set +e
  (( status != 0 )) || status=$suite_code
  if [[ -n "$progress_pid" ]]; then kill -TERM "$progress_pid" 2>/dev/null; wait "$progress_pid" 2>/dev/null; fi
  if [[ -n "$active_pid" ]]; then
    kill -TERM "$active_pid" 2>/dev/null
    wait "$active_pid" 2>/dev/null
    if [[ -f "$OUT/$active_architecture/analysis/levels.csv" ]]; then runs+=("$OUT/$active_architecture"); fi
  fi
  if (( ${#runs[@]} )); then
    python3 "$ROOT_DIR/experiments/compare_pilots.py" --output "$OUT/comparison" \
      --no-plots "${runs[@]}" >"$OUT/comparison.log" 2>&1
    if (( $? != 0 )); then
      plots=analysis_failed
      (( status != 0 )) || status=1
    else
      plots=summary_only
      if [[ "${AUTO_PLOT:-1}" == 1 ]]; then
        local plot_python="${PLOT_PYTHON:-$ROOT_DIR/.venv/bin/python}"
        [[ -x "$plot_python" ]] || plot_python=python3
        "$plot_python" "$ROOT_DIR/experiments/compare_pilots.py" --output "$OUT/comparison" \
          "${runs[@]}" >>"$OUT/comparison.log" 2>&1
        if (( $? == 0 )); then plots=generated; else plots=plots_pending; fi
      fi
    fi
  fi
  : >"$OUT/status.json"
  if ! dataset_return_ownership "$OUT"; then (( status != 0 )) || status=1; fi
  python3 - "$OUT/status.json" "$SUITE_ID" "$status" "$plots" "${failed_architectures[@]}" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
    'suiteId': sys.argv[2], 'exitCode': int(sys.argv[3]), 'comparison': sys.argv[4],
    'failedArchitectures': sys.argv[5:],
}) + '\n')
PY
  if (( ${#failed_architectures[@]} )); then
    echo "[suite] Failed architectures: ${failed_architectures[*]} (see their logs/status.json)" >&2
  fi
  echo "[suite] Results: $OUT (exit=$status, comparison=$plots)"
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
echo "[suite] Sequential architectures: $ARCHITECTURES"
for architecture in "${architectures[@]}"; do
  echo "[suite] Starting $architecture"
  active_architecture="$architecture"
  printf '{"architecture":"%s","phase":"start","timestamp":"%s"}\n' \
    "$architecture" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$OUT/phases.jsonl"
  # The background child's redirection may not have created the file yet.
  : >"$OUT/$architecture.log"
  PILOT_SUITE_ID="$SUITE_ID" RUN_ID="$SUITE_ID-$architecture" ARCHITECTURE="$architecture" PILOT_ISOLATED=1 \
    bash "$ROOT_DIR/run.sh" >"$OUT/$architecture.log" 2>&1 &
  active_pid=$!
  tail --pid="$active_pid" -n +1 -F "$OUT/$architecture.log" &
  progress_pid=$!
  code=0
  wait "$active_pid" || code=$?
  active_pid=''
  wait "$progress_pid" || true
  progress_pid=''
  if [[ -f "$OUT/$architecture/analysis/levels.csv" ]]; then runs+=("$OUT/$architecture"); fi
  stopped=0
  python3 - "$OUT/$architecture/status.json" <<'PY' && stopped=1 || { (( code != 0 )) || code=1; }
import json, sys
if json.load(open(sys.argv[1])).get('containerStop') != 'stopped':
    sys.exit('Containers did not stop; refusing to start another architecture')
PY
  printf '{"architecture":"%s","phase":"finish","exitCode":%d}\n' "$architecture" "$code" >>"$OUT/phases.jsonl"
  if (( code != 0 )); then
    echo "[suite] $architecture failed; see $OUT/$architecture.log" >&2
    tail -n 35 "$OUT/$architecture.log" >&2
    failed_architectures+=("$architecture")
    (( suite_code != 0 )) || suite_code=$code
  fi
  if (( stopped == 0 || code == 130 || code == 143 )); then
    echo '[suite] Sequence stopped: shutdown was not confirmed or the runner was interrupted.' >&2
    exit "$code"
  fi
  if (( code != 0 )); then
    echo "[suite] Containers stopped; continuing after failure of $architecture." >&2
  fi
done
