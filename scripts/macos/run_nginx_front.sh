#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NGINX_BIN="${NGINX_BIN:-/opt/homebrew/bin/nginx}"
CONFIG="$ROOT_DIR/deploy/nginx/macos/vava-front.conf"

mkdir -p "$ROOT_DIR/run/nginx" "$ROOT_DIR/logs/nginx"

exec "$NGINX_BIN" -c "$CONFIG"
