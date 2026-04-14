#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-}"
if [[ -z "${PORT}" ]]; then
  echo "[error] set PORT=/dev/serial/by-id/..." >&2
  exit 2
fi

OUT_DIR="${OUT_DIR:-autopos_V1/logs/v1_run_$(date +%Y%m%d_%H%M%S)}"
SW_SETS="${SW_SETS:-100}"
CM_LINES="${CM_LINES:-100}"
TAG_NAME="${TAG_NAME:-BSF66F}"
ORDER="${ORDER:-ABCDEFGH}"
TIMEOUT_S="${TIMEOUT_S:-1800}"

cd "$(dirname "$0")/../.."

python3 scripts/run_autopos_vx_capture_and_solve.py \
  --version v1 \
  --port "${PORT}" \
  --order "${ORDER}" \
  --sw-sets "${SW_SETS}" \
  --timeout-s "${TIMEOUT_S}" \
  --tag-name "${TAG_NAME}" \
  --cm-lines "${CM_LINES}" \
  --out-dir "${OUT_DIR}"

echo "[ok] V1 run done: ${OUT_DIR}"

