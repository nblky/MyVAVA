#!/bin/zsh
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/rtmp_ingest_server.py" "$@"
