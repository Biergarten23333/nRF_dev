#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <tag_id> <tdma_slot_index> [tdma_slot_count] [build_dir]" >&2
  exit 1
fi

tag_id="$1"
slot_index="$2"
slot_count="${3:-2}"
build_dir="${4:-build-tag-ble-motion-tag${tag_id}-slot${slot_index}}"
device_name="${TAG_DEVICE_NAME:-Tag_rot_${tag_id}}"
tag_logical_id="${TAG_LOGICAL_ID:-}"

if [ -z "${tag_logical_id}" ]; then
  case "${tag_id}" in
    113) tag_logical_id="3" ;;
    127) tag_logical_id="2" ;;
    *)
      if [[ "${tag_id}" =~ ^[0-9]+$ ]] && [ "${tag_id}" -lt 10 ]; then
        tag_logical_id="${tag_id}"
      else
        tag_logical_id="0"
      fi
      ;;
  esac
fi

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag_ble_lite \
  -d "${build_dir}" \
  --pristine=always \
  -- \
  -DAPP_TAG_ID="${tag_logical_id}" \
  -DAPP_TAG_BLE_TOKEN_ID="${tag_id}" \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DCONFIG_BT_DEVICE_NAME=\"${device_name}\" \
  -DAPP_TAG_BLE_OTA_ENABLE=0 \
  -DAPP_TAG_BLE_SETTINGS_ENABLE=0 \
  -DAPP_TAG_BLE_COMPACT_STATUS=1 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS=3 \
  -DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=250 \
  -DAPP_TAG_MCUBOOT_ENABLE=0 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="${slot_index}" \
  -DAPP_TAG_TDMA_SLOT_COUNT="${slot_count}" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS=25 \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS=20 \
  -DAPP_TAG_FAST_TRACKING=1 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=8 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT=4 \
  -DAPP_TAG_SUMMARY_PERIOD=1 \
  -DAPP_TAG_PENDING_PRINT_PERIOD=0 \
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

echo
echo "Built: ${build_dir}"
echo "Hex:   ${build_dir}/merged.hex"
echo "Name:  ${device_name}"
