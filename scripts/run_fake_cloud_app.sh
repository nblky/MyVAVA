#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

exec "$ROOT_DIR/.venv/bin/python" -m uvicorn fake_cloud_app.app:app \
  --host "${VAVA_FASTAPI_HOST:-127.0.0.1}" \
  --port "${VAVA_FASTAPI_PORT:-18080}"
