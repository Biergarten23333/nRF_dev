#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control}"

# By default, build an all-in-one master_control image that embeds the latest
# unified anchor OTA payload (apps/master_ota/generated/ota_image.inc).
#
# Set MASTER_CONTROL_EMBED_ANCHOR_OTA=0 for a faster build that compiles only
# master_control (keeps whatever ota_image.inc is currently in the tree).
embed_anchor_ota="${MASTER_CONTROL_EMBED_ANCHOR_OTA:-1}"

if [ "$embed_anchor_ota" != "0" ]; then
  # Reuse the proven bundle builder to:
  # 1) build anchor OTA image (signed bin)
  # 2) regenerate ota_image.inc
  # 3) build master_control that includes master_ota (and the payload)
  #
  # Defaults are chosen to match the stable OTA bundle flow; callers may
  # override via environment.
  anchor_build_dir="${MASTER_CONTROL_ANCHOR_BUILD_DIR:-build-anchor-unified-ota}"
  fw_marker="${MASTER_CONTROL_FW_MARKER:-}"
  if [ -n "$fw_marker" ]; then
    bash scripts/build_anchor_ota_control_bundle.sh "$anchor_build_dir" "$build_dir" "$fw_marker"
  else
    bash scripts/build_anchor_ota_control_bundle.sh "$anchor_build_dir" "$build_dir"
  fi
  echo
  echo "Built (all-in-one): $build_dir"
  echo "Hex:              $build_dir/merged.hex"
  exit 0
fi

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
echo "Hex:   $build_dir/merged.hex"
