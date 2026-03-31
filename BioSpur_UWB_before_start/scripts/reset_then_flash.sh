#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <snr> <hex_path>" >&2
  exit 1
fi

snr="$1"
hex_path="$(realpath "$2")"
lock_file="${BIOSPUR_FLASH_LOCK_FILE:-/tmp/biospur_flash.lock}"
lock_wait_s="${BIOSPUR_FLASH_LOCK_WAIT_S:-120}"

# Prevent VSCode Nordic background hotplug scanner from racing J-Link access
# and triggering interactive probe-selection dialogs.
pkill -f "nrfutil-device --json list --hotplug" >/dev/null 2>&1 || true
sleep 0.2

if [ ! -f "$hex_path" ]; then
  echo "[error] image not found: $hex_path" >&2
  exit 2
fi

exec 9>"$lock_file"
if ! flock -w "$lock_wait_s" 9; then
  echo "[error] could not acquire flash lock: $lock_file (wait=${lock_wait_s}s)" >&2
  exit 3
fi

run_nrf_cmd() {
  if [ "${BIOSPUR_FLASH_FORCE_JLINK:-1}" = "1" ]; then
    return 43
  fi
  local rc tmp
  tmp="$(mktemp)"
  if "$@" >"$tmp" 2>&1; then
    cat "$tmp"
    rm -f "$tmp"
    return 0
  fi
  rc=$?
  cat "$tmp" >&2
  if grep -Eq "JLinkARM\\.dll reported error -256|does not match the expected serial number|There is no debugger connected|The operation timed out" "$tmp"; then
    rm -f "$tmp"
    return 42
  fi
  rm -f "$tmp"
  return "$rc"
}

fallback_with_jlink() {
  local jlink_cmd
  echo "[info] falling back to JLinkExe -SelectEmuBySN ${snr}" >&2
  if ! command -v JLinkExe >/dev/null 2>&1; then
    echo "[error] fallback required but JLinkExe not found" >&2
    return 1
  fi
  jlink_cmd="$(mktemp)"
  trap "rm -f '$jlink_cmd'" EXIT
  cat >"$jlink_cmd" <<EOF
Device nRF52832_XXAA
SelectInterface SWD
Speed 4000
Connect
LoadFile $hex_path
Reset
Go
Exit
EOF
  JLinkExe -NoGui 1 -SelectEmuBySN "$snr" -CommanderScript "$jlink_cmd"
}

if [ "${BIOSPUR_FLASH_FORCE_JLINK:-1}" != "1" ] && command -v nrfjprog >/dev/null 2>&1; then
  ids="$(run_nrf_cmd nrfjprog --ids || true)"
  if ! printf '%s\n' "$ids" | grep -qx "$snr"; then
    echo "[error] target snr ${snr} not present in nrfjprog --ids list" >&2
    echo "[info] visible probes:" >&2
    printf '%s\n' "$ids" >&2
    exit 4
  fi
fi

echo "[reset-before-flash] $snr"
echo "[flash] $snr $hex_path"
# Use nrfjprog as the primary path to guarantee non-interactive SN-pinned flashing.
# This avoids SEGGER probe-selection popups in multi-probe environments.
if [ "${BIOSPUR_FLASH_FORCE_JLINK:-1}" != "1" ] && command -v nrfjprog >/dev/null 2>&1; then
  nrf_transport_error=0
  set +e
  run_nrf_cmd nrfjprog --reset -f NRF52 --snr "$snr"
  rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    if [ "$rc" -eq 42 ]; then
      nrf_transport_error=1
    else
      exit "$rc"
    fi
  fi
  if [ "$nrf_transport_error" -eq 0 ]; then
    set +e
    run_nrf_cmd nrfjprog --program "$hex_path" --sectorerase --verify -f NRF52 --snr "$snr"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      if [ "$rc" -eq 42 ]; then
        nrf_transport_error=1
      else
        exit "$rc"
      fi
    fi
  fi
  if [ "$nrf_transport_error" -eq 0 ]; then
    set +e
    run_nrf_cmd nrfjprog --reset -f NRF52 --snr "$snr"
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
      if [ "$rc" -eq 42 ]; then
        nrf_transport_error=1
      else
        exit "$rc"
      fi
    fi
  fi
  if [ "$nrf_transport_error" -ne 0 ]; then
    fallback_with_jlink
  fi
else
  if [ "${BIOSPUR_FLASH_FORCE_JLINK:-1}" = "1" ]; then
    echo "[info] BIOSPUR_FLASH_FORCE_JLINK=1, using JLink SN-pinned path" >&2
  else
    echo "[warn] nrfjprog not found, using JLink fallback path directly" >&2
  fi
  fallback_with_jlink
fi

echo "[reset-after-flash] $snr"
