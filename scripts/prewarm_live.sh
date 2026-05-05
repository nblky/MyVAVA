#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE="${API_BASE:-http://127.0.0.1:18079}"
PYTHON_BIN="${ROOT}/.venv/bin/python"
QUALITY="${QUALITY:-auto}"
KEEP_ALIVE="${KEEP_ALIVE:-1}"
REASON="${REASON:-prewarm}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "missing ${PYTHON_BIN}" >&2
  exit 1
fi

camera_args=("$@")

if (( ${#camera_args[@]} == 0 )); then
  mapfile -t camera_args < <("${PYTHON_BIN}" - <<'PY'
from app.store import get_store

camera_index = get_store().camera_index_payload()
for item in camera_index.get("cameraList", []):
    if not isinstance(item, dict):
        continue
    camera_sn = str(item.get("cameraSn", "") or "").strip()
    station_sn = str(item.get("stationSn", "") or "").strip()
    if camera_sn and station_sn:
        print(f"{camera_sn}|{station_sn}")
PY
)
fi

if (( ${#camera_args[@]} == 0 )); then
  echo "no cameras found" >&2
  exit 1
fi

for item in "${camera_args[@]}"; do
  camera_sn="${item%%|*}"
  station_sn="${item#*|}"
  if [[ -z "${camera_sn}" || -z "${station_sn}" || "${camera_sn}" == "${station_sn}" ]]; then
    echo "skip invalid camera argument: ${item}" >&2
    continue
  fi
  json_payload="$(printf '{"cameraSn":"%s","stationSn":"%s","quality":"%s","keepAlive":%s,"reason":"%s"}' "${camera_sn}" "${station_sn}" "${QUALITY}" "${KEEP_ALIVE}" "${REASON}")"
  echo "prewarm ${camera_sn} via ${station_sn} quality=${QUALITY}"
  curl -fsS \
    -X POST \
    -H 'Content-Type: application/json' \
    -d "${json_payload}" \
    "${API_BASE}/monitor/live/control/start"
  echo
done
