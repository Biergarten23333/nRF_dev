#!/usr/bin/env bash
# LSTAT-based liveness preflight for the SELF-HEAL listener fw (correct check).
# Old preflight checked lpd/lrd rows => needs traffic AND a >10s controller cold-start window
# => false-fails. On self-heal fw the right signal is the LSTAT heartbeat (printed every 5s
# regardless of traffic) + counters responding. No ranging needed. No reflash; 盖格 untouched.
# alive = lstat_rows>=2 (heartbeat printing). self_recover high is NORMAL (silent-air self-heal).
# Usage: listener_preflight_lstat.sh [BASE_DIR]  ; exit 0 = all 5 alive, 1 = some silent.
set -u
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start || exit 9
LORDER="L955A L9336 LB LE LF"
declare -A SN=( [L955A]=760186081 [L9336]=760186071 [LB]=760184545 [LE]=760184767 [LF]=760184964 )
declare -A PT=( [L955A]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00 \
                [L9336]=/dev/serial/by-id/usb-SEGGER_J-Link_000760186071-if00 \
                [LB]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184545-if00 \
                [LE]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184767-if00 \
                [LF]=/dev/serial/by-id/usb-SEGGER_J-Link_000760184964-if00 )
BASE="${1:-SS-TWR/alt-SS-TWR/broadcast/logs/lpreflight_lstat_$(date +%Y%m%d_%H%M%S)}"; mkdir -p "$BASE"
DUR=20   # >= 3 LSTAT heartbeats (5s period) even with serial-open latency
for L in $LORDER; do test -e "${PT[$L]}" || { echo "  $L PORT MISSING ${PT[$L]}"; }; done
echo "[preflight-lstat] capturing 5 listeners ${DUR}s (no ranging; heartbeat liveness) ..."
for L in $LORDER; do
  python3 SS-TWR/alt-SS-TWR/broadcast/scripts/capture_uwb_poll_listener.py --port "${PT[$L]}" --baud 460800 --duration $DUR --out-dir "$BASE/$L" >/dev/null 2>&1 &
done
wait
SILENT=""
for L in $LORDER; do
  LS=$(find "$BASE/$L" -name lstat.csv 2>/dev/null | head -1)
  if [ -z "$LS" ]; then echo "  $L SILENT (no lstat)"; SILENT="$SILENT $L"; continue; fi
  read n g s fp < <(python3 - "$LS" <<'PY'
import sys,csv
rows=list(csv.DictReader(open(sys.argv[1])))
if not rows: print("0 0 0 0"); raise SystemExit
print(len(rows), rows[-1]['good_frames'], rows[-1]['self_recover'], max(int(r['fps']) for r in rows))
PY
)
  if [ "${n:-0}" -ge 2 ]; then printf "  %-6s ALIVE  lstat_rows=%s good_frames=%s self_recover=%s fps_max=%s\n" "$L" "$n" "$g" "$s" "$fp"
  else printf "  %-6s SILENT lstat_rows=%s\n" "$L" "${n:-0}"; SILENT="$SILENT $L"; fi
done
SILENT=$(echo $SILENT | xargs)
if [ -z "$SILENT" ]; then echo "PREFLIGHT_PASS all 5 listeners alive (LSTAT heartbeat)"; exit 0; fi
echo "PREFLIGHT_FAIL silent: $SILENT"
echo ">>> POWER-CYCLE (unplug/replug USB) these boards, then re-run:"
for L in $SILENT; do echo "      $L  SNR=${SN[$L]}  ${PT[$L]}"; done
exit 1
