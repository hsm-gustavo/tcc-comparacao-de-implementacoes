#!/usr/bin/env bash
# Fresh PostgreSQL/broker state for every architecture/rate/repetition.
set -euo pipefail
umask 077
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: SUITE_ID=independent-01 RATES=1,2,5 REPETITIONS=8 bash run-independent-suite.sh'
  echo 'New volumes + seed + warmup for EVERY trial; only its own volumes are deleted afterward.'
  echo 'Build once per architecture. PostgreSQL only; PRELOAD_ORDERS=0. Existing suites are never reused.'
  exit 0
fi
(( $# == 0 )) || exit 1
export SUITE_ID="${SUITE_ID:-independent-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$SUITE_ID" =~ ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,39}$ ]] || { echo 'Invalid SUITE_ID (maximum 40 characters)' >&2; exit 1; }
OUT="$ROOT_DIR/experiments/datasets/pilot/$SUITE_ID"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || { echo "Refusing to overwrite $OUT" >&2; exit 1; }
source "$ROOT_DIR/scripts/diagnostics/dataset-permissions.sh"
dataset_prepare_parent "$ROOT_DIR" "$OUT"
active_pid=''
finish() {
  local code=$?
  trap - EXIT INT TERM
  if [[ -n "$active_pid" ]] && kill -0 "$active_pid" 2>/dev/null; then
    kill -TERM "$active_pid" 2>/dev/null || true
    wait "$active_pid" || true
  fi
  if [[ -d "$OUT" ]]; then dataset_return_ownership "$OUT" || code=1; fi
  exit "$code"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
python3 "$ROOT_DIR/scripts/pilot/independent.py" --output "$OUT" &
active_pid=$!
wait "$active_pid"
active_pid=''
