#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <tag_id> [build_dir]" >&2
  exit 1
fi

tag_id="$1"
build_dir="${2:-build-tag-usb-tag${tag_id}}"
tag_logical_id="${TAG_LOGICAL_ID:-}"

if [ -z "$tag_logical_id" ]; then
  case "$tag_id" in
    113) tag_logical_id="3" ;;
    127) tag_logical_id="2" ;;
    *)
      if [[ "$tag_id" =~ ^[0-9]+$ ]] && [ "$tag_id" -lt 10 ]; then
        tag_logical_id="$tag_id"
      else
        tag_logical_id="0"
      fi
      ;;
  esac
fi

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag_usb \
  -d "$build_dir" \
  --no-sysbuild \
  --pristine=always \
  -- \
  -DAPP_TAG_ID="$tag_logical_id"

python3 scripts/write_build_source.py \
  --build-dir "$build_dir" \
  --source "scripts/build_tag_usb.sh" \
  --command "$0 $*"

echo
echo "Built: $build_dir"
echo "Hex:   $build_dir/zephyr/zephyr.hex"
