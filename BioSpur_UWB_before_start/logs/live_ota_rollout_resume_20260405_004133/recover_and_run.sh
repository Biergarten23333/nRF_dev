#!/usr/bin/env bash
set -euo pipefail
RUN_ROOT="$1"
MASTER_PORT='/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00'
MASTER_SNR='683234364'

check_status() {
python3 - <<'PY'
import os, serial, time, sys
p='/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00'
if not os.path.exists(p):
    print('CDC_MISSING')
    raise SystemExit(2)
try:
    s=serial.Serial(p,115200,timeout=0.3)
    s.write(b'\nstatus\n')
    s.flush()
    time.sleep(0.8)
    d=s.read(6000).decode('utf-8','ignore')
    s.close()
    ok=('Control mode loaded:' in d and 'UART control ready' in d) or ('BioSpur BLE master control ready' in d)
    print('CDC_OPEN_OK' if ok else 'CDC_OPEN_WEAK')
    print(d[:1200])
    raise SystemExit(0 if ok else 3)
except Exception as e:
    print('CDC_OPEN_FAIL', repr(e))
    raise SystemExit(4)
PY
}

jlink_reset() {
  cat >/tmp/jlink_reset_52840_cdc.cmd <<'EOF'
r
g
qc
EOF
  JLinkExe -NoGui 1 -SelectEmuBySN "$MASTER_SNR" -CommandFile /tmp/jlink_reset_52840_cdc.cmd > "$RUN_ROOT/jlink_reset_$(date +%H%M%S).log" 2>&1 || return 1
  udevadm settle || true
  sleep 2
}

echo '=== CDC health gate ===' | tee "$RUN_ROOT/recovery.log"
method='CDC already healthy'
if ! check_status > "$RUN_ROOT/cdc_status_initial.log" 2>&1; then
  method='J-Link soft reset recovery'
  echo 'CDC unhealthy -> soft reset #1' | tee -a "$RUN_ROOT/recovery.log"
  jlink_reset || true
  if ! check_status > "$RUN_ROOT/cdc_status_after_reset1.log" 2>&1; then
    echo 'CDC still unhealthy -> soft reset #2' | tee -a "$RUN_ROOT/recovery.log"
    jlink_reset || true
    if ! check_status > "$RUN_ROOT/cdc_status_after_reset2.log" 2>&1; then
      method='J-Link soft reset + reflash recovery'
      echo 'CDC still unhealthy -> fallback flash' | tee -a "$RUN_ROOT/recovery.log"
      scripts/flash_master_noninteractive.sh build-master-control-ota-fix-20260403/master_control/zephyr/zephyr.hex > "$RUN_ROOT/master_reflash.log" 2>&1 || true
      if ! check_status > "$RUN_ROOT/cdc_status_after_reflash.log" 2>&1; then
        echo "$method" > "$RUN_ROOT/recovery_method.txt"
        echo 'RECOVERY_FAIL' | tee -a "$RUN_ROOT/recovery.log"
        exit 55
      fi
    fi
  fi
fi

echo "$method" > "$RUN_ROOT/recovery_method.txt"

printf "Anchor\tUUID\tRunDir\tStarted\tCompleted\tPendingTest\tReset\tSuccess\tBlocker\n" > "$RUN_ROOT/result.tsv"

while IFS=$'\t' read -r a uuid aport; do
  echo "=== ${a} start $(date +%H:%M:%S) ===" | tee -a "$RUN_ROOT/progress.log"

  # baseline
  python3 - <<'PY' "$MASTER_PORT" > "$RUN_ROOT/${a}_baseline.log" 2>&1 || true
import serial,time,sys
p=sys.argv[1]
s=serial.Serial(p,115200,timeout=0.2)
s.write(b'\nmode recv\nstatus\n'); s.flush(); time.sleep(0.9)
print(s.read(8000).decode('utf-8','ignore'))
s.close()
PY

  out_dir="$RUN_ROOT/anchor_${a}_$(date +%H%M%S)/stage1"
  mkdir -p "$out_dir"

  rc=0
  python3 scripts/ota_single_shot_stable.py \
    --timeout-s 900 \
    --port "$MASTER_PORT" \
    --target-uuid "$uuid" \
    --anchor-port "$aport" \
    --anchor-reset-preflight \
    --out-dir "$out_dir" > "$RUN_ROOT/anchor_${a}.launcher.log" 2>&1 || rc=$?

  summary="$out_dir/summary.json"
  if [ -f "$summary" ]; then
    vals=$(python3 - <<'PY' "$summary"
import json,sys
p=sys.argv[1]
d=json.load(open(p))
keys=['ota_upload_started_seen','ota_upload_complete_seen','ota_pending_test_seen','ota_reset_request_seen','ota_success_seen','blocker']
out=[]
for k in keys:
    v=d.get(k)
    out.append('' if v is None else str(v))
print('\t'.join(out))
PY
)
    printf "%s\t%s\t%s\t%s\n" "$a" "$uuid" "${out_dir%/stage1}" "$vals" >> "$RUN_ROOT/result.tsv"
  else
    printf "%s\t%s\t%s\t-\t-\t-\t-\tfalse\tno_summary_rc_%s\n" "$a" "$uuid" "${out_dir%/stage1}" "$rc" >> "$RUN_ROOT/result.tsv"
  fi
  tail -n 1 "$RUN_ROOT/result.tsv" | tee -a "$RUN_ROOT/progress.log"

done < "$RUN_ROOT/anchors.tsv"

echo 'DONE' | tee -a "$RUN_ROOT/progress.log"
