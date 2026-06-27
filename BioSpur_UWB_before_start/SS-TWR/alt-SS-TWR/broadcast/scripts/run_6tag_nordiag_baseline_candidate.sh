#!/usr/bin/env bash
set -euo pipefail

# Reproduce the current safest no-RFD 6-tag baseline candidate.
#
# Reference evidence:
# After the 2026-06-27 fixed-slot restore tests, the safest no-RFD baseline is
# the fixed-slot Master_Tag image plus the legacy/no-touch capture discipline:
# do not send broad MODE IDLE, do not clear stale TDMA state, do not send target
# CIR commands, and do not run cleanup. If one Tag has fallen into a bad UWB
# runtime state, recover that Tag with targeted cmd REBOOT first, then verify
# with a passive/no-command monitor.
#
# This script intentionally does not flash anything. It assumes Master_Tag is
# already running the 6-tag no-RFD/10x9 candidate image. Before starting the
# capture, it checks that all six Tags are BLE-visible; if any Tag, especially
# BSDC91, is absent, it exits before disturbing the baseline capture.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

resolve_master_tag_port() {
  if [ -n "${MASTER_TAG_PORT:-}" ]; then
    printf '%s\n' "${MASTER_TAG_PORT}"
    return 0
  fi

  local candidate
  for candidate in \
    /dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00 \
    /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
    /dev/serial/by-id/usb-BioSpur_1_BioSpur_BLE_Control_6918E0384172A49F-if00
  do
    if [ -e "${candidate}" ]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  echo "ERROR: Master_Tag control port not found" >&2
  return 1
}

MASTER_TAG_PORT="$(resolve_master_tag_port)"
VISIBILITY_S="${VISIBILITY_S:-45}"
STAMP="$(date +%Y%m%d_%H%M%S)"

cd "${REPO_ROOT}"

if [ "${SKIP_VISIBILITY_CHECK:-0}" != "1" ]; then
  MASTER_TAG_PORT="${MASTER_TAG_PORT}" VISIBILITY_S="${VISIBILITY_S}" python3 - <<'PY'
import os
import re
import serial
import sys
import time

port = os.environ["MASTER_TAG_PORT"]
duration_s = float(os.environ.get("VISIBILITY_S", "45"))
required = {"BSF66F", "BS2DCE", "BSDC91", "BSCCF4", "BS9336", "BS955A"}
seen = set()
tag_re = re.compile(r"\b(BS[0-9A-F]{4})\b")

print(f"[6TAG-GATE] open Master_Tag: {port}", flush=True)
with serial.Serial(port, 115200, timeout=0.2) as ser:
    time.sleep(0.3)
    ser.reset_input_buffer()
    for cmd in [
        "ota_target token -1",
        "ota_target name -",
        "ota_target prefix BS",
        "ota_target uuid -",
        "conn",
    ]:
        print(f"[6TAG-GATE] >>> {cmd}", flush=True)
        ser.write((cmd + "\n").encode())
        ser.flush()
        time.sleep(0.5)
        data = ser.read(8192).decode(errors="replace")
        for match in tag_re.finditer(data):
            if match.group(1) in required:
                seen.add(match.group(1))

    print(f"[6TAG-GATE] listen {duration_s:.0f}s for BS* visibility", flush=True)
    end = time.time() + duration_s
    while time.time() < end:
        data = ser.read(8192).decode(errors="replace")
        for match in tag_re.finditer(data):
            tag = match.group(1)
            if tag in required:
                seen.add(tag)
        missing = sorted(required - seen)
        if not missing:
            break

missing = sorted(required - seen)
print(f"[6TAG-GATE] seen={','.join(sorted(seen)) or '-'}", flush=True)
if missing:
    print(f"[6TAG-GATE] missing={','.join(missing)}", flush=True)
    print("[6TAG-GATE] aborting before capture; recover missing Tag BLE visibility first", flush=True)
    sys.exit(3)
print("[6TAG-GATE] all six Tags visible; starting no-RFD baseline capture", flush=True)
PY
fi

python3 scripts/run_recv_tdma_capture.py \
  --port "${MASTER_TAG_PORT}" \
  --skip-anchor-preflight \
  --duration "${DURATION_S:-120}" \
  --targets BSF66F,BS2DCE,BSDC91,BSCCF4,BS9336,BS955A \
  --tdma-roster-targets BSF66F,BS2DCE,BSDC91,BSCCF4,BS9336,BS955A \
  --tr-hz 10 \
  --tag-cir off \
  --skip-target-cir-command \
  --tag-link-stable-s 5 \
  --tdma-config-retries 3 \
  --known-bs-tags BSF66F,BS2DCE,BSDC91,BSCCF4,BS9336,BS955A \
  --legacy-no-touch-tags \
  --legacy-keep-tdma-state \
  --legacy-skip-link-ready-wait \
  --allow-legacy-tdma-show-only \
  --no-cleanup \
  --out-dir "logs/restore_6tag_nordiag_baseline_candidate_${STAMP}" \
  --no-tr-timeout-s 20
