#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control-b120-m1}"

west build \
  --no-sysbuild \
  -b nrf5340dk/nrf5340/cpuapp \
  -s apps/master_control \
  -d "$build_dir" \
  --pristine=always

python3 scripts/write_build_source.py \
  --build-dir "$build_dir" \
  --source "scripts/build_master_control_b120_m1.sh" \
  --command "$0 $*"

echo
echo "Built: $build_dir"
echo "ELF:   $build_dir/zephyr/zephyr.elf"
echo "HEX:   $build_dir/zephyr/zephyr.hex"
