#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${ROOT}/bin/ppcs_bridge_x86_64"

exec arch -x86_64 "${BIN}" "$@"
