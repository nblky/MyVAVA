#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
UVICORN_BIN="${VENV_DIR}/bin/uvicorn"

MODE="${1:-full}" # full | fastapi

FASTAPI_HOST="${VAVA_FASTAPI_HOST:-127.0.0.1}"
FASTAPI_PORT="${VAVA_FASTAPI_PORT:-18080}"
LEGACY_HOST="${VAVA_LEGACY_HOST:-127.0.0.1}"
LEGACY_PORT="${VAVA_LEGACY_PORT:-18443}"
PROXY_LISTEN_HOST="${VAVA_PROXY_LISTEN_HOST:-0.0.0.0}"
PROXY_LISTEN_PORT="${VAVA_PROXY_LISTEN_PORT:-8888}"
PROXY_UPSTREAM_HOST="${VAVA_PROXY_UPSTREAM_HOST:-127.0.0.1}"
PROXY_UPSTREAM_PORT="${VAVA_PROXY_UPSTREAM_PORT:-443}"

mkdir -p "${ROOT_DIR}/data" "${ROOT_DIR}/logs" "${ROOT_DIR}/run"

if [[ ! -x "${PYTHON_BIN}" || ! -x "${UVICORN_BIN}" ]]; then
  echo "missing venv, run: ${ROOT_DIR}/scripts/setup_venv.sh" >&2
  exit 1
fi

cd "${ROOT_DIR}"

# Initialize DB/schema/state snapshot if needed.
"${PYTHON_BIN}" - <<'PY'
from app.store import get_store

store = get_store()
print(f"db ready: {store.db_path}")
print(f"state ready: {store.state_path}")
PY

start_bg() {
  local name="$1"
  local pid_file="$2"
  local log_file="$3"
  shift 3

  if [[ -f "${pid_file}" ]]; then
    local old_pid
    old_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
      echo "${name} already running pid=${old_pid}"
      return 0
    fi
    rm -f "${pid_file}"
  fi

  nohup "$@" >>"${log_file}" 2>&1 &
  local new_pid=$!
  echo "${new_pid}" >"${pid_file}"
  echo "started ${name} pid=${new_pid}"
}

start_nginx() {
  local pid_file="${ROOT_DIR}/run/nginx/nginx.pid"
  local pid=""
  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "nginx already running pid=${pid}"
      return 0
    fi
  fi
  mkdir -p "${ROOT_DIR}/run/nginx" "${ROOT_DIR}/logs/nginx"
  "${ROOT_DIR}/scripts/macos/run_nginx_front.sh"
  sleep 0.5
  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}" 2>/dev/null || true)"
  fi
  if [[ -z "${pid}" ]] || ! kill -0 "${pid}" 2>/dev/null; then
    echo "failed to start nginx front (443)" >&2
    exit 1
  fi
  echo "started nginx pid=${pid}"
}

start_bg \
  "fastapi" \
  "${ROOT_DIR}/run/fastapi_${FASTAPI_PORT}.pid" \
  "${ROOT_DIR}/logs/uvicorn_${FASTAPI_PORT}.out" \
  "${UVICORN_BIN}" app.main:app --host "${FASTAPI_HOST}" --port "${FASTAPI_PORT}"

if [[ "${MODE}" == "full" ]]; then
  start_bg \
    "legacy_https" \
    "${ROOT_DIR}/run/legacy_https_${LEGACY_PORT}.pid" \
    "${ROOT_DIR}/logs/legacy_https_${LEGACY_PORT}.out" \
    "${PYTHON_BIN}" "${ROOT_DIR}/legacy/mock_sunvalley_https.py" \
      --host "${LEGACY_HOST}" \
      --port "${LEGACY_PORT}" \
      --state "${ROOT_DIR}/data/sunvalley_state.json" \
      --db "${ROOT_DIR}/data/sunvalley_state.sqlite3"

  start_bg \
    "connect_proxy" \
    "${ROOT_DIR}/run/connect_proxy_${PROXY_LISTEN_PORT}.pid" \
    "${ROOT_DIR}/logs/connect_proxy_${PROXY_LISTEN_PORT}.out" \
    "${PYTHON_BIN}" "${ROOT_DIR}/legacy/mock_sunvalley_connect_proxy.py" \
      --listen-host "${PROXY_LISTEN_HOST}" \
      --listen-port "${PROXY_LISTEN_PORT}" \
      --upstream-host "${PROXY_UPSTREAM_HOST}" \
      --upstream-port "${PROXY_UPSTREAM_PORT}"

  start_nginx
fi

echo "mode=${MODE} started"
