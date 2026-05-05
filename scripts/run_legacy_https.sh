#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/legacy"

exec "$ROOT_DIR/.venv/bin/python" mock_sunvalley_https.py \
  --state "$ROOT_DIR/data/sunvalley_state.json" \
  --db "$ROOT_DIR/data/sunvalley_state.sqlite3"
