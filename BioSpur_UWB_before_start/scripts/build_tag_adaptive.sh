#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <tag_id> [build_dir]" >&2
  exit 1
fi

tag_id="$1"
build_dir="${2:-build-tag-adaptive-tag${tag_id}}"

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag \
  -d "$build_dir" \
  --no-sysbuild \
  --pristine=always \
  -- \
  -DAPP_TAG_ID="$tag_id" \
  -DAPP_TAG_FIXED_MODE=0 \
  -DAPP_TAG_TDMA_ENABLE=0 \
  -DAPP_TAG_FAST_TRACKING=1 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=4 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT=6

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/zephyr.hex"
