#!/usr/bin/env bash
# G3 -- flash backup, then the timed RAM-dump rehearsal, then offline parsing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: g3_dump.sh <logdir> [elf|auto] [fingerprint-cache]}"
ELF="${2:-auto}"
CACHE="${3:-}"
mkdir -p "$LOG"

echo "### 1/3  FULL FLASH BACKUP (before anything is written)"
# Reuse an existing backup rather than spending another 7 s of somebody holding
# needles on pads. Safe because nothing between the two runs writes to flash:
# G4 is the only step that does, and it refuses to run until this file exists.
if [ -s "$LOG/BSF6C53_flash_backup.bin" ] && \
   [ "$(stat -c%s "$LOG/BSF6C53_flash_backup.bin")" = "1048576" ]; then
	echo "[info] reusing the backup already in $LOG (1 MiB, taken before any write)"
	sha256sum "$LOG/BSF6C53_flash_backup.bin" | tee "$LOG/BSF6C53_flash_backup.bin.sha256"
else
	"$HERE/run_jlink.sh" dump_flash.jlink "$LOG" "FLASHDUMP_PATH=$LOG/BSF6C53_flash_backup.bin"
	sha256sum "$LOG/BSF6C53_flash_backup.bin" | tee "$LOG/BSF6C53_flash_backup.bin.sha256"
fi

# The ELF must be the one the board is RUNNING, not the one we are about to
# flash. Parsing against the wrong ELF does not fail loudly -- it walks
# _kernel.threads from the wrong address and prints confident nonsense. The
# backup we just took settles it, so `auto` is the default.
if [ "$ELF" = "auto" ]; then
	echo
	echo "### 1b/3  IDENTIFY THE RUNNING IMAGE (offline, from the backup)"
	"$HERE/identify_flash_image.py" "$LOG/BSF6C53_flash_backup.bin" \
		--builds "$(cd "$HERE/../.." && pwd)/builds" \
		${CACHE:+--cache "$CACHE"} \
		--json "$LOG/g3_image_id.json" | tee "$LOG/g3_image_id.txt"
	ELF="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["elf"])' \
		"$LOG/g3_image_id.json")"
	echo "[info] parsing against $ELF"
fi
[ -f "$ELF" ] || { echo "[error] no such ELF: $ELF" >&2; exit 2; }

echo
echo "### 1c/3  CONTACT CHECK before committing the dump"
# Measured on this bench: a hand-held TC2030 fails to attach often enough to
# matter (2 of 6 attempts), and VTref is NOT the tell -- it read 3.300 V in both
# failures. What separates them is that a bad attach makes J-Link fall back to
# connect-under-reset, which on a wedged board is the corpse gone.
#
# This does not remove that hazard; nothing can, since the fallback is
# undisableable. It makes the FIRST attach the cheap read-only one that the
# runbook already puts at step 1, so bad contact is discovered before the dump
# is spent rather than by losing it. id_target connects as a generic CORTEX-M4
# and runs no device InitTarget at all, so it is also the lightest attach here.
if ! "$HERE/g1_identify.sh" "$LOG" >/dev/null 2>&1; then
	echo "[error] contact check FAILED -- not attempting the RAM dump." >&2
	echo "[error] Re-seat the probe and run again. Attempting the dump on a" >&2
	echo "[error] marginal contact is how a wedged board's corpse gets lost." >&2
	exit 8
fi
echo "[info] contact check OK"

echo
echo "### 2/3  RAM DUMP -- the exact wedged-board sequence. TIMING THIS."
start=$(date +%s%N)
"$HERE/run_jlink.sh" dump_ram.jlink "$LOG" "RAMDUMP_PATH=$LOG/BSF6C53_ram_rehearsal.bin"
end=$(date +%s%N)
secs=$(awk "BEGIN{printf \"%.1f\", ($end-$start)/1000000000}")
sha256sum "$LOG/BSF6C53_ram_rehearsal.bin" | tee "$LOG/BSF6C53_ram_rehearsal.bin.sha256"
echo "RAM_DUMP_SECONDS=$secs" | tee "$LOG/g3_timing.txt"
echo ">>> That is how long the operator must hold the probe. <<<"

echo
echo "### 3/3  OFFLINE PARSE -- proves the analysis path works end to end"
"$HERE/parse_ram_dump.py" "$LOG/BSF6C53_ram_rehearsal.bin" --elf "$ELF" \
	--expect-healthy | tee "$LOG/BSF6C53_ram_rehearsal.threads.txt"
