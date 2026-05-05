#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR/legacy"

exec "$ROOT_DIR/.venv/bin/python" mock_sunvalley_https.py \
  --host 127.0.0.1 \
  --port 18443 \
  --state "$ROOT_DIR/data/sunvalley_state.json" \
  --db "$ROOT_DIR/data/sunvalley_state.sqlite3"
