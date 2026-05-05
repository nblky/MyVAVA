#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${VAVA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOST="${VAVA_LEGACY_HOST:-127.0.0.1}"
PORT="${VAVA_LEGACY_PORT:-18443}"

cd "$ROOT_DIR/legacy"

exec "$ROOT_DIR/.venv/bin/python" mock_sunvalley_https.py \
  --host "$HOST" \
  --port "$PORT" \
  --state "$ROOT_DIR/data/sunvalley_state.json" \
  --db "$ROOT_DIR/data/sunvalley_state.sqlite3"
