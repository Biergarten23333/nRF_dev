#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-build-tag-ota-ref115-calibration-cm-streamoff}"

cd "${REPO_ROOT}"

west build -b decawave_dwm1001_dev -s apps/tag -d "${BUILD_DIR}" --sysbuild --pristine=always -- \
  -DAPP_TAG_ID=1 \
  -DAPP_TAG_BLE_TOKEN_ID=111 \
  -DCONFIG_BT_DEVICE_NAME=\"BSF66F\" \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DAPP_TAG_BLE_OTA_ENABLE=1 \
  -DAPP_TAG_BLE_SETTINGS_ENABLE=1 \
  -DAPP_TAG_MCUBOOT_ENABLE=1 \
  -DAPP_TAG_CALIBRATION_MODE=1 \
  -DAPP_TAG_TDMA_ENABLE=0 \
  -DAPP_TAG_FIXED_MODE=0 \
  -DAPP_TAG_FAST_TRACKING=1 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT=8 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=1 \
  -DAPP_TAG_VERBOSE_RANGING=1 \
  -DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
  -DAPP_TAG_VERBOSE_PERF=0 \
  -DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20 \
  -DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=140 \
  -DAPP_TAG_RANGE_HARD_RESIDUAL_MM=260 \
  -DAPP_TAG_STREAM_FORCE_OFF_AT_BOOT=1 \
  -DAPP_TAG_FW_MARKER=\"ref115-calibration-cm-streamoff\" \
  -DCONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION=\"0.0.1+115cmstreamoff\"

echo "[ok] build dir: ${REPO_ROOT}/${BUILD_DIR}"
echo "[ok] hex: ${REPO_ROOT}/${BUILD_DIR}/merged.hex"
