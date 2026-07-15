#!/usr/bin/env bash
# Phase 4 launcher: start 2 listeners (background, full duration), warmup TDMA,
# then run the 5-level randomized power sweep. Assumes anchors already responder@MAX.
set -uo pipefail
BCAST=/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast
EXP=/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/experiments/power_campaign_20260714
cd "$BCAST"
source /tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/f50e2943-3acd-445a-b5e5-900f61051f16/scratchpad/ncs_env.sh
TPORT=/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00
# Listeners: L-955A (near wand) + L-E (anchor side) — spatially separated
L1_PORT=/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00   # L-955A
L2_PORT=/dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00   # L-E
LDUR=2100   # covers ~30min controller-reset sweep + warmup + margin
echo "=== Phase 4 start $(date) ==="
echo "--- starting listeners (dur=${LDUR}s) ---"
python3 scripts/capture_uwb_poll_listener.py --port "$L1_PORT" --baud 460800 --duration "$LDUR" \
  --out-dir "$EXP/listener/L-955A" > "$EXP/listener/L-955A.console" 2>&1 &
L1=$!
python3 scripts/capture_uwb_poll_listener.py --port "$L2_PORT" --baud 460800 --duration "$LDUR" \
  --out-dir "$EXP/listener/L-E" > "$EXP/listener/L-E.console" 2>&1 &
L2=$!
echo "  listener PIDs: $L1 $L2"
sleep 8   # let listeners open + start logging BEFORE first cell
echo "--- warmup: establish clean TDMA + resident links (controller-reset) ---"
python3 scripts/run_recv_tdma_capture.py --port "$TPORT" --controller-reset-snr 1050070698 \
  --targets BS9336,BS955A,BSCCF4 --tr-hz 10 --tdma-profile motion --duration 20 \
  --skip-anchor-preflight --out-dir logs/power_sweep_warmup_20260714 > "$EXP/warmup.caplog" 2>&1
echo "  warmup rc=$?"
echo "--- running 5-level sweep ---"
bash "$EXP/run_power_sweep.sh"
echo "--- sweep done; waiting for listeners to finish ---"
wait $L1 2>/dev/null; wait $L2 2>/dev/null
echo "=== Phase 4 complete $(date) ==="
# listener row counts
for L in L-955A L-E; do
  f="$EXP/listener/$L/lpd.csv"; [ -f "$f" ] && echo "  $L lpd rows: $(($(wc -l <"$f")-1))" || echo "  $L: no lpd.csv"
done
