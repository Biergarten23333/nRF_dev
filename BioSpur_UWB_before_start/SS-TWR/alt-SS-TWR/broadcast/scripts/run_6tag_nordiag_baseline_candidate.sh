#!/usr/bin/env bash
set -euo pipefail

# Reproduce the best current no-RFD 6-tag baseline candidate.
#
# Reference evidence:
#   logs/rfdiag_v2_overnight_20260625/
#     capture_six_targets_bleint12_nopreflight_nordiag_120s_20260626_132626_20260626_132626
#   ge7=0.939401, ge8=0.421001, rfd_all=0, tr_diag_all=0.
#
# This script intentionally does not flash anything. It assumes Master_Tag is
# already running the 6-tag no-RFD/10x9 candidate image. Before starting the
# capture, it checks that all six Tags are BLE-visible; if any Tag, especially
# BSDC91, is absent, it exits before disturbing the baseline capture.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MASTER_TAG_PORT="${MASTER_TAG_PORT:-/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00}"
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
  --tr-hz 10 \
  --tag-cir off \
  --skip-target-cir-command \
  --tag-link-stable-s 5 \
  --tdma-config-retries 3 \
  --skip-initial-mode-idle \
  --skip-final-mode-idle \
  --out-dir "logs/restore_6tag_nordiag_baseline_candidate_${STAMP}" \
  --no-tr-timeout-s 20
