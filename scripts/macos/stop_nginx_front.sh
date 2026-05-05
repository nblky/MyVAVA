#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PID_FILE="$ROOT_DIR/run/nginx/nginx.pid"

kill -QUIT "$(cat "$PID_FILE")"
