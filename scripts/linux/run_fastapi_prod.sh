#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${VAVA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
HOST="${VAVA_FASTAPI_HOST:-127.0.0.1}"
PORT="${VAVA_FASTAPI_PORT:-18080}"

cd "$ROOT_DIR"

exec "$ROOT_DIR/.venv/bin/uvicorn" app.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --proxy-headers
