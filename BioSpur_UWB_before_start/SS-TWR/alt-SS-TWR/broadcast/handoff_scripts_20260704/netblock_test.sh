#!/usr/bin/env bash
# Static net IN/OUT occlusion test on BSCCF4.
# Steel net blocks the BSCCF4 tag antenna; we watch whether LF/LB (far listeners) first-path
# steps down when the net is IN. Self-referencing (IN vs OUT), no periodicity needed.
#
# Runs the capture in the BACKGROUND so you can mark transitions from the SAME terminal.
#   bash netblock_test.sh start [DUR]   # launch (default DUR=300s), prints base dir
#   bash netblock_test.sh mark IN       # run the instant you put the net IN (blocking)
#   bash netblock_test.sh mark OUT      # run the instant you take it OUT
#   bash netblock_test.sh stop          # end early
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
LR=SS-TWR/alt-SS-TWR/broadcast/logs
PTR=/tmp/netblock_current
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
declare -A SNR=( [LCCF4]=760184784 [LF]=760184964 [LB]=760184545 [LE]=760184767 )

case "${1:-start}" in
  mark)
    B=$(cat "$PTR" 2>/dev/null) || { echo "no active session — run: bash $0 start"; exit 1; }
    echo "$(date +%s) $(date +%H:%M:%S) ${2:-mark}" | tee -a "$B/marks.txt"; exit 0;;
  stop)
    B=$(cat "$PTR" 2>/dev/null)
    [ -n "$B" ] && [ -f "$B/pids" ] && { kill $(cat "$B/pids") 2>/dev/null; echo "stopped $B"; } || echo "nothing to stop"
    exit 0;;
  start)
    DUR=${2:-300}
    B=$LR/netblock_$(date +%Y%m%d_%H%M%S); mkdir -p "$B"; echo "$B" > "$PTR"; : > "$B/marks.txt"; : > "$B/pids"
    for L in LCCF4 LF LB LE; do
      python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py \
        --port /dev/serial/by-id/usb-SEGGER_J-Link_000${SNR[$L]}-if00 --baud 460800 \
        --duration $((DUR+20)) --out-dir "$B/$L" > "$B/$L.log" 2>&1 &
      echo $! >> "$B/pids"
    done
    sleep 2
    python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
      --targets BS9336,BS955A,BSCCF4 --duration "$DUR" --tr-hz 10 --controller-reset-snr 1050070698 \
      --tag-cir compact --skip-anchor-preflight --legacy-skip-link-ready-wait \
      --no-silence-non-target-tags --no-prewarm-reroll --out-dir "$B/recv" > "$B/recv.log" 2>&1 &
    echo $! >> "$B/pids"
    sleep 8
    echo "=========================================================="
    echo " RECORDING -> $B   (${DUR}s)"
    echo " Mark each move (same terminal):"
    echo "     bash $0 mark IN      # net blocking BSCCF4"
    echo "     bash $0 mark OUT     # net removed"
    echo " Suggested: OUT 25s, IN 25s, OUT 25s, IN 25s, OUT 25s (5+ marks)"
    echo " End early:  bash $0 stop"
    echo "=========================================================="
    ;;
esac
