#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stop_from_pidfile() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    kill "${pid}" 2>/dev/null || true
    sleep 0.5
    kill -9 "${pid}" 2>/dev/null || true
    echo "stopped ${name} pid=${pid}"
  fi
  rm -f "${pid_file}"
}

stop_from_pidfile "fastapi" "${ROOT_DIR}/run/fastapi_18080.pid"
stop_from_pidfile "legacy_https" "${ROOT_DIR}/run/legacy_https_18443.pid"
stop_from_pidfile "connect_proxy" "${ROOT_DIR}/run/connect_proxy_8888.pid"

if [[ -f "${ROOT_DIR}/run/nginx/nginx.pid" ]]; then
  "${ROOT_DIR}/scripts/macos/stop_nginx_front.sh" || true
  echo "stopped nginx front"
fi

# Fallback cleanup for processes launched from this workspace.
pkill -f "${ROOT_DIR}/legacy/mock_sunvalley_https.py" 2>/dev/null || true
pkill -f "${ROOT_DIR}/legacy/mock_sunvalley_connect_proxy.py" 2>/dev/null || true
pkill -f "${ROOT_DIR}/.venv/bin/uvicorn app.main:app" 2>/dev/null || true
pkill -f "${ROOT_DIR}/deploy/nginx/macos/vava-front.conf" 2>/dev/null || true

echo "project services stopped"
