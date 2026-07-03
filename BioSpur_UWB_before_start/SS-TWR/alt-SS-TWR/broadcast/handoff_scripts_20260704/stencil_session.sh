#!/usr/bin/env bash
# Attended stencil session (2026-07-04): A3 steel PCB stencil, static occluder tests.
# ONE continuous capture (all 6 listeners + ranging keeper); YOU move the stencil and
# log markers. Segments (suggested, ~35 min total):
#   S0  5 min  clean room, stencil far away, you seated still at desk
#   S1  8 min  stencil suspended IN the LOS tube of Wand-A(BSCCF4) -> Anchor B
#              (both ends have CIR probes: L-CCF4 quasi-monostatic + L-B co-located)
#   S2  8 min  stencil >=1m away from EVERY LOS line (bulb test: FP must stay quiet,
#              only CIR tails may change) — pick spot before starting
#   S3  5 min  stencil removed from room, restore-check
# Log each move IMMEDIATELY with:   bash stencil_session.sh mark "S1 placed, desc..."
# (finds the latest session dir automatically)
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
LOGROOT=SS-TWR/alt-SS-TWR/broadcast/logs
if [ "${1:-}" = "mark" ]; then
  B=$(ls -dt "$LOGROOT"/stencil_session_* 2>/dev/null | head -1)
  [ -z "$B" ] && { echo "no session dir"; exit 1; }
  echo "[$(date +%H:%M:%S)] ${2:-mark}" | tee -a "$B/markers.txt"; exit 0
fi
DUR=${DUR:-2400}   # 40 min
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
declare -A PT=( [L955A]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00 \
                [L9336]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00 \
                [LCCF4]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184784-if00 \
                [LB]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00 \
                [LE]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00 \
                [LF]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184964-if00 )
BASE="$LOGROOT/stencil_session_$(date +%Y%m%d_%H%M%S)"; mkdir -p "$BASE"
echo "[$(date +%H:%M:%S)] SESSION START dur=${DUR}s base=$BASE" | tee "$BASE/markers.txt"
for L in L955A L9336 LCCF4 LB LE LF; do
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py \
    --port "${PT[$L]}" --baud 460800 --duration $((DUR+30)) --out-dir "$BASE/$L" \
    > "$BASE/$L.console.log" 2>&1 &
done
sleep 3
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
  --targets BS9336,BS955A,BSCCF4 --duration $DUR --tr-hz 10 --controller-reset-snr 1050070698 \
  --tag-cir compact --skip-anchor-preflight --legacy-skip-link-ready-wait --no-silence-non-target-tags \
  --out-dir "$BASE/recv" > "$BASE/recv.console.log" 2>&1
echo "[$(date +%H:%M:%S)] recv done rc=$?" | tee -a "$BASE/markers.txt"
wait
echo "[$(date +%H:%M:%S)] SESSION COMPLETE $BASE" | tee -a "$BASE/markers.txt"
