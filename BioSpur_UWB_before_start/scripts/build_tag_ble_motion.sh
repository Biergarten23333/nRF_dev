#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <tag_id_label> [tdma_slot_index=0] [tdma_slot_count=10] [build_dir]" >&2
  exit 1
fi

tag_id="$1"
slot_index="${2:-0}"
slot_count="${3:-10}"
build_dir="${4:-build-tag-ble-motion-tag${tag_id}-${slot_index}}"
device_name="${TAG_DEVICE_NAME:-BS_AUTO}"
uwb_channel="${APP_UWB_CHANNEL:-5}"
uwb_pan_id="${APP_UWB_PAN_ID:-0xDECA}"
if [ -z "${ZEPHYR_NRF_MODULE_DIR:-}" ]; then
  export ZEPHYR_NRF_MODULE_DIR="$(dirname "${ZEPHYR_BASE}")/nrf"
fi
app_tag_preload_file="$(mktemp /tmp/app_tag_preload_motion.XXXXXX.cmake)"
cleanup() {
  rm -f "${app_tag_preload_file}"
}
trap cleanup EXIT

if [ "${slot_index}" = "auto" ] || [ -z "${slot_index}" ]; then
  slot_index="0"
fi

{
  printf 'set(APP_TAG_TDMA_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "1"
  printf 'set(APP_TAG_TDMA_SLOT_INDEX %s CACHE STRING "Motion tag preload" FORCE)\n' "${slot_index}"
  printf 'set(APP_TAG_TDMA_SLOT_COUNT %s CACHE STRING "Motion tag preload" FORCE)\n' "${slot_count}"
  printf 'set(APP_TAG_TDMA_SLOT_PERIOD_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "25"
  printf 'set(APP_TAG_TDMA_SLOT_ACTIVE_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "20"
  printf 'set(APP_TAG_MULTITAG_PLAN_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "1"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "0"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "1"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_2_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "4"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_3_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "5"
  printf 'set(APP_TAG_STANDBY_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "2"
  printf 'set(APP_TAG_STANDBY_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "6"
  printf 'set(APP_TAG_RESERVE_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "3"
  printf 'set(APP_TAG_RESERVE_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "7"
  printf 'set(APP_UWB_CHANNEL %s CACHE STRING "Motion tag preload" FORCE)\n' "${uwb_channel}"
  printf 'set(APP_UWB_PAN_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${uwb_pan_id}"
} > "${app_tag_preload_file}"

export APP_TAG_PRELOAD_FILE="${app_tag_preload_file}"
export KCONFIG_ALLOW_WARNINGS=1
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:${PYTHONPATH}}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag \
  -d "${build_dir}" \
  --pristine=always \
  -- \
  -DSB_CONFIG_PARTITION_MANAGER=y \
  -DSB_CONFIG_COMPILER_WARNINGS_AS_ERRORS=n \
  -DAPP_UWB_CHANNEL="${uwb_channel}" \
  -DAPP_UWB_PAN_ID="${uwb_pan_id}" \
  -DAPP_TAG_USB_DIAG_TRACE=1 \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DCONFIG_BT_DEVICE_NAME=\"${device_name}\" \
  -DAPP_TAG_BLE_OTA_ENABLE=1 \
  -DAPP_TAG_BLE_SETTINGS_ENABLE=0 \
  -DAPP_TAG_BLE_COMPACT_STATUS=1 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS=3 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=250 \
  -DAPP_TAG_MCUBOOT_ENABLE=1 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="${slot_index}" \
  -DAPP_TAG_TDMA_SLOT_COUNT="${slot_count}" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS=25 \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS=20 \
  -DAPP_TAG_MULTITAG_PLAN_MODE=1 \
  -DAPP_TAG_ACTIVE_ANCHOR_0_ID=0 \
  -DAPP_TAG_ACTIVE_ANCHOR_1_ID=1 \
  -DAPP_TAG_ACTIVE_ANCHOR_2_ID=4 \
  -DAPP_TAG_ACTIVE_ANCHOR_3_ID=5 \
  -DAPP_TAG_STANDBY_ANCHOR_0_ID=2 \
  -DAPP_TAG_STANDBY_ANCHOR_1_ID=6 \
  -DAPP_TAG_RESERVE_ANCHOR_0_ID=3 \
  -DAPP_TAG_RESERVE_ANCHOR_1_ID=7 \
  -DAPP_TAG_FAST_TRACKING=1 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=8 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT=4 \
  -DAPP_TAG_SUMMARY_PERIOD=1 \
  -DAPP_TAG_PENDING_PRINT_PERIOD=1 \
  -DAPP_TAG_IMU_SAMPLE_PERIOD=2 \
  -DAPP_TAG_VERBOSE_RANGING=0 \
  -DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
  -DAPP_TAG_EKF_ENABLE=1 \
  -DAPP_TAG_EKF_MEAS_STD_MM=35 \
  -DAPP_TAG_EKF_RESIDUAL_GAIN_PCT=0 \
  -DAPP_TAG_EKF_PROC_ACCEL_MM_S2=500 \
  -DAPP_TAG_EKF_INIT_POS_STD_MM=200 \
  -DAPP_TAG_EKF_INIT_VEL_STD_MM_S=1200 \
  -DAPP_TAG_EKF_OUTLIER_GATE_MM=120 \
  -DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=180 \
  -DAPP_TAG_RANGE_HARD_RESIDUAL_MM=350 \
  -DAPP_TAG_MOTION_FULL_SWEEP_INTERVAL=0 \
  -DAPP_TAG_MOTION_SPEED_THRESHOLD_MM_S=100 \
  -DAPP_TAG_MOTION_RANGE_SOFT_BONUS_MM=140 \
  -DAPP_TAG_MOTION_RANGE_HARD_BONUS_MM=260 \
  -DAPP_TAG_MOTION_EKF_MEAS_STD_MM=35 \
  -DAPP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2=1800 \
  -DAPP_TAG_MOTION_EKF_OUTLIER_GATE_MM=220 \
  -DAPP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG=250 \
  -DAPP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG=140

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_ble_motion.sh" \
  --command "$0 $*"

echo
echo "Built: ${build_dir}"
echo "Hex:   ${build_dir}/merged.hex"
echo "Name:  ${device_name}"
echo "Tag preload file: ${app_tag_preload_file}"
