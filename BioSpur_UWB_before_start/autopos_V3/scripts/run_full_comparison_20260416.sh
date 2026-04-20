#!/usr/bin/env bash
set -euo pipefail

# AutoPos 2026-04-16 full comparison runner:
# - OTA all anchors A-H to latest unified anchor build (over BLE, no SWD)
# - Flash nRF52840 master_control to latest build (SWD via JLinkExe, explicit SNR, no popup)
# - Run one 50-set A-H anchor sweep with Tag quieting enabled
# - Solve V1 / V2 / V3-lite (with soft constraints)
# - Solve V3-full (with Tag115 floating reference) and V3-full-no115
# - Produce layout quality report for all variants
#
# Output root:
#   autopos_V3/logs/AutoPos_20260416_full_comparison/<run_stamp>/

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00}"
MASTER_SNR="${MASTER_SNR:-683234364}"
TAG_NAME="${TAG_NAME:-BSF66F}"
QUIET_TAG_NAME="${QUIET_TAG_NAME:-BSF66F}"
ORDER="${ORDER:-ABCDEFGH}"

# 50 sets sweep as requested.
SW_SETS="${SW_SETS:-50}"

# Tag CM capture: keep >=100 lines by default (more helps floating-ref stability).
CM_LINES="${CM_LINES:-120}"
CM_TRAIN_LINES="${CM_TRAIN_LINES:-90}"
CM_TEST_LINES="${CM_TEST_LINES:-30}"

# Timeouts (leave generous headroom).
OTA_TIMEOUT_S="${OTA_TIMEOUT_S:-900}"
SWEEP_TIMEOUT_S="${SWEEP_TIMEOUT_S:-1800}"

# Build artifacts (picked from most recent known-good bundle in this workspace).
ANCHOR_UNIFIED_OTA_HEX="${ANCHOR_UNIFIED_OTA_HEX:-build-anchor-unified-ota-quality-startup/merged.hex}"
MASTER_ANCHOR_OTA_HEX="${MASTER_ANCHOR_OTA_HEX:-build-master-control-anchor-ota-quality-startup/merged.hex}"
MASTER_SWEEP_HEX="${MASTER_SWEEP_HEX:-build-master-control/merged.hex}"

OUT_ROOT="${OUT_ROOT:-autopos_V3/logs/AutoPos_20260416_full_comparison}"
STAMP="${STAMP:-$(date +%Y%m%d_%H%M%S)}"
RUN_DIR="${RUN_DIR:-${OUT_ROOT}/${STAMP}}"

mkdir -p "$RUN_DIR"
export RUN_DIR

echo "run_dir=${RUN_DIR}"
echo "port=${PORT}"
echo "order=${ORDER}"
echo "tag_name=${TAG_NAME} quiet_tag_name=${QUIET_TAG_NAME}"
echo "sw_sets=${SW_SETS} sweep_timeout_s=${SWEEP_TIMEOUT_S}"
echo "cm_lines=${CM_LINES} train=${CM_TRAIN_LINES} test=${CM_TEST_LINES}"
echo "anchor_unified_ota_hex=${ANCHOR_UNIFIED_OTA_HEX}"
echo "master_anchor_ota_hex=${MASTER_ANCHOR_OTA_HEX}"
echo "master_sweep_hex=${MASTER_SWEEP_HEX}"

# 1) Flash nRF52840 into a master_control build that is known-good for Anchor OTA mode.
echo "[step1] flash master_control for anchor OTA (snr=${MASTER_SNR})"
scripts/jlink_flash_hex_by_snr.sh "${MASTER_SNR}" nRF52840_xxAA "${MASTER_ANCHOR_OTA_HEX}" \
  |& tee "${RUN_DIR}/flash_master_anchor_ota.log"

# 2) OTA all anchors A-H to the unified anchor build.
echo "[step2] OTA deploy anchors A-H via BLE DFU (no SWD)"
python3 scripts/ota_deploy_anchor_set.py \
  --port "${PORT}" \
  --order "${ORDER}" \
  --timeout-s "${OTA_TIMEOUT_S}" \
  --out-dir "${RUN_DIR}/ota_anchors" \
  |& tee "${RUN_DIR}/ota_log.txt"

# 3) Flash nRF52840 into the regular master_control build used for sweep/AUTOPOS.
echo "[step3] flash master_control for sweep/AUTOPOS (snr=${MASTER_SNR})"
scripts/jlink_flash_hex_by_snr.sh "${MASTER_SNR}" nRF52840_xxAA "${MASTER_SWEEP_HEX}" \
  |& tee "${RUN_DIR}/flash_master_sweep.log"

# 4) Capture once: sweep 50 sets + Tag115 CM, then solve V1/V2/V3-lite.
echo "[step4] capture sweep+tagcm once and solve v1/v2/v3-lite"
python3 scripts/run_autopos_capture_once_and_solve_v1_v2_v3.py \
  --port "${PORT}" \
  --order "${ORDER}" \
  --sw-sets "${SW_SETS}" \
  --timeout-s "${SWEEP_TIMEOUT_S}" \
  --tag-name "${TAG_NAME}" \
  --quiet-tag-name "${QUIET_TAG_NAME}" \
  --cm-lines "${CM_LINES}" \
  --cm-train-lines "${CM_TRAIN_LINES}" \
  --cm-test-lines "${CM_TEST_LINES}" \
  --out-dir "${RUN_DIR}/capture_solve_v123" \
  |& tee "${RUN_DIR}/capture_solve_v123.log"

SOLVE_DIR="$(python3 - <<'PY'
import json
from pathlib import Path
import os
run_dir = Path(os.environ["RUN_DIR"]).resolve()
manifest = json.loads((run_dir / "capture_solve_v123" / "run_manifest.json").read_text(encoding="utf-8"))
print(manifest["solve_dir"])
PY
)"

PAIRS_CSV="${SOLVE_DIR}/pairs_all.csv"
FLOAT_TRAIN="${SOLVE_DIR}/floating_ref115_train"
FLOAT_HOLDOUT="${SOLVE_DIR}/floating_ref115_holdout"

# Keep a convenient copy of the raw directed samples (50 sets x 56 directed pairs, minus invalid 0/0 tuples).
cp -f "${PAIRS_CSV}" "${RUN_DIR}/sweep_50sets.csv"

# 5) Solve V3-full (with Tag115) and V3-full-no115.
echo "[step5] solve V3-full (with115) and V3-full-no115"
python3 scripts/prepare_autopos_v3_full.py \
  --pairs-csv "${PAIRS_CSV}" \
  --out-dir "${RUN_DIR}/v3_full_with115" \
  --floating-reference-session "${FLOAT_TRAIN}" \
  --floating-reference-z-prior-mm 820 \
  --bias-sigma-mm 200 \
  --sigma-dist-mm 80 \
  --sigma-ref-mm 150 \
  --max-iters 15 \
  --verbose 1 \
  |& tee "${RUN_DIR}/v3_full_with115.log"

python3 scripts/prepare_autopos_v3_full.py \
  --pairs-csv "${PAIRS_CSV}" \
  --out-dir "${RUN_DIR}/v3_full_no115" \
  --bias-sigma-mm 200 \
  --sigma-dist-mm 80 \
  --sigma-ref-mm 150 \
  --max-iters 15 \
  --verbose 1 \
  |& tee "${RUN_DIR}/v3_full_no115.log"

# 6) Layout quality summary (fit residuals vs measured distances, plus holdout floating-ref eval where available).
echo "[step6] generate layout quality summary"
python3 scripts/autopos_eval_layout_quality.py \
  --distances "${PAIRS_CSV}" \
  --layout "V1=${SOLVE_DIR}/v1/anchor_layout_v1_soft_iterative.json" \
  --layout "V2=${SOLVE_DIR}/v2/v2_fused/anchor_layout_v2_iterative.json" \
  --layout "V3_LITE=${SOLVE_DIR}/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json" \
  --layout "V3_FULL_WITH115=${RUN_DIR}/v3_full_with115/anchor_layout_v3_full.json" \
  --layout "V3_FULL_NO115=${RUN_DIR}/v3_full_no115/anchor_layout_v3_full.json" \
  --floating-ref-session "${FLOAT_HOLDOUT}" \
  --out-json "${RUN_DIR}/layout_quality_summary.json" \
  --out-md "${RUN_DIR}/layout_quality_summary.md" \
  |& tee "${RUN_DIR}/layout_quality_summary.log"

echo "[ok] done: ${RUN_DIR}"
