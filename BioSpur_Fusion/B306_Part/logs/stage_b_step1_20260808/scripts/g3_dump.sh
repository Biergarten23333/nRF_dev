#!/usr/bin/env bash
# G3 -- flash backup, then the timed RAM-dump rehearsal, then offline parsing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: g3_dump.sh <logdir> <elf>}"
ELF="${2:?usage: g3_dump.sh <logdir> <elf>}"
mkdir -p "$LOG"

echo "### 1/3  FULL FLASH BACKUP (before anything is written)"
"$HERE/run_jlink.sh" dump_flash.jlink "$LOG" "FLASHDUMP_PATH=$LOG/BSF6C53_flash_backup.bin"
sha256sum "$LOG/BSF6C53_flash_backup.bin" | tee "$LOG/BSF6C53_flash_backup.bin.sha256"

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
