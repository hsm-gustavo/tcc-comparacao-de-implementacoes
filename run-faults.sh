#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == --help ]]; then
  python3 "$ROOT_DIR/scripts/faults/run.py" --help
  exit
fi
export RUN_ID="${RUN_ID:-faults-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$RUN_ID" =~ ^[a-zA-Z0-9._-]+$ ]] || { echo 'Invalid RUN_ID' >&2; exit 1; }
OUTPUT="$ROOT_DIR/experiments/datasets/faults/$RUN_ID"
source "$ROOT_DIR/scripts/diagnostics/dataset-permissions.sh"
dataset_prepare_parent "$ROOT_DIR" "$OUTPUT"
trap 'dataset_return_ownership "$OUTPUT"' EXIT
python3 "$ROOT_DIR/scripts/faults/run.py" --output "$OUTPUT" "$@"
