#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00}"
SW_SETS="${SW_SETS:-100}"
CM_LINES="${CM_LINES:-100}"
TAG_NAME="${TAG_NAME:-BSF66F}"
ORDER="${ORDER:-ABCDEFGH}"
TIMEOUT_S="${TIMEOUT_S:-4800}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

STAMP="$(date +%Y%m%d_%H%M%S)"
DRIVER_DIR="logs/autopos_v123_overnight_${STAMP}"
mkdir -p "${DRIVER_DIR}"

echo "PORT=${PORT}" | tee "${DRIVER_DIR}/env.txt"
echo "SW_SETS=${SW_SETS}" | tee -a "${DRIVER_DIR}/env.txt"
echo "CM_LINES=${CM_LINES}" | tee -a "${DRIVER_DIR}/env.txt"
echo "TAG_NAME=${TAG_NAME}" | tee -a "${DRIVER_DIR}/env.txt"
echo "ORDER=${ORDER}" | tee -a "${DRIVER_DIR}/env.txt"
echo "TIMEOUT_S=${TIMEOUT_S}" | tee -a "${DRIVER_DIR}/env.txt"

V1_DIR="autopos_V1/logs/v1_run_${STAMP}"
V2_DIR="autopos_V2/logs/v2_run_${STAMP}"
V3_DIR="autopos_V3/logs/v3_run_${STAMP}"
mkdir -p "${V1_DIR}" "${V2_DIR}" "${V3_DIR}"

echo "[step] V1 capture+solve -> ${V1_DIR}" | tee "${DRIVER_DIR}/driver.log"
python3 scripts/run_autopos_vx_capture_and_solve.py \
  --version v1 \
  --port "${PORT}" \
  --order "${ORDER}" \
  --sw-sets "${SW_SETS}" \
  --timeout-s "${TIMEOUT_S}" \
  --tag-name "${TAG_NAME}" \
  --cm-lines "${CM_LINES}" \
  --out-dir "${V1_DIR}" |& stdbuf -oL -eL tee -a "${DRIVER_DIR}/driver.log"

echo "[step] V2 capture+solve -> ${V2_DIR}" | tee -a "${DRIVER_DIR}/driver.log"
python3 scripts/run_autopos_vx_capture_and_solve.py \
  --version v2 \
  --port "${PORT}" \
  --order "${ORDER}" \
  --sw-sets "${SW_SETS}" \
  --timeout-s "${TIMEOUT_S}" \
  --tag-name "${TAG_NAME}" \
  --cm-lines "${CM_LINES}" \
  --out-dir "${V2_DIR}" |& stdbuf -oL -eL tee -a "${DRIVER_DIR}/driver.log"

echo "[step] V3-lite capture+solve -> ${V3_DIR}" | tee -a "${DRIVER_DIR}/driver.log"
python3 scripts/run_autopos_vx_capture_and_solve.py \
  --version v3 \
  --port "${PORT}" \
  --order "${ORDER}" \
  --sw-sets "${SW_SETS}" \
  --timeout-s "${TIMEOUT_S}" \
  --tag-name "${TAG_NAME}" \
  --cm-lines "${CM_LINES}" \
  --out-dir "${V3_DIR}" |& stdbuf -oL -eL tee -a "${DRIVER_DIR}/driver.log"

REPORT="CODEx_AUTOPOS_V1_V2_V3_REPORT_${STAMP}.md"
python3 scripts/autopos_generate_v1_v2_v3_report.py \
  --v1-manifest "${V1_DIR}/latest_run_manifest.json" \
  --v2-manifest "${V2_DIR}/latest_run_manifest.json" \
  --v3-manifest "${V3_DIR}/latest_run_manifest.json" \
  --out "${REPORT}" |& stdbuf -oL -eL tee -a "${DRIVER_DIR}/driver.log"

echo "[ok] report: ${REPORT}" | tee -a "${DRIVER_DIR}/driver.log"
echo "[ok] driver_dir: ${DRIVER_DIR}" | tee -a "${DRIVER_DIR}/driver.log"
