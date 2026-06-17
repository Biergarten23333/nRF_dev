#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-tag-ota-b70-full-cir-a8-static}"

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

fw_marker="${APP_TAG_FW_MARKER:-b70-ota-full-cir-a8-static-20260607}"
resp_spacing_us="${APP_ALT_SS_TWR_RESP_SPACING_US:-1000}"
guard_us="${APP_ALT_SS_TWR_GUARD_US:-2500}"
chunk_bytes="${APP_TAG_CIR_FULL_CHUNK_BYTES:-48}"
priority_mask="${APP_TAG_CIR_FULL_PRIORITY_MASK:-0xFF}"
heap_mem_pool_size="${APP_TAG_FULL_CIR_HEAP_MEM_POOL_SIZE:-4096}"

app_tag_preload_file="$(mktemp /tmp/app_tag_preload_ota_full_cir.XXXXXX.cmake)"
cleanup() {
  rm -f "${app_tag_preload_file}"
}
trap cleanup EXIT

{
  printf 'set(APP_TAG_FW_MARKER "%s" CACHE STRING "OTA full CIR preload" FORCE)\n' "${fw_marker}"
  printf 'set(APP_TAG_BLE_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_BLE_OTA_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_MCUBOOT_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_UWB_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_TDMA_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_FAST_TRACKING 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_FULL_SWEEP_INTERVAL 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_TRACK_ANCHOR_COUNT 8 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_MULTITAG_PLAN_MODE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_NORMAL_OUTPUT_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CONSOLE_SUMMARY_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_BCAST_SUMMARY_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_VERBOSE_PERF 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_PENDING_PRINT_PERIOD 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_IMU_SAMPLE_PERIOD 255 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_CIR_FULL_CHUNK_BYTES %s CACHE STRING "OTA full CIR preload" FORCE)\n' "${chunk_bytes}"
  printf 'set(APP_TAG_CIR_FULL_PRIORITY_MASK %s CACHE STRING "OTA full CIR preload" FORCE)\n' "${priority_mask}"
  printf 'set(APP_ALT_SS_TWR_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_MODE 2 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE 1 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_ALT_SS_TWR_RESP_SPACING_US %s CACHE STRING "OTA full CIR preload" FORCE)\n' "${resp_spacing_us}"
  printf 'set(APP_ALT_SS_TWR_GUARD_US %s CACHE STRING "OTA full CIR preload" FORCE)\n' "${guard_us}"
  printf 'set(APP_TAG_RESP_RX_TIMEOUT_UUS 3000 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_BLE_TOKEN_ID 115 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_BLE_PACKET_BUNDLE_RECORDS 2 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_UWB_CHANNEL 5 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_UWB_PAN_ID 0xDECA CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_ID 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
  printf 'set(APP_TAG_USB_DIAG_TRACE 0 CACHE STRING "OTA full CIR preload" FORCE)\n'
} > "${app_tag_preload_file}"

export APP_TAG_PRELOAD_FILE="${app_tag_preload_file}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag \
  -d "${build_dir}" \
  --sysbuild \
  --pristine=always \
  -- \
  -DSB_CONFIG_PARTITION_MANAGER=y \
  -DSB_CONFIG_COMPILER_WARNINGS_AS_ERRORS=n \
  -DCONFIG_HEAP_MEM_POOL_SIZE="${heap_mem_pool_size}" \
  ${TAG_CMAKE_ARGS:-}

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_b70_ota_full_cir.sh" \
  --command "$0 $*"

echo
echo "Built:  ${build_dir}"
echo "Signed: ${build_dir}/tag/zephyr/zephyr.signed.bin"
echo "DFU:    ${build_dir}/dfu_application.zip"
echo "Marker: ${fw_marker}"
echo "Mode:   OTA-capable tag, BLE/MCUboot enabled, full CIR over CDC"
echo "Heap:   CONFIG_HEAP_MEM_POOL_SIZE=${heap_mem_pool_size}"
