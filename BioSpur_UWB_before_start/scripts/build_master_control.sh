#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control}"

west build \
  -b nrf52840dk/nrf52840 \
  -s apps/master_control \
  -d "$build_dir" \
  --pristine=always

python3 scripts/write_build_source.py \
  --build-dir "$build_dir" \
  --source "scripts/build_master_control.sh" \
  --command "$0 $*"

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/zephyr.hex"
