#!/usr/bin/env bash
set -euo pipefail

# Build an OTA-capable unified anchor image and a matching nRF52840 control build
# that embeds this anchor image as OTA payload.
#
# Usage:
#   scripts/build_anchor_ota_control_bundle.sh [anchor_build_dir] [control_build_dir] [fw_marker]
#
# Example:
#   scripts/build_anchor_ota_control_bundle.sh \
#     build-anchor-unified-ota-v1 \
#     build-master-control-anchor-ota-v1 \
#     anchor-ota-v1

ANCHOR_BUILD_DIR="${1:-build-anchor-unified-ota}"
CONTROL_BUILD_DIR="${2:-build-master-control-anchor-ota}"
FW_MARKER="${3:-anchor-ota-v1}"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"

echo "[anchor ota bundle] anchor_build_dir=${ANCHOR_BUILD_DIR}"
echo "[anchor ota bundle] control_build_dir=${CONTROL_BUILD_DIR}"
echo "[anchor ota bundle] fw_marker=${FW_MARKER}"

export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$NCS_ROOT/nrf}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(west list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/anchor \
  -d "${ANCHOR_BUILD_DIR}" \
  --sysbuild \
  --pristine=always \
  -- \
  -DSB_CONFIG_BOOTLOADER_MCUBOOT=y \
  "-DCONF_FILE=prj.conf;prj_ota.conf" \
  "-DAPP_ANCHOR_FW_MARKER=${FW_MARKER}"

SIGNED_BIN="${ANCHOR_BUILD_DIR}/anchor/zephyr/zephyr.signed.bin"
if [[ ! -f "${SIGNED_BIN}" ]]; then
  SIGNED_BIN="${ANCHOR_BUILD_DIR}/zephyr/zephyr.signed.bin"
fi
if [[ ! -f "${SIGNED_BIN}" ]]; then
  echo "Missing signed image: ${SIGNED_BIN}" >&2
  exit 1
fi

python3 scripts/gen_ota_image_inc.py \
  "${SIGNED_BIN}" \
  apps/master_ota/generated/ota_image.inc

west build \
  -b nrf52840dk/nrf52840 \
  -s apps/master_control \
  -d "${CONTROL_BUILD_DIR}" \
  --pristine=always \
  -- \
  -DAPP_MASTER_OTA_TARGET_NAME="" \
  -DAPP_MASTER_OTA_TARGET_NAME_PREFIX="BS" \
  -DAPP_MASTER_OTA_TARGET_TOKEN_ID=-1

python3 scripts/write_build_source.py \
  --build-dir "${ANCHOR_BUILD_DIR}" \
  --source "scripts/build_anchor_ota_control_bundle.sh" \
  --command "$0 $* (anchor ota image)"

python3 scripts/write_build_source.py \
  --build-dir "${CONTROL_BUILD_DIR}" \
  --source "scripts/build_anchor_ota_control_bundle.sh" \
  --command "$0 $* (52840 control center)"

echo
echo "Built anchor OTA image: ${ANCHOR_BUILD_DIR}"
echo "  ${ANCHOR_BUILD_DIR}/merged.hex"
echo "  ${SIGNED_BIN}"
echo "Built 52840 control image: ${CONTROL_BUILD_DIR}"
echo "  ${CONTROL_BUILD_DIR}/zephyr/zephyr.hex"
