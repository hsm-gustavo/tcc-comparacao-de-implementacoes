#!/usr/bin/env bash
# Same suite workload, with volatile per-service storage and no PostgreSQL.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DB_MODE=mock DB_DIAGNOSTICS=0
export PRELOAD_ORDERS="${PRELOAD_ORDERS:-0}"
if [[ "$PRELOAD_ORDERS" != 0 ]]; then
  echo 'Mock does not support SQL historical preload; use PRELOAD_ORDERS=0.' >&2
  exit 1
fi
export SUITE_ID="${SUITE_ID:-mock-$(date -u +%Y%m%dT%H%M%SZ)}"
export ARCHITECTURES="${ARCHITECTURES:-distributed-baseline,tcc,two-phase-commit,orchestrated-saga,choreographed-saga}"
exec bash "$ROOT_DIR/run-suite.sh" "$@"
