#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT}/.venv/bin/python"
HELPER_BIN="${ROOT}/scripts/run_ppcs_bridge.sh"
FFMPEG_BIN="${FFMPEG_BIN:-/opt/homebrew/bin/ffmpeg}"

cd "${ROOT}"

STATION_SN="${STATION_SN:-}"
CAMERA_SN="${CAMERA_SN:-}"
CHANNEL="${CHANNEL:-}"
QUALITY="${QUALITY:-}"
ENABLE_AUDIO="${ENABLE_AUDIO:-0}"
VIDEO_FPS="${VIDEO_FPS:-10}"
GOP_SECONDS="${GOP_SECONDS:-1}"
READ_TIMEOUT_MS="${READ_TIMEOUT_MS:-5000}"
CMD_DELAY_MS="${CMD_DELAY_MS:-500}"
HLS_TIME="${HLS_TIME:-1.0}"
HLS_LIST_SIZE="${HLS_LIST_SIZE:-5}"
HLS_DELETE_THRESHOLD="${HLS_DELETE_THRESHOLD:-3}"
VIDEO_CODEC_MODE="${VIDEO_CODEC_MODE:-auto}"
SKIP_P2P_SETUP="${SKIP_P2P_SETUP:-0}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "missing ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -x "${HELPER_BIN}" ]]; then
  echo "missing ${HELPER_BIN}" >&2
  exit 1
fi

if [[ ! -x "${FFMPEG_BIN}" ]]; then
  echo "missing ffmpeg at ${FFMPEG_BIN}" >&2
  exit 1
fi

eval "$("${PYTHON_BIN}" - <<'PY' "${STATION_SN}" "${CAMERA_SN}" "${CHANNEL}"
import shlex
import sys

from app.config import get_settings
from app.store import DEFAULT_CAMERA_SN, DEFAULT_STATION_SN, get_store


station_sn = (sys.argv[1] or "").strip()
camera_sn = (sys.argv[2] or "").strip()
channel_arg = (sys.argv[3] or "").strip()

store = get_store()
camera_index = store.camera_index_payload()
cameras = [item for item in camera_index.get("cameraList", []) if isinstance(item, dict)]
stations = [item for item in camera_index.get("stationList", []) if isinstance(item, dict)]

if not station_sn and camera_sn:
    for camera in cameras:
        if str(camera.get("cameraSn", "") or "").strip() == camera_sn:
            station_sn = str(camera.get("stationSn", "") or "").strip()
            break

if not station_sn:
    station_sn = str((stations[0].get("stationSn", "") if stations else DEFAULT_STATION_SN) or DEFAULT_STATION_SN).strip()

selected_camera = None
if camera_sn:
    for camera in cameras:
        if str(camera.get("cameraSn", "") or "").strip() == camera_sn:
            selected_camera = camera
            break

if selected_camera is None:
    for camera in cameras:
        if str(camera.get("stationSn", "") or "").strip() == station_sn:
            selected_camera = camera
            break

if selected_camera is None:
    selected_camera = {"cameraSn": DEFAULT_CAMERA_SN, "stationSn": station_sn, "channel": 0}

camera_sn = str(camera_sn or selected_camera.get("cameraSn", "") or DEFAULT_CAMERA_SN).strip()
channel = int(channel_arg) if channel_arg else int(selected_camera.get("channel", 0) or 0)
attr = selected_camera.get("attrObject", {}) if isinstance(selected_camera.get("attrObject"), dict) else {}
video_codec = int(attr.get("videocodec", 0) or 0)
video_input_format = "hevc" if video_codec == 1 else "h264"

did = store.station_did_payload(station_sn)
session_payload = store.session_key_payload(station_sn)
session_key = str((session_payload[0].get("sessionKey", "") if session_payload else "") or "")

values = {
    "LIVE_STATION_SN": station_sn,
    "LIVE_CAMERA_SN": camera_sn,
    "LIVE_CHANNEL": channel,
    "LIVE_DID_CODE": str(did.get("didCode", "") or ""),
    "LIVE_INIT_CODE": str(did.get("initCode", did.get("initString", "")) or ""),
    "LIVE_SESSION_KEY": session_key,
    "LIVE_VIDEO_CODEC": video_codec,
    "LIVE_VIDEO_INPUT_FORMAT": video_input_format,
    "LIVE_HLS_ROOT": str(get_settings().live_hls_root),
}

for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"

WORK_DIR="${LIVE_HLS_ROOT}/${LIVE_CAMERA_SN}"
HLS_DIR="${WORK_DIR}/hls"
VIDEO_FIFO="${WORK_DIR}/video.h264"
AUDIO_FIFO="${WORK_DIR}/audio.aac"
BRIDGE_LOG="${WORK_DIR}/bridge.log"
FFMPEG_LOG="${WORK_DIR}/ffmpeg.log"
BRIDGE_PID_FILE="${WORK_DIR}/bridge.pid"
FFMPEG_PID_FILE="${WORK_DIR}/ffmpeg.pid"

mkdir -p "${WORK_DIR}"

if [[ "${VIDEO_CODEC_MODE}" == "auto" ]]; then
  if [[ "${LIVE_VIDEO_CODEC:-0}" == "1" ]]; then
    VIDEO_CODEC_MODE="vt_h264"
  else
    VIDEO_CODEC_MODE="copy"
  fi
fi

CAMERA_SN="${LIVE_CAMERA_SN}" "${ROOT}/scripts/stop_live_hls.sh" >/dev/null 2>&1 || true

stop_existing() {
  local pid_file pid
  for pid_file in "${BRIDGE_PID_FILE}" "${FFMPEG_PID_FILE}"; do
    if [[ -f "${pid_file}" ]]; then
      pid="$(<"${pid_file}")"
      if [[ -n "${pid}" ]]; then
        kill "${pid}" 2>/dev/null || true
      fi
      rm -f "${pid_file}"
    fi
  done
}

cleanup() {
  stop_existing
  exec 3>&- 2>/dev/null || true
  exec 4>&- 2>/dev/null || true
  rm -f "${VIDEO_FIFO}" "${AUDIO_FIFO}"
}

trap cleanup EXIT INT TERM

stop_existing
rm -rf "${HLS_DIR}"
mkdir -p "${HLS_DIR}"
rm -f "${VIDEO_FIFO}" "${AUDIO_FIFO}"
mkfifo "${VIDEO_FIFO}"
exec 3<>"${VIDEO_FIFO}"

if [[ "${ENABLE_AUDIO}" == "1" ]]; then
  mkfifo "${AUDIO_FIFO}"
  exec 4<>"${AUDIO_FIFO}"
fi

ffmpeg_cmd=(
  "${FFMPEG_BIN}"
  -hide_banner
  -loglevel warning
  -fflags +genpts+discardcorrupt+nobuffer
  -flags low_delay
  -thread_queue_size 128
  -probesize 8192
  -analyzeduration 0
  -fpsprobesize 0
  -use_wallclock_as_timestamps 1
  -f "${LIVE_VIDEO_INPUT_FORMAT}"
  -r "${VIDEO_FPS}"
  -i "${VIDEO_FIFO}"
)

if [[ "${ENABLE_AUDIO}" == "1" ]]; then
  ffmpeg_cmd+=(
    -thread_queue_size 128
    -probesize 8192
    -analyzeduration 0
    -use_wallclock_as_timestamps 1
    -f aac
    -i "${AUDIO_FIFO}"
  )
fi

ffmpeg_cmd+=(
  -map 0:v:0
)

if [[ "${VIDEO_CODEC_MODE}" == "copy" ]]; then
  ffmpeg_cmd+=(
    -c:v copy
  )
else
  ffmpeg_cmd+=(
    -c:v h264_videotoolbox
    -allow_sw 1
    -realtime true
    -bf 0
    -g "$(( VIDEO_FPS * GOP_SECONDS ))"
    -profile:v baseline
    -pix_fmt nv12
  )
fi

if [[ "${ENABLE_AUDIO}" == "1" ]]; then
  ffmpeg_cmd+=(
    -map 1:a:0
    -c:a copy
  )
fi

hls_flags="delete_segments+append_list+omit_endlist+program_date_time"
if [[ "${VIDEO_CODEC_MODE}" == "copy" ]]; then
  hls_flags="${hls_flags}+split_by_time"
else
  hls_flags="${hls_flags}+independent_segments"
fi

ffmpeg_cmd+=(
  -max_interleave_delta 0
  -muxpreload 0
  -muxdelay 0
  -flush_packets 1
  -f hls
  -hls_time "${HLS_TIME}"
  -hls_list_size "${HLS_LIST_SIZE}"
  -hls_delete_threshold "${HLS_DELETE_THRESHOLD}"
  -hls_allow_cache 0
  -hls_flags "${hls_flags}"
  -hls_segment_filename "${HLS_DIR}/seg_%06d.ts"
  "${HLS_DIR}/index.m3u8"
)

"${ffmpeg_cmd[@]}" >"${FFMPEG_LOG}" 2>&1 &
FFMPEG_PID=$!
echo "${FFMPEG_PID}" > "${FFMPEG_PID_FILE}"

sleep 1

helper_cmd=(
  "${HELPER_BIN}"
  --target-id "${LIVE_DID_CODE}"
  --init-string "${LIVE_INIT_CODE}"
  --auth-session-key "${LIVE_SESSION_KEY}"
  --cmd-delay-ms "${CMD_DELAY_MS}"
  --read-av-count -1
  --read-timeout-ms "${READ_TIMEOUT_MS}"
  --video-out "${VIDEO_FIFO}"
)

if [[ "${SKIP_P2P_SETUP}" != "1" ]]; then
  helper_cmd+=(--cmd "1:{\"channel\":${LIVE_CHANNEL}}")
  helper_cmd+=(--cmd "3:{\"channel\":${LIVE_CHANNEL}}")
fi

if [[ -n "${QUALITY}" && "${SKIP_P2P_SETUP}" != "1" ]]; then
  helper_cmd+=(--cmd "202:{\"channel\":${LIVE_CHANNEL},\"quality\":${QUALITY}}")
fi

if [[ "${ENABLE_AUDIO}" == "1" ]]; then
  helper_cmd+=(--cmd "5:{\"channel\":${LIVE_CHANNEL}}")
  helper_cmd+=(--audio-out "${AUDIO_FIFO}")
fi

"${helper_cmd[@]}" >"${BRIDGE_LOG}" 2>&1 &
BRIDGE_PID=$!
echo "${BRIDGE_PID}" > "${BRIDGE_PID_FILE}"

echo "station=${LIVE_STATION_SN}"
echo "camera=${LIVE_CAMERA_SN}"
echo "channel=${LIVE_CHANNEL}"
echo "video_codec_mode=${VIDEO_CODEC_MODE}"
echo "video_input_format=${LIVE_VIDEO_INPUT_FORMAT}"
echo "monitor_url=http://127.0.0.1:18079/monitor"
echo "live_url=http://127.0.0.1:18079/monitor/live/${LIVE_CAMERA_SN}/index.m3u8"
echo "bridge_log=${BRIDGE_LOG}"
echo "ffmpeg_log=${FFMPEG_LOG}"

wait "${BRIDGE_PID}" "${FFMPEG_PID}"
