#!/usr/bin/env bash
set -euo pipefail
#
# Freeze-grade dual-core nRF5340 (B120 master) FULL-ERASE + flash.
#
# Why this exists (vs jlink_flash_nrf5340_dualcore_by_snr.sh):
#   1. FULL ERASE that actually kills the settings NVS.  A stale persisted
#      `master_ctrl/mode`(AUTOPOS)+roster otherwise survives a plain loadfile
#      (loadfile only erases the sectors it programs, ~0x0..0x60080; the
#      settings partition near 0xF8000 is left intact) and the master re-grabs
#      wand tags on boot.
#   2. NO PROSE SOURCE.  The old flasher `source`d .protec/noflash960148546,
#      which is prose, not shell -> exit 127 under `set -e`.  Here we only TEST
#      the marker's existence.
#
# CRITICAL nRF5340 / JLink QUIRK (do not "simplify" this):
#   Plain `erase` (Erase Chip / NVMC ERASEALL) leaves the nRF5340 app core in a
#   state where subsequent debug flash programming DOES NOT PERSIST across a
#   power cycle: `loadfile` reports O.K. and an in-session `mem32 0x0` reads the
#   just-written data back correctly (from JLink's flash-download CACHE), but the
#   PHYSICAL flash stays blank -- a FRESH-session read (or a POR) shows 0x0 =
#   0xFFFFFFFF -> blank vector table -> HardFault/LOCKUP on boot (PC=0xEFFFFFFE,
#   IPSR=3).  The reliable fix is JLink `recover` (CTRL-AP mass-erase + clears
#   APPROTECT), which also wipes the settings NVS, THEN `loadfile` each core in
#   its OWN fresh session.  Verified on both B120 masters 2026-07-15.
#   Sanity rule proven the hard way: only a FRESH-SESSION physical read of 0x0
#   proves persistence; an in-session read after loadfile can be a cache hit.
#
# Also: after flashing, a JLink SYSRESETREQ ("debug reset") does NOT re-run the
# nRF5340 secure-boot ROM, so the app can come up with a mis-configured VTOR and
# fault.  A physical POWER CYCLE (cold POR) is required to validate the boot.
# This script leaves the device programmed + cleanly reset; power-cycle to boot.
#
# Usage:
#   scripts/flash_b120_master_freeze.sh <snr> <build_dir> [speed_khz]
# Expects inside <build_dir>:
#   - hci_ipc/zephyr/merged_CPUNET.hex   (net core)
#   - zephyr/merged.hex                  (app core, holds settings NVS)
#
# Protected Master_Anchor (SNR 960148546): requires
#   BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <snr> <build_dir> [speed_khz]" >&2
  exit 2
fi

SNR="$1"
BUILD_DIR="$2"
SPEED_KHZ="${3:-4000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Locate repo root by the .protec marker (existence test only -- never sourced).
REPO_ROOT=""
SEARCH_DIR="$SCRIPT_DIR"
while [ "$SEARCH_DIR" != "/" ]; do
  if [ -e "$SEARCH_DIR/.protec/noflash960148546" ]; then
    REPO_ROOT="$SEARCH_DIR"
    break
  fi
  SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi
PROTECT_FILE="$REPO_ROOT/.protec/noflash960148546"
ASSERT_SCRIPT="$SCRIPT_DIR/assert_b120_internal_osc_build.sh"

# Known B120 serials (override via env if the rig changes).
ANCHOR_SNR="${BIOSPUR_ANCHOR_SNR:-960148546}"
TAG_SNR="${BIOSPUR_TAG_SNR:-1050070698}"

NET_HEX="${BUILD_DIR}/hci_ipc/zephyr/merged_CPUNET.hex"
APP_HEX="${BUILD_DIR}/zephyr/merged.hex"
[ -f "$NET_HEX" ] || { echo "[error] CPUNET image not found: $NET_HEX" >&2; exit 3; }
[ -f "$APP_HEX" ] || { echo "[error] CPUAPP image not found: $APP_HEX" >&2; exit 4; }

BUILD_HINT="$(realpath "$BUILD_DIR" 2>/dev/null || printf '%s' "$BUILD_DIR")"
BUILD_LOWER="$(printf '%s' "$BUILD_HINT" | tr '[:upper:]' '[:lower:]')"

LOGICAL_ROLE="unknown"
if [ "$SNR" = "$ANCHOR_SNR" ]; then LOGICAL_ROLE="Master_Anchor"
elif [ "$SNR" = "$TAG_SNR" ]; then LOGICAL_ROLE="Master_Tag"; fi

echo "[flash-guard] SNR=${SNR} role=${LOGICAL_ROLE} build=${BUILD_HINT}"

# Role-vs-image mismatch guard (build-dir name is authoritative).
if [[ "$BUILD_LOWER" == *master-tag* ]] && [ "$LOGICAL_ROLE" = "Master_Anchor" ]; then
  echo "[error] role mismatch: refusing to flash a master-tag build onto Master_Anchor (${SNR})" >&2
  exit 8
fi
if [[ "$BUILD_LOWER" == *master-anchor* ]] && [ "$LOGICAL_ROLE" = "Master_Tag" ]; then
  echo "[error] role mismatch: refusing to flash a master-anchor build onto Master_Tag (${SNR})" >&2
  exit 9
fi

# Protected Master_Anchor gate.
if [ "$SNR" = "960148546" ] && [ -e "$PROTECT_FILE" ] && \
   [ "${BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH:-0}" != "1" ]; then
  echo "[error] protected B120 SNR 960148546; refusing (set BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 to override)" >&2
  exit 5
fi
if [ "$SNR" = "960148546" ] && [ -e "$PROTECT_FILE" ]; then
  echo "[flash-guard] OVERRIDE: BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 permits approved Master_Anchor flash"
fi

"$ASSERT_SCRIPT" "$BUILD_DIR"

command -v JLinkExe >/dev/null 2>&1 || { echo "[error] JLinkExe not found in PATH" >&2; exit 1; }

# run_jlink <device> <commands...> : one fresh JLinkExe session.
run_jlink() {
  local device="$1"; shift
  local f; f="$(mktemp -t jlink_frz_XXXXXX.jlink)"
  {
    echo "si 1"
    echo "speed ${SPEED_KHZ}"
    echo "device ${device}"
    echo "if SWD"
    echo "connect"
    for c in "$@"; do echo "$c"; done
    echo "exit"
  } >"$f"
  JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$f"
  local rc=$?
  rm -f "$f"
  return $rc
}

echo "tool=flash_b120_master_freeze snr=${SNR} build_dir=${BUILD_DIR} speed_khz=${SPEED_KHZ}"
echo "cpunet=${NET_HEX}"
echo "cpuapp=${APP_HEX}"

# recover = CTRL-AP mass-erase + unprotect (whole nRF5340, both cores). This is
# what makes debug programming PERSIST (plain `erase` does not; see quirk note).
# It also wipes the settings NVS. Then loadfile each core in its OWN session.
echo "[1/3] RECOVER (CTRL-AP mass-erase + unprotect; wipes settings NVS)"
run_jlink nrf5340_xxaa_app "recover"
echo "[2/3] NET  program"
run_jlink nrf5340_xxaa_net "loadfile ${NET_HEX}"
echo "[3/3] APP  program"
run_jlink nrf5340_xxaa_app "loadfile ${APP_HEX}"

# Verify the app vector table actually landed at 0x0 (guards against the
# same-session erase/loadfile skip regressing silently).
echo "[verify] app vector table @ 0x0"
vec="$(run_jlink nrf5340_xxaa_app "halt" "mem32 0x00000000 2" 2>/dev/null | grep -E '^00000000 = ' || true)"
echo "  ${vec:-<no read>}"
if printf '%s' "$vec" | grep -qiE '^00000000 = FFFFFFFF'; then
  echo "[error] app flash @ 0x0 is BLANK after program -- flashing FAILED (erase/loadfile skip?)" >&2
  exit 11
fi

# Clean reset (separate session; SYSRESETREQ). A physical POWER CYCLE is still
# required for a fully-clean nRF5340 cold boot (secure ROM + USB re-enumerate).
echo "[reset] clean SYSRESETREQ"
run_jlink nrf5340_xxaa_app "r" "g" || true

echo "[ok] full-erase dual-core freeze flash done: SNR=${SNR} role=${LOGICAL_ROLE}"
echo "[note] POWER-CYCLE this B120 to validate cold boot (debug reset alone will not boot the secure image cleanly)."
