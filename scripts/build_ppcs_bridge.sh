#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PPCS_ROOT="${PPCS_ROOT:-/Users/lky-mm/Downloads/vava came pro/PeerToPeer-master}"
INCLUDE_DIR="${PPCS_ROOT}/Include/PPCS"
LIB_DIR="${PPCS_ROOT}/Lib/osX/x64"
OUT_DIR="${ROOT}/bin"
SRC="${ROOT}/native/ppcs_bridge.cpp"
OUT="${OUT_DIR}/ppcs_bridge_x86_64"
LIB_DST="${OUT_DIR}/libPPCS_API.dylib"

mkdir -p "${OUT_DIR}"

clang++ \
  -std=c++17 \
  -O2 \
  -Wall \
  -Wextra \
  -arch x86_64 \
  -DLINUX \
  -I "${INCLUDE_DIR}" \
  "${SRC}" \
  -L "${LIB_DIR}" \
  -lPPCS_API \
  -lpthread \
  -o "${OUT}"

cp "${LIB_DIR}/libPPCS_API.dylib" "${LIB_DST}"
install_name_tool -id "@executable_path/libPPCS_API.dylib" "${LIB_DST}"
install_name_tool -change "libPPCS_API.dylib" "@executable_path/libPPCS_API.dylib" "${OUT}"

echo "built ${OUT}"
