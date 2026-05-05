#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

exec "$ROOT_DIR/.venv/bin/uvicorn" app.main:app --host 0.0.0.0 --port 18080 --reload
