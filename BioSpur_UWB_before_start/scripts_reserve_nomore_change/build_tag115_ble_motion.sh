#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 2 ]; then
  echo "Usage: $0 [slot_count=10] [build_dir]" >&2
  exit 1
fi

slot_count="${1:-10}"
build_dir="${2:-build-tag-ble-motion-tag115-auto}"

if [ "${ALLOW_REF115_MOTION_BUILD:-0}" != "1" ]; then
  cat >&2 <<'EOF'
Ref115 safety guard:
  This script builds the motion/BLE OTA family for SNR 760186115.
  It is NOT the static calibration/reference firmware used by autopositioning.
If you really intend motion-family build for 115, rerun with:
  ALLOW_REF115_MOTION_BUILD=1 ./scripts/build_tag115_ble_motion.sh ...
EOF
  exit 2
fi

./scripts/build_tag_ble_motion.sh 115 auto "${slot_count}" "${build_dir}"

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag115_ble_motion.sh" \
  --command "$0 $*"

echo
echo "Built: ${build_dir}"
echo "Hex:   ${build_dir}/zephyr/zephyr.hex"
echo "Name:  BS auto identity (runtime)"
