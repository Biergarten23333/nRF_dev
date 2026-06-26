#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-52840-dongle-ble-listener}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"

HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"

case "$build_dir" in
  /*) build_dir_abs="$build_dir" ;;
  *) build_dir_abs="$repo_root/$build_dir" ;;
esac

(cd "$NCS_ROOT" && "$WEST_BIN" build \
  --no-sysbuild \
  -b nrf52840dongle/nrf52840 \
  -s "$repo_root/apps/ble_listener" \
  -d "$build_dir_abs" \
  --pristine=always)

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "$build_dir_abs" \
  --source "scripts/build_52840_dongle_ble_listener.sh" \
  --command "$0 $*"

echo
echo "Built BLE listener dongle: $build_dir_abs"
echo "ELF:   $build_dir_abs/zephyr/zephyr.elf"
echo "HEX:   $build_dir_abs/zephyr/zephyr.hex"
