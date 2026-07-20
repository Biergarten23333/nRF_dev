#!/usr/bin/env bash
set -euo pipefail

build_dir_request="${1:-master-control-b120-m1}"
MASTER_CMAKE_ARGS="${MASTER_CMAKE_ARGS:-}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=resolve_build_dir.sh
source "$repo_root/scripts/resolve_build_dir.sh"
build_dir_abs="$(biospur_uwb_build_dir "$build_dir_request")"
default_internal_conf="$repo_root/configs/b120_internal_osc_default_usb.conf"
master_tag_conf="$repo_root/configs/b120_master_tag_lfrc.conf"
master_anchor_conf="$repo_root/configs/b120_master_anchor_lfrc.conf"
NCS_ROOT="${NCS_ROOT:-/home/zekaixiao/ncs/v2.8.0}"
WEST_BIN="${WEST_BIN:-west}"
# Avoid /usr/local site-packages here because this machine has an incompatible
# `enum34` package there, while the NCS toolchain Python still needs distro
# packages such as PyYAML.
HOST_SITE_PACKAGES="$(python3 -c 'import site; print(":".join(p for p in site.getsitepackages() if not p.startswith("/usr/local/lib/python")) )')"
export PYTHONPATH="${HOST_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ "$MASTER_CMAKE_ARGS" != *"EXTRA_CONF_FILE"* ]]; then
  build_dir_lower="$(basename "$build_dir_abs" | tr '[:upper:]' '[:lower:]')"
  selected_conf="$default_internal_conf"
  selected_note="default USB identity"
  if [[ "$build_dir_lower" == *"master-tag"* ]]; then
    selected_conf="$master_tag_conf"
    selected_note="Master_Tag USB identity"
  elif [[ "$build_dir_lower" == *"master-anchor"* ]]; then
    selected_conf="$master_anchor_conf"
    selected_note="Master_Anchor USB identity"
  fi
  if [ ! -f "$selected_conf" ]; then
    echo "[error] missing B120 config: $selected_conf" >&2
    exit 2
  fi
  MASTER_CMAKE_ARGS="-DEXTRA_CONF_FILE=$selected_conf -Dhci_ipc_EXTRA_CONF_FILE=$selected_conf $MASTER_CMAKE_ARGS"
  echo "[build] B120 clock/USB policy: internal LFRC on CPUAPP+CPUNET, $selected_note"
fi

extra_args=()
if [ -n "$MASTER_CMAKE_ARGS" ]; then
  read -r -a extra_args <<<"$MASTER_CMAKE_ARGS"
fi

(cd "$NCS_ROOT" && "$WEST_BIN" build \
  --no-sysbuild \
  -b nrf5340dk/nrf5340/cpuapp \
  -s "$repo_root/apps/master_control" \
  -d "$build_dir_abs" \
  --pristine=always \
  -- \
  "${extra_args[@]}")

python3 "$repo_root/scripts/write_build_source.py" \
  --build-dir "$build_dir_abs" \
  --source "scripts/build_master_control_b120_m1.sh" \
  --command "$0 $*"

if [[ -f "$repo_root/apps/master_ota/generated/active_ota_payload.json" ]]; then
  cp "$repo_root/apps/master_ota/generated/active_ota_payload.json" \
    "$build_dir_abs/active_ota_payload.json"
fi

echo
echo "Built: $build_dir_abs"
echo "ELF:   $build_dir_abs/zephyr/zephyr.elf"
echo "HEX:   $build_dir_abs/zephyr/zephyr.hex"

"$repo_root/scripts/assert_b120_internal_osc_build.sh" "$build_dir_abs"
