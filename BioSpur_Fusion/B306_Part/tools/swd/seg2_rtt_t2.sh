#!/usr/bin/env bash
#
# Segment 2, hand-held: F3b sample -> RTT capture across a T2 re-run. NO FLASH.
#
# WHY THE FLASH IS NOT HERE ANY MORE
# ----------------------------------
# It used to be, and on 2026-08-09 it failed: after pylink's RTT session closed,
# JLinkExe could not get a clean reset, fell back to `Reset: Using fallback:
# VECTRESET` -- which does not reset peripherals, so NVMC state persisted -- and
# the programming step died on `****** Error: Failed to erase sectors.` after
# 1651 ms against the 9897 ms a real flash takes. The readback never ran, and the
# board silently stayed on the old image.
#
# Two tools sharing one probe inside one contact is the whole problem. Flashing
# now lives in seg_flash.sh, which is the first and only thing in its own
# session, exactly as it was on the segment where it worked.
#
# ORDER MATTERS AND IS NOT MINE. F3b samples the hci_rx pool BEFORE anything can
# reset the board, because the open question -- row [1], initialised, free,
# ref=168 -- only exists on a pool that has been used. A reset zeroes that
# history, and T2 reset the board last time.
#
# THE J-LINK IS EXCLUSIVE. pylink's RTT session and JLinkExe cannot hold the
# probe at once, so the sequence opens and closes it in turn: dump (JLinkExe) ->
# close -> RTT (pylink) -> close -> flash (JLinkExe). The operator's hand stays
# down throughout; only the software session changes owner.
#
# RTT IS OPENED WITH reset_target=False. A reset here would destroy the very
# thing being captured.
#
# Usage: seg2_rtt_t2.sh <logdir> [t2_attempts]
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
LOG="${1:?usage: seg2_rtt_t2.sh <logdir> [attempts]}"
ATTEMPTS="${2:-1}"
mkdir -p "$LOG"

echo "=== 1/3 CONTACT CHECK (retries allowed; not fatal) ==="
n=0
until [ "$n" -ge 5 ]; do
	n=$((n+1))
	"$HERE/g1_identify.sh" "$LOG" >/dev/null 2>&1 && { echo "  contact OK on attempt $n"; break; }
	echo "  attempt $n failed"
	[ "$n" -ge 5 ] && { echo "[error] contact failed 5x" >&2; exit 8; }
done
f="$(ls -t "$LOG"/id_target_*.log | head -1)"
echo "  InitTarget=$(grep -o 'InitTarget() end - Took [0-9.]*ms' "$f" | head -1 | grep -o '[0-9.]*ms' || echo n/a) attempts=$n"

echo
echo "=== 2/3 F3b: RAM sample of the WELL-USED pool, before any reset ==="
"$HERE/run_jlink.sh" dump_ram.jlink "$LOG" \
	"RAMDUMP_PATH=$ROOT/$LOG/BSF6C53_f3b_ram.bin" 2>&1 | grep -E "SWD_OK|error"
sha256sum "$LOG/BSF6C53_f3b_ram.bin" | tee "$LOG/BSF6C53_f3b_ram.bin.sha256"

echo
echo "=== 3/3 RTT capture across a T2 re-run (max $ATTEMPTS attempt(s)) ==="
python3 "$HERE/rtt_t2_capture.py" --outdir "$LOG" --attempts "$ATTEMPTS"
t2rc=$?
echo "  t2_capture rc=$t2rc"

echo "SEGMENT2_RC=$t2rc"
echo
echo "=== PROBE MAY BE RELEASED ==="
echo "Flashing is a SEPARATE segment: tools/swd/seg_flash.sh <logdir> <hex>"
exit "$t2rc"
