#!/usr/bin/env bash
# Same pilot/collectors/load as run.sh, with a final Little's law report.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: sudo RUN_ID=vm-pg-little-01 bash run-little.sh'
  echo 'Defaults: RATES=100 (fixed), REPETITIONS=20, DURATION=3m, DB_DIAGNOSTICS=1 (fixed).'
  echo 'Other pilot variables are inherited from run.sh. No pool/timeout/socket changes.'
  echo 'K6_CSV_STORAGE=tmpfs keeps CSVs in RAM during all repetitions; exported before charts/Little report.'
  exit 0
fi
(( $# == 0 )) || { echo 'Use bash run-little.sh --help' >&2; exit 1; }
export RUN_ID="${RUN_ID:-pg-little-100rps-$(date -u +%Y%m%dT%H%M%SZ)}"
export RATES=100 REPETITIONS="${REPETITIONS:-20}" DURATION="${DURATION:-3m}" DB_DIAGNOSTICS=1
OUT="$ROOT_DIR/experiments/datasets/pilot/$RUN_ID"
if [[ -e "$OUT" || -L "$OUT" ]]; then
  echo "[little] Refusing to overwrite $OUT" >&2
  exit 1
fi
status=0
bash "$ROOT_DIR/run.sh" || status=$?
if [[ -f "$OUT/application.log" ]] && compgen -G "$OUT/rate*_run*.json" >/dev/null; then
  python3 "$ROOT_DIR/experiments/analyze_little.py" "$OUT" \
    --target-rps 100 --expected-repetitions "$REPETITIONS" || { (( status != 0 )) || status=1; }
  # run.sh already returned ownership; these two reports were written afterwards.
  if (( EUID == 0 )) && [[ "${SUDO_UID:-}" =~ ^[0-9]+$ && "${SUDO_GID:-}" =~ ^[0-9]+$ ]]; then
    for report in "$OUT/analysis/little.json" "$OUT/analysis/little.csv"; do
      [[ ! -f "$report" ]] || chown -- "$SUDO_UID:$SUDO_GID" "$report"
    done
  fi
else
  echo '[little] No benchmark artifacts: no C calculated. Inspect the pilot failure logs.' >&2
  (( status != 0 )) || status=1
fi
exit "$status"
