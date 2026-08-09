#!/usr/bin/env bash
#
# Hand-held flash segment with a CONTACT GATE. One continuous hold, N attempts.
#
# THE GATE, AND WHY IT IS NOT CAUTION
# -----------------------------------
# On 2026-08-09 the same script, hex and probe produced two different outcomes:
# contact on attempt 1 -> flash in 9941 ms, readback PASS; contact on attempt 4
# after two connect-under-reset fallbacks -> "Failed to preserve target RAM @
# 0x20000000-0x2003FFFF", "Failed to prepare for programming", "SYSRESETREQ has
# confused core". The variable was the hand, not the toolchain.
#
# So marginal contact is never flashed on. It is measured, named, and retried.
#
# MEASURING IT TAKES TWO SESSIONS, WHICH IS THE BUG THIS FIXES
# ------------------------------------------------------------
# seg_flash.sh's report_attach() grepped 'InitTarget() end - Took' out of the
# id_target log. The REGEX WAS FINE. The file was wrong: id_target.jlink
# connects as a generic CORTEX-M4 (deliberately -- two pad sets), and
# InitTarget is an nRF52 device-script step that a generic connect never runs.
# So it printed InitTarget=n/a on every press and nobody could tell a good
# contact from a bad one. Sixth entry in the runbook's false-verdict table:
# a checker pointing at an artefact that structurally cannot answer it.
#
# id_target still runs first -- it is the cheap read-only check that the pads
# are the B306's and not the DWM1001C's. contact_probe.jlink then supplies the
# number.
#
# Usage: seg_flash_gated.sh <logdir> <merged.hex> [max_attempts]
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${1:?usage: seg_flash_gated.sh <logdir> <merged.hex> [max]}"
HEX="${2:?usage: seg_flash_gated.sh <logdir> <merged.hex> [max]}"
MAX="${3:-5}"
mkdir -p "$LOG"
[ -f "$HEX" ] || { echo "[error] no such hex: $HEX" >&2; exit 2; }

# THE BAND IS NECESSARY AND NOT SUFFICIENT -- read this before trusting it.
#
# Measured: 1.6-1.9 ms on all four good attaches during G1-G4, ~104 ms on the
# marginal ones. The two regimes are 50x apart, so the band catches a genuinely
# bad seat with no ambiguity.
#
# It did NOT catch tonight's failure. The session that died on "Failed to
# preserve target RAM" measured InitTarget=1.99 ms against the successful
# session's 1.79 ms. On n=2 those are the same number. A tighter band would be
# fitting noise, so the band stays wide and honest.
#
# What DID separate them: the successful press took contact on attempt 1; the
# failed press needed four, with two failed attaches before it. The predictive
# fact is not the winning measurement, it is that the hold needed retries at
# all -- a hand that has already slipped twice is a hand that is still
# marginal, and the attempt that finally attaches is not evidence otherwise.
#
# Hence RESEAT_CONFIRM below: once any attempt in this hold has failed, one
# clean measurement is no longer enough to flash on. Two consecutive clean
# measurements are required. That is the rule tonight's data actually supports.
GOOD_MIN=1.0
GOOD_MAX=4.0
RESEAT_CONFIRM=2

contact_ms=""      # set by measure_contact
contact_why=""

measure_contact() {
	contact_ms=""; contact_why=""
	if ! "$HERE/run_jlink.sh" id_target.jlink "$LOG" >/dev/null 2>&1; then
		contact_why="id_target attach FAILED (wrong pads, or no contact)"
		return 1
	fi
	"$HERE/run_jlink.sh" contact_probe.jlink "$LOG" >/dev/null 2>&1
	local f
	f="$(ls -t "$LOG"/contact_probe_*.log 2>/dev/null | head -1)"
	if [ -z "$f" ]; then
		contact_why="no contact_probe session log"
		return 1
	fi
	if grep -qi "connect under reset" "$f"; then
		contact_why="CONNECT-UNDER-RESET fallback fired"
		return 1
	fi
	contact_ms="$(grep -o 'InitTarget() end - Took [0-9.]*ms' "$f" |
		head -1 | grep -oE '[0-9.]+')"
	if [ -z "$contact_ms" ]; then
		contact_why="no InitTarget in the log (attach never got that far)"
		return 1
	fi
	if ! awk -v v="$contact_ms" -v lo="$GOOD_MIN" -v hi="$GOOD_MAX" \
		'BEGIN{exit !(v>=lo && v<=hi)}'; then
		contact_why="InitTarget=${contact_ms}ms OUTSIDE ${GOOD_MIN}-${GOOD_MAX}ms"
		return 1
	fi
	return 0
}

echo "=== FLASH r7-val, held probe, up to $MAX attempts ==="
n=0; had_failure=0; clean_run=0
while [ "$n" -lt "$MAX" ]; do
	n=$((n+1))
	if ! measure_contact; then
		echo "  [$n/$MAX] NO FLASH -- $contact_why"
		had_failure=1; clean_run=0
		# No explicit pause: the two read-only sessions above take ~1-2 s
		# each, which is the spacing. Re-seat during that, hand stays down.
		continue
	fi
	clean_run=$((clean_run+1))
	need=1
	[ "$had_failure" = "1" ] && need="$RESEAT_CONFIRM"
	if [ "$clean_run" -lt "$need" ]; then
		echo "  [$n/$MAX] contact OK (${contact_ms}ms) but this hold has already"\
		     "slipped -- need $need clean in a row, have $clean_run. Re-seat."
		continue
	fi
	echo "  [$n/$MAX] contact OK, InitTarget=${contact_ms}ms, clean_run=$clean_run -- flashing"
	if "$HERE/g4_flash.sh" "$LOG" "$HEX" >"$LOG/flash_attempt_$n.out" 2>&1; then
		grep -E "READBACK|elapsed_ms" "$LOG/flash_attempt_$n.out" | sed 's/^/      /'
		echo "  [$n/$MAX] FLASH OK -- RELEASE THE PROBE"
		echo "CONTACT_ATTEMPTS=$n CONTACT_MS=$contact_ms RESULT=PASS"
		exit 0
	fi
	echo "  [$n/$MAX] flash failed: $(grep -m1 -oE '\*\*\*\*\*\* Error: .*' \
		"$LOG"/flash_validation_*.log 2>/dev/null | tail -1)"
done

echo "  $MAX attempts exhausted -- RELEASE THE PROBE"
echo "CONTACT_ATTEMPTS=$n RESULT=FAIL"
exit 8
