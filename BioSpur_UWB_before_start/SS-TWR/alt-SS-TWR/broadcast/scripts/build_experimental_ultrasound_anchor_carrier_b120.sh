#!/usr/bin/env bash
set -euo pipefail

# Build plan for "方案 C":
#   1. Keep the currently generated/frozen Master_Anchor OTA payload metadata intact.
#   2. Build an experimental unified Anchor OTA image with HC-SR04 commands enabled.
#   3. Temporarily install that image as the active anchor OTA payload.
#   4. Build an experimental B120 Master_Anchor carrier image that embeds the payload.
#   5. Restore the generated/frozen payload metadata in the source tree.
#
# This script builds artifacts only. It does not flash anything.

NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="${1:-$(date +%Y%m%d_%H%M%S)}"
TAIL_COMPRESS_ENABLE="${APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE:-1}"
TAIL_START_RANK="${APP_ALT_SS_TWR_TAIL_START_RANK:-5}"
TAIL_RESP_SPACING_US="${APP_ALT_SS_TWR_TAIL_RESP_SPACING_US:-800}"

ANCHOR_MARKER="us-hc-${STAMP}"
ANCHOR_BUILD_DIR="build-anchor-unified-ota-${ANCHOR_MARKER}"
MASTER_BUILD_DIR="build-master-control-b120-${ANCHOR_MARKER}"
GENERATED_DIR="${REPO_ROOT}/apps/master_ota/generated"
BACKUP_DIR="${REPO_ROOT}/build-generated-backup-${ANCHOR_MARKER}"

HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(cd "$NCS_ROOT" && "$WEST_BIN" list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"

# The anchor app is a sysbuild child image. Its CMake reads APP_* values from
# the environment, so keep these exports in sync with the -D arguments below.
# Baseline mirrors the frozen 2026-05-12 alt-broadcast responder image:
#   ALT=1 BCAST=1 GUARD=1200 RESP_SPACING=1000 RESP_DELAY=1200
# This experimental build keeps the frozen guard/base spacing and optionally
# compresses only tail ranks. Override APP_ALT_SS_TWR_TAIL_* per experiment.
export APP_ANCHOR_FW_MARKER="${ANCHOR_MARKER}"
export APP_ANCHOR_SCHEDULE_MODE=2
export APP_ANCHOR_ALLOW_TAG_POLLS=1
export APP_ANCHOR_RESP_DELAY_UUS=1200
export APP_ALT_SS_TWR_ENABLE=1
export APP_ALT_SS_TWR_BCAST_ENABLE=1
export APP_ALT_SS_TWR_GUARD_US=1200
export APP_ALT_SS_TWR_RESP_SPACING_US=1000
export APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE="${TAIL_COMPRESS_ENABLE}"
export APP_ALT_SS_TWR_TAIL_START_RANK="${TAIL_START_RANK}"
export APP_ALT_SS_TWR_TAIL_RESP_SPACING_US="${TAIL_RESP_SPACING_US}"
export APP_ANCHOR_ULTRASOUND_ENABLE=1
export APP_ANCHOR_ULTRASOUND_TRIG_PIN=6
export APP_ANCHOR_ULTRASOUND_ECHO_PIN=7
export APP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS=100
export APP_ANCHOR_ULTRASOUND_MAX_SAMPLES=300

echo "[ultrasound carrier] repo=${REPO_ROOT}"
echo "[ultrasound carrier] marker=${ANCHOR_MARKER}"
echo "[ultrasound carrier] anchor_build=${ANCHOR_BUILD_DIR}"
echo "[ultrasound carrier] master_build=${MASTER_BUILD_DIR}"
echo "[ultrasound carrier] tail_compress=${APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE} start_rank=${APP_ALT_SS_TWR_TAIL_START_RANK} spacing_us=${APP_ALT_SS_TWR_TAIL_RESP_SPACING_US}"

rm -rf "${BACKUP_DIR}"
mkdir -p "${BACKUP_DIR}"
if [[ -d "${GENERATED_DIR}" ]]; then
  cp -a "${GENERATED_DIR}/." "${BACKUP_DIR}/"
fi

restore_generated() {
  rm -rf "${GENERATED_DIR}"
  mkdir -p "${GENERATED_DIR}"
  if [[ -d "${BACKUP_DIR}" ]]; then
    cp -a "${BACKUP_DIR}/." "${GENERATED_DIR}/"
  fi
}
trap restore_generated EXIT

(cd "$NCS_ROOT" && "$WEST_BIN" build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s "${REPO_ROOT}/apps/anchor" \
  -d "${REPO_ROOT}/${ANCHOR_BUILD_DIR}" \
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
  -DAPP_ANCHOR_ALLOW_TAG_POLLS=1 \
  -DAPP_ANCHOR_RESP_DELAY_UUS=1200 \
  -DAPP_ALT_SS_TWR_ENABLE=1 \
  -DAPP_ALT_SS_TWR_BCAST_ENABLE=1 \
  -DAPP_ALT_SS_TWR_GUARD_US=1200 \
  -DAPP_ALT_SS_TWR_RESP_SPACING_US=1000 \
  -DAPP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE="${APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE}" \
  -DAPP_ALT_SS_TWR_TAIL_START_RANK="${APP_ALT_SS_TWR_TAIL_START_RANK}" \
  -DAPP_ALT_SS_TWR_TAIL_RESP_SPACING_US="${APP_ALT_SS_TWR_TAIL_RESP_SPACING_US}" \
  "-DAPP_ANCHOR_FW_MARKER=${ANCHOR_MARKER}" \
  -DAPP_ANCHOR_ULTRASOUND_ENABLE=1 \
  -DAPP_ANCHOR_ULTRASOUND_TRIG_PIN=6 \
  -DAPP_ANCHOR_ULTRASOUND_ECHO_PIN=7 \
  -DAPP_ANCHOR_ULTRASOUND_SAMPLE_PERIOD_MS=100 \
  -DAPP_ANCHOR_ULTRASOUND_MAX_SAMPLES=300)

(cd "${REPO_ROOT}" && \
  env WEST_TOPDIR="${NCS_ROOT}" ZEPHYR_BASE="${NCS_ROOT}/zephyr" \
  "${CMAKE_BIN}" --build "${ANCHOR_BUILD_DIR}")

SIGNED_BIN="${REPO_ROOT}/${ANCHOR_BUILD_DIR}/anchor/zephyr/zephyr.signed.bin"
DFU_ZIP="${REPO_ROOT}/${ANCHOR_BUILD_DIR}/dfu_application.zip"
if [[ ! -f "${SIGNED_BIN}" ]]; then
  echo "Missing signed image: ${SIGNED_BIN}" >&2
  exit 1
fi
if [[ ! -f "${DFU_ZIP}" ]]; then
  echo "Missing DFU zip: ${DFU_ZIP}" >&2
  exit 1
fi

(cd "${REPO_ROOT}" && python3 scripts/prepare_alt_ota_payload.py \
  --kind anchor \
  --marker "${ANCHOR_MARKER}" \
  --build-dir "${ANCHOR_BUILD_DIR}" \
  --signed-bin "${SIGNED_BIN}" \
  --dfu-zip "${DFU_ZIP}" \
  --control-build-dir "${MASTER_BUILD_DIR}")

(cd "${REPO_ROOT}" && python3 scripts/assert_active_ota_payload.py --expected anchor)

(cd "${REPO_ROOT}" && scripts/build_master_control_b120_m1.sh "${MASTER_BUILD_DIR}")
(cd "${REPO_ROOT}" && scripts/assert_b120_internal_osc_build.sh "${MASTER_BUILD_DIR}")

cp "${GENERATED_DIR}/active_ota_payload.json" "${REPO_ROOT}/${MASTER_BUILD_DIR}/active_ota_payload.ultrasound.json"

echo
echo "[ultrasound carrier] built experimental artifacts:"
echo "  anchor OTA build: ${REPO_ROOT}/${ANCHOR_BUILD_DIR}"
echo "  anchor signed bin: ${SIGNED_BIN}"
echo "  anchor DFU zip: ${DFU_ZIP}"
echo "  experimental Master_Anchor carrier: ${REPO_ROOT}/${MASTER_BUILD_DIR}/zephyr/merged_domains.hex"
echo "  payload manifest copy: ${REPO_ROOT}/${MASTER_BUILD_DIR}/active_ota_payload.ultrasound.json"
echo
echo "[ultrasound carrier] generated payload metadata will now be restored to the pre-build frozen state."
echo "[ultrasound carrier] This script did not flash any device."
