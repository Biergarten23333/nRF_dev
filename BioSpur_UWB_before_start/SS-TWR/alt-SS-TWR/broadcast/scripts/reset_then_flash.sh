#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <snr> <hex_path>" >&2
  exit 1
fi

snr="$1"
hex_path="$(realpath "$2")"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
protect_file="$repo_root/.protec/noflash960148546"
lock_file="${BIOSPUR_FLASH_LOCK_FILE:-/tmp/biospur_flash.lock}"
lock_wait_s="${BIOSPUR_FLASH_LOCK_WAIT_S:-120}"
allow_jlink_fallback="${BIOSPUR_FLASH_ALLOW_JLINK_FALLBACK:-1}"
jlink_device="${BIOSPUR_FLASH_JLINK_DEVICE:-}"
force_jlink="${BIOSPUR_FLASH_FORCE_JLINK:-0}"
# Default to JLinkExe path to avoid any possibility of SEGGER probe-selection GUI popups.
# Set to 1 if you explicitly want the nrfjprog path.
prefer_nrfjprog="${BIOSPUR_FLASH_PREFER_NRFJPROG:-0}"

if [ -z "$jlink_device" ]; then
  if [ "$snr" = "683234364" ]; then
    jlink_device="nRF52840_xxAA"
  elif [[ "$snr" == 9* ]] || [[ "$snr" == 10* ]]; then
    # BioSpur Master controllers are B120/nRF5340 boards. Keep Master_Anchor
    # and Master_Tag away from the legacy nRF52832 anchor default.
    jlink_device="NRF5340_XXAA_APP"
  else
    jlink_device="nRF52832_XXAA"
  fi
fi

# Prevent VSCode Nordic background hotplug scanner from racing J-Link access
# and triggering interactive probe-selection dialogs.
kill_jlink_racers() {
  # VSCode nRF Connect keeps respawning this in the background.
  pkill -f "nrfutil-device --json list --hotplug" >/dev/null 2>&1 || true
  # Clear any stuck backend workers; they can keep an exclusive handle to the probe.
  pkill -f "jlinkarm_nrf_worker_linux" >/dev/null 2>&1 || true
}

# Try a few times because the hotplug process can respawn immediately.
for _i in $(seq 1 10); do
  kill_jlink_racers
  if pgrep -f "nrfutil-device --json list --hotplug" >/dev/null 2>&1; then
    sleep 0.2
    continue
  fi
  break
done

if [ ! -f "$hex_path" ]; then
  echo "[error] image not found: $hex_path" >&2
  exit 2
fi

if [ "$snr" = "960148546" ] && [ -e "$protect_file" ]; then
  echo "[error] protected B120 SNR 960148546; refusing to flash because $protect_file exists" >&2
  exit 5
fi

exec 9>"$lock_file"
if ! flock -w "$lock_wait_s" 9; then
  echo "[error] could not acquire flash lock: $lock_file (wait=${lock_wait_s}s)" >&2
  exit 3
fi

run_nrf_cmd() {
  if [ "$force_jlink" = "1" ]; then
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
  if [ "$allow_jlink_fallback" != "1" ]; then
    echo "[error] JLink fallback disabled (BIOSPUR_FLASH_ALLOW_JLINK_FALLBACK=${allow_jlink_fallback})." >&2
    echo "[error] refusing fallback to avoid interactive probe selection popup." >&2
    return 55
  fi
  local jlink_cmd
  echo "[info] falling back to JLinkExe -SelectEmuBySN ${snr}" >&2
  if ! command -v JLinkExe >/dev/null 2>&1; then
    echo "[error] fallback required but JLinkExe not found" >&2
    return 1
  fi
  jlink_cmd="$(mktemp)"
  trap "rm -f '$jlink_cmd'" EXIT
  cat >"$jlink_cmd" <<EOF
Device $jlink_device
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

ensure_jlink_snr_present() {
  # Non-interactive SN check that avoids nrfjprog/SeggerBackend popups.
  local tmp rc
  tmp="$(mktemp)"
  rc=0
  printf "ShowEmuList USB\nExit\n" | JLinkExe -NoGui 1 >"$tmp" 2>&1 || rc=$?
  if [ "$rc" -ne 0 ]; then
    cat "$tmp" >&2
    rm -f "$tmp"
    return "$rc"
  fi
  if ! grep -Eq "Serial number: ${snr}(,|\\b)" "$tmp"; then
    echo "[error] target snr ${snr} not present in JLinkExe ShowEmuList USB output" >&2
    echo "[info] visible probes:" >&2
    grep -E "Serial number:" "$tmp" >&2 || true
    rm -f "$tmp"
    return 4
  fi
  rm -f "$tmp"
  return 0
}

ensure_jlink_snr_present

echo "[reset-before-flash] $snr"
echo "[flash] $snr $hex_path"
echo "[device] $jlink_device"
# One more kill right before we touch the probe.
kill_jlink_racers
# Use JLinkExe by default to guarantee: no GUI probe selection + SN pinned.
# If you truly need nrfjprog, set BIOSPUR_FLASH_PREFER_NRFJPROG=1.
if [ "$force_jlink" = "1" ] || [ "$prefer_nrfjprog" != "1" ]; then
  fallback_with_jlink
  echo "[reset-after-flash] $snr"
  exit 0
fi

if command -v nrfjprog >/dev/null 2>&1; then
  nrf_transport_error=0
  set +e
  kill_jlink_racers
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
    kill_jlink_racers
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
    kill_jlink_racers
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
  if [ "$force_jlink" = "1" ]; then
    echo "[error] BIOSPUR_FLASH_FORCE_JLINK=1 is forbidden in non-interactive flow." >&2
    exit 56
  fi
  echo "[error] nrfjprog missing; refusing JLink fallback in strict non-interactive mode." >&2
  exit 57
fi

echo "[reset-after-flash] $snr"
