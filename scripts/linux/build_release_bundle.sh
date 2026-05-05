#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${VAVA_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VERSION="${1:-$(date +%Y%m%d_%H%M%S)}"
OUT_DIR="${2:-$ROOT_DIR/release}"
PACKAGE_NAME="vava-server-${VERSION}"
STAGE_BASE="$(mktemp -d "/tmp/${PACKAGE_NAME}.XXXX")"
STAGE_DIR="${STAGE_BASE}/${PACKAGE_NAME}"

cleanup() {
  rm -rf "$STAGE_BASE"
}
trap cleanup EXIT

mkdir -p "$STAGE_DIR"
mkdir -p "$OUT_DIR"

if command -v rsync >/dev/null 2>&1; then
  RSYNC_BIN="rsync"
else
  echo "rsync is required to build the release bundle." >&2
  exit 1
fi

INCLUDE_PATHS=(
  ".env.example"
  "README.md"
  "requirements.txt"
  "mediamtx-vava.yml"
  "LIVE_DELIVERY_NOTES.md"
  "VAVA_APP_DOMAIN_LIST.md"
  "app"
  "bin"
  "deploy"
  "docs"
  "fake_cloud_app"
  "legacy"
  "native"
  "scripts"
)

RSYNC_EXCLUDES=(
  "--exclude=.DS_Store"
  "--exclude=*.pyc"
  "--exclude=__pycache__/"
  "--exclude=.venv/"
  "--exclude=data/"
  "--exclude=logs/"
  "--exclude=run/"
  "--exclude=release/"
  "--exclude=legacy/cert_backups/"
  "--exclude=legacy/sunvalley_local_root_ca.key"
  "--exclude=legacy/*.srl"
  "--exclude=fake_cloud_app/.venv/"
)

for path in "${INCLUDE_PATHS[@]}"; do
  if [[ ! -e "${ROOT_DIR}/${path}" ]]; then
    echo "Skip missing path: ${path}" >&2
    continue
  fi
  "$RSYNC_BIN" -a "${RSYNC_EXCLUDES[@]}" "${ROOT_DIR}/${path}" "${STAGE_DIR}/"
done

# Prepare empty runtime directories; runtime state should not be shipped.
mkdir -p "${STAGE_DIR}/data" "${STAGE_DIR}/logs" "${STAGE_DIR}/run"
touch "${STAGE_DIR}/data/.keep" "${STAGE_DIR}/logs/.keep" "${STAGE_DIR}/run/.keep"

MANIFEST_PATH="${STAGE_DIR}/RELEASE_MANIFEST.txt"
{
  echo "Package: ${PACKAGE_NAME}"
  echo "BuiltAt: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "RootDir: ${ROOT_DIR}"
  echo
  echo "[Included paths]"
  for path in "${INCLUDE_PATHS[@]}"; do
    echo "- ${path}"
  done
  echo
  echo "[Excluded runtime/private paths]"
  echo "- .venv/"
  echo "- data/*"
  echo "- logs/*"
  echo "- run/*"
  echo "- legacy/cert_backups/"
  echo "- legacy/sunvalley_local_root_ca.key"
  echo "- legacy/*.srl"
  echo "- __pycache__/"
  echo "- *.pyc"
  echo "- .DS_Store"
} > "${MANIFEST_PATH}"

ARCHIVE_PATH="${OUT_DIR}/${PACKAGE_NAME}.tar.gz"
(
  cd "$STAGE_BASE"
  tar -czf "$ARCHIVE_PATH" "${PACKAGE_NAME}"
)

CHECKSUM_PATH="${ARCHIVE_PATH}.sha256"
if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$OUT_DIR"
    sha256sum "$(basename "$ARCHIVE_PATH")" > "$(basename "$CHECKSUM_PATH")"
  )
elif command -v shasum >/dev/null 2>&1; then
  (
    cd "$OUT_DIR"
    shasum -a 256 "$(basename "$ARCHIVE_PATH")" > "$(basename "$CHECKSUM_PATH")"
  )
else
  echo "No sha256 tool found; skip checksum generation." >&2
  CHECKSUM_PATH=""
fi

echo "Release bundle created:"
echo "  - ${ARCHIVE_PATH}"
if [[ -n "${CHECKSUM_PATH}" ]]; then
  echo "  - ${CHECKSUM_PATH}"
fi
