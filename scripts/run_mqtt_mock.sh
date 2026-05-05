#!/bin/sh
set -eu

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

exec python3 "$BASE_DIR/scripts/mqtt_mock_broker.py" \
  --host 0.0.0.0 \
  --port 9903 \
  --timeout 60 \
  --certfile "$BASE_DIR/legacy/sunvalley_multi_san_fullchain.crt" \
  --keyfile "$BASE_DIR/legacy/sunvalley_multi_san.key" \
  "$@"
