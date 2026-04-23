#!/usr/bin/env bash
set -euo pipefail

TAG_BUILD_DIR="${1:-build-tag-ota-uwb}"
MASTER_BUILD_DIR="${2:-build-master-ota}"
TAG_BOARD="${TAG_BOARD:-decawave_dwm1001_dev}"
MASTER_BOARD="${MASTER_BOARD:-nrf52840dk/nrf52840}"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
NCS_TOOLCHAIN_ROOT="${NCS_TOOLCHAIN_ROOT:-/home/zekaixiao/ncs/toolchains/b81a7cd864}"
TAG_SUMMARY_PERIOD="${TAG_SUMMARY_PERIOD:-1}"
TAG_CMAKE_ARGS="${TAG_CMAKE_ARGS:-}"
TAG_SIGN_VERSION="${TAG_SIGN_VERSION:-}"
MASTER_CMAKE_ARGS="${MASTER_CMAKE_ARGS:-}"
REPO_ROOT="/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start"

export ZEPHYR_NRF_MODULE_DIR="${ZEPHYR_NRF_MODULE_DIR:-$NCS_ROOT/nrf}"
export ZEPHYR_MODULES="${ZEPHYR_MODULES:-$(west list --format={abspath} | tr '\n' ';' | sed 's/;$//')}"
export PYTHONPATH="${PYTHONPATH:-}:/usr/lib/python3/dist-packages:/usr/lib/python3.12/dist-packages:$NCS_TOOLCHAIN_ROOT/usr/local/lib/python3.12/site-packages"

EXTRA_TAG_ARGS=()
if [ -n "$TAG_CMAKE_ARGS" ]; then
  read -r -a EXTRA_TAG_ARGS <<<"$TAG_CMAKE_ARGS"
fi
if [ -n "$TAG_SIGN_VERSION" ]; then
  EXTRA_TAG_ARGS+=("-DCONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION=\"${TAG_SIGN_VERSION}\"")
fi

SYSBUILD_TAG_ARGS=()
EXTRA_MASTER_ARGS=()
APP_TAG_PRELOAD_FILE="$(mktemp /tmp/app_tag_preload.XXXXXX.cmake)"
printf 'set(APP_TAG_SUMMARY_PERIOD %s CACHE STRING "App tag preload" FORCE)\n' "$TAG_SUMMARY_PERIOD" >> "$APP_TAG_PRELOAD_FILE"
for arg in "${EXTRA_TAG_ARGS[@]}"; do
  if [[ "$arg" == -DAPP_TAG_* ]]; then
    key="${arg#-D}"
    key="${key%%=*}"
    value="${arg#*=}"
    printf 'set(%s %s CACHE STRING "App tag preload" FORCE)\n' "$key" "$value" >> "$APP_TAG_PRELOAD_FILE"
  else
    SYSBUILD_TAG_ARGS+=("$arg")
  fi
done
export APP_TAG_PRELOAD_FILE

if [ -n "$MASTER_CMAKE_ARGS" ]; then
  read -r -a EXTRA_MASTER_ARGS <<<"$MASTER_CMAKE_ARGS"
fi

west build \
  -b "$TAG_BOARD" \
  -s apps/tag \
  -d "$TAG_BUILD_DIR" \
  --sysbuild \
  --pristine=always \
  -- \
  "${SYSBUILD_TAG_ARGS[@]}"

SIGNED_BIN="$(find "$TAG_BUILD_DIR" -name zephyr.signed.bin | head -n 1)"
if [ -z "${SIGNED_BIN:-}" ]; then
  echo "Could not find zephyr.signed.bin under $TAG_BUILD_DIR" >&2
  exit 1
fi

python3 scripts/write_build_source.py \
  --build-dir "$TAG_BUILD_DIR" \
  --source "scripts/build_uwb_tag_ota_test.sh" \
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
if not signed_bin.is_absolute():
    signed_bin = repo_root / signed_bin
signed_bin = signed_bin.resolve()
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
  --pristine=always \
  -- \
  "${EXTRA_MASTER_ARGS[@]}"

python3 scripts/write_build_source.py \
  --build-dir "$MASTER_BUILD_DIR" \
  --source "scripts/build_uwb_tag_ota_test.sh" \
  --command "$0 $* (master build)"

echo
echo "UWB tag OTA build:  $TAG_BUILD_DIR"
echo "Master OTA build:   $MASTER_BUILD_DIR"
echo "Signed image:       $SIGNED_BIN"
echo "Summary period:     $TAG_SUMMARY_PERIOD"
if [ ${#EXTRA_TAG_ARGS[@]} -ne 0 ]; then
  echo "Extra tag args:     ${EXTRA_TAG_ARGS[*]}"
fi
if [ ${#SYSBUILD_TAG_ARGS[@]} -ne 0 ]; then
  echo "Sysbuild tag args:  ${SYSBUILD_TAG_ARGS[*]}"
fi
if [ ${#EXTRA_MASTER_ARGS[@]} -ne 0 ]; then
  echo "Master args:        ${EXTRA_MASTER_ARGS[*]}"
fi
echo "Tag preload file:   $APP_TAG_PRELOAD_FILE"
if [ -n "$TAG_SIGN_VERSION" ]; then
  echo "Tag sign version:   $TAG_SIGN_VERSION"
fi
