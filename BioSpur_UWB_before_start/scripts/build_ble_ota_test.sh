#!/usr/bin/env bash
set -euo pipefail

TAG_BUILD_DIR="${1:-build-tag-ota}"
MASTER_BUILD_DIR="${2:-build-master-ota}"
TAG_BOARD="${TAG_BOARD:-decawave_dwm1001_dev}"
MASTER_BOARD="${MASTER_BOARD:-nrf54l15dk/nrf54l15/cpuapp}"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"

export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$NCS_ROOT/nrf}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(west list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"
export PYTHONPATH="${PYTHONPATH:-}:/usr/lib/python3/dist-packages:/usr/lib/python3.12/dist-packages"

west build \
  -b "$TAG_BOARD" \
  -s apps/tag_ota \
  -d "$TAG_BUILD_DIR" \
  --sysbuild \
  --pristine=always

SIGNED_BIN="$(find "$TAG_BUILD_DIR" -name zephyr.signed.bin | head -n 1)"
if [ -z "${SIGNED_BIN:-}" ]; then
  echo "Could not find zephyr.signed.bin under $TAG_BUILD_DIR" >&2
  exit 1
fi

python3 scripts/gen_ota_image_inc.py \
  "$SIGNED_BIN" \
  apps/master_ota/generated/ota_image.inc

west build \
  -b "$MASTER_BOARD" \
  -s apps/master_ota \
  -d "$MASTER_BUILD_DIR" \
  --no-sysbuild \
  --pristine=always

echo
echo "Tag OTA build:   $TAG_BUILD_DIR"
echo "Master OTA build: $MASTER_BUILD_DIR"
echo "Signed image:    $SIGNED_BIN"
