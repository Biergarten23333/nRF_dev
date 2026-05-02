#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <build-dir-or-image-path>" >&2
  exit 2
fi

input="$1"

find_build_dir() {
  local p="$1"
  if [ -d "$p" ]; then
    printf '%s\n' "$p"
    return 0
  fi

  p="$(dirname "$p")"
  while [ "$p" != "." ] && [ "$p" != "/" ]; do
    if [ -f "$p/zephyr/.config" ] || [ -f "$p/hci_ipc/zephyr/.config" ]; then
      printf '%s\n' "$p"
      return 0
    fi
    p="$(dirname "$p")"
  done

  return 1
}

build_dir="$(find_build_dir "$input")" || {
  echo "[error] cannot locate Zephyr build dir from: $input" >&2
  exit 3
}

check_config() {
  local label="$1"
  local cfg="$2"

  if [ ! -f "$cfg" ]; then
    echo "[error] missing $label config: $cfg" >&2
    exit 4
  fi

  if ! grep -qx 'CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y' "$cfg"; then
    echo "[error] $label is not LFRC: missing CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y" >&2
    exit 5
  fi

  if ! grep -qx 'CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y' "$cfg"; then
    echo "[error] $label is not LFRC-calibrated: missing CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y" >&2
    exit 6
  fi

  if grep -qx 'CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL=y' "$cfg"; then
    echo "[error] $label uses external LFXO/XTAL; B120 must use internal LFRC" >&2
    exit 7
  fi

  if grep -qx 'CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH=y' "$cfg"; then
    echo "[error] $label uses LFSYNT; B120 must use internal LFRC" >&2
    exit 8
  fi
}

check_config "CPUAPP" "$build_dir/zephyr/.config"
check_config "CPUNET" "$build_dir/hci_ipc/zephyr/.config"

echo "[ok] B120 internal LFRC verified: $build_dir"
