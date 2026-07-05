#!/usr/bin/env bash
# Circular-SAR capture: rotating BS2DCE (+3 static wand tags) ranged by master while
# 6 fixed CIR listeners record. recv drives the tags; we wait until ranging stabilizes
# (ge7>0) BEFORE launching the listeners so their whole window overlaps live traffic.
set -u
OUT="${1:?usage: roto_sar_capture.sh <out_dir> [rec_s] [lis_s]}"
RECS="${2:-320}"     # recv ranging seconds
LISS="${3:-260}"     # listener seconds (started after ranging is live)
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
declare -A SN=( [LCCF4]=760184784 [L9336]=760186071 [L955A]=760186081 [LB]=760184545 [LE]=760184767 [LF]=760184964 )
mkdir -p "$OUT"
echo "[$(date +%H:%M:%S)] launch recv 4-tag (BS9336,BS955A,BSCCF4,BS2DCE) dur=${RECS}s"
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
  --targets BS9336,BS955A,BSCCF4,BS2DCE --duration "$RECS" --tr-hz 10 \
  --controller-reset-snr 1050070698 --tag-cir compact --skip-anchor-preflight \
  --legacy-skip-link-ready-wait --no-silence-non-target-tags \
  --out-dir "$OUT/recv" --out-dir-exact > "$OUT/recv.log" 2>&1 &
RPID=$!
echo "  recv pid=$RPID; waiting for ranging to go live (ge7>0)..."
for i in $(seq 1 60); do
  sleep 5
  if grep -qE "ge7=[1-9][0-9]?%|ge7=100%" "$OUT/recv.log" 2>/dev/null; then
    echo "[$(date +%H:%M:%S)] ranging LIVE after ~$((i*5))s"; break
  fi
  kill -0 $RPID 2>/dev/null || { echo "recv died during setup"; exit 2; }
done
echo "[$(date +%H:%M:%S)] launch 6 listeners dur=${LISS}s"
LP=()
for L in LCCF4 L9336 L955A LB LE LF; do
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py \
    --port /dev/serial/by-id/usb-SEGGER_J-Link_000${SN[$L]}-if00 --baud 460800 \
    --duration "$LISS" --out-dir "$OUT/$L" > "$OUT/$L.log" 2>&1 &
  LP+=($!)
done
wait "${LP[@]}"
echo "[$(date +%H:%M:%S)] listeners done; waiting recv..."
wait $RPID 2>/dev/null
echo "[$(date +%H:%M:%S)] === CIR counts ==="
for L in LCCF4 L9336 L955A LB LE LF; do
  D=$(ls -d "$OUT/$L"/listener_* 2>/dev/null | head -1)
  echo "  $L: lcirm=$(($(wc -l < "$D/lcirm.csv" 2>/dev/null)-1))"
done
echo "[$(date +%H:%M:%S)] SAR CAPTURE COMPLETE $OUT"
