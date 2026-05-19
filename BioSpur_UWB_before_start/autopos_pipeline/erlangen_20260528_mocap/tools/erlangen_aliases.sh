#!/usr/bin/env bash
# Erlangen 2026-05-28 BioSpur field helpers.
#
# Usage:
#   source /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh
#   bio_setup
#   static -id ID01
#   roto -id R01
#   wand -id W01
#
# This file is meant to be sourced, not executed.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Source this file instead of executing it:" >&2
  echo "  source ${BASH_SOURCE[0]}" >&2
  exit 2
fi

_BIO_ERLANGEN_TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export BIOSPUR_ERLANGEN_ROOT="$(cd "${_BIO_ERLANGEN_TOOL_DIR}/.." && pwd)"
export BIOSPUR_REPO_ROOT="$(cd "${BIOSPUR_ERLANGEN_ROOT}/../.." && pwd)"
export BIOSPUR_BCAST_DIR="${BIOSPUR_REPO_ROOT}/SS-TWR/alt-SS-TWR/broadcast"
export BIOSPUR_CAPTURE_ROOT="${BIOSPUR_ERLANGEN_ROOT}/captures"
export BIOSPUR_DOC_ROOT="${BIOSPUR_ERLANGEN_ROOT}/docs"

export BIOSPUR_ANCHOR_SNR="${BIOSPUR_ANCHOR_SNR:-960148546}"
export BIOSPUR_TAG_SNR="${BIOSPUR_TAG_SNR:-1050070698}"
export BIOSPUR_H_UUID="${BIOSPUR_H_UUID:-B1E487C2B1FD740D1442206A1857DFA1}"
export BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM="${BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM:-107}"

# These are the 2026-05-19 desktop paths. On the Erlangen laptop, run `bio_ports`
# and override them if /dev/serial/by-id names differ.
export BIOSPUR_ANCHOR_PORT="${BIOSPUR_ANCHOR_PORT:-/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00}"
export BIOSPUR_TAG_PORT="${BIOSPUR_TAG_PORT:-/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00}"

_bio_ts() {
  date +%Y%m%d_%H%M%S
}

_bio_usage() {
  cat <<'EOF'
BioSpur Erlangen helpers:

  bio_setup [session_name]
      Create/select a session under captures/.

  static -id ID01 [-s 120]
      Capture BSF66F for 120 s by default.

  roto -id R01 [-s 120]
      Capture BS2DCE,BSDC91 for 120 s by default.

  wand -id W01 [-s 120]
      Capture BS9336,BS955A,BSCCF4 for 120 s by default.

  sweep -id SW01
      AutoPos sweep: 1000 formal sets + 10 prewarm sets.

  us30 -id US01
      Standalone H ultrasound 30 s capture, writes ultrasound_H.csv.

  bio_check_latest
      Print the latest summary.json under the current session.

  bio_note ID "free text"
      Append an experiment note to session_notes.csv.

  bio_ports
      Show configured serial ports and visible /dev/serial/by-id entries.
EOF
}

help_erlangen() {
  _bio_usage
}

bio_ports() {
  echo "[ports] BIOSPUR_ANCHOR_PORT=${BIOSPUR_ANCHOR_PORT}"
  echo "[ports] BIOSPUR_TAG_PORT=${BIOSPUR_TAG_PORT}"
  echo "[ports] BIOSPUR_ANCHOR_SNR=${BIOSPUR_ANCHOR_SNR}"
  echo "[ports] BIOSPUR_TAG_SNR=${BIOSPUR_TAG_SNR}"
  echo
  echo "[ports] visible /dev/serial/by-id:"
  ls -l /dev/serial/by-id 2>/dev/null || true
  echo
  [[ -e "${BIOSPUR_ANCHOR_PORT}" ]] || echo "[WARN] Master_Anchor port path does not exist on this machine."
  [[ -e "${BIOSPUR_TAG_PORT}" ]] || echo "[WARN] Master_Tag port path does not exist on this machine."
}

bio_setup() {
  local session="${1:-erlangen_20260528_mocap_run_$(date +%Y%m%d_%H%M%S)}"
  export BIOSPUR_SESSION_ROOT="${BIOSPUR_CAPTURE_ROOT}/${session}"
  mkdir -p "${BIOSPUR_SESSION_ROOT}"
  if [[ ! -f "${BIOSPUR_SESSION_ROOT}/session_notes.csv" ]]; then
    echo "timestamp,id,type,path,notes" > "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  fi
  echo "[setup] session=${BIOSPUR_SESSION_ROOT}"
  echo "[setup] broadcast_dir=${BIOSPUR_BCAST_DIR}"
  bio_ports
  _bio_usage
}

_bio_need_setup() {
  if [[ -z "${BIOSPUR_SESSION_ROOT:-}" ]]; then
    echo "[ERR] Run bio_setup first." >&2
    return 2
  fi
  if [[ ! -d "${BIOSPUR_BCAST_DIR}" ]]; then
    echo "[ERR] Broadcast directory not found: ${BIOSPUR_BCAST_DIR}" >&2
    return 2
  fi
  if [[ ! -e "${BIOSPUR_ANCHOR_PORT}" ]]; then
    echo "[ERR] Master_Anchor port not found: ${BIOSPUR_ANCHOR_PORT}" >&2
    echo "      Run bio_ports, then export BIOSPUR_ANCHOR_PORT=/dev/serial/by-id/..." >&2
    return 2
  fi
  if [[ ! -e "${BIOSPUR_TAG_PORT}" ]]; then
    echo "[ERR] Master_Tag port not found: ${BIOSPUR_TAG_PORT}" >&2
    echo "      Run bio_ports, then export BIOSPUR_TAG_PORT=/dev/serial/by-id/..." >&2
    return 2
  fi
}

_bio_csv_escape() {
  local s="${1//\"/\"\"}"
  printf '"%s"' "${s}"
}

bio_note() {
  _bio_need_setup || return $?
  local id="${1:-NOTE}"
  shift || true
  local text="$*"
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ',note,,'
    _bio_csv_escape "${text}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  echo "[note] appended to ${BIOSPUR_SESSION_ROOT}/session_notes.csv"
}

_bio_parse_common_capture_args() {
  BIO_ID=""
  BIO_DURATION="120"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -id|--id)
        BIO_ID="${2:-}"
        shift 2
        ;;
      -s|--seconds|--duration)
        BIO_DURATION="${2:-}"
        shift 2
        ;;
      -h|--help)
        return 9
        ;;
      *)
        echo "[ERR] Unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  if [[ -z "${BIO_ID}" ]]; then
    echo "[ERR] Missing -id, for example: static -id ID01" >&2
    return 2
  fi
}

_bio_run_capture() {
  local kind="$1"
  local id="$2"
  local duration="$3"
  local targets="$4"
  local base="${kind}_${id}_${targets//,/_}_${duration}s"
  local out="${BIOSPUR_SESSION_ROOT}/${base}"

  _bio_need_setup || return $?
  mkdir -p "${out}"
  echo "[capture] kind=${kind} id=${id} duration=${duration}s targets=${targets}"
  echo "[capture] base_out=${out}"

  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 scripts/run_dual_master_tdma_capture.py \
      --anchor-port "${BIOSPUR_ANCHOR_PORT}" \
      --anchor-snr "${BIOSPUR_ANCHOR_SNR}" \
      --tag-port "${BIOSPUR_TAG_PORT}" \
      --tag-snr "${BIOSPUR_TAG_SNR}" \
      --duration "${duration}" \
      --targets "${targets}" \
      --tr-hz 10 \
      --out-dir "${out}"
  )
  local rc=$?

  local final_path
  final_path="$(find "${BIOSPUR_SESSION_ROOT}" -maxdepth 1 -type d -name "${base}_*" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
  [[ -n "${final_path}" ]] || final_path="${out}"

  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ','
    _bio_csv_escape "${kind}"
    printf ','
    _bio_csv_escape "${final_path}"
    printf ','
    _bio_csv_escape "duration_s=${duration}; targets=${targets}; rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"

  echo "[capture] rc=${rc}"
  echo "[capture] final_path=${final_path}"
  return "${rc}"
}

static() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: static -id ID01 [-s 120]"
    return 2
  }
  _bio_run_capture "static" "${BIO_ID}" "${BIO_DURATION}" "BSF66F"
}

roto() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: roto -id R01 [-s 120]"
    return 2
  }
  _bio_run_capture "roto" "${BIO_ID}" "${BIO_DURATION}" "BS2DCE,BSDC91"
}

wand() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: wand -id W01 [-s 120]"
    return 2
  }
  _bio_run_capture "wand3" "${BIO_ID}" "${BIO_DURATION}" "BS9336,BS955A,BSCCF4"
}

sweep() {
  local id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -id|--id)
        id="${2:-}"
        shift 2
        ;;
      -h|--help)
        echo "Usage: sweep -id SW01"
        return 2
        ;;
      *)
        echo "[ERR] Unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  [[ -n "${id}" ]] || { echo "[ERR] Missing -id, for example: sweep -id SW01" >&2; return 2; }
  _bio_need_setup || return $?

  local out="${BIOSPUR_SESSION_ROOT}/sweep_${id}_1000_prewarm10_$(_bio_ts)"
  mkdir -p "${out}"
  echo "[sweep] id=${id}"
  echo "[sweep] out=${out}"
  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 scripts/run_autopos_sweep_loop.py \
      --port "${BIOSPUR_ANCHOR_PORT}" \
      --order ABCDEFGH \
      --sw-sets 1000 \
      --prewarm-sw-sets 10 \
      --round-retries 1 \
      --out-dir "${out}/sweep1000" \
      --verbose 1 2>&1 | tee "${out}/sweep1000.console.log"
  )
  local rc=$?
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ',sweep,'
    _bio_csv_escape "${out}"
    printf ','
    _bio_csv_escape "sw_sets=1000; prewarm=10; rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  echo "[sweep] rc=${rc}"
  return "${rc}"
}

us30() {
  local id=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -id|--id)
        id="${2:-}"
        shift 2
        ;;
      -h|--help)
        echo "Usage: us30 -id US01"
        return 2
        ;;
      *)
        echo "[ERR] Unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  [[ -n "${id}" ]] || { echo "[ERR] Missing -id, for example: us30 -id US01" >&2; return 2; }
  _bio_need_setup || return $?

  local out="${BIOSPUR_SESSION_ROOT}/us_${id}_H_US30_$(_bio_ts)"
  mkdir -p "${out}"
  echo "[us30] id=${id}"
  echo "[us30] out=${out}"
  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 - "${BIOSPUR_ANCHOR_PORT}" "${BIOSPUR_H_UUID}" "${out}" <<'PY'
import json
import sys
from pathlib import Path

from scripts.run_autopos_ultrasound_motion_triplet import (
    master_anchor_us_cmd,
    parse_us_status,
    wait_for_us_done,
    write_us_csv,
)

anchor_port, uuid, out_dir_s = sys.argv[1], sys.argv[2], sys.argv[3]
out_dir = Path(out_dir_s)
rows = []

cmd_log, resp = master_anchor_us_cmd(anchor_port, uuid, "USON 30", out_dir, "us_on", wait_s=2.0, setup_wait_s=12.0)
row = parse_us_status(resp)
row.update({"cycle": "1", "phase": "on"})
rows.append(row)
if not resp.startswith("OK USON"):
    master_anchor_us_cmd(anchor_port, uuid, "USOFF", out_dir, "us_off_after_failed_on", wait_s=1.5, setup_wait_s=5.0)
    write_us_csv(out_dir / "ultrasound_H.csv", rows)
    print(json.dumps({"success": False, "error": "uson_failed", "response": resp, "csv": str(out_dir / "ultrasound_H.csv")}, indent=2))
    raise SystemExit(3)

done, poll_rows = wait_for_us_done(anchor_port, uuid, 1, out_dir, 30)
rows.extend(poll_rows)
_, off_resp = master_anchor_us_cmd(anchor_port, uuid, "USOFF", out_dir, "us_off", wait_s=1.5, setup_wait_s=5.0)
off_row = parse_us_status(off_resp)
off_row.update({"cycle": "1", "phase": "off"})
rows.append(off_row)

write_us_csv(out_dir / "ultrasound_H.csv", rows)
print(json.dumps({
    "success": bool(done),
    "off_response": off_resp,
    "csv": str(out_dir / "ultrasound_H.csv"),
    "last": rows[-2] if len(rows) >= 2 else rows[-1],
}, indent=2))
raise SystemExit(0 if done else 4)
PY
  )
  local rc=$?
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ',us30,'
    _bio_csv_escape "${out}/ultrasound_H.csv"
    printf ','
    _bio_csv_escape "anchor=H; duration_s=30; ant_center_offset_mm=${BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM}; rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  echo "[us30] rc=${rc}"
  echo "[us30] csv=${out}/ultrasound_H.csv"
  return "${rc}"
}

bio_check_latest() {
  _bio_need_setup || return $?
  local latest
  latest="$(find "${BIOSPUR_SESSION_ROOT}" -name summary.json -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)"
  if [[ -z "${latest}" ]]; then
    echo "[check] no summary.json found under ${BIOSPUR_SESSION_ROOT}"
    return 1
  fi
  echo "[check] latest summary: ${latest}"
  python3 - "${latest}" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
d = json.loads(p.read_text())
print(json.dumps({
    "success": d.get("success"),
    "out_dir": d.get("out_dir"),
    "error": d.get("error", ""),
    "tag_capture_success": (d.get("tag_capture") or {}).get("success"),
    "raw_log": (d.get("tag_capture") or {}).get("raw_log"),
    "tr_all_csv": (d.get("tag_capture") or {}).get("tr_all_csv"),
}, indent=2))
PY
}

echo "[erlangen] helpers loaded from ${BASH_SOURCE[0]}"
echo "[erlangen] run: bio_setup"
