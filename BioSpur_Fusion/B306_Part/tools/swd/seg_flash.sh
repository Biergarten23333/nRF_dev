#!/usr/bin/env bash
#
# One hand-held probe segment: contact check (with retries), flash, readback.
#
# WHY RETRIES ARE ALLOWED HERE AND NOWHERE ELSE
# ---------------------------------------------
# g3_dump.sh refuses to continue after a failed contact check, because on a
# wedged board a failed attach triggers the undisableable connect-under-reset
# and the corpse is gone -- one attempt is all you get.
#
# A FLASHING segment has no such state: the board carries nothing irreproducible,
# every image is rebuildable from builds/, and a failed attach costs nothing but
# a moment. So this driver retries instead of aborting, and the safety script is
# left exactly as it is for the rounds that need it.
#
# Every attempt is recorded -- attempt number, InitTarget duration, outcome --
# because "it worked on the third try" and "it worked" are different facts about
# a hand-held probe, and only the first one tells you whether to get a clamp.
#
# Usage: seg_flash.sh <logdir> <merged.hex> [--pre-dump] [max_attempts]
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: seg_flash.sh <logdir> <merged.hex> [--pre-dump] [max]}"
HEX="${2:?usage: seg_flash.sh <logdir> <merged.hex> [--pre-dump] [max]}"
PRE_DUMP=0
MAX=5
for a in "${@:3}"; do
	case "$a" in
	--pre-dump) PRE_DUMP=1 ;;
	*[0-9]*) MAX="$a" ;;
	esac
done
mkdir -p "$LOG"
[ -f "$HEX" ] || { echo "[error] no such hex: $HEX" >&2; exit 2; }

report_attach() {   # $1 = label
	local f
	f="$(ls -t "$LOG"/$1_*.log 2>/dev/null | head -1)"
	[ -n "$f" ] || { echo "      (no session log)"; return; }
	local it ok
	it="$(grep -o 'InitTarget() end - Took [0-9.]*ms' "$f" | head -1 | grep -o '[0-9.]*ms')"
	grep -q 'Cortex-M4 identified' "$f" && ok=OK || ok=FAILED
	echo "      attach=$ok InitTarget=${it:-n/a} fallback=$(grep -c 'connect under reset' "$f")"
}

echo "=== CONTACT CHECK (up to $MAX attempts; a failure here is not fatal) ==="
attempts=0
until [ "$attempts" -ge "$MAX" ]; do
	attempts=$((attempts+1))
	echo "  attempt $attempts/$MAX"
	if "$HERE/g1_identify.sh" "$LOG" >/dev/null 2>&1; then
		report_attach id_target
		echo "  CONTACT OK on attempt $attempts"
		break
	fi
	report_attach id_target
	[ "$attempts" -ge "$MAX" ] && { echo "[error] contact failed $MAX times" >&2; exit 8; }
done
echo "CONTACT_ATTEMPTS=$attempts"

if [ "$PRE_DUMP" = "1" ]; then
	echo
	echo "=== F3b: RAM snapshot BEFORE the flash (the pool as this image left it) ==="
	"$HERE/run_jlink.sh" dump_ram.jlink "$LOG" \
		"RAMDUMP_PATH=$PWD/$LOG/BSF6C53_pre_flash_ram.bin" 2>&1 | grep -E "SWD_OK|error"
	sha256sum "$LOG/BSF6C53_pre_flash_ram.bin" | tee "$LOG/BSF6C53_pre_flash_ram.bin.sha256"
fi

echo
echo "=== FLASH + INDEPENDENT READBACK ==="
"$HERE/g4_flash.sh" "$LOG" "$HEX" 2>&1 | grep -E "info|READBACK|SWD_OK|error|warn"
rc=${PIPESTATUS[0]}
echo "SEGMENT_RC=$rc"
echo
echo "=== PROBE MAY BE RELEASED ==="
exit "$rc"
