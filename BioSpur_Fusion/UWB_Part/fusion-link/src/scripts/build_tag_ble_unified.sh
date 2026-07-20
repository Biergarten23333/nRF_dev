#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 3 ]; then
  echo "Usage: $0 [tdma_slot_index=0] [tdma_slot_count=10] [build_dir]" >&2
  exit 1
fi

slot_index="${1:-0}"
slot_count="${2:-10}"
build_dir="${3:-build-tag-ble-unified-mode-switch}"

# Unified BLE tag build:
# - One OTA-capable image keeps raw range output enabled.
# - Capture workflow/profile remains runtime config; CIR is orthogonal runtime state.
# - CIR defaults to OFF at boot and is enabled only through CIR OFF/COMPACT/FULL.
APP_TAG_FW_MARKER="${APP_TAG_FW_MARKER:-tag-fusion-link-v2}" \
APP_TAG_NORMAL_OUTPUT_ENABLE="${APP_TAG_NORMAL_OUTPUT_ENABLE:-1}" \
APP_TAG_TR_BCAST_V2_ENABLE="${APP_TAG_TR_BCAST_V2_ENABLE:-1}" \
APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP="${APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP:-1}" \
APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE="${APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE:-0}" \
APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE="${APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE:-0}" \
APP_TAG_CIR_FEATURE_OUTPUT_ENABLE="${APP_TAG_CIR_FEATURE_OUTPUT_ENABLE:-1}" \
APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE="${APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE:-1}" \
APP_TAG_CIR_FULL_OUTPUT_ENABLE="${APP_TAG_CIR_FULL_OUTPUT_ENABLE:-1}" \
APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE="${APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE:-1}" \
APP_TAG_CIR_FULL_CHUNK_BYTES="${APP_TAG_CIR_FULL_CHUNK_BYTES:-48}" \
APP_TAG_CIR_FULL_PRIORITY_MASK="${APP_TAG_CIR_FULL_PRIORITY_MASK:-0}" \
APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP="${APP_TAG_CIR_FULL_PRIORITY_ONLY_SWEEP:-0}" \
APP_TAG_CIR_COMPACT_SAMPLE_PERIOD="${APP_TAG_CIR_COMPACT_SAMPLE_PERIOD:-8}" \
./scripts/build_tag_ble_motion.sh "${slot_index}" "${slot_count}" "${build_dir}"

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_ble_unified.sh" \
  --command "$0 $*"

echo
echo "Built unified BLE tag image: ${build_dir}"
echo "Hex: ${build_dir}/merged.hex"
echo "Runtime commands: MODE? | MODE RUN | MODE IDLE | CIR? | CIR OFF | CIR COMPACT | CIR FULL"
