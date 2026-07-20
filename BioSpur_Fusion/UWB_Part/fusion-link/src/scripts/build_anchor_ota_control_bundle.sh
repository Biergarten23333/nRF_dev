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
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANCHOR_BUILD_PATH="${ANCHOR_BUILD_DIR}"
CONTROL_BUILD_PATH="${CONTROL_BUILD_DIR}"
if [[ "${ANCHOR_BUILD_PATH}" != /* ]]; then
  ANCHOR_BUILD_PATH="${REPO_ROOT}/${ANCHOR_BUILD_PATH}"
fi
if [[ "${CONTROL_BUILD_PATH}" != /* ]]; then
  CONTROL_BUILD_PATH="${REPO_ROOT}/${CONTROL_BUILD_PATH}"
fi

# Sysbuild does not automatically forward arbitrary top-level -DAPP_ANCHOR_*
# cache entries into the child "anchor" image. The anchor app CMake reads these
# values from the environment, so mirror explicit extra args there as well.
for _arg in ${ANCHOR_EXTRA_CMAKE_ARGS}; do
  case "${_arg}" in
    -DAPP_ANCHOR_*=*|-DAPP_UWB_*=*|-DAPP_ALT_*=*)
      export "${_arg#-D}"
      ;;
  esac
done
unset _arg

if [[ -n "${FW_MARKER_INPUT}" ]]; then
  FW_MARKER="${FW_MARKER_INPUT}"
else
  GIT_SHA="$(git -C "${REPO_ROOT}" rev-parse --short=7 HEAD 2>/dev/null || echo nogit)"
  FW_MARKER="anchor-$(date +%Y%m%d)-${GIT_SHA}"
fi

echo "[anchor ota bundle] anchor_build_dir=${ANCHOR_BUILD_DIR}"
echo "[anchor ota bundle] control_build_dir=${CONTROL_BUILD_DIR}"
echo "[anchor ota bundle] fw_marker=${FW_MARKER}"

export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$NCS_ROOT/nrf}"
export APP_ANCHOR_FW_MARKER="${FW_MARKER}"
WEST_BIN="${WEST_BIN:-west}"
WEST_TOPDIR="${WEST_TOPDIR:-$NCS_ROOT}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(cd "$WEST_TOPDIR" && "$WEST_BIN" list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"
# Avoid /usr/local site-packages here because this machine has an incompatible
# `enum34` package there, which breaks both west and the NCS toolchain Python.
HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
CMAKE_BIN="${CMAKE_BIN:-cmake}"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"

(cd "$WEST_TOPDIR" && "$WEST_BIN" build \
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
  "-DAPP_ANCHOR_FW_MARKER=${FW_MARKER}" \
  ${ANCHOR_EXTRA_CMAKE_ARGS})

(cd "${REPO_ROOT}" && \
  env \
  WEST_TOPDIR="${WEST_TOPDIR}" \
  ZEPHYR_BASE="${NCS_ROOT}/zephyr" \
  PYTHONPATH="${PYTHONPATH}" \
  "${CMAKE_BIN}" --build "${ANCHOR_BUILD_DIR}")

SIGNED_BIN="${ANCHOR_BUILD_PATH}/anchor/zephyr/zephyr.signed.bin"
if [[ ! -f "${SIGNED_BIN}" ]]; then
  SIGNED_BIN="${ANCHOR_BUILD_PATH}/zephyr/zephyr.signed.bin"
fi
if [[ ! -f "${SIGNED_BIN}" ]]; then
  echo "Missing signed image: ${SIGNED_BIN}" >&2
  exit 1
fi

printf '%s\n' "${FW_MARKER}" > "${ANCHOR_BUILD_PATH}/fw_marker.txt"
python3 "${REPO_ROOT}/scripts/prepare_alt_ota_payload.py" \
  --kind anchor \
  --marker "${FW_MARKER}" \
  --build-dir "${ANCHOR_BUILD_PATH}" \
  --signed-bin "${SIGNED_BIN}" \
  --dfu-zip "${ANCHOR_BUILD_PATH}/dfu_application.zip" \
  --control-build-dir "${CONTROL_BUILD_PATH}"

cp "${REPO_ROOT}/apps/master_ota/generated/anchor_ota_manifest.json" \
  "${ANCHOR_BUILD_PATH}/build_manifest.json"

python3 "${REPO_ROOT}/scripts/verify_ota_payload_kind.py" --expected anchor

(cd "$WEST_TOPDIR" && "$WEST_BIN" build \
  -b nrf52840dk/nrf52840 \
  -s "${REPO_ROOT}/apps/master_control" \
  -d "${REPO_ROOT}/${CONTROL_BUILD_DIR}" \
  --cmake-only \
  --pristine=always \
  -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DCONFIG_BUILD_OUTPUT_META=n \
  -DAPP_MASTER_OTA_TARGET_NAME="" \
  -DAPP_MASTER_OTA_TARGET_NAME_PREFIX="BS" \
  -DAPP_MASTER_OTA_TARGET_TOKEN_ID=-1)

(cd "${REPO_ROOT}" && \
  env \
  WEST_TOPDIR="${WEST_TOPDIR}" \
  ZEPHYR_BASE="${NCS_ROOT}/zephyr" \
  PYTHONPATH="${PYTHONPATH}" \
  "${CMAKE_BIN}" --build "${CONTROL_BUILD_DIR}")

python3 "${REPO_ROOT}/scripts/write_build_source.py" \
  --build-dir "${ANCHOR_BUILD_PATH}" \
  --source "scripts/build_anchor_ota_control_bundle.sh" \
  --command "$0 $* (anchor ota image)"

python3 "${REPO_ROOT}/scripts/write_build_source.py" \
  --build-dir "${CONTROL_BUILD_PATH}" \
  --source "scripts/build_anchor_ota_control_bundle.sh" \
  --command "$0 $* (52840 control center)"

echo
echo "Built anchor OTA image: ${ANCHOR_BUILD_DIR}"
echo "  ${ANCHOR_BUILD_DIR}/merged.hex"
echo "  ${SIGNED_BIN}"
echo "Built 52840 control image: ${CONTROL_BUILD_DIR}"
echo "  ${CONTROL_BUILD_DIR}/zephyr/zephyr.hex"
