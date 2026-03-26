#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [build_dir]" >&2
  exit 1
fi

build_dir="${1:-build-ref115-monitor-4}"

export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:$PYTHONPATH}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag \
  -d "$build_dir" \
  --no-sysbuild \
  --pristine=always \
  -- \
  -DAPP_TAG_ID=1 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX=1 \
  -DAPP_TAG_TDMA_SLOT_COUNT=6 \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS=10 \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS=9 \
  -DAPP_TAG_EKF_ENABLE=0 \
  -DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=140 \
  -DAPP_TAG_RANGE_HARD_RESIDUAL_MM=260 \
  -DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20 \
  -DAPP_TAG_RANGE_CONTINUITY_ENABLE=0 \
  -DAPP_TAG_VERBOSE_RANGING=0 \
  -DAPP_TAG_VERBOSE_MEASUREMENTS=0 \
  -DAPP_TAG_FIXED_MODE=1 \
  -DAPP_TAG_FIXED_ANCHOR_COUNT=4 \
  -DAPP_TAG_FIXED_ANCHOR_0_ID=1 \
  -DAPP_TAG_FIXED_ANCHOR_1_ID=2 \
  -DAPP_TAG_FIXED_ANCHOR_2_ID=5 \
  -DAPP_TAG_FIXED_ANCHOR_3_ID=6

python3 scripts/write_build_source.py \
  --build-dir "$build_dir" \
  --source "scripts/build_ref115_monitor_4.sh" \
  --command "$0 $*"

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/merged.hex"
