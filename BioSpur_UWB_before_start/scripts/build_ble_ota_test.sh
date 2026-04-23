#!/usr/bin/env bash
set -euo pipefail

TAG_BUILD_DIR="${1:-build-tag-ota}"
MASTER_BUILD_DIR="${2:-build-master-ota}"
TAG_BOARD="${TAG_BOARD:-decawave_dwm1001_dev}"
MASTER_BOARD="${MASTER_BOARD:-nrf52840dk/nrf52840}"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
REPO_ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start"

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

python3 scripts/write_build_source.py \
  --build-dir "$TAG_BUILD_DIR" \
  --source "scripts/build_ble_ota_test.sh" \
  --command "$0 $* (tag build)"

python3 scripts/gen_ota_image_inc.py \
  "$SIGNED_BIN" \
  apps/master_ota/generated/ota_image.inc

python3 - <<'PY' "$REPO_ROOT" "$TAG_BUILD_DIR" "$MASTER_BUILD_DIR" "$SIGNED_BIN"
import json
import re
import sys
import time
from pathlib import Path

repo_root = Path(sys.argv[1])
tag_build_dir = repo_root / sys.argv[2]
master_build_dir = sys.argv[3]
signed_bin = Path(sys.argv[4])
cache = tag_build_dir / "CMakeCache.txt"
fw_marker = "-"
if cache.exists():
    text = cache.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^APP_TAG_FW_MARKER:STRING=(.+)$", text, re.MULTILINE)
    if m:
        fw_marker = m.group(1).strip()
manifest = {
    "kind": "tag_ota_bundle",
    "fw_marker": fw_marker,
    "tag_build_dir": tag_build_dir.name,
    "control_build_dir": master_build_dir,
    "signed_bin": str(signed_bin.relative_to(repo_root)),
    "dfu_zip": f"{tag_build_dir.name}/dfu_application.zip",
    "generated_at_epoch": int(time.time()),
}
(repo_root / "apps" / "master_ota" / "generated" / "tag_ota_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
header = repo_root / "apps" / "master_ota" / "generated" / "tag_ota_manifest.h"
header.write_text(
    "#pragma once\n\n"
    f"#define APP_MASTER_OTA_TAG_FW_MARKER \"{fw_marker}\"\n"
    f"#define APP_MASTER_OTA_TAG_BUILD_DIR \"{tag_build_dir.name}\"\n"
    f"#define APP_MASTER_OTA_TAG_DFU_ZIP \"{tag_build_dir.name}/dfu_application.zip\"\n",
    encoding="utf-8",
)
PY

west build \
  -b "$MASTER_BOARD" \
  -s apps/master_ota \
  -d "$MASTER_BUILD_DIR" \
  --no-sysbuild \
  --pristine=always

python3 scripts/write_build_source.py \
  --build-dir "$MASTER_BUILD_DIR" \
  --source "scripts/build_ble_ota_test.sh" \
  --command "$0 $* (master build)"

echo
echo "Tag OTA build:   $TAG_BUILD_DIR"
echo "Master OTA build: $MASTER_BUILD_DIR"
echo "Signed image:    $SIGNED_BIN"
