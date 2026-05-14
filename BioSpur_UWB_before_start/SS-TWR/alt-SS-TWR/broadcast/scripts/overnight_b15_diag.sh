#!/usr/bin/env bash
set -euo pipefail

# b15 overnight diagnostics:
# - no firmware changes
# - no anchor changes
# - capture-only, with listener + A-H anchor serial logs

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BCAST_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${BCAST_ROOT}"

MASTER_TAG_PORT="${MASTER_TAG_PORT:-/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00}"
MASTER_TAG_SNR="${MASTER_TAG_SNR:-1050070698}"
LISTENER_PORT="${LISTENER_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00}"

SINGLE_S="${SINGLE_S:-120}"
PAIR_S="${PAIR_S:-120}"
LONG_S="${LONG_S:-300}"
LISTENER_EXTRA_S="${LISTENER_EXTRA_S:-15}"
STATIC_HZ="${STATIC_HZ:-5}"
ROTO_HZ="${ROTO_HZ:-10}"
MOTION_HZ="${MOTION_HZ:-5}"

LOG_ROOT="${LOG_ROOT:-${BCAST_ROOT}/logs/overnight_b15_diag_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "${LOG_ROOT}"

declare -A ANCHOR_PORTS=(
  [A]="/dev/serial/by-id/usb-SEGGER_J-Link_000760184781-if00"
  [B]="/dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00"
  [C]="/dev/serial/by-id/usb-SEGGER_J-Link_000760185878-if00"
  [D]="/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00"
  [E]="/dev/serial/by-id/usb-SEGGER_J-Link_000760185904-if00"
  [F]="/dev/serial/by-id/usb-SEGGER_J-Link_000760186124-if00"
  [G]="/dev/serial/by-id/usb-SEGGER_J-Link_000760185889-if00"
  [H]="/dev/serial/by-id/usb-SEGGER_J-Link_000760186121-if00"
)
ANCHOR_LOGGER_PID=""

start_anchor_serial_logger() {
  local out_dir="$1"
  local duration_s="$2"
  mkdir -p "${out_dir}/anchor_serial"
  python3 - "${out_dir}" "${duration_s}" > "${out_dir}/anchor_serial/logger_stdout.log" 2>&1 <<'PY' &
import pathlib
import serial
import sys
import threading
import time

out = pathlib.Path(sys.argv[1]) / "anchor_serial"
duration_s = float(sys.argv[2])
stop = time.time() + duration_s
ports = {
    "A": "/dev/serial/by-id/usb-SEGGER_J-Link_000760184781-if00",
    "B": "/dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00",
    "C": "/dev/serial/by-id/usb-SEGGER_J-Link_000760185878-if00",
    "D": "/dev/serial/by-id/usb-SEGGER_J-Link_000760186081-if00",
    "E": "/dev/serial/by-id/usb-SEGGER_J-Link_000760185904-if00",
    "F": "/dev/serial/by-id/usb-SEGGER_J-Link_000760186124-if00",
    "G": "/dev/serial/by-id/usb-SEGGER_J-Link_000760185889-if00",
    "H": "/dev/serial/by-id/usb-SEGGER_J-Link_000760186121-if00",
}

def worker(label, port):
    path = out / f"{label}.log"
    with path.open("w", buffering=1, errors="replace") as f:
        f.write(f"ANCHOR_LOG_START label={label} port={port}\n")
        try:
            ser = serial.Serial(port, 115200, timeout=0.2)
        except Exception as exc:
            f.write(f"OPEN_FAIL {exc}\n")
            return
        with ser:
            while time.time() < stop:
                try:
                    data = ser.readline()
                except Exception as exc:
                    f.write(f"READ_FAIL {exc}\n")
                    break
                if data:
                    f.write(data.decode("utf-8", "replace"))
        f.write("ANCHOR_LOG_END\n")

threads = []
for label, port in ports.items():
    t = threading.Thread(target=worker, args=(label, port), daemon=False)
    t.start()
    threads.append(t)
for t in threads:
    t.join()
PY
  ANCHOR_LOGGER_PID="$!"
}

summarize_capture() {
  local test_dir="$1"
  python3 - "${test_dir}" <<'PY'
import csv
import json
import pathlib
import statistics
import sys
from collections import Counter, defaultdict

base = pathlib.Path(sys.argv[1])
recv_dirs = sorted(base.glob("recv_*"))
listener_dirs = sorted((base / "listener").glob("listener_*")) if (base / "listener").exists() else []
summary = {"test_dir": str(base)}

def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

if recv_dirs:
    recv = recv_dirs[-1]
    summary["recv_dir"] = str(recv)
    cm = read_csv(recv / "cm_all.csv")
    cf = read_csv(recv / "cf_all.csv")
    cr = read_csv(recv / "cr_all.csv")
    pos = read_csv(recv / "positions_all.csv")
    summary["rows"] = {
        "cm": len(cm),
        "cf": len(cf),
        "cr": len(cr),
        "positions": len(pos),
    }
    summary["cm_status"] = dict(Counter(r.get("status", "") for r in cm))
    summary["cm_by_tag_status"] = {
        f"{tag}:{status}": count
        for (tag, status), count in Counter((r.get("peer_name", ""), r.get("status", "")) for r in cm).items()
    }
    summary["cm_by_tag_anchor_status"] = {
        f"{tag}:{anchor}:{status}": count
        for (tag, anchor, status), count in Counter(
            (r.get("peer_name", ""), r.get("anchor_id", ""), r.get("status", "")) for r in cm
        ).items()
    }
    summary["cr_by_tag_anchor_reason"] = {
        f"{tag}:{anchor}:{reason}": count
        for (tag, anchor, reason), count in Counter(
            (r.get("peer_name", ""), r.get("anchor_label", ""), r.get("reason", "")) for r in cr
        ).items()
    }
    summary["cf_solve_reason"] = dict(Counter(r.get("solve_reason", "") for r in cf))
    if cf:
        by_tag = defaultdict(list)
        for row in cf:
            by_tag[row.get("peer_name", "")].append(row)
        summary["cf_by_tag"] = {}
        for tag, rows in by_tag.items():
            ftl = [int(r["first_to_last_us"]) for r in rows if r.get("first_to_last_us", "").isdigit()]
            frame = [int(r["frame_us"]) for r in rows if r.get("frame_us", "").isdigit()]
            summary["cf_by_tag"][tag] = {
                "rows": len(rows),
                "first_to_last_min_med_max": [min(ftl), statistics.median(ftl), max(ftl)] if ftl else [],
                "frame_us_med_p95_max": [
                    statistics.median(frame),
                    sorted(frame)[int(0.95 * (len(frame) - 1))],
                    max(frame),
                ] if frame else [],
            }
    if pos:
        by_tag = defaultdict(list)
        for row in pos:
            by_tag[row.get("peer_name", "")].append(row)
        summary["positions_by_tag"] = {}
        for tag, rows in by_tag.items():
            rms = [int(r["rms_mm"]) for r in rows if str(r.get("rms_mm", "")).lstrip("-").isdigit()]
            maxv = [int(r["max_mm"]) for r in rows if str(r.get("max_mm", "")).lstrip("-").isdigit()]
            summary["positions_by_tag"][tag] = {
                "rows": len(rows),
                "rms_med_p95_max": [
                    statistics.median(rms),
                    sorted(rms)[int(0.95 * (len(rms) - 1))],
                    max(rms),
                ] if rms else [],
                "max_med_p95_max": [
                    statistics.median(maxv),
                    sorted(maxv)[int(0.95 * (len(maxv) - 1))],
                    max(maxv),
                ] if maxv else [],
                "anchors_top": Counter(r.get("anchors", "") for r in rows).most_common(8),
            }

if listener_dirs:
    listener = listener_dirs[-1]
    summary["listener_dir"] = str(listener)
    uf = read_csv(listener / "uf.csv")
    ul = read_csv(listener / "ul.csv")
    summary["listener"] = {
        "uf_rows": len(uf),
        "ul_rows": len(ul),
        "uf_code": dict(Counter(r.get("code", "") for r in uf)),
        "ul_anchor": dict(Counter(r.get("anchor", "") for r in ul)),
    }

(base / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
with (base / "summary.txt").open("w", encoding="utf-8") as f:
    f.write(json.dumps(summary, indent=2, sort_keys=True))
    f.write("\n")
print(json.dumps(summary, indent=2, sort_keys=True))
PY
}

run_capture() {
  local name="$1"
  local duration_s="$2"
  local targets="$3"
  local profiles="$4"
  local out_dir="${LOG_ROOT}/${name}"
  mkdir -p "${out_dir}"

  echo "===== START ${name} duration=${duration_s}s targets=${targets} profiles=${profiles} =====" | tee -a "${LOG_ROOT}/run.log"
  start_anchor_serial_logger "${out_dir}" "$((duration_s + LISTENER_EXTRA_S + 25))"
  local logger_pid="${ANCHOR_LOGGER_PID}"

  python3 scripts/run_recv_tdma_capture_with_listener.py \
    --listener-port "${LISTENER_PORT}" \
    --listener-extra-s "${LISTENER_EXTRA_S}" \
    --out-dir "${out_dir}" \
    -- \
    --port "${MASTER_TAG_PORT}" \
    --controller-reset-snr "${MASTER_TAG_SNR}" \
    --duration "${duration_s}" \
    --targets "${targets}" \
    --profiles "${profiles}" \
    --static-hz "${STATIC_HZ}" \
    --roto-hz "${ROTO_HZ}" \
    --motion-hz "${MOTION_HZ}" \
    --skip-anchor-preflight \
    --skip-cm-probe \
    --allow-zero-positions 2>&1 | tee "${out_dir}/capture_stdout.log"

  if [[ -n "${logger_pid}" ]]; then
    wait "${logger_pid}" || true
  fi
  summarize_capture "${out_dir}" | tee "${out_dir}/summary_stdout.log"
  echo "===== DONE ${name} =====" | tee -a "${LOG_ROOT}/run.log"
}

cat > "${LOG_ROOT}/plan.txt" <<EOF
b15 overnight diagnostic plan

SINGLE_S=${SINGLE_S}
PAIR_S=${PAIR_S}
LONG_S=${LONG_S}

Test 1:
  1a BSF66F only static
  1b BS2DCE only roto
  1c BSDC91 only roto

Test 2:
  2a BSF66F static + BS2DCE roto
  2b BSF66F static + BSDC91 roto
  2c BS2DCE roto + BSDC91 roto

Test 3:
  3a 3-tag static/roto long run for CM/CF timeout matrix

Note:
  all-motion emits TS positions but no CM/CF timeout matrix, so this script keeps Test 3 in calibration profiles.
  Set RUN_ALL_MOTION=1 to append an optional 3-tag all-motion position run.
EOF

run_capture "01_single_BSF66F_static" "${SINGLE_S}" "BSF66F" "BSF66F:static"
run_capture "02_single_BS2DCE_roto" "${SINGLE_S}" "BS2DCE" "BS2DCE:roto"
run_capture "03_single_BSDC91_roto" "${SINGLE_S}" "BSDC91" "BSDC91:roto"

run_capture "04_pair_BSF66F_static_BS2DCE_roto" "${PAIR_S}" "BSF66F,BS2DCE" "BSF66F:static,BS2DCE:roto"
run_capture "05_pair_BSF66F_static_BSDC91_roto" "${PAIR_S}" "BSF66F,BSDC91" "BSF66F:static,BSDC91:roto"
run_capture "06_pair_BS2DCE_roto_BSDC91_roto" "${PAIR_S}" "BS2DCE,BSDC91" "BS2DCE:roto,BSDC91:roto"

run_capture "07_three_tag_cal_long" "${LONG_S}" "BSF66F,BS2DCE,BSDC91" "BSF66F:static,BS2DCE:roto,BSDC91:roto"

if [[ "${RUN_ALL_MOTION:-0}" == "1" ]]; then
  run_capture "08_three_tag_motion_optional" "${LONG_S}" "BSF66F,BS2DCE,BSDC91" "BSF66F:motion,BS2DCE:motion,BSDC91:motion"
fi

python3 - "${LOG_ROOT}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
items = []
for path in sorted(root.glob("*/summary.json")):
    items.append(json.loads(path.read_text(encoding="utf-8")))
(root / "combined_summary.json").write_text(json.dumps(items, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"combined_summary={root / 'combined_summary.json'}")
PY

echo "OVERNIGHT_DONE ${LOG_ROOT}"
