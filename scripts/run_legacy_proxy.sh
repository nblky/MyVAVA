#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR/legacy"

exec "$ROOT_DIR/.venv/bin/python" mock_sunvalley_connect_proxy.py
