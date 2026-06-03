#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-tag-b70-full-cir-usb}"

if [ -z "${ZEPHYR_BASE:-}" ]; then
  export ZEPHYR_BASE="/home/zekaixiao/ncs/v2.8.0/zephyr"
fi
if [ -z "${WEST_TOPDIR:-}" ]; then
  export WEST_TOPDIR="/home/zekaixiao/ncs/v2.8.0"
fi
if [ -z "${ZEPHYR_NRF_MODULE_DIR:-}" ]; then
  export ZEPHYR_NRF_MODULE_DIR="$(dirname "${ZEPHYR_BASE}")/nrf"
fi
export KCONFIG_ALLOW_WARNINGS=1
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:${PYTHONPATH}}"

fw_marker="${APP_TAG_FW_MARKER:-b70-full-cir-usb-a8-20260602}"
resp_spacing_us="${APP_ALT_SS_TWR_RESP_SPACING_US:-1000}"
guard_us="${APP_ALT_SS_TWR_GUARD_US:-2500}"
chunk_bytes="${APP_TAG_CIR_FULL_CHUNK_BYTES:-48}"
priority_mask="${APP_TAG_CIR_FULL_PRIORITY_MASK:-0xA0}"
priority_only_sweep="${APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP:-1}"
app_tag_preload_file="$(mktemp /tmp/app_tag_preload_full_cir_usb.XXXXXX.cmake)"
cleanup() {
  rm -f "${app_tag_preload_file}"
}
trap cleanup EXIT

{
  printf 'set(APP_TAG_FW_MARKER "%s" CACHE STRING "Full CIR USB preload" FORCE)\n' "${fw_marker}"
  printf 'set(APP_TAG_BLE_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_MCUBOOT_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_TDMA_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_FAST_TRACKING 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_FULL_SWEEP_INTERVAL 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_TRACK_ANCHOR_COUNT 8 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_MULTITAG_PLAN_MODE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_NORMAL_OUTPUT_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CONSOLE_SUMMARY_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_BCAST_SUMMARY_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_VERBOSE_PERF 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_PENDING_PRINT_PERIOD 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_IMU_SAMPLE_PERIOD 255 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_CHUNK_BYTES %s CACHE STRING "Full CIR USB preload" FORCE)\n' "${chunk_bytes}"
  printf 'set(APP_TAG_CIR_FULL_PRIORITY_MASK %s CACHE STRING "Full CIR USB preload" FORCE)\n' "${priority_mask}"
  printf 'set(APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP %s CACHE STRING "Full CIR USB preload" FORCE)\n' "${priority_only_sweep}"
  printf 'set(APP_ALT_SS_TWR_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_MODE 2 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE 1 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_RESP_SPACING_US %s CACHE STRING "Full CIR USB preload" FORCE)\n' "${resp_spacing_us}"
  printf 'set(APP_ALT_SS_TWR_GUARD_US %s CACHE STRING "Full CIR USB preload" FORCE)\n' "${guard_us}"
  printf 'set(APP_TAG_RESP_RX_TIMEOUT_UUS 3000 CACHE STRING "Full CIR USB preload" FORCE)\n'
  printf 'set(APP_TAG_USB_DIAG_TRACE 0 CACHE STRING "Full CIR USB preload" FORCE)\n'
} > "${app_tag_preload_file}"

export APP_TAG_PRELOAD_FILE="${app_tag_preload_file}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag_usb \
  -d "${build_dir}" \
  --pristine=always \
  -- \
  -DSB_CONFIG_COMPILER_WARNINGS_AS_ERRORS=n \
  ${TAG_CMAKE_ARGS:-}

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_b70_full_cir_usb.sh" \
  --command "$0 $*"

echo
echo "Built:  ${build_dir}"
echo "Hex:    ${build_dir}/merged.hex"
echo "Marker: ${fw_marker}"
echo "Mode:   USB/J-Link VCOM full CIR, forced broadcast sweep"
echo "Priority mask: ${priority_mask}"
echo "Priority-only sweep: ${priority_only_sweep}"
