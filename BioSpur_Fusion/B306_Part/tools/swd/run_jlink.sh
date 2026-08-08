#!/usr/bin/env bash
#
# The ONLY way a J-Link command reaches BSF6C53.
#
# WHY A WRAPPER AND NOT JUST JLinkExe
# -----------------------------------
# 1. A bare `JLinkExe` with more than one probe attached opens a GUI probe
#    picker and blocks. Every call here passes -NoGui 1 -ExitOnError 1 and
#    -SelectEmuBySN, always, with no path that omits them.
# 2. A reset destroys .noinit, the trajectory ring and the wedge state -- on a
#    wedged board that IS the evidence. So this script greps the command file
#    for reset commands and REFUSES to run one unless the caller has passed
#    --allow-reset, which only the G4 flashing step does.
# 3. Placeholders (RAMDUMP_PATH and friends) are substituted here, into a copy,
#    so the committed script text is exactly what was reviewed and the run
#    keeps a rendered copy of exactly what was executed.
#
# Usage:
#   run_jlink.sh <script.jlink> <logdir> [--allow-reset] [KEY=VALUE ...]
set -euo pipefail

readonly EXPECTED_SNR="1050070698"          # nRF5340 DK onboard J-Link OB
readonly SPEED_KHZ=4000
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$#" -ge 2 ] || { echo "usage: $0 <script.jlink> <logdir> [--allow-reset] [K=V ...]" >&2; exit 2; }
SCRIPT="$1"; LOGDIR="$2"; shift 2
[ -f "$SCRIPT" ] || SCRIPT="$HERE/$SCRIPT"
[ -f "$SCRIPT" ] || { echo "[error] no such script: $1" >&2; exit 2; }

ALLOW_RESET=0
SUBS=()
for arg in "$@"; do
	case "$arg" in
	--allow-reset) ALLOW_RESET=1 ;;
	*=*) SUBS+=("$arg") ;;
	*) echo "[error] unrecognised argument: $arg" >&2; exit 2 ;;
	esac
done

command -v JLinkExe >/dev/null 2>&1 || { echo "[error] JLinkExe not found" >&2; exit 3; }
mkdir -p "$LOGDIR"

label="$(basename "$SCRIPT" .jlink)"
stamp="$(date +%Y%m%dT%H%M%S)"
rendered="$LOGDIR/${label}_${stamp}.jlink"
output="$LOGDIR/${label}_${stamp}.log"

cp "$SCRIPT" "$rendered"
for sub in "${SUBS[@]}"; do
	key="${sub%%=*}"; val="${sub#*=}"
	# '|' as the sed delimiter: every substitution here is a filesystem path.
	sed -i "s|${key}|${val}|g" "$rendered"
done

# Any placeholder left unsubstituted would be passed to J-Link as a literal
# filename, and savebin would happily create a file called RAMDUMP_PATH in the
# working directory. Catch it here rather than discovering it afterwards.
if grep -nE '\b(RAMDUMP_PATH|FLASHDUMP_PATH|MERGED_HEX_PATH|READBACK_PATH|READBACK_LEN)\b' "$rendered"; then
	echo "[error] unsubstituted placeholder(s) above; refusing to run" >&2
	exit 4
fi

# THE GATE. Strip comments first so the word "reset" in prose cannot trip it and,
# more importantly, so a real reset command cannot hide behind one.
stripped="$(sed -e 's://.*$::' -e 's:^[[:space:]]*::' "$rendered")"
if printf '%s\n' "$stripped" | grep -qiE '^(r|rx[[:space:]]+[0-9]+|reset|resettarget|rsettype([[:space:]]|$))'; then
	if [ "$ALLOW_RESET" -ne 1 ]; then
		echo "[error] $label contains a RESET command and --allow-reset was not given." >&2
		echo "[error] A reset destroys .noinit, the trajectory ring and the wedge state." >&2
		echo "[error] If this is the G4 flashing step, pass --allow-reset explicitly." >&2
		exit 5
	fi
	echo "[warn] $label WILL RESET THE TARGET (--allow-reset given)"
fi

echo "[info] probe SNR $EXPECTED_SNR, ${SPEED_KHZ} kHz, script $label"
echo "[info] rendered: $rendered"

start_ns=$(date +%s%N)
set +e
JLinkExe -NoGui 1 -ExitOnError 1 \
	-SelectEmuBySN "$EXPECTED_SNR" \
	-SettingsFile "$HERE/jlink_settings.ini" \
	-CommanderScript "$rendered" 2>&1 | tee "$output"
rc=${PIPESTATUS[0]}
set -e
end_ns=$(date +%s%N)
elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))

echo "[info] elapsed_ms=$elapsed_ms rc=$rc" | tee -a "$output"

# ---------------------------------------------------------------------------
# THE RESET-FALLBACK DETECTOR.
#
# Found while testing this wrapper with no target attached, which is exactly
# where you want to find it. J-Link V9.24a prints:
#
#     Connecting to target via SWD
#     Failed to attach to CPU. Trying connect under reset.
#
# It does that AUTOMATICALLY when the normal attach fails, and it does it
# regardless of `ConnectUnderReset = 0` in the settings file -- that setting
# controls the deliberate mode, not the fallback. There is no command-line
# option to disable the fallback either; -ConnectUnderReset, -NoConnectUnderReset,
# -CUR and -AutoConnectUnderReset are all rejected as unknown by this version.
#
# So it cannot be PREVENTED, only DETECTED -- and detecting it is worth a great
# deal. On a wedged board a silent connect-under-reset destroys the corpse and
# then hands back a clean-looking session, so the run would report "no corpse
# present" and we would believe the detector had failed to fire. Failing the
# session instead means the report says "the evidence was destroyed on attach",
# which is a completely different and recoverable conclusion.
#
# In practice the fallback only fires when the first attach fails, and on a
# board that is wedged-but-running the first attach succeeds. G2 measures that;
# this is the backstop for when it does not.
if grep -qiE 'connect under reset|Reset: Halt core|Resetting target' "$output"; then
	echo "[error] ############################################################" >&2
	echo "[error] J-Link FELL BACK TO CONNECT-UNDER-RESET in session '$label'." >&2
	echo "[error] If this was a wedged board, .noinit / the ring / the corpse" >&2
	echo "[error] ARE GONE. Do not report this run as 'no corpse present'." >&2
	echo "[error] ############################################################" >&2
	if [ "$ALLOW_RESET" -ne 1 ]; then
		exit 7
	fi
fi

# J-Link exits 0 on some failures it only reports in prose, so both are checked.
if [ "$rc" -ne 0 ] || grep -qiE \
	'Cannot connect|Could not connect|FATAL ERROR|Command failed|Unknown command|Could not find core|ERROR: ' \
	"$output"; then
	echo "[error] J-Link session failed closed: label=$label rc=$rc" >&2
	exit 6
fi
echo "SWD_OK label=$label elapsed_ms=$elapsed_ms log=$output"
