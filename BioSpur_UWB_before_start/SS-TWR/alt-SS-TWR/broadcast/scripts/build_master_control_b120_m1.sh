#!/usr/bin/env bash
set -euo pipefail

build_dir="${1:-build-master-control-b120-m1}"
MASTER_CMAKE_ARGS="${MASTER_CMAKE_ARGS:-}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
default_internal_conf="$repo_root/configs/b120_internal_osc_default_usb.conf"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"
WEST_TOPDIR="${WEST_TOPDIR:-$NCS_ROOT}"
if [[ "$build_dir" != /* ]]; then
  build_dir="$repo_root/$build_dir"
fi

# Keep the NCS toolchain Python able to import host Python packages such as
# yaml/cbor2, while avoiding /usr/local packages that are known to break west
# on this machine.
HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "$MASTER_CMAKE_ARGS" != *"EXTRA_CONF_FILE"* ]]; then
  if [ ! -f "$default_internal_conf" ]; then
    echo "[error] missing default B120 internal oscillator config: $default_internal_conf" >&2
    exit 2
  fi
  MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$default_internal_conf -Dhci_ipc_EXTRA_CONF_FILE=$default_internal_conf $MASTER_CMAKE_ARGS"
  echo "[build] B120 default clock policy: internal LFRC on CPUAPP+CPUNET"
fi

extra_args=()
if [ -n "$MASTER_CMAKE_ARGS" ]; then
  read -r -a extra_args <<<"$MASTER_CMAKE_ARGS"
fi

(cd "$WEST_TOPDIR" && "$WEST_BIN" build \
  --no-sysbuild \
  -b nrf5340dk/nrf5340/cpuapp \
  -s "$repo_root/apps/master_control" \
  -d "$build_dir" \
  --pristine=always \
  -- \
  "${extra_args[@]}")

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "$build_dir" \
  --source "scripts/build_master_control_b120_m1.sh" \
  --command "$0 $*"

if [[ -f "$repo_root/apps/master_ota/generated/active_ota_payload.json" ]]; then
  cp "$repo_root/apps/master_ota/generated/active_ota_payload.json" \
    "$build_dir/active_ota_payload.json"
fi

echo
echo "Built: $build_dir"
echo "ELF:   $build_dir/zephyr/zephyr.elf"
echo "HEX:   $build_dir/zephyr/zephyr.hex"

"$repo_root/scripts/assert_b120_internal_osc_build.sh" "$build_dir"
