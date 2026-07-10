#!/usr/bin/env bash
# ============================================================================
# COLD-START THERMAL CHARACTERIZATION — experiment runner
#
# Drives a real cold-start: prompts the operator, marks t=0 at power-on,
# captures 90 min of listener serial (CFO warm-up), then auto-analyzes.
#
# CFO (cfo_ppm = rxtofs/ttcki*1e6) is the temperature proxy. The DW1000
# on-chip temp sensor is NOT read (reading it wedges the SPI clock domain).
# Spatial thermal imaging is done separately with the IR camera (manual).
# ============================================================================
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Output root defaults to this script's own dir (inside the repo) so runs are
# version-tracked alongside the scripts. Override with BIOSPUR_THERMAL_ROOT.
ROOT="${BIOSPUR_THERMAL_ROOT:-$HERE}"
DURATION_MIN="${DURATION_MIN:-90}"
EXPECT_PORTS=6

CAPTURE="$HERE/capture_coldstart.py"
ANALYZE="$HERE/analyze_coldstart.py"

echo "=== COLD-START THERMAL CHARACTERIZATION ==="
echo "Prerequisites:"
echo "  1. ALL UWB nodes have been powered off for 2+ hours"
echo "  2. IR camera (FR02E) on tripod aimed at one anchor board"
echo "  3. All 6 listener USB cables connected to this PC"
echo ""
echo "When you power on the nodes, the script will start recording."
echo "Take an IR photo every 30 seconds for the first 15 minutes."
echo ""

read -r -p "Press Enter when ready to begin..." _

# --- enumerate serial ports ---
echo ""
echo "Enumerating /dev/ttyACM* ..."
shopt -s nullglob
PORTS=(/dev/ttyACM*)
shopt -u nullglob
NPORTS=${#PORTS[@]}
echo "Found $NPORTS /dev/ttyACM* port(s):"
for p in "${PORTS[@]}"; do echo "    $p"; done

if [ "$NPORTS" -lt "$EXPECT_PORTS" ]; then
    echo ""
    echo "WARNING: expected $EXPECT_PORTS listener ports but found $NPORTS."
    echo "         (Note: this counts ALL ttyACM devices, including anchors/masters;"
    echo "          the capture script binds listeners by J-Link SNR, not by ttyACM.)"
    read -r -p "Continue anyway? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES) : ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

# --- t=0 ---
echo ""
echo ">>> Power on ALL nodes NOW, then press Enter <<<"
read -r _
T0_ISO="$(date -Iseconds)"
RUN_TS="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$ROOT/run_$RUN_TS"
echo "[t=0] $T0_ISO   run dir: $RUN_DIR"
echo ""

# --- capture (foreground); --no-prompt because t=0 was just marked here ---
python3 "$CAPTURE" --run-dir "$RUN_DIR" --duration-min "$DURATION_MIN" --no-prompt
CAP_RC=$?
echo ""
echo "[capture exited rc=$CAP_RC]"

# --- analyze ---
if [ -d "$RUN_DIR/raw" ]; then
    echo ""
    echo "Running analysis..."
    python3 "$ANALYZE" "$RUN_DIR"
    echo ""
    echo "=== DONE ==="
    echo "Report : $RUN_DIR/coldstart_report.txt"
    echo "Summary: $RUN_DIR/coldstart_summary.json"
    echo "Figures: $RUN_DIR/figures/cfo_vs_time.png"
    echo "         $RUN_DIR/figures/cfo_drift_rate.png"
else
    echo "ERROR: no raw/ directory at $RUN_DIR — capture produced no data." >&2
    exit 1
fi
