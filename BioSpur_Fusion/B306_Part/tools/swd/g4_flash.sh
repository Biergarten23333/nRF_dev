#!/usr/bin/env bash
# G4 -- program the validation image. THE ONLY SCRIPT THAT WRITES.
#
# Refuses unless a flash backup from G3 exists: programming without a restore
# path is not a step, it is a gamble.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: g4_flash.sh <logdir> <merged.hex>}"
HEX="${2:?usage: g4_flash.sh <logdir> <merged.hex>}"
mkdir -p "$LOG"
[ -f "$HEX" ] || { echo "[error] no such hex: $HEX" >&2; exit 2; }

backup="$(ls -t "$LOG"/../*/BSF6C53_flash_backup.bin "$LOG"/BSF6C53_flash_backup.bin 2>/dev/null | head -1 || true)"
[ -n "$backup" ] || { echo "[error] no G3 flash backup found; refusing to write" >&2; exit 3; }
echo "[info] restore path: $backup"
echo "[info] programming:  $HEX ($(sha256sum "$HEX" | cut -c1-16)...)"

"$HERE/run_jlink.sh" flash_validation.jlink "$LOG" --allow-reset "MERGED_HEX_PATH=$HEX"

echo
echo "### independent readback, in a SEPARATE session"
len=$(python3 - "$HEX" <<'PY'
import sys
seg=0; hi=0
for line in open(sys.argv[1]):
    if line[7:9]=='04': seg=int(line[9:13],16)<<16
    elif line[7:9]=='00': hi=max(hi, (seg|int(line[3:7],16))+int(line[1:3],16))
print((hi + 0xFFF) & ~0xFFF)
PY
)
"$HERE/run_jlink.sh" verify_flash.jlink "$LOG" \
	"READBACK_PATH=$LOG/readback.bin" "READBACK_LEN=$len"
python3 - "$HEX" "$LOG/readback.bin" "$len" <<'PY'
import sys
hexf, binf, ln = sys.argv[1], sys.argv[2], int(sys.argv[3])
want = bytearray(b'\xff' * ln); seg = 0
for line in open(hexf):
    if line[7:9] == '04': seg = int(line[9:13], 16) << 16
    elif line[7:9] == '00':
        a = seg | int(line[3:7], 16); n = int(line[1:3], 16)
        want[a:a+n] = bytes.fromhex(line[9:9+2*n])
got = open(binf, 'rb').read()[:ln]
bad = [i for i in range(ln) if want[i] != 0xFF and want[i] != got[i]]
print(f"READBACK {'PASS' if not bad else 'FAIL'}: {ln} bytes, {len(bad)} mismatches")
raise SystemExit(1 if bad else 0)
PY
