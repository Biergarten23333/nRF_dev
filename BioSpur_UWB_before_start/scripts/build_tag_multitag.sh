#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 9 ]; then
  echo "Usage: $0 <tag_id> <slot_index> [slot_count=10] [slot_period_ms=10] [slot_active_ms=9] [active_ids=0,1,4,5] [standby_ids=2,6] [reserve_ids=3,7] [build_dir]" >&2
  exit 1
fi

tag_id="$1"
slot_index="$2"
slot_count="${3:-10}"
slot_period_ms="${4:-10}"
slot_active_ms="${5:-9}"
active_ids="${6:-0,1,4,5}"
standby_ids="${7:-2,6}"
reserve_ids="${8:-3,7}"
build_dir="${9:-build-tag-multitag-slot${slot_index}-tag${tag_id}}"

IFS=',' read -r active0 active1 active2 active3 <<< "$active_ids"
IFS=',' read -r standby0 standby1 <<< "$standby_ids"
IFS=',' read -r reserve0 reserve1 <<< "$reserve_ids"

if [ -z "${active0:-}" ] || [ -z "${active1:-}" ] || [ -z "${active2:-}" ] || [ -z "${active3:-}" ]; then
  echo "active_ids must contain exactly four comma-separated anchor IDs" >&2
  exit 1
fi

if [ -z "${standby0:-}" ] || [ -z "${standby1:-}" ]; then
  echo "standby_ids must contain exactly two comma-separated anchor IDs" >&2
  exit 1
fi

if [ -z "${reserve0:-}" ] || [ -z "${reserve1:-}" ]; then
  echo "reserve_ids must contain exactly two comma-separated anchor IDs" >&2
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
  -DAPP_TAG_FIXED_MODE=0 \
  -DAPP_TAG_MULTITAG_PLAN_MODE=1 \
  -DAPP_TAG_ACTIVE_ANCHOR_0_ID="$active0" \
  -DAPP_TAG_ACTIVE_ANCHOR_1_ID="$active1" \
  -DAPP_TAG_ACTIVE_ANCHOR_2_ID="$active2" \
  -DAPP_TAG_ACTIVE_ANCHOR_3_ID="$active3" \
  -DAPP_TAG_STANDBY_ANCHOR_0_ID="$standby0" \
  -DAPP_TAG_STANDBY_ANCHOR_1_ID="$standby1" \
  -DAPP_TAG_RESERVE_ANCHOR_0_ID="$reserve0" \
  -DAPP_TAG_RESERVE_ANCHOR_1_ID="$reserve1" \
  -DAPP_TAG_REFRESH_ANCHOR_BUDGET=1 \
  -DAPP_TAG_REFRESH_INTERVAL=10 \
  -DAPP_TAG_MAINTENANCE_FULL_INTERVAL=100 \
  -DAPP_TAG_TDMA_ENABLE=1 \
  -DAPP_TAG_TDMA_SLOT_INDEX="$slot_index" \
  -DAPP_TAG_TDMA_SLOT_COUNT="$slot_count" \
  -DAPP_TAG_TDMA_SLOT_PERIOD_MS="$slot_period_ms" \
  -DAPP_TAG_TDMA_SLOT_ACTIVE_MS="$slot_active_ms" \
  -DAPP_TAG_FAST_TRACKING=0 \
  -DAPP_TAG_FULL_SWEEP_INTERVAL=1 \
  -DAPP_TAG_TRACK_ANCHOR_COUNT=8

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/zephyr.hex"
