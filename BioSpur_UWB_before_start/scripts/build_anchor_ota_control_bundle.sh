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
FW_MARKER_INPUT="${3:-}"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
ANCHOR_EXTRA_CMAKE_ARGS="${ANCHOR_EXTRA_CMAKE_ARGS:-}"

if [[ -n "${FW_MARKER_INPUT}" ]]; then
  FW_MARKER="${FW_MARKER_INPUT}"
else
  GIT_SHA="$(git -C /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start rev-parse --short=7 HEAD 2>/dev/null || echo nogit)"
  FW_MARKER="anchor-$(date +%Y%m%d)-${GIT_SHA}"
fi

echo "[anchor ota bundle] anchor_build_dir=${ANCHOR_BUILD_DIR}"
echo "[anchor ota bundle] control_build_dir=${CONTROL_BUILD_DIR}"
echo "[anchor ota bundle] fw_marker=${FW_MARKER}"

export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$NCS_ROOT/nrf}"
WEST_BIN="${WEST_BIN:-west}"
WEST_TOPDIR="${WEST_TOPDIR:-$NCS_ROOT}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(cd "$WEST_TOPDIR" && "$WEST_BIN" list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"
# Avoid /usr/local site-packages here because this machine has an incompatible
# `enum34` package there, which breaks both west and the NCS toolchain Python.
HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"

(cd "$WEST_TOPDIR" && "$WEST_BIN" build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/anchor" \
  -d "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/${ANCHOR_BUILD_DIR}" \
  --sysbuild \
  --cmake-only \
  --pristine=always \
  -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DCONFIG_BUILD_OUTPUT_META=n \
  -Dmcuboot_CONFIG_BUILD_OUTPUT_META=n \
  -DSB_CONFIG_BOOTLOADER_MCUBOOT=y \
  "-DCONF_FILE=prj.conf;prj_ota.conf" \
  -DAPP_ANCHOR_SCHEDULE_MODE=2 \
  "-DAPP_ANCHOR_FW_MARKER=${FW_MARKER}" \
  ${ANCHOR_EXTRA_CMAKE_ARGS})

(cd "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start" && \
  env \
  WEST_TOPDIR="${WEST_TOPDIR}" \
  ZEPHYR_BASE="${NCS_ROOT}/zephyr" \
  PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}" \
  /usr/bin/cmake --build "${ANCHOR_BUILD_DIR}")

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

(cd "$WEST_TOPDIR" && "$WEST_BIN" build \
  -b nrf52840dk/nrf52840 \
  -s "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/master_control" \
  -d "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/${CONTROL_BUILD_DIR}" \
  --cmake-only \
  --pristine=always \
  -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DCONFIG_BUILD_OUTPUT_META=n \
  -DAPP_MASTER_OTA_TARGET_NAME="" \
  -DAPP_MASTER_OTA_TARGET_NAME_PREFIX="BS" \
  -DAPP_MASTER_OTA_TARGET_TOKEN_ID=-1)

(cd "/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start" && \
  env \
  WEST_TOPDIR="${WEST_TOPDIR}" \
  ZEPHYR_BASE="${NCS_ROOT}/zephyr" \
  PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}" \
  /usr/bin/cmake --build "${CONTROL_BUILD_DIR}")

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
