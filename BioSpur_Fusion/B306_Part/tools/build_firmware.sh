#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [build-name]" >&2
  exit 2
fi

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
builds_root="$workspace_root/B306_Part/builds"
build_name="${1:-b306-current}"
build_name="${build_name#build-}"

case "$build_name" in
  ""|*/*)
    echo "build name must be one path component" >&2
    exit 2
    ;;
esac

build_dir="$builds_root/$build_name"
toolchain_root="/home/zekaixiao/ncs/toolchains/b81a7cd864"

mkdir -p "$builds_root"

PYTHONNOUSERSITE=1 \
PYTHONPATH="$toolchain_root/usr/local/lib/python3.12/site-packages" \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR="$toolchain_root/opt/zephyr-sdk" \
"$toolchain_root/usr/local/bin/python3" -m west \
  build --sysbuild --pristine=always \
  -b biospur_fusion_nrf52840/nrf52840 \
  -s "$workspace_root/B306_Part/firmware" \
  -d "$build_dir" \
  -- -DBOARD_ROOT="$workspace_root/B306_Part/firmware"

python3 "$workspace_root/tools/zephyr_memory_gate.py" \
  --zephyr-dir "$build_dir/firmware/zephyr" \
  --flash-limit-percent 95 \
  --ram-limit-percent 85

python3 "$workspace_root/B306_Part/tools/check_deployed_marker.py" \
  --elf "$build_dir/firmware/zephyr/zephyr.elf" \
  --artifact "$build_dir/firmware/zephyr/zephyr.signed.bin" \
  --manifest "$workspace_root/B306_Part/firmware/deployed_markers.json"

echo "Built B306 firmware: $build_dir"
