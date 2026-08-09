#!/usr/bin/env bash
#
# One continuous probe hold: G1 -> G2 -> G3. Nothing here writes to the target.
#
# WHY ONE SCRIPT AND NOT THREE COMMANDS
# -------------------------------------
# The TC2030 is hand-held. Every gap between commands is time somebody spends
# pressing needles onto pads, so the three read-only gates run back to back and
# the operator is told once when to hold and once when to let go.
#
# HARD STOP AT G2. If run_jlink.sh exits 7 -- J-Link fell back to
# connect-under-reset -- this script stops there. It does not retry, it does not
# work around it, and it does not run G3. On a wedged board that exit code means
# the evidence is already gone, and the whole point of proving it on a healthy
# board first is to never find that out on the wedged one.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: bench_g1_g3.sh <logdir> [fingerprint-cache]}"
CACHE="${2:-}"
mkdir -p "$LOG"

banner() { echo; echo "================ $* ================"; }
verdict() { echo "GATE_RESULT $1 $2"; }

banner "G1  TARGET IDENTIFICATION  (read only)"
if ! "$HERE/g1_identify.sh" "$LOG"; then
	verdict G1 FAIL
	echo "[stop] target identification failed. Not proceeding." >&2
	exit 1
fi
verdict G1 PASS

banner "G2  NO-RESET PROOF  (connect + halt, witnessed on the master link)"
# The witness records the master's view of BSF6C53 for 6 s before the attach and
# 12 s after it, so the node's own uptime and reset_reason bracket the halt.
python3 "$HERE/link_witness.py" watch \
	--out "$LOG/g2_witness.jsonl" --settle 4 --before 6 --after 12 \
	--run "$HERE/g2_noreset.sh $LOG"
g2_rc=$?
echo "[info] G2 J-Link rc=$g2_rc"

if [ "$g2_rc" -eq 7 ]; then
	verdict G2 FAIL_RESET_ON_ATTACH
	# Still read the witness -- offline, on a file already on disk. This is
	# evidence collection, not a retry and not a second opinion that could
	# talk anyone past the stop: the gate is already failed either way. It
	# exists because "J-Link's log detector fired" and "the node actually
	# reset" are different claims, and the report has to say which one it is.
	echo "[stop] collecting the master-link witness for the report:" >&2
	python3 "$HERE/link_witness.py" report "$LOG/g2_witness.jsonl" \
		--node BSF6C53 | tee "$LOG/g2_witness.txt" || true
	echo "[stop] ####################################################" >&2
	echo "[stop] J-Link RESET THE TARGET ON ATTACH (exit 7)." >&2
	echo "[stop] Stopping here by instruction: no retry, no bypass," >&2
	echo "[stop] no G3, no G4. Report the J-Link configuration used." >&2
	echo "[stop] ####################################################" >&2
	exit 7
fi
if [ "$g2_rc" -ne 0 ]; then
	verdict G2 FAIL
	echo "[stop] G2 J-Link session failed (rc=$g2_rc). Not proceeding." >&2
	exit "$g2_rc"
fi

# The halt in attach_noreset.jlink is short; the link should not even drop, so
# a disconnect is NOT allowed here. G3's long halt is where that changes.
python3 "$HERE/link_witness.py" report "$LOG/g2_witness.jsonl" \
	--node BSF6C53 | tee "$LOG/g2_witness.txt"
w_rc=${PIPESTATUS[0]}
if [ "$w_rc" -ne 0 ]; then
	verdict G2 FAIL_LINK_WITNESS
	echo "[stop] the master link says the node did not survive the attach cleanly." >&2
	exit 1
fi
verdict G2 PASS

banner "G3  FLASH BACKUP + TIMED RAM DUMP + OFFLINE PARSE"
# The RAM dump halts the core for as long as the transfer takes, which is longer
# than the BLE supervision timeout, so a disconnect here is expected and is not
# a reset. node_ms is what tells the two apart.
python3 "$HERE/link_witness.py" watch \
	--out "$LOG/g3_witness.jsonl" --settle 4 --before 5 --after 12 \
	--run "$HERE/g3_dump.sh $LOG auto $CACHE"
g3_rc=$?
echo "[info] G3 rc=$g3_rc"
if [ "$g3_rc" -ne 0 ]; then
	verdict G3 FAIL
	echo "[stop] G3 failed (rc=$g3_rc). NOT running G4: programming a board" >&2
	echo "[stop] whose dump did not parse would destroy the only evidence" >&2
	echo "[stop] that the analysis path is broken." >&2
	exit "$g3_rc"
fi

halt_ms=$(awk -F= '/RAM_DUMP_SECONDS/{printf "%d", $2*1000}' "$LOG/g3_timing.txt" 2>/dev/null || echo 20000)
python3 "$HERE/link_witness.py" report "$LOG/g3_witness.jsonl" \
	--node BSF6C53 --allow-disconnect --halt-ms "${halt_ms:-20000}" \
	| tee "$LOG/g3_witness.txt"
w_rc=${PIPESTATUS[0]}
if [ "$w_rc" -ne 0 ]; then
	verdict G3 FAIL_LINK_WITNESS
	exit 1
fi
verdict G3 PASS

banner "G1-G3 COMPLETE -- PROBE MAY BE RELEASED"
echo "G4 is a separate hold. It is the only step that writes."
