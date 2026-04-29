#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 3 ]; then
  echo "Usage: $0 [tdma_slot_index=0] [tdma_slot_count=10] [build_dir]" >&2
  exit 1
fi

slot_index="${1:-0}"
slot_count="${2:-10}"
build_dir="${3:-build-tag-ble-motion-unified}"
device_name="${TAG_DEVICE_NAME:-BS_AUTO}"
fw_marker="${APP_TAG_FW_MARKER:-unified-default}"
uwb_channel="${APP_UWB_CHANNEL:-5}"
uwb_pan_id="${APP_UWB_PAN_ID:-0xDECA}"
alt_ss_twr_enable="${APP_ALT_SS_TWR_ENABLE:-0}"
alt_ss_twr_mode="${APP_ALT_SS_TWR_MODE:-2}"
alt_ss_twr_poll_spacing_us="${APP_ALT_SS_TWR_POLL_SPACING_US:-200}"
alt_ss_twr_guard_us="${APP_ALT_SS_TWR_GUARD_US:-500}"
alt_ss_twr_resp_spacing_us="${APP_ALT_SS_TWR_RESP_SPACING_US:-800}"
multitag_plan_mode="${APP_TAG_MULTITAG_PLAN_MODE:-0}"
maintenance_full_interval="${APP_TAG_MAINTENANCE_FULL_INTERVAL:-100}"
range_filter_outlier_mm="${APP_TAG_RANGE_FILTER_OUTLIER_MM:-450}"
range_continuity_enable="${APP_TAG_RANGE_CONTINUITY_ENABLE:-1}"
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
  printf 'set(APP_TAG_MULTITAG_PLAN_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "${multitag_plan_mode}"
  printf 'set(APP_TAG_MAINTENANCE_FULL_INTERVAL %s CACHE STRING "Motion tag preload" FORCE)\n' "${maintenance_full_interval}"
  printf 'set(APP_TAG_RANGE_FILTER_OUTLIER_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${range_filter_outlier_mm}"
  printf 'set(APP_TAG_RANGE_CONTINUITY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${range_continuity_enable}"
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
  printf 'set(APP_TAG_FW_MARKER %s CACHE STRING "Motion tag preload" FORCE)\n' "${fw_marker}"
  printf 'set(APP_ALT_SS_TWR_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_enable}"
  printf 'set(APP_ALT_SS_TWR_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_mode}"
  printf 'set(APP_ALT_SS_TWR_POLL_SPACING_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_poll_spacing_us}"
  printf 'set(APP_ALT_SS_TWR_GUARD_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_guard_us}"
  printf 'set(APP_ALT_SS_TWR_RESP_SPACING_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_resp_spacing_us}"
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
  -DAPP_TAG_FW_MARKER="${fw_marker}" \
  -DAPP_ALT_SS_TWR_ENABLE="${alt_ss_twr_enable}" \
  -DAPP_ALT_SS_TWR_MODE="${alt_ss_twr_mode}" \
  -DAPP_ALT_SS_TWR_POLL_SPACING_US="${alt_ss_twr_poll_spacing_us}" \
  -DAPP_ALT_SS_TWR_GUARD_US="${alt_ss_twr_guard_us}" \
  -DAPP_ALT_SS_TWR_RESP_SPACING_US="${alt_ss_twr_resp_spacing_us}" \
  -DAPP_TAG_USB_DIAG_TRACE=1 \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DCONFIG_BT_DEVICE_NAME=\"${device_name}\" \
  -DAPP_TAG_BLE_OTA_ENABLE=1 \
  -DAPP_TAG_BLE_SETTINGS_ENABLE=1 \
  -DAPP_TAG_BLE_COMPACT_STATUS=1 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS=3 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=250 \
  -DAPP_TAG_MCUBOOT_ENABLE=1 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="${slot_index}" \
  -DAPP_TAG_TDMA_SLOT_COUNT="${slot_count}" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS=25 \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS=20 \
  -DAPP_TAG_MULTITAG_PLAN_MODE="${multitag_plan_mode}" \
  -DAPP_TAG_MAINTENANCE_FULL_INTERVAL="${maintenance_full_interval}" \
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
  -DAPP_TAG_TRACK_ANCHOR_COUNT=6 \
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
  -DAPP_TAG_RANGE_FILTER_OUTLIER_MM="${range_filter_outlier_mm}" \
  -DAPP_TAG_RANGE_CONTINUITY_ENABLE="${range_continuity_enable}" \
  -DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20 \
  -DAPP_TAG_MOTION_FULL_SWEEP_INTERVAL=0 \
  -DAPP_TAG_MOTION_SPEED_THRESHOLD_MM_S=100 \
  -DAPP_TAG_MOTION_RANGE_SOFT_BONUS_MM=140 \
  -DAPP_TAG_MOTION_RANGE_HARD_BONUS_MM=260 \
  -DAPP_TAG_MOTION_EKF_MEAS_STD_MM=35 \
  -DAPP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2=1800 \
  -DAPP_TAG_MOTION_EKF_OUTLIER_GATE_MM=220 \
  -DAPP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG=250 \
  -DAPP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG=140 \
  ${TAG_CMAKE_ARGS:-}

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_ble_motion.sh" \
  --command "$0 $*"

echo
echo "Built: ${build_dir}"
echo "Hex:   ${build_dir}/merged.hex"
echo "Name:  ${device_name}"
echo "Tag preload file: ${app_tag_preload_file}"
