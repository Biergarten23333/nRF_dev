#!/usr/bin/env bash
# Phase 4 power sweep: 5 levels x 3min, randomized order, resident links (no reset,
# no anchor preflight => master + geometry stay fixed; only tag TX power changes).
# Assumes: anchors already in responder@MAX, all 3 tags linked (warmup done by caller),
# and 2 listeners already logging in the background.
set -uo pipefail
BCAST=/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast
EXP=/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/experiments/power_campaign_20260714
SWEEP="$EXP/sweep"; mkdir -p "$SWEEP"
cd "$BCAST"
source /tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/f50e2943-3acd-445a-b5e5-900f61051f16/scratchpad/ncs_env.sh
TPORT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
# Randomized (non-monotonic) order. Powers dB above floor: M6=2.5 MAX=8.5 M12=0 M3=5.5 POR=4.0
ORDER=(M6 MAX M12 M3 POR)
DWELL=180; SETTLE=10
META="$SWEEP/sweep_meta.json"
echo "{\"order\":[\"${ORDER[0]}\",\"${ORDER[1]}\",\"${ORDER[2]}\",\"${ORDER[3]}\",\"${ORDER[4]}\"],\"sweep_start_epoch\":$(python3 -c 'import time;print(time.time())'),\"cells\":[" > "$META"
i=0
for LVL in "${ORDER[@]}"; do
  echo "=== CELL $i level=$LVL $(date +%T) ==="
  ACK=$(python3 "$EXP/set_txpwr.py" "$LVL")
  echo "  set_txpwr: $ACK"
  sleep "$SETTLE"
  CSTART=$(python3 -c 'import time;print(time.time())')
  OUT="logs/power_sweep_cell${i}_${LVL}_20260714"
  # controller-reset per cell = proven path that drives TDMA sweep-counting and
  # writes range_diag_joined.csv (reuse-links leaves 0 counted sweeps). Wand
  # geometry fixed; TXPWR persists across the master reset (tag configure_radio is
  # boot-only) and was set above while tags were linked from the previous cell.
  python3 scripts/run_recv_tdma_capture.py --port "$TPORT" \
    --controller-reset-snr 1050070698 --skip-anchor-preflight \
    --targets BS9336,BS955A,BSCCF4 --tr-hz 10 --tdma-profile motion \
    --duration "$DWELL" --out-dir "$OUT" > "$SWEEP/cell${i}_${LVL}.caplog" 2>&1
  CAPRC=$?
  CEND=$(python3 -c 'import time;print(time.time())')
  RESOLVED=$(ls -dt ${OUT}* 2>/dev/null | head -1)
  SEP=$([ $i -gt 0 ] && echo "," || echo "")
  echo "${SEP}{\"idx\":$i,\"level\":\"$LVL\",\"ack\":$ACK,\"cap_start_epoch\":$CSTART,\"cap_end_epoch\":$CEND,\"cap_rc\":$CAPRC,\"out_dir\":\"$RESOLVED\"}" >> "$META"
  echo "  cell $i done rc=$CAPRC dir=$RESOLVED"
  i=$((i+1))
done
echo "]}" >> "$META"
# restore MAX on all tags
python3 "$EXP/set_txpwr.py" MAX
echo "SWEEP_DONE $(date)"
