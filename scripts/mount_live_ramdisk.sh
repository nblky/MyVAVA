#!/bin/zsh
set -euo pipefail

SIZE_MB="${SIZE_MB:-512}"
MOUNT_NAME="${MOUNT_NAME:-VAVA_LIVE_RAM}"
TARGET_DIR="${TARGET_DIR:-/Volumes/${MOUNT_NAME}/live_hls}"

if ! [[ "${SIZE_MB}" =~ ^[0-9]+$ ]] || [[ "${SIZE_MB}" -le 0 ]]; then
  echo "SIZE_MB must be a positive integer" >&2
  exit 1
fi

if [[ -d "${TARGET_DIR}" ]]; then
  echo "ramdisk already mounted"
  echo "export VAVA_LIVE_HLS_ROOT=${TARGET_DIR}"
  exit 0
fi

sectors=$(( SIZE_MB * 2048 ))
device="$(hdiutil attach -nomount "ram://${sectors}" | tail -n 1 | tr -d '[:space:]')"

diskutil erasevolume HFS+ "${MOUNT_NAME}" "${device}" >/dev/null
mkdir -p "${TARGET_DIR}"

echo "mounted ${TARGET_DIR}"
echo "export VAVA_LIVE_HLS_ROOT=${TARGET_DIR}"
