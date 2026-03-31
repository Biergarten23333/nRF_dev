#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <build-dir> <anchor-id> <role:{master|matrix|responder|legacy}> [schedule-mode]" >&2
  exit 1
fi

build_dir="$1"
anchor_id="$2"
role_name="$3"
schedule_mode="${4:-2}"

case "$role_name" in
  legacy) role=0 ;;
  master) role=1 ;;
  matrix) role=2 ;;
  responder) role=3 ;;
  *)
    echo "Invalid role: $role_name (expected: master|matrix|responder|legacy)" >&2
    exit 2
    ;;
esac

west build -b decawave_dwm1001_dev \
  -s apps/anchor \
  -d "$build_dir" \
  --no-sysbuild \
  --pristine=always \
  -- \
  -DAPP_ANCHOR_ID="$anchor_id" \
  -DAPP_ANCHOR_ROLE="$role" \
  -DAPP_ANCHOR_SCHEDULE_MODE="$schedule_mode"

echo "Unified anchor build complete:"
echo "  build_dir=$build_dir"
echo "  anchor_id=$anchor_id"
echo "  role=$role_name ($role)"
