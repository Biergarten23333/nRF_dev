#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 2 ]; then
  echo "Usage: $0 [slot_count=10] [build_dir]" >&2
  exit 1
fi

slot_count="${1:-10}"
build_dir="${2:-build-tag-ble-motion-tag127-auto}"

./scripts/build_tag_ble_motion.sh 127 auto "${slot_count}" "${build_dir}"

python3 scripts/write_build_source.py \
  --build-dir "${build_dir}" \
  --source "scripts/build_tag127_ble_motion.sh" \
  --command "$0 $*"

echo
echo "Built: ${build_dir}"
echo "Hex:   ${build_dir}/zephyr/zephyr.hex"
echo "Name:  BS auto identity (runtime)"
