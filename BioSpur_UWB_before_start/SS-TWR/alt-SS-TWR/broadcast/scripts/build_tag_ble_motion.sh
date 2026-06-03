#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 3 ]; then
  echo "Usage: $0 [tdma_slot_index=0] [tdma_slot_count=10] [build_dir]" >&2
  exit 1
fi

slot_index="${1:-0}"
slot_count="${2:-10}"
build_dir="${3:-build-tag-ble-motion-unified}"
slot_period_ms="${APP_TAG_TDMA_SLOT_PERIOD_MS:-25}"
slot_active_ms="${APP_TAG_TDMA_SLOT_ACTIVE_MS:-20}"
slot_active_us="${APP_TAG_TDMA_SLOT_ACTIVE_US:-0}"
device_name="${TAG_DEVICE_NAME:-BS_AUTO}"
ble_name_prefix="${APP_TAG_BLE_NAME_PREFIX:-}"
wand_mode_enable="${APP_TAG_WAND_MODE_ENABLE:-0}"
fw_marker="${APP_TAG_FW_MARKER:-unified-default}"
uwb_channel="${APP_UWB_CHANNEL:-5}"
uwb_pan_id="${APP_UWB_PAN_ID:-0xDECA}"
alt_ss_twr_enable="${APP_ALT_SS_TWR_ENABLE:-0}"
alt_ss_twr_bcast_enable="${APP_ALT_SS_TWR_BCAST_ENABLE:-0}"
alt_ss_twr_mode="${APP_ALT_SS_TWR_MODE:-2}"
alt_ss_twr_poll_spacing_us="${APP_ALT_SS_TWR_POLL_SPACING_US:-200}"
alt_ss_twr_guard_us="${APP_ALT_SS_TWR_GUARD_US:-500}"
alt_ss_twr_resp_spacing_us="${APP_ALT_SS_TWR_RESP_SPACING_US:-800}"
alt_ss_twr_bcast_force_full_sweep="${APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP:-0}"
alt_ss_twr_light_tdma_enable="${APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE:-0}"
alt_ss_twr_bcast_immediate_tx_enable="${APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE:-0}"
alt_ss_twr_bcast_prewrite_tx_enable="${APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE:-0}"
multitag_plan_mode="${APP_TAG_MULTITAG_PLAN_MODE:-0}"
maintenance_full_interval="${APP_TAG_MAINTENANCE_FULL_INTERVAL:-100}"
range_filter_outlier_mm="${APP_TAG_RANGE_FILTER_OUTLIER_MM:-120000}"
range_continuity_enable="${APP_TAG_RANGE_CONTINUITY_ENABLE:-0}"
output_filter_rms_mm="${APP_TAG_OUTPUT_FILTER_RMS_MM:-0}"
output_filter_speed_mm_s="${APP_TAG_OUTPUT_FILTER_SPEED_MM_S:-0}"
position_output_enable="${APP_TAG_POSITION_OUTPUT_ENABLE:-0}"
tr_bcast_v2_enable="${APP_TAG_TR_BCAST_V2_ENABLE:-0}"
bcast_summary_enable="${APP_TAG_BCAST_SUMMARY_ENABLE:-1}"
bcast_summary_period_ms="${APP_TAG_BCAST_SUMMARY_PERIOD_MS:-1000}"
tag_loc_fast_all_valid_enable="${APP_TAG_LOC_FAST_ALL_VALID_ENABLE:-0}"
sweep_diag_enable="${APP_TAG_SWEEP_DIAG_ENABLE:-0}"
sweep_diag_period="${APP_TAG_SWEEP_DIAG_PERIOD:-10}"
tag_console_summary_enable="${APP_TAG_CONSOLE_SUMMARY_ENABLE:-1}"
tag_verbose_perf="${APP_TAG_VERBOSE_PERF:-1}"
tag_pending_print_period="${APP_TAG_PENDING_PRINT_PERIOD:-1}"
tag_ble_packet_bundle_records="${APP_TAG_BLE_PACKET_BUNDLE_RECORDS:-3}"
tag_ble_packet_bundle_flush_ms="${APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS:-250}"
tag_ble_tx_item_count="${APP_TAG_BLE_TX_ITEM_COUNT:-10}"
tag_alt_rxg_ble_diag_enable="${APP_TAG_ALT_RXG_BLE_DIAG_ENABLE:-1}"
tag_imu_sample_period="${APP_TAG_IMU_SAMPLE_PERIOD:-2}"
tag_track_anchor_count="${APP_TAG_TRACK_ANCHOR_COUNT:-6}"
tag_tr_imu_summary_enable="${APP_TAG_TR_IMU_SUMMARY_ENABLE:-0}"
tag_tr_imu_raw_enable="${APP_TAG_TR_IMU_RAW_ENABLE:-0}"
tag_tr_imu_summary_window="${APP_TAG_TR_IMU_SUMMARY_WINDOW:-5}"
tag_normal_output_enable="${APP_TAG_NORMAL_OUTPUT_ENABLE:-1}"
tag_cir_feature_output_enable="${APP_TAG_CIR_FEATURE_OUTPUT_ENABLE:-0}"
tag_cir_feature_output_ble_enable="${APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE:-1}"
tag_ekf_enable="${APP_TAG_EKF_ENABLE:-1}"
tag_loc_min_quality_percent="${APP_TAG_LOC_MIN_QUALITY_PERCENT:-20}"
tag_motion_speed_threshold_mm_s="${APP_TAG_MOTION_SPEED_THRESHOLD_MM_S:-100}"
tag_motion_range_soft_bonus_mm="${APP_TAG_MOTION_RANGE_SOFT_BONUS_MM:-140}"
tag_motion_range_hard_bonus_mm="${APP_TAG_MOTION_RANGE_HARD_BONUS_MM:-260}"
tag_motion_ekf_meas_std_mm="${APP_TAG_MOTION_EKF_MEAS_STD_MM:-35}"
tag_motion_ekf_proc_accel_mm_s2="${APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2:-1800}"
tag_motion_ekf_outlier_gate_mm="${APP_TAG_MOTION_EKF_OUTLIER_GATE_MM:-220}"
tag_motion_imu_delta_threshold_mg="${APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG:-250}"
tag_motion_imu_gravity_err_threshold_mg="${APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG:-140}"
tag_usb_diag_trace="${APP_TAG_USB_DIAG_TRACE:-1}"
active_anchor_0_id="${APP_TAG_ACTIVE_ANCHOR_0_ID:-0}"
active_anchor_1_id="${APP_TAG_ACTIVE_ANCHOR_1_ID:-1}"
active_anchor_2_id="${APP_TAG_ACTIVE_ANCHOR_2_ID:-4}"
active_anchor_3_id="${APP_TAG_ACTIVE_ANCHOR_3_ID:-5}"
standby_anchor_0_id="${APP_TAG_STANDBY_ANCHOR_0_ID:-2}"
standby_anchor_1_id="${APP_TAG_STANDBY_ANCHOR_1_ID:-6}"
reserve_anchor_0_id="${APP_TAG_RESERVE_ANCHOR_0_ID:-3}"
reserve_anchor_1_id="${APP_TAG_RESERVE_ANCHOR_1_ID:-7}"
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
  printf 'set(APP_TAG_TDMA_SLOT_PERIOD_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${slot_period_ms}"
  printf 'set(APP_TAG_TDMA_SLOT_ACTIVE_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${slot_active_ms}"
  printf 'set(APP_TAG_TDMA_SLOT_ACTIVE_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${slot_active_us}"
  printf 'set(APP_TAG_MULTITAG_PLAN_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "${multitag_plan_mode}"
  printf 'set(APP_TAG_MAINTENANCE_FULL_INTERVAL %s CACHE STRING "Motion tag preload" FORCE)\n' "${maintenance_full_interval}"
  printf 'set(APP_TAG_RANGE_FILTER_OUTLIER_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${range_filter_outlier_mm}"
  printf 'set(APP_TAG_RANGE_CONTINUITY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${range_continuity_enable}"
  printf 'set(APP_TAG_OUTPUT_FILTER_RMS_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${output_filter_rms_mm}"
  printf 'set(APP_TAG_OUTPUT_FILTER_SPEED_MM_S %s CACHE STRING "Motion tag preload" FORCE)\n' "${output_filter_speed_mm_s}"
  printf 'set(APP_TAG_POSITION_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${position_output_enable}"
  printf 'set(APP_TAG_TR_BCAST_V2_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tr_bcast_v2_enable}"
  printf 'set(APP_TAG_BCAST_SUMMARY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${bcast_summary_enable}"
  printf 'set(APP_TAG_BCAST_SUMMARY_PERIOD_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${bcast_summary_period_ms}"
  printf 'set(APP_TAG_LOC_FAST_ALL_VALID_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_loc_fast_all_valid_enable}"
  printf 'set(APP_TAG_SWEEP_DIAG_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${sweep_diag_enable}"
  printf 'set(APP_TAG_SWEEP_DIAG_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${sweep_diag_period}"
  printf 'set(APP_TAG_CONSOLE_SUMMARY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_console_summary_enable}"
  printf 'set(APP_TAG_VERBOSE_PERF %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_verbose_perf}"
  printf 'set(APP_TAG_PENDING_PRINT_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_pending_print_period}"
  printf 'set(APP_TAG_BLE_PACKET_BUNDLE_RECORDS %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_packet_bundle_records}"
  printf 'set(APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_packet_bundle_flush_ms}"
  printf 'set(APP_TAG_BLE_TX_ITEM_COUNT %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_tx_item_count}"
  printf 'set(APP_TAG_ALT_RXG_BLE_DIAG_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_alt_rxg_ble_diag_enable}"
  printf 'set(APP_TAG_IMU_SAMPLE_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_imu_sample_period}"
  printf 'set(APP_TAG_TR_IMU_SUMMARY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_tr_imu_summary_enable}"
  printf 'set(APP_TAG_TR_IMU_RAW_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_tr_imu_raw_enable}"
  printf 'set(APP_TAG_TR_IMU_SUMMARY_WINDOW %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_tr_imu_summary_window}"
  printf 'set(APP_TAG_NORMAL_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_normal_output_enable}"
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_feature_output_enable}"
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_feature_output_ble_enable}"
  printf 'set(APP_TAG_EKF_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ekf_enable}"
  printf 'set(APP_TAG_LOC_MIN_QUALITY_PERCENT %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_loc_min_quality_percent}"
  printf 'set(APP_TAG_MOTION_SPEED_THRESHOLD_MM_S %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_speed_threshold_mm_s}"
  printf 'set(APP_TAG_MOTION_RANGE_SOFT_BONUS_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_range_soft_bonus_mm}"
  printf 'set(APP_TAG_MOTION_RANGE_HARD_BONUS_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_range_hard_bonus_mm}"
  printf 'set(APP_TAG_MOTION_EKF_MEAS_STD_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_ekf_meas_std_mm}"
  printf 'set(APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2 %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_ekf_proc_accel_mm_s2}"
  printf 'set(APP_TAG_MOTION_EKF_OUTLIER_GATE_MM %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_ekf_outlier_gate_mm}"
  printf 'set(APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_imu_delta_threshold_mg}"
  printf 'set(APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_motion_imu_gravity_err_threshold_mg}"
  printf 'set(APP_TAG_USB_DIAG_TRACE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_usb_diag_trace}"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${active_anchor_0_id}"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${active_anchor_1_id}"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_2_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${active_anchor_2_id}"
  printf 'set(APP_TAG_ACTIVE_ANCHOR_3_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${active_anchor_3_id}"
  printf 'set(APP_TAG_STANDBY_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${standby_anchor_0_id}"
  printf 'set(APP_TAG_STANDBY_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${standby_anchor_1_id}"
  printf 'set(APP_TAG_RESERVE_ANCHOR_0_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${reserve_anchor_0_id}"
  printf 'set(APP_TAG_RESERVE_ANCHOR_1_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${reserve_anchor_1_id}"
  printf 'set(APP_UWB_CHANNEL %s CACHE STRING "Motion tag preload" FORCE)\n' "${uwb_channel}"
  printf 'set(APP_UWB_PAN_ID %s CACHE STRING "Motion tag preload" FORCE)\n' "${uwb_pan_id}"
  printf 'set(APP_TAG_FW_MARKER %s CACHE STRING "Motion tag preload" FORCE)\n' "${fw_marker}"
  printf 'set(APP_TAG_BLE_NAME_PREFIX %s CACHE STRING "Motion tag preload" FORCE)\n' "${ble_name_prefix}"
  printf 'set(APP_TAG_WAND_MODE_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${wand_mode_enable}"
  printf 'set(APP_ALT_SS_TWR_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_enable}"
  printf 'set(APP_ALT_SS_TWR_BCAST_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_bcast_enable}"
  printf 'set(APP_ALT_SS_TWR_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_mode}"
  printf 'set(APP_ALT_SS_TWR_POLL_SPACING_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_poll_spacing_us}"
  printf 'set(APP_ALT_SS_TWR_GUARD_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_guard_us}"
  printf 'set(APP_ALT_SS_TWR_RESP_SPACING_US %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_resp_spacing_us}"
  printf 'set(APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_bcast_force_full_sweep}"
  printf 'set(APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_light_tdma_enable}"
  printf 'set(APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_bcast_immediate_tx_enable}"
  printf 'set(APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${alt_ss_twr_bcast_prewrite_tx_enable}"
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
  -DAPP_ALT_SS_TWR_BCAST_ENABLE="${alt_ss_twr_bcast_enable}" \
  -DAPP_ALT_SS_TWR_MODE="${alt_ss_twr_mode}" \
  -DAPP_ALT_SS_TWR_POLL_SPACING_US="${alt_ss_twr_poll_spacing_us}" \
  -DAPP_ALT_SS_TWR_GUARD_US="${alt_ss_twr_guard_us}" \
  -DAPP_ALT_SS_TWR_RESP_SPACING_US="${alt_ss_twr_resp_spacing_us}" \
  -DAPP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP="${alt_ss_twr_bcast_force_full_sweep}" \
  -DAPP_ALT_SS_TWR_LIGHT_TDMA_ENABLE="${alt_ss_twr_light_tdma_enable}" \
  -DAPP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE="${alt_ss_twr_bcast_immediate_tx_enable}" \
  -DAPP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE="${alt_ss_twr_bcast_prewrite_tx_enable}" \
  -DAPP_TAG_USB_DIAG_TRACE="${tag_usb_diag_trace}" \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DCONFIG_BT_DEVICE_NAME=\"${device_name}\" \
  -DAPP_TAG_BLE_OTA_ENABLE=1 \
  -DAPP_TAG_BLE_SETTINGS_ENABLE=1 \
  -DAPP_TAG_BLE_COMPACT_STATUS=1 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS="${tag_ble_packet_bundle_records}" \
  -DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS="${tag_ble_packet_bundle_flush_ms}" \
  -DAPP_TAG_BLE_TX_ITEM_COUNT="${tag_ble_tx_item_count}" \
  -DAPP_TAG_ALT_RXG_BLE_DIAG_ENABLE="${tag_alt_rxg_ble_diag_enable}" \
  -DAPP_TAG_BLE_NAME_PREFIX="${ble_name_prefix}" \
  -DAPP_TAG_WAND_MODE_ENABLE="${wand_mode_enable}" \
  -DAPP_TAG_MCUBOOT_ENABLE=1 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="${slot_index}" \
  -DAPP_TAG_TDMA_SLOT_COUNT="${slot_count}" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS="${slot_period_ms}" \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS="${slot_active_ms}" \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_US="${slot_active_us}" \
  -DAPP_TAG_MULTITAG_PLAN_MODE="${multitag_plan_mode}" \
  -DAPP_TAG_MAINTENANCE_FULL_INTERVAL="${maintenance_full_interval}" \
  -DAPP_TAG_ACTIVE_ANCHOR_0_ID="${active_anchor_0_id}" \
  -DAPP_TAG_ACTIVE_ANCHOR_1_ID="${active_anchor_1_id}" \
  -DAPP_TAG_ACTIVE_ANCHOR_2_ID="${active_anchor_2_id}" \
  -DAPP_TAG_ACTIVE_ANCHOR_3_ID="${active_anchor_3_id}" \
  -DAPP_TAG_STANDBY_ANCHOR_0_ID="${standby_anchor_0_id}" \
  -DAPP_TAG_STANDBY_ANCHOR_1_ID="${standby_anchor_1_id}" \
  -DAPP_TAG_RESERVE_ANCHOR_0_ID="${reserve_anchor_0_id}" \
  -DAPP_TAG_RESERVE_ANCHOR_1_ID="${reserve_anchor_1_id}" \
  -DAPP_TAG_FAST_TRACKING=1 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=8 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT="${tag_track_anchor_count}" \
  -DAPP_TAG_SUMMARY_PERIOD=1 \
  -DAPP_TAG_PENDING_PRINT_PERIOD="${tag_pending_print_period}" \
  -DAPP_TAG_IMU_SAMPLE_PERIOD="${tag_imu_sample_period}" \
  -DAPP_TAG_TR_IMU_SUMMARY_ENABLE="${tag_tr_imu_summary_enable}" \
  -DAPP_TAG_TR_IMU_RAW_ENABLE="${tag_tr_imu_raw_enable}" \
  -DAPP_TAG_TR_IMU_SUMMARY_WINDOW="${tag_tr_imu_summary_window}" \
  -DAPP_TAG_NORMAL_OUTPUT_ENABLE="${tag_normal_output_enable}" \
  -DAPP_TAG_CIR_FEATURE_OUTPUT_ENABLE="${tag_cir_feature_output_enable}" \
  -DAPP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE="${tag_cir_feature_output_ble_enable}" \
  -DAPP_TAG_VERBOSE_RANGING=0 \
  -DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
  -DAPP_TAG_EKF_ENABLE="${tag_ekf_enable}" \
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
  -DAPP_TAG_OUTPUT_FILTER_RMS_MM="${output_filter_rms_mm}" \
  -DAPP_TAG_OUTPUT_FILTER_SPEED_MM_S="${output_filter_speed_mm_s}" \
  -DAPP_TAG_POSITION_OUTPUT_ENABLE="${position_output_enable}" \
  -DAPP_TAG_TR_BCAST_V2_ENABLE="${tr_bcast_v2_enable}" \
  -DAPP_TAG_BCAST_SUMMARY_ENABLE="${bcast_summary_enable}" \
  -DAPP_TAG_BCAST_SUMMARY_PERIOD_MS="${bcast_summary_period_ms}" \
  -DAPP_TAG_LOC_FAST_ALL_VALID_ENABLE="${tag_loc_fast_all_valid_enable}" \
  -DAPP_TAG_SWEEP_DIAG_ENABLE="${sweep_diag_enable}" \
  -DAPP_TAG_SWEEP_DIAG_PERIOD="${sweep_diag_period}" \
  -DAPP_TAG_CONSOLE_SUMMARY_ENABLE="${tag_console_summary_enable}" \
  -DAPP_TAG_VERBOSE_PERF="${tag_verbose_perf}" \
  -DAPP_TAG_LOC_MIN_QUALITY_PERCENT="${tag_loc_min_quality_percent}" \
  -DAPP_TAG_MOTION_FULL_SWEEP_INTERVAL=0 \
  -DAPP_TAG_MOTION_SPEED_THRESHOLD_MM_S="${tag_motion_speed_threshold_mm_s}" \
  -DAPP_TAG_MOTION_RANGE_SOFT_BONUS_MM="${tag_motion_range_soft_bonus_mm}" \
  -DAPP_TAG_MOTION_RANGE_HARD_BONUS_MM="${tag_motion_range_hard_bonus_mm}" \
  -DAPP_TAG_MOTION_EKF_MEAS_STD_MM="${tag_motion_ekf_meas_std_mm}" \
  -DAPP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2="${tag_motion_ekf_proc_accel_mm_s2}" \
  -DAPP_TAG_MOTION_EKF_OUTLIER_GATE_MM="${tag_motion_ekf_outlier_gate_mm}" \
  -DAPP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG="${tag_motion_imu_delta_threshold_mg}" \
  -DAPP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG="${tag_motion_imu_gravity_err_threshold_mg}" \
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
