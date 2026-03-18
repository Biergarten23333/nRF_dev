#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 7 ]; then
  echo "Usage: $0 <tag_id> <slot_index> [slot_count=10] [slot_period_ms=10] [slot_active_ms=9] [fixed_anchor_ids=1,2,5,6] [build_dir]" >&2
  exit 1
fi

tag_id="$1"
slot_index="$2"
slot_count="${3:-10}"
slot_period_ms="${4:-10}"
slot_active_ms="${5:-9}"
fixed_anchor_ids="${6:-1,2,5,6}"
build_dir="${7:-build-tag-slot${slot_index}-tag${tag_id}}"

IFS=',' read -r fixed0 fixed1 fixed2 fixed3 <<< "$fixed_anchor_ids"

if [ -z "${fixed0:-}" ] || [ -z "${fixed1:-}" ] || [ -z "${fixed2:-}" ] || [ -z "${fixed3:-}" ]; then
  echo "fixed_anchor_ids must contain exactly four comma-separated anchor IDs" >&2
  exit 1
fi

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag \
  -d "$build_dir" \
  --no-sysbuild \
  --pristine=always \
  -- \
  -DAPP_TAG_ID="$tag_id" \
  -DAPP_TAG_FIXED_MODE=1 \
  -DAPP_TAG_FIXED_ANCHOR_COUNT=4 \
  -DAPP_TAG_FIXED_ANCHOR_0_ID="$fixed0" \
  -DAPP_TAG_FIXED_ANCHOR_1_ID="$fixed1" \
  -DAPP_TAG_FIXED_ANCHOR_2_ID="$fixed2" \
  -DAPP_TAG_FIXED_ANCHOR_3_ID="$fixed3" \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="$slot_index" \
  -DAPP_TAG_TDMA_SLOT_COUNT="$slot_count" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS="$slot_period_ms" \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS="$slot_active_ms"

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/zephyr.hex"
