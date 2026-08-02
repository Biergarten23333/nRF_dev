#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 3 ]; then
  echo "Usage: $0 [tdma_slot_index=0] [tdma_slot_count=10] [build_name]" >&2
  exit 1
fi

slot_index="${1:-0}"
slot_count="${2:-10}"
build_dir_request="${3:-tag-ble-motion-unified}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=resolve_build_dir.sh
source "$repo_root/scripts/resolve_build_dir.sh"
build_dir_abs="$(biospur_uwb_build_dir "$build_dir_request")"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"
ZEPHYR_BASE="${ZEPHYR_BASE:-${NCS_ROOT}/zephyr}"
slot_period_ms="${APP_TAG_TDMA_SLOT_PERIOD_MS:-10}"
slot_active_ms="${APP_TAG_TDMA_SLOT_ACTIVE_MS:-9}"
slot_active_us="${APP_TAG_TDMA_SLOT_ACTIVE_US:-0}"
device_name="${TAG_DEVICE_NAME:-BS_AUTO}"
ble_name_prefix="${APP_TAG_BLE_NAME_PREFIX:-}"
fw_marker="${APP_TAG_FW_MARKER:-unified-default}"
self_confirm_mode="${APP_TAG_SELF_CONFIRM_MODE:-0}"
self_confirm_timeout_ms="${APP_TAG_SELF_CONFIRM_TIMEOUT_MS:-10000}"
uwb_channel="${APP_UWB_CHANNEL:-5}"
uwb_pan_id="${APP_UWB_PAN_ID:-0xDECA}"
alt_ss_twr_enable="${APP_ALT_SS_TWR_ENABLE:-1}"
alt_ss_twr_bcast_enable="${APP_ALT_SS_TWR_BCAST_ENABLE:-1}"
alt_ss_twr_mode="${APP_ALT_SS_TWR_MODE:-2}"
alt_ss_twr_poll_spacing_us="${APP_ALT_SS_TWR_POLL_SPACING_US:-200}"
alt_ss_twr_guard_us="${APP_ALT_SS_TWR_GUARD_US:-1200}"
alt_ss_twr_resp_spacing_us="${APP_ALT_SS_TWR_RESP_SPACING_US:-1000}"
alt_ss_twr_bcast_force_full_sweep="${APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP:-1}"
alt_ss_twr_light_tdma_enable="${APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE:-1}"
alt_ss_twr_bcast_immediate_tx_enable="${APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE:-0}"
alt_ss_twr_bcast_prewrite_tx_enable="${APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE:-0}"
tag_alt_bcast_rank_offset_override="${APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE:-255}"
multitag_plan_mode="${APP_TAG_MULTITAG_PLAN_MODE:-0}"
maintenance_full_interval="${APP_TAG_MAINTENANCE_FULL_INTERVAL:-100}"
tr_bcast_v2_enable="${APP_TAG_TR_BCAST_V2_ENABLE:-1}"
bcast_summary_enable="${APP_TAG_BCAST_SUMMARY_ENABLE:-0}"
bcast_summary_period_ms="${APP_TAG_BCAST_SUMMARY_PERIOD_MS:-1000}"
sweep_diag_enable="${APP_TAG_SWEEP_DIAG_ENABLE:-0}"
sweep_diag_period="${APP_TAG_SWEEP_DIAG_PERIOD:-10}"
tag_ble_packet_bundle_records="${APP_TAG_BLE_PACKET_BUNDLE_RECORDS:-8}"
tag_ble_packet_bundle_flush_ms="${APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS:-100}"
tag_ble_tx_item_count="${APP_TAG_BLE_TX_ITEM_COUNT:-16}"
tag_alt_rxg_ble_diag_enable="${APP_TAG_ALT_RXG_BLE_DIAG_ENABLE:-0}"
tag_normal_output_enable="${APP_TAG_NORMAL_OUTPUT_ENABLE:-1}"
tag_cir_feature_output_enable="${APP_TAG_CIR_FEATURE_OUTPUT_ENABLE:-0}"
tag_cir_feature_output_ble_enable="${APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE:-1}"
tag_cir_full_output_enable="${APP_TAG_CIR_FULL_OUTPUT_ENABLE:-0}"
tag_cir_full_output_cdc_enable="${APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE:-1}"
tag_cir_full_chunk_bytes="${APP_TAG_CIR_FULL_CHUNK_BYTES:-48}"
tag_cir_full_priority_mask="${APP_TAG_CIR_FULL_PRIORITY_MASK:-0}"
tag_cir_full_priority_only_sweep="${APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP:-0}"
tag_cir_compact_sample_period="${APP_TAG_CIR_COMPACT_SAMPLE_PERIOD:-8}"
tag_rf_diag_output_enable="${APP_TAG_RF_DIAG_OUTPUT_ENABLE:-0}"
tag_rf_diag_output_ble_enable="${APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE:-1}"
tag_rf_diag_output_period="${APP_TAG_RF_DIAG_OUTPUT_PERIOD:-1}"
tag_rf_diag_legacy_rfd_enable="${APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE:-0}"
tag_tr_rf_diag_compact_enable="${APP_TAG_TR_RF_DIAG_COMPACT_ENABLE:-1}"
tag_rf_diag_tag_rx_enable="${APP_TAG_RF_DIAG_TAG_RX_ENABLE:-0}"
tag_usb_diag_trace="${APP_TAG_USB_DIAG_TRACE:-0}"
active_anchor_0_id="${APP_TAG_ACTIVE_ANCHOR_0_ID:-0}"
active_anchor_1_id="${APP_TAG_ACTIVE_ANCHOR_1_ID:-1}"
active_anchor_2_id="${APP_TAG_ACTIVE_ANCHOR_2_ID:-4}"
active_anchor_3_id="${APP_TAG_ACTIVE_ANCHOR_3_ID:-5}"
standby_anchor_0_id="${APP_TAG_STANDBY_ANCHOR_0_ID:-2}"
standby_anchor_1_id="${APP_TAG_STANDBY_ANCHOR_1_ID:-6}"
reserve_anchor_0_id="${APP_TAG_RESERVE_ANCHOR_0_ID:-3}"
reserve_anchor_1_id="${APP_TAG_RESERVE_ANCHOR_1_ID:-7}"
export ZEPHYR_BASE
export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$(dirname "${ZEPHYR_BASE}")/nrf}"
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
  printf 'set(APP_TAG_TR_BCAST_V2_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tr_bcast_v2_enable}"
  printf 'set(APP_TAG_BCAST_SUMMARY_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${bcast_summary_enable}"
  printf 'set(APP_TAG_BCAST_SUMMARY_PERIOD_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${bcast_summary_period_ms}"
  printf 'set(APP_TAG_SWEEP_DIAG_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${sweep_diag_enable}"
  printf 'set(APP_TAG_SWEEP_DIAG_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${sweep_diag_period}"
  printf 'set(APP_TAG_BLE_PACKET_BUNDLE_RECORDS %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_packet_bundle_records}"
  printf 'set(APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_packet_bundle_flush_ms}"
  printf 'set(APP_TAG_BLE_TX_ITEM_COUNT %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_ble_tx_item_count}"
  printf 'set(APP_TAG_ALT_RXG_BLE_DIAG_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_alt_rxg_ble_diag_enable}"
  printf 'set(APP_TAG_NORMAL_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_normal_output_enable}"
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_feature_output_enable}"
  printf 'set(APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_feature_output_ble_enable}"
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_full_output_enable}"
  printf 'set(APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_full_output_cdc_enable}"
  printf 'set(APP_TAG_CIR_FULL_CHUNK_BYTES %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_full_chunk_bytes}"
  printf 'set(APP_TAG_CIR_FULL_PRIORITY_MASK %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_full_priority_mask}"
  printf 'set(APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_full_priority_only_sweep}"
  printf 'set(APP_TAG_CIR_COMPACT_SAMPLE_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_cir_compact_sample_period}"
  printf 'set(APP_TAG_RF_DIAG_OUTPUT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_rf_diag_output_enable}"
  printf 'set(APP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_rf_diag_output_ble_enable}"
  printf 'set(APP_TAG_RF_DIAG_OUTPUT_PERIOD %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_rf_diag_output_period}"
  printf 'set(APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_rf_diag_legacy_rfd_enable}"
  printf 'set(APP_TAG_TR_RF_DIAG_COMPACT_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_tr_rf_diag_compact_enable}"
  printf 'set(APP_TAG_RF_DIAG_TAG_RX_ENABLE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_rf_diag_tag_rx_enable}"
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
  printf 'set(APP_TAG_SELF_CONFIRM_MODE %s CACHE STRING "Motion tag preload" FORCE)\n' "${self_confirm_mode}"
  printf 'set(APP_TAG_SELF_CONFIRM_TIMEOUT_MS %s CACHE STRING "Motion tag preload" FORCE)\n' "${self_confirm_timeout_ms}"
  printf 'set(APP_TAG_BLE_NAME_PREFIX %s CACHE STRING "Motion tag preload" FORCE)\n' "${ble_name_prefix}"
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
  printf 'set(APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE %s CACHE STRING "Motion tag preload" FORCE)\n' "${tag_alt_bcast_rank_offset_override}"
} > "${app_tag_preload_file}"

export APP_TAG_PRELOAD_FILE="${app_tag_preload_file}"
export KCONFIG_ALLOW_WARNINGS=1
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:${PYTHONPATH}}"

(cd "$NCS_ROOT" && "$WEST_BIN" build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s "$repo_root/apps/tag" \
  -d "${build_dir_abs}" \
  --pristine=always \
  -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
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
  -DAPP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE="${tag_alt_bcast_rank_offset_override}" \
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
  -DAPP_TAG_NORMAL_OUTPUT_ENABLE="${tag_normal_output_enable}" \
  -DAPP_TAG_CIR_FEATURE_OUTPUT_ENABLE="${tag_cir_feature_output_enable}" \
  -DAPP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE="${tag_cir_feature_output_ble_enable}" \
  -DAPP_TAG_CIR_FULL_OUTPUT_ENABLE="${tag_cir_full_output_enable}" \
  -DAPP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE="${tag_cir_full_output_cdc_enable}" \
  -DAPP_TAG_CIR_FULL_CHUNK_BYTES="${tag_cir_full_chunk_bytes}" \
  -DAPP_TAG_CIR_FULL_PRIORITY_MASK="${tag_cir_full_priority_mask}" \
  -DAPP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP="${tag_cir_full_priority_only_sweep}" \
  -DAPP_TAG_CIR_COMPACT_SAMPLE_PERIOD="${tag_cir_compact_sample_period}" \
  -DAPP_TAG_RF_DIAG_OUTPUT_ENABLE="${tag_rf_diag_output_enable}" \
  -DAPP_TAG_RF_DIAG_OUTPUT_BLE_ENABLE="${tag_rf_diag_output_ble_enable}" \
  -DAPP_TAG_RF_DIAG_OUTPUT_PERIOD="${tag_rf_diag_output_period}" \
  -DAPP_TAG_RF_DIAG_LEGACY_RFD_ENABLE="${tag_rf_diag_legacy_rfd_enable}" \
  -DAPP_TAG_TR_RF_DIAG_COMPACT_ENABLE="${tag_tr_rf_diag_compact_enable}" \
  -DAPP_TAG_RF_DIAG_TAG_RX_ENABLE="${tag_rf_diag_tag_rx_enable}" \
  -DAPP_TAG_VERBOSE_RANGING=0 \
  -DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
  -DAPP_TAG_TR_BCAST_V2_ENABLE="${tr_bcast_v2_enable}" \
  -DAPP_TAG_BCAST_SUMMARY_ENABLE="${bcast_summary_enable}" \
  -DAPP_TAG_BCAST_SUMMARY_PERIOD_MS="${bcast_summary_period_ms}" \
  -DAPP_TAG_SWEEP_DIAG_ENABLE="${sweep_diag_enable}" \
  -DAPP_TAG_SWEEP_DIAG_PERIOD="${sweep_diag_period}" \
  ${TAG_CMAKE_ARGS:-})

workspace_root="$(cd "$repo_root/../../.." && pwd)"
python3 "$workspace_root/tools/zephyr_memory_gate.py" \
  --zephyr-dir "${build_dir_abs}/tag/zephyr" \
  --flash-limit-percent 95 \
  --ram-limit-percent 85

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "${build_dir_abs}" \
  --source "scripts/build_tag_ble_motion.sh" \
  --command "$0 $*"

echo
echo "Built: ${build_dir_abs}"
echo "Hex:   ${build_dir_abs}/merged.hex"
echo "Name:  ${device_name}"
echo "Tag preload file: ${app_tag_preload_file}"
