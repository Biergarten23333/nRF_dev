#!/usr/bin/env bash
# Robust reconnecting serial capture for the Geiger overnight static run.
# - stty -hupcl keeps DTR asserted across close/reopen so we do NOT pulse-reset
#   the target on every reconnect (a plain `cat` reopen can reset the nRF).
# - Auto-reconnects on EOF / device re-enumeration (thermal resets, USB blips),
#   which is exactly what an overnight drift capture must tolerate.
PORT="/dev/ttyACM2"
DIR="/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/logs/geiger_overnight_static_20260711"
OUT="$DIR/scan.log"
ERR="$DIR/capture.err"
ts() { date '+%Y-%m-%dT%H:%M:%S%z'; }
echo "[$(ts)] capture_reconnect start, PORT=$PORT pid=$$" >> "$ERR"
while true; do
  if [ -e "$PORT" ]; then
    stty -F "$PORT" raw -echo -hupcl 2>>"$ERR"
    echo "[$(ts)] open $PORT" >> "$ERR"
    cat "$PORT" >> "$OUT" 2>>"$ERR"
    echo "[$(ts)] $PORT EOF/closed -> reconnecting" >> "$ERR"
  else
    echo "[$(ts)] $PORT absent -> waiting" >> "$ERR"
  fi
  sleep 0.5
done
