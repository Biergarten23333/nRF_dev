#!/usr/bin/env bash
set -euo pipefail

# Build an OTA-deliverable Tag115 calibration profile + matching master_ota image.
# This fills the BLE OTA calibration gap for the current workflow.
#
# Usage:
#   ./scripts/build_ref115_calibration_ota_profile.sh [tag_build_dir] [master_ota_build_dir]
#
# Environment overrides:
#   TAG_TOKEN_ID (default: 111)  # current token for device 760186115
#   TAG_LOGICAL_ID (default: 1)
#   TAG_DEVICE_NAME (default: BS_AUTO)
#   TAG_SIGN_VERSION (default: 0.0.1+115)

TAG_BUILD_DIR="${1:-build-tag-ota-ref115-calibration}"
MASTER_BUILD_DIR="${2:-build-master-ota-ref115-calibration}"

TAG_TOKEN_ID="${TAG_TOKEN_ID:-111}"
TAG_LOGICAL_ID="${TAG_LOGICAL_ID:-1}"
TAG_DEVICE_NAME="${TAG_DEVICE_NAME:-BS_AUTO}"
TAG_SIGN_VERSION="${TAG_SIGN_VERSION:-0.0.1+115}"

MASTER_CMAKE_ARGS_DEFAULT="-DAPP_MASTER_OTA_TARGET_TOKEN_ID=${TAG_TOKEN_ID} -DAPP_MASTER_OTA_TARGET_NAME_PREFIX=BS"
export MASTER_CMAKE_ARGS="${MASTER_CMAKE_ARGS:-$MASTER_CMAKE_ARGS_DEFAULT}"
export TAG_SIGN_VERSION

# Calibration profile: prioritize raw ranging capture quality over runtime tracking cadence.
export TAG_CMAKE_ARGS="${TAG_CMAKE_ARGS:-\
-DAPP_TAG_ID=${TAG_LOGICAL_ID} \
-DAPP_TAG_BLE_TOKEN_ID=${TAG_TOKEN_ID} \
-DAPP_TAG_BLE_ENABLE=1 \
-DCONFIG_BT_DEVICE_NAME=\"${TAG_DEVICE_NAME}\" \
-DAPP_TAG_BLE_OTA_ENABLE=1 \
-DAPP_TAG_BLE_SETTINGS_ENABLE=1 \
-DAPP_TAG_BLE_COMPACT_STATUS=0 \
-DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS=1 \
-DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=0 \
-DAPP_TAG_MCUBOOT_ENABLE=1 \
-DAPP_TAG_FW_MARKER=\"ref115-calibration-ota\" \
-DAPP_TAG_CALIBRATION_MODE=1 \
-DAPP_TAG_TDMA_ENABLE=0 \
-DAPP_TAG_FIXED_MODE=0 \
-DAPP_TAG_FAST_TRACKING=1 \
-DAPP_TAG_TRACK_ANCHOR_COUNT=8 \
-DAPP_TAG_FULL_SWEEP_INTERVAL=1 \
-DAPP_TAG_EKF_ENABLE=0 \
-DAPP_TAG_RANGE_CONTINUITY_ENABLE=0 \
-DAPP_TAG_RNG_DELAY_MS=60 \
-DAPP_TAG_VERBOSE_RANGING=1 \
-DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
-DAPP_TAG_VERBOSE_PERF=0 \
-DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20 \
-DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=140 \
-DAPP_TAG_RANGE_HARD_RESIDUAL_MM=260 \
-DAPP_TAG_USB_MIRROR_BLE_STATUS=0}"

echo "[ref115 calibration ota] token=${TAG_TOKEN_ID} logical_id=${TAG_LOGICAL_ID}"
echo "[ref115 calibration ota] tag_build_dir=${TAG_BUILD_DIR}"
echo "[ref115 calibration ota] master_ota_build_dir=${MASTER_BUILD_DIR}"

scripts/build_uwb_tag_ota_test.sh "$TAG_BUILD_DIR" "$MASTER_BUILD_DIR"

python3 scripts/write_build_source.py \
  --build-dir "$TAG_BUILD_DIR" \
  --source "scripts/build_ref115_calibration_ota_profile.sh" \
  --command "$0 $* (tag calibration ota profile)"

python3 scripts/write_build_source.py \
  --build-dir "$MASTER_BUILD_DIR" \
  --source "scripts/build_ref115_calibration_ota_profile.sh" \
  --command "$0 $* (master ota for ref115 calibration profile)"

echo
echo "Built Ref115 OTA calibration profile."
echo "Tag build dir:    ${TAG_BUILD_DIR}"
echo "Master OTA build: ${MASTER_BUILD_DIR}"
echo "Master OTA hex:   ${MASTER_BUILD_DIR}/zephyr/zephyr.hex"
