#!/usr/bin/env bash
# Overnight LOS baseline + CIR-probe soak (responder-only; NO AutoPos/matrix).
# Chunked so a wedge/battery-death costs <=1 chunk, not the night. Per chunk:
#   - 5 listener captures (L-B raw.log carries LCIRD full-CIR; all give LPD/LSTAT)
#   - recv ranging (3 wand tags @10Hz, --tag-cir compact) with controller reset (clean start)
# After each chunk: append per-tag continuity (rows/dropouts + wall timestamp) + listener health
# to soak_continuity.log. Battery has NO live telemetry -> continuity IS the battery proxy
# (rider 1): a wand dropping to ~0 rows = dead, timestamp recorded for the morning report.
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
declare -A PT=( [L955A]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00 \
                [L9336]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00 \
                [LB]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00 \
                [LE]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00 \
                [LF]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184964-if00 )
CHUNK=${CHUNK:-2700}          # 45 min ranging per chunk
NCHUNKS=${NCHUNKS:-8}         # ~6 h total
BASE=SS-TWR/alt-SS-TWR/broadcast/logs/overnight_soak_$(date +%Y%m%d_%H%M%S)
mkdir -p "$BASE"; CL="$BASE/soak_continuity.log"; STOP="$BASE/STOP"
log(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$CL"; }
log "SOAK START base=$BASE chunk=${CHUNK}s x${NCHUNKS} (responder-only; L-B=CIR probe)"
for c in $(seq 1 "$NCHUNKS"); do
  [ -f "$STOP" ] && { log "STOP file seen; ending after chunk $((c-1))"; break; }
  st=$(date +%Y%m%d_%H%M%S); CD="$BASE/chunk${c}_${st}"; mkdir -p "$CD"
  log "CHUNK $c/$NCHUNKS start dir=$CD"
  declare -A cp
  for L in L955A L9336 LB LE LF; do
    python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py \
      --port "${PT[$L]}" --baud 460800 --duration $((CHUNK+40)) --out-dir "$CD/$L" \
      > "$CD/$L.console.log" 2>&1 & cp[$L]=$!
  done
  sleep 3
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
    --targets BS9336,BS955A,BSCCF4 --duration $CHUNK --tr-hz 10 --controller-reset-snr 1050070698 \
    --tag-cir compact --skip-anchor-preflight --legacy-skip-link-ready-wait --no-silence-non-target-tags \
    --out-dir "$CD/recv" > "$CD/recv.console.log" 2>&1
  log "  chunk $c recv rc=$?"
  # continuity (battery proxy): final per-tag summary from recv console
  grep -E "BS9336: rows=|BS955A: rows=|BSCCF4: rows=|M1 anchor coverage" "$CD/recv.console.log" 2>/dev/null | tail -4 | while read -r ln; do log "  $ln"; done
  for L in L955A L9336 LB LE LF; do wait ${cp[$L]} 2>/dev/null; done
  # listener health: last LSTAT self_recover / ring_drops / good_frames; L-B: LCIR capture count
  for L in L955A L9336 LB LE LF; do
    ls="$(find "$CD/$L" -name lstat.csv 2>/dev/null | head -1)"
    if [ -n "$ls" ]; then
      read gf sr rd < <(python3 - "$ls" <<'PY'
import sys,csv
r=list(csv.DictReader(open(sys.argv[1])))
if r: print(r[-1].get('good_frames','?'), r[-1].get('self_recover','?'), r[-1].get('ring_drops','?'))
else: print("0 0 0")
PY
)
      extra=""
      if [ "$L" = "LB" ]; then
        raw="$(find "$CD/$L" -name raw.log 2>/dev/null | head -1)"
        nc=$(grep -c "^LCIRE;" "$raw" 2>/dev/null || echo 0); extra=" LCIR_captures=$nc"
      fi
      log "  $L good_frames=$gf self_recover=$sr ring_drops=$rd$extra"
    else
      log "  $L NO LSTAT (listener silent this chunk?)"
    fi
  done
  log "CHUNK $c done"
done
log "SOAK COMPLETE base=$BASE"
