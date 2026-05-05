#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-18079}"
exec "$ROOT_DIR/.venv/bin/uvicorn" app.cloud_platform.app:app --host 0.0.0.0 --port "${PORT}"
