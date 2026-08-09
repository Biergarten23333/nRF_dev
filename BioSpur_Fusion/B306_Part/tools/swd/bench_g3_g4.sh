#!/usr/bin/env bash
#
# Second and last probe hold: G3 (read only) then G4 (the only step that writes).
#
# G4 runs ONLY if G3 passed. Programming a board whose RAM dump did not parse
# would erase the one piece of evidence that the analysis path is broken, and
# the analysis path is the entire deliverable of this step.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: bench_g3_g4.sh <logdir> <merged.hex> [fingerprint-cache]}"
HEX="${2:?usage: bench_g3_g4.sh <logdir> <merged.hex> [fingerprint-cache]}"
CACHE="${3:-}"
mkdir -p "$LOG"

banner() { echo; echo "================ $* ================"; }
verdict() { echo "GATE_RESULT $1 $2"; }

banner "G3  FLASH BACKUP + TIMED RAM DUMP + OFFLINE PARSE"
python3 "$HERE/link_witness.py" watch \
	--out "$LOG/g3_witness.jsonl" --settle 4 --before 5 --after 12 \
	--run "$HERE/g3_dump.sh $LOG auto $CACHE"
g3_rc=$?
echo "[info] G3 rc=$g3_rc"
if [ "$g3_rc" -ne 0 ]; then
	verdict G3 FAIL
	echo "[stop] G3 failed (rc=$g3_rc). NOT running G4." >&2
	exit "$g3_rc"
fi

halt_ms=$(awk -F= '/RAM_DUMP_SECONDS/{printf "%d", $2*1000}' "$LOG/g3_timing.txt" 2>/dev/null)
python3 "$HERE/link_witness.py" report "$LOG/g3_witness.jsonl" \
	--node BSF6C53 --allow-disconnect --halt-ms "${halt_ms:-20000}" \
	| tee "$LOG/g3_witness.txt"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
	verdict G3 FAIL_LINK_WITNESS
	echo "[stop] the node did not survive the dump. NOT running G4." >&2
	exit 1
fi
verdict G3 PASS

banner "G4  PROGRAM THE VALIDATION IMAGE  (erases and resets, deliberately)"
# The witness here is NOT a no-reset check -- G4 resets on purpose. It records
# the master's view so the reboot, the reconnect and the node's fresh uptime are
# on the record, which is what shows the board came back on the new image.
python3 "$HERE/link_witness.py" watch \
	--out "$LOG/g4_witness.jsonl" --settle 4 --before 5 --after 25 \
	--run "$HERE/g4_flash.sh $LOG $HEX"
g4_rc=$?
echo "[info] G4 rc=$g4_rc"
if [ "$g4_rc" -ne 0 ]; then
	verdict G4 FAIL
	echo "[stop] G4 failed (rc=$g4_rc). The board may be half-programmed:" >&2
	echo "[stop] the restore path is $LOG/BSF6C53_flash_backup.bin" >&2
	exit "$g4_rc"
fi
verdict G4 PASS

banner "G1-G4 COMPLETE -- PROBE MAY BE RELEASED"
echo "Do not touch the board: the 15-minute quiet soak starts now."
