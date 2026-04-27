#!/usr/bin/env bash
set -euo pipefail

# Official unified nRF52840 control-center build:
# - receiver mode (scan/connect/receive/control)
# - OTA mode (identity-safe target gate)
# Mode switching is runtime-controlled via UART/button, no reflashing required.

build_dir="${1:-build-master-control-unified}"

west build \
  -b nrf52840dk/nrf52840 \
  -s apps/master_control \
  -d "$build_dir" \
  --pristine=always

python3 scripts/write_build_source.py \
  --build-dir "$build_dir" \
  --source "scripts/build_master_control_unified.sh" \
  --command "$0 $*"

echo
echo "Built unified control image: $build_dir"
if [ -f "$build_dir/master_control/zephyr/zephyr.hex" ]; then
  echo "Hex: $build_dir/master_control/zephyr/zephyr.hex"
else
  echo "Hex: $build_dir/zephyr/zephyr.hex"
fi
