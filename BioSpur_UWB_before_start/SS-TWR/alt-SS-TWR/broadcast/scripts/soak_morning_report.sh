#!/usr/bin/env bash
# Morning: turn the overnight soak into the calibrated LOS baseline + continuity/health summary.
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
BASE=${1:-$(ls -dt SS-TWR/alt-SS-TWR/broadcast/logs/overnight_soak_* 2>/dev/null | head -1)}
echo "=== SOAK $BASE ==="
echo "--- continuity (per-tag rows/dropouts per chunk = battery proxy; watch for a wand -> ~0 rows) ---"
grep -E "CHUNK|rows=|M1 anchor|good_frames|LCIR_captures|SILENT|COMPLETE|STOP" "$BASE/soak_continuity.log" 2>/dev/null
echo "--- build per-tag->B LOS baseline from ALL L-B chunk raw.logs ---"
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/cir_notch_detector.py baseline \
  --raw "$BASE"/chunk*/LB/*/raw.log --out "$BASE/lb_los_baseline.json"
echo
echo "--- PER-LINK background census (all tag->anchor; static furniture = baseline) + time-vary watch ---"
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/soak_link_census.py --base "$BASE"
echo
echo "LIVE notch (tomorrow, in your terminal):"
echo "  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/cir_notch_detector.py live \\"
echo "    --baseline $BASE/lb_los_baseline.json \\"
echo "    --port /dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00"
