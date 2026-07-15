#!/usr/bin/env bash
set -euo pipefail
#
# Flash ONE listener-fleet unit with the common freeze image via USB J-Link.
#   scripts/flash_listener_freeze.sh <SNR> <hex>
#
# HARD SAFETY:
#   - SNR must be one of the 9 fleet units (allowlist below). Any other SNR aborts.
#   - SNR 760185886 (盖格 / legacy Geiger air monitor) is EXPLICITLY denied.
#   - Full chip ERASE via JLink `recover` (CTRL-AP mass-erase + APPROTECT clear)
#     BEFORE program -> wipes any factory Decawave PANS firmware/bootloader/settings
#     so the result is purely the Alt-SS-TWR listener. recover + loadfile in
#     separate sessions; a fresh-session 0x0 read proves persistence.

SNR="${1:?usage: flash_listener_freeze.sh <SNR> <hex>}"
HEX="${2:?usage: flash_listener_freeze.sh <SNR> <hex>}"

# The 9 fleet units (2026-07-15 deployment). NOTHING else may be flashed.
ALLOWED="760184753 760184548 760181725 760184784 760184964 760184767 760184545 760181879 760186115"
GEIGER="760185886"

if [ "$SNR" = "$GEIGER" ]; then
  echo "[ABORT] SNR $SNR is the OFF-LIMITS 盖格 legacy Geiger air monitor — NEVER flash." >&2
  exit 9
fi
case " $ALLOWED " in
  *" $SNR "*) : ;;
  *) echo "[ABORT] SNR $SNR is not in the 9-unit listener fleet allowlist — refusing." >&2; exit 9 ;;
esac
[ -f "$HEX" ] || { echo "[ABORT] hex not found: $HEX" >&2; exit 2; }

DEV=NRF52832_XXAA
jl() { JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "$SNR" -device "$DEV" -if SWD -speed 4000 -CommanderScript "$1"; }

echo "[flash-listener] SNR=$SNR hex=$HEX (allowlisted; NOT the Geiger)"

R="$(mktemp)"; printf 'si SWD\nspeed 4000\nrecover\nexit\n' > "$R"
echo "[1/3] recover (full erase + unprotect; clears PANS)"
jl "$R"; rm -f "$R"

L="$(mktemp)"; printf 'si SWD\nspeed 4000\nr\nh\nloadfile %s\nr\ng\nexit\n' "$HEX" > "$L"
echo "[2/3] program common image"
jl "$L"; rm -f "$L"

V="$(mktemp)"; printf 'si SWD\nspeed 4000\nr\nh\nmem32 0x00000000 4\nexit\n' > "$V"
echo "[3/3] verify vector @ 0x0 (fresh session)"
vec="$(jl "$V" 2>/dev/null | grep -E '^00000000 = ' || true)"
rm -f "$V"
echo "  $vec"
if printf '%s' "$vec" | grep -qiE '^00000000 = FFFFFFFF'; then
  echo "[FAIL] app flash @ 0x0 is BLANK after program — flashing FAILED" >&2
  exit 11
fi
echo "[ok] flashed + verified SNR=$SNR"
