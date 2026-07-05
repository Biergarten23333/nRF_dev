#!/usr/bin/env bash
# Overnight self-healing circular-SAR capture.
#   5 tags @ 5Hz (3 static wand + 2 rotating RotoArm BS2DCE/BSDC91) ranged by master;
#   6 fixed CIR listeners record. recv drives the tags -> wait ge7>0 -> launch listeners.
# Robust: frees serial ports each chunk, restores anchor responder on ge7 drop, retries,
#   guarded single-listener re-flash self-heal (LISTENERS ONLY, never anchors).
# Writes to the 500GB disk (repo logs). Touch <BASE>/STOP to halt.
set -u
TS=$(date +%Y%m%d_%H%M%S)
BASE="SS-TWR/alt-SS-TWR/broadcast/logs/roto_sar_overnight_${TS}"
mkdir -p "$BASE"
LOG="$BASE/driver.log"
CHUNK_S="${1:-900}"      # ranging seconds per chunk (15 min)
MAX_CHUNKS="${2:-36}"    # ~9h cap
TRHZ=5
TARGETS=BS9336,BS955A,BSCCF4,BS2DCE,BSDC91
MT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
MA=/dev/serial/by-id/usb-Master_Anchor_Master_Anchor_Control_87EA2F4A526C5A02-if00
CIRHEX=SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-cirprobe_gen_20260704/zephyr/zephyr.hex
declare -A SN=( [LCCF4]=760184784 [L9336]=760186071 [L955A]=760186081 [LB]=760184545 [LE]=760184767 [LF]=760184964 )
declare -A ZERO=( [LCCF4]=0 [L9336]=0 [L955A]=0 [LB]=0 [LE]=0 [LF]=0 )
LISTENERS="LCCF4 L9336 L955A LB LE LF"
say(){ echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

free_ports(){ for pid in $(pgrep -f "capture_uwb_poll_listener|run_recv_tdma_capture" 2>/dev/null); do kill "$pid" 2>/dev/null; done; sleep 3; }

restore_anchors(){
  say "  restore anchors -> responder"
  timeout 140 python3 SS-TWR/alt-SS-TWR/broadcast/scripts/restore_and_smoke_test_anchor_responder.py \
    --anchor-port "$MA" --tag-port "$MT" --targets BS9336,BS955A,BSCCF4 --duration 15 --tr-hz 10 \
    --out-dir "$BASE/restore_c${1}" > "$BASE/restore_c${1}.log" 2>&1
  free_ports
}

say "OVERNIGHT SAR start base=$BASE chunk=${CHUNK_S}s max=${MAX_CHUNKS} tr=${TRHZ}Hz targets=$TARGETS"
free_ports
restore_anchors 0

for c in $(seq 1 "$MAX_CHUNKS"); do
  [ -f "$BASE/STOP" ] && { say "STOP file -> halt"; break; }
  CD="$BASE/chunk${c}"; mkdir -p "$CD"
  say "=== chunk $c/$MAX_CHUNKS ==="
  free_ports
  # launch recv (5 tags @ 5Hz); listeners will start after ranging is live
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/run_recv_tdma_capture.py --port "$MT" \
    --targets "$TARGETS" --duration $((CHUNK_S+120)) --tr-hz "$TRHZ" \
    --controller-reset-snr 1050070698 --tag-cir compact --skip-anchor-preflight \
    --legacy-skip-link-ready-wait --no-silence-non-target-tags \
    --out-dir "$CD/recv" --out-dir-exact > "$CD/recv.log" 2>&1 &
  RPID=$!
  live=0
  for i in $(seq 1 20); do
    sleep 5
    grep -qE "ge7=[1-9][0-9]?%|ge7=100%" "$CD/recv.log" 2>/dev/null && { live=1; say "  ranging live ~$((i*5))s"; break; }
    kill -0 $RPID 2>/dev/null || { say "  recv died in setup"; break; }
  done
  if [ "$live" -ne 1 ]; then
    say "  no ranging this chunk -> kill+restore, skip"; kill $RPID 2>/dev/null; free_ports; restore_anchors "$c"; continue
  fi
  # launch 6 listeners for the chunk
  LP=()
  for L in $LISTENERS; do
    python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py \
      --port /dev/serial/by-id/usb-SEGGER_J-Link_000${SN[$L]}-if00 --baud 460800 \
      --duration "$CHUNK_S" --out-dir "$CD/$L" > "$CD/$L.log" 2>&1 &
    LP+=($!)
  done
  wait "${LP[@]}"; wait $RPID 2>/dev/null
  # health
  GE7=$(grep -oE "ge7=[0-9]+%" "$CD/recv.log" | tail -1)
  H="chunk $c health: $GE7 |"
  onlyzero=""; nonzero=0
  for L in $LISTENERS; do
    D=$(ls -d "$CD/$L"/listener_* 2>/dev/null | head -1); n=$(($(wc -l < "$D/lcirm.csv" 2>/dev/null)-1)); [ "$n" -lt 0 ] && n=0
    H="$H $L=$n"
    if [ "$n" -eq 0 ]; then ZERO[$L]=$((${ZERO[$L]}+1)); onlyzero="$onlyzero $L"; else ZERO[$L]=0; nonzero=$((nonzero+1)); fi
  done
  say "$H"
  # ge7-drop -> restore before next chunk
  pct=$(echo "$GE7" | grep -oE "[0-9]+"); [ -z "$pct" ] && pct=0
  if [ "$pct" -lt 50 ]; then say "  ge7 low ($pct%) -> restore"; restore_anchors "$c"; fi
  # guarded self-heal: exactly ONE listener zero for >=2 chunks while others OK -> reflash THAT listener only
  nz=$(echo $onlyzero | wc -w)
  if [ "$nz" -eq 1 ] && [ "$nonzero" -ge 4 ]; then
    L=$(echo $onlyzero | tr -d ' ')
    if [ "${ZERO[$L]}" -ge 2 ]; then
      say "  SELF-HEAL: $L (SNR ${SN[$L]}) zero CIR x${ZERO[$L]} while others OK -> reflash cirprobe (LISTENER ONLY)"
      free_ports
      timeout 90 bash scripts_reserve_nomore_change/jlink_flash_hex_by_snr.sh "${SN[$L]}" nRF52832_XXAA "$CIRHEX" 4000 >> "$BASE/selfheal_${L}_c${c}.log" 2>&1 && say "  reflash $L rc=0" || say "  reflash $L FAILED"
      ZERO[$L]=0
    fi
  fi
done
say "OVERNIGHT SAR DONE base=$BASE"
