#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${VAVA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
LISTEN_HOST="${VAVA_PROXY_LISTEN_HOST:-0.0.0.0}"
LISTEN_PORT="${VAVA_PROXY_LISTEN_PORT:-8888}"
UPSTREAM_HOST="${VAVA_PROXY_UPSTREAM_HOST:-127.0.0.1}"
UPSTREAM_PORT="${VAVA_PROXY_UPSTREAM_PORT:-443}"

cd "$ROOT_DIR/legacy"

exec "$ROOT_DIR/.venv/bin/python" mock_sunvalley_connect_proxy.py \
  --listen-host "$LISTEN_HOST" \
  --listen-port "$LISTEN_PORT" \
  --upstream-host "$UPSTREAM_HOST" \
  --upstream-port "$UPSTREAM_PORT"
