#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT}/.venv/bin/python"
CAMERA_SN="${CAMERA_SN:-}"
LIVE_HLS_ROOT=""

eval "$("${PYTHON_BIN}" - <<'PY' "${CAMERA_SN}"
import shlex
import sys

from app.config import get_settings
from app.store import DEFAULT_CAMERA_SN


camera_sn = (sys.argv[1] or "").strip() or DEFAULT_CAMERA_SN
print(f"CAMERA_SN={shlex.quote(camera_sn)}")
print(f"LIVE_HLS_ROOT={shlex.quote(str(get_settings().live_hls_root))}")
PY
)"

WORK_DIR="${LIVE_HLS_ROOT}/${CAMERA_SN}"
HLS_DIR="${WORK_DIR}/hls"

kill_matching() {
  local pattern="$1"
  local pids=()
  local parents=()
  local pid=""
  while IFS= read -r pid; do
    [[ -z "${pid}" ]] && continue
    [[ "${pid}" == "$$" ]] && continue
    pids+=("${pid}")
    parent="$(ps -p "${pid}" -o ppid= 2>/dev/null | tr -d ' ' || true)"
    [[ -n "${parent}" && "${parent}" != "1" && "${parent}" != "$$" ]] && parents+=("${parent}")
  done < <(pgrep -f "${pattern}" 2>/dev/null || true)

  for pid in "${pids[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  for pid in "${parents[@]}"; do
    kill "${pid}" 2>/dev/null || true
  done
  sleep 1
  for pid in "${pids[@]}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  for pid in "${parents[@]}"; do
    kill -9 "${pid}" 2>/dev/null || true
  done
}

for pid_file in "${WORK_DIR}/bridge.pid" "${WORK_DIR}/ffmpeg.pid"; do
  if [[ -f "${pid_file}" ]]; then
    pid="$(<"${pid_file}")"
    if [[ -n "${pid}" ]]; then
      kill "${pid}" 2>/dev/null || true
    fi
    rm -f "${pid_file}"
  fi
done

kill_matching "${WORK_DIR}/video.h264"
kill_matching "${WORK_DIR}/audio.aac"
kill_matching "${HLS_DIR}/index.m3u8"

rm -f "${WORK_DIR}/video.h264" "${WORK_DIR}/audio.aac"
rm -rf "${HLS_DIR}"
echo "stopped ${CAMERA_SN}"
