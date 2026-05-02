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
# - One image supports runtime mode switching through BLE commands:
#   MODE CAL / MODE MOTION / MODE FIXED (plus MCAL/MMOT shortcuts).
# - No separate calibration/motion firmware split is required.
APP_TAG_FW_MARKER=unified-ble-mode-switch \
./scripts/build_tag_ble_motion.sh "${slot_index}" "${slot_count}" "${build_dir}"

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag_ble_unified.sh" \
  --command "$0 $*"

echo
echo "Built unified BLE tag image: ${build_dir}"
echo "Hex: ${build_dir}/merged.hex"
echo "Runtime BLE mode switch: MODE? | MODE CAL | MODE MOTION | MODE FIXED | MCAL | MMOT"
