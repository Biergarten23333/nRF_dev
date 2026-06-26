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
export BIOSPUR_CAPTURE_ROOT="${BIOSPUR_CAPTURE_ROOT:-${BIOSPUR_ERLANGEN_ROOT}/captures}"
export BIOSPUR_DOC_ROOT="${BIOSPUR_ERLANGEN_ROOT}/docs"

export BIOSPUR_ANCHOR_SNR="${BIOSPUR_ANCHOR_SNR:-960148546}"
export BIOSPUR_TAG_SNR="${BIOSPUR_TAG_SNR:-1050070698}"
export BIOSPUR_F_UUID="${BIOSPUR_F_UUID:-840C68591E90019821AACFF1B73AAA34}"
export BIOSPUR_G_UUID="${BIOSPUR_G_UUID:-B3087BC3D87CCCD316AEDC6B71D6677F}"
export BIOSPUR_H_UUID="${BIOSPUR_H_UUID:-B1E487C2B1FD740D1442206A1857DFA1}"
export BIOSPUR_US_F_ANTENNA_CENTER_OFFSET_MM="${BIOSPUR_US_F_ANTENNA_CENTER_OFFSET_MM:-122}"
export BIOSPUR_US_G_ANTENNA_CENTER_OFFSET_MM="${BIOSPUR_US_G_ANTENNA_CENTER_OFFSET_MM:-126}"
export BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM="${BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM:-107}"
export BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE="${BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE:-1}"
export BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE="${BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE:-0}"
export BIOSPUR_RESET_TAG_BEFORE_CAPTURE="${BIOSPUR_RESET_TAG_BEFORE_CAPTURE:-1}"
export BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP="${BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP:-1}"
export BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE="${BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE:-1}"
export BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE="${BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE:-1}"

# These are the dual-master B120 desktop paths. On another machine, run
# `bio_ports` and override them if /dev/serial/by-id names differ.
export BIOSPUR_ANCHOR_PORT="${BIOSPUR_ANCHOR_PORT:-/dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00}"
export BIOSPUR_TAG_PORT="${BIOSPUR_TAG_PORT:-/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00}"
export BIOSPUR_CIR_TAG_USB_PORT="${BIOSPUR_CIR_TAG_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00}"
export BIOSPUR_CIR_A_USB_PORT="${BIOSPUR_CIR_A_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760184781-if00}"
export BIOSPUR_CIR_B_USB_PORT="${BIOSPUR_CIR_B_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760185876-if00}"
export BIOSPUR_CIR_C_USB_PORT="${BIOSPUR_CIR_C_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760185878-if00}"
export BIOSPUR_CIR_D_USB_PORT="${BIOSPUR_CIR_D_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760184974-if00}"
export BIOSPUR_CIR_E_USB_PORT="${BIOSPUR_CIR_E_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760185904-if00}"
export BIOSPUR_CIR_F_USB_PORT="${BIOSPUR_CIR_F_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760186124-if00}"
export BIOSPUR_CIR_G_USB_PORT="${BIOSPUR_CIR_G_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760185889-if00}"
export BIOSPUR_CIR_H_USB_PORT="${BIOSPUR_CIR_H_USB_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760184500-if00}"
export BIOSPUR_CIR_FULL_USB_PORTS="${BIOSPUR_CIR_FULL_USB_PORTS:-TAG=${BIOSPUR_CIR_TAG_USB_PORT},A=${BIOSPUR_CIR_A_USB_PORT},B=${BIOSPUR_CIR_B_USB_PORT},C=${BIOSPUR_CIR_C_USB_PORT},D=${BIOSPUR_CIR_D_USB_PORT},E=${BIOSPUR_CIR_E_USB_PORT},F=${BIOSPUR_CIR_F_USB_PORT},G=${BIOSPUR_CIR_G_USB_PORT},H=${BIOSPUR_CIR_H_USB_PORT}}"

_bio_ts() {
  date +%Y%m%d_%H%M%S
}

_bio_usage() {
  cat <<'EOF'
BioSpur Erlangen helpers:

  bio_setup [session_name]
      Create/select a session under captures/.

  static -id ID01 [-s 120] [-cir off|compact|full]
      Capture BSF66F for 120 s by default.

  roto -id R01 [-s 120] [-cir off|compact|full]
      Capture BS2DCE,BSDC91 for 120 s by default.

  wand -id W01 [-s 120] [-cir off|compact|full]
      Capture BS9336,BS955A,BSCCF4 for 120 s by default.

  free -id F01 -targets BSF66F,BS2DCE [-s 120] [-cir off|compact|full]
      Capture an explicit comma-separated BS tag roster for 120 s by default.

  sweep -id SW01 [-n 1000] [-p 10]
      AutoPos sweep with configurable formal sets and prewarm sets.

  us30 -id US01
      Standalone F/G/H ultrasound 30 s capture, writes ultrasound_F/G/H.csv.

  bio_check_latest
      Print the latest summary.json under the current session.

  bio_note ID "free text"
      Append an experiment note to session_notes.csv.

  bio_ports
      Show configured serial ports and visible /dev/serial/by-id entries.

  bio_reset_masters
      Reset Master_Anchor and Master_Tag by explicit J-Link SNR, then wait for CDC.

  bio_all_anchor_responder
      Set all discovered anchors to runtime responder through Master_Anchor and
      verify the runtime responder ack.

  bio_usb_on
      Disable Linux runtime power management for visible BioSpur/J-Link tty
      devices when permitted by system udev rules. Does not prompt for sudo.

Environment:
  BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE=1
      Reset selected master boards before capture-scene runs.

  BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE=0
      Do not reset Master_Anchor before tag capture by default. This avoids
      Master_Anchor boot discovery racing Master_Tag for BSF66F.

  BIOSPUR_RESET_TAG_BEFORE_CAPTURE=1
      Reset Master_Tag before tag capture.

  BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP=1
      Reset Master_Anchor before AutoPos sweep.

  BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1
      Do not force Master_Anchor through AUTOPOS responder preflight before
      tag capture. Erlangen flow should run sweep first, then capture tags.

  BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE=1
      Keep already-online Master_Tag to BSxxxx links instead of clearing them
      with mode recv before each capture.
EOF
}

help_erlangen() {
  _bio_usage
}

bio_ports() {
  echo "[ports] BIOSPUR_ANCHOR_PORT=${BIOSPUR_ANCHOR_PORT}"
  echo "[ports] BIOSPUR_TAG_PORT=${BIOSPUR_TAG_PORT}"
  echo "[ports] BIOSPUR_CIR_FULL_USB_PORTS=${BIOSPUR_CIR_FULL_USB_PORTS}"
  echo "[ports] BIOSPUR_ANCHOR_SNR=${BIOSPUR_ANCHOR_SNR}"
  echo "[ports] BIOSPUR_TAG_SNR=${BIOSPUR_TAG_SNR}"
  echo
  echo "[ports] visible /dev/serial/by-id:"
  ls -l /dev/serial/by-id 2>/dev/null || true
  echo
  [[ -e "${BIOSPUR_ANCHOR_PORT}" ]] || echo "[WARN] Master_Anchor port path does not exist on this machine."
  [[ -e "${BIOSPUR_TAG_PORT}" ]] || echo "[WARN] Master_Tag port path does not exist on this machine."
}

bio_usb_on() {
  local dev sys p value wrote=0 blocked=0
  for dev in /dev/ttyACM*; do
    [[ -e "${dev}" ]] || continue
    sys="$(udevadm info -q path -n "${dev}" 2>/dev/null || true)"
    [[ -n "${sys}" ]] || continue
    for p in \
      "/sys${sys}/power/control" \
      "/sys$(dirname "${sys}")/power/control" \
      "/sys$(dirname "$(dirname "${sys}")")/power/control"; do
      [[ -e "${p}" ]] || continue
      value="$(cat "${p}" 2>/dev/null || true)"
      if [[ "${value}" = "on" ]]; then
        echo "[usb] ${dev}: ${p} already on"
        continue
      fi
      if [[ -w "${p}" ]]; then
        echo "[usb] ${dev}: ${p} -> on"
        printf 'on\n' > "${p}" || return $?
        wrote=1
      else
        echo "[WARN] ${dev}: ${p} is ${value:-unknown} and not writable by current user." >&2
        blocked=1
      fi
    done
  done
  echo "[usb] current power/control values:"
  for dev in /dev/ttyACM*; do
    [[ -e "${dev}" ]] || continue
    sys="$(udevadm info -q path -n "${dev}" 2>/dev/null || true)"
    [[ -n "${sys}" && -e "/sys${sys}/power/control" ]] || continue
    printf '[usb] %s ' "${dev}"
    cat "/sys${sys}/power/control"
  done
  if [[ "${blocked}" = "1" ]]; then
    echo "[ERR] USB power control needs the one-time udev rule from README.md." >&2
    echo "      Install it once, then replug the hub or run: sudo udevadm trigger" >&2
    return 2
  fi
  [[ "${wrote}" = "1" ]] && echo "[usb] updated writable power controls"
}

_bio_jlink_reset_snr() {
  local snr="$1"
  local label="$2"
  local cmdfile
  local core
  local rc
  local overall=0
  if ! command -v JLinkExe >/dev/null 2>&1; then
    echo "[ERR] JLinkExe not found; cannot reset ${label} (${snr})." >&2
    return 2
  fi
  for core in NET APP; do
    cmdfile="$(mktemp)"
    {
      echo "r"
      echo "g"
      echo "q"
    } > "${cmdfile}"
    echo "[reset] ${label}: J-Link reset ${core} snr=${snr}"
    timeout 25s JLinkExe \
      -NoGui 1 \
      -SelectEmuBySN "${snr}" \
      -device "NRF5340_XXAA_${core}" \
      -if SWD \
      -speed 4000 \
      -autoconnect 1 \
      -CommanderScript "${cmdfile}"
    rc=$?
    rm -f "${cmdfile}"
    if [[ "${rc}" -ne 0 ]]; then
      overall="${rc}"
    fi
  done
  return "${overall}"
}

_bio_wait_for_path() {
  local label="$1"
  local path="$2"
  local timeout_s="${3:-20}"
  local end=$((SECONDS + timeout_s))
  while (( SECONDS < end )); do
    [[ -e "${path}" ]] && {
      echo "[reset] ${label} CDC present: ${path}"
      return 0
    }
    sleep 0.5
  done
  echo "[ERR] ${label} CDC did not appear within ${timeout_s}s: ${path}" >&2
  return 1
}

bio_reset_masters() {
  echo "[reset] reset both master boards before capture"
  _bio_jlink_reset_snr "${BIOSPUR_ANCHOR_SNR}" "Master_Anchor" || return $?
  _bio_jlink_reset_snr "${BIOSPUR_TAG_SNR}" "Master_Tag" || return $?
  sleep 2
  _bio_wait_for_path "Master_Anchor" "${BIOSPUR_ANCHOR_PORT}" 25 || return $?
  _bio_wait_for_path "Master_Tag" "${BIOSPUR_TAG_PORT}" 25 || return $?
  bio_ports
}

bio_all_anchor_responder() {
  _bio_need_setup || return $?
  [[ -e "${BIOSPUR_ANCHOR_PORT}" ]] || {
    echo "[ERR] Master_Anchor port path does not exist: ${BIOSPUR_ANCHOR_PORT}" >&2
    return 2
  }
  local out="${BIOSPUR_SESSION_ROOT}/manual_all_anchor_responder_$(_bio_ts)"
  mkdir -p "${out}"
  echo "[responder] set all anchors to runtime responder"
  echo "[responder] out=${out}"
  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 scripts/verify_all_anchor_responder_runtime.py \
      --port "${BIOSPUR_ANCHOR_PORT}" \
      --out-dir "${out}/verify_all_anchor_responder_runtime" \
      --live-output \
      --verbose 2
  )
  local rc=$?
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "ALL_RESPONDER"
    printf ',anchor_responder,'
    _bio_csv_escape "${out}"
    printf ','
    _bio_csv_escape "rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  echo "[responder] rc=${rc}"
  return "${rc}"
}

bio_reset_capture_controllers() {
  echo "[reset] capture reset: anchor=${BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE} tag=${BIOSPUR_RESET_TAG_BEFORE_CAPTURE}"
  if [[ "${BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE}" = "1" ]]; then
    _bio_jlink_reset_snr "${BIOSPUR_ANCHOR_SNR}" "Master_Anchor" || return $?
  fi
  if [[ "${BIOSPUR_RESET_TAG_BEFORE_CAPTURE}" = "1" ]]; then
    _bio_jlink_reset_snr "${BIOSPUR_TAG_SNR}" "Master_Tag" || return $?
  fi
  sleep 2
  if [[ "${BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE}" = "1" ]]; then
    _bio_wait_for_path "Master_Anchor" "${BIOSPUR_ANCHOR_PORT}" 25 || return $?
  fi
  if [[ "${BIOSPUR_RESET_TAG_BEFORE_CAPTURE}" = "1" ]]; then
    _bio_wait_for_path "Master_Tag" "${BIOSPUR_TAG_PORT}" 25 || return $?
  fi
  bio_ports
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
  BIO_CIR="off"
  BIO_FULL_CIR_DURATION="30"
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
      -cir|--cir|--tag-cir)
        BIO_CIR="${2:-}"
        shift 2
        ;;
      -cir-s|--cir-seconds|--full-cir-duration-s)
        BIO_FULL_CIR_DURATION="${2:-}"
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
  case "${BIO_CIR}" in
    off|compact|full) ;;
    *)
      echo "[ERR] Invalid CIR mode: ${BIO_CIR} (expected off, compact, or full)" >&2
      return 2
      ;;
  esac
}

_bio_run_capture() {
  local kind="$1"
  local id="$2"
  local duration="$3"
  local targets="$4"
  local tag_cir="${5:-off}"
  local full_cir_duration="${6:-30}"
  local base="${kind}_${id}_${targets//,/_}_${duration}s"
  local out="${BIOSPUR_SESSION_ROOT}/${base}"
  local tdma_profile="motion"

  if [[ -z "${BIOSPUR_SESSION_ROOT:-}" ]]; then
    echo "[ERR] Run bio_setup first." >&2
    return 2
  fi
  if [[ ! -d "${BIOSPUR_BCAST_DIR}" ]]; then
    echo "[ERR] Broadcast directory not found: ${BIOSPUR_BCAST_DIR}" >&2
    return 2
  fi
  if [[ "${BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE}" = "1" ]]; then
    bio_reset_capture_controllers || return $?
  fi
  _bio_need_setup || return $?
  mkdir -p "${out}"
  echo "[capture] kind=${kind} id=${id} duration=${duration}s targets=${targets} cir=${tag_cir} full_cir_duration=${full_cir_duration}s"
  echo "[capture] tdma_profile=${tdma_profile} (capture scene does not change firmware PMODE)"
  echo "[capture] base_out=${out}"

  local preflight_args=()
  if [[ "${BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE}" = "1" ]]; then
    preflight_args+=(--skip-anchor-preflight)
    echo "[capture] anchor_preflight=skip (BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1)"
  else
    echo "[capture] anchor_preflight=run"
  fi

  local tag_link_args=()
  if [[ "${BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE}" = "1" ]]; then
    tag_link_args+=(--reuse-tag-links)
    echo "[capture] tag_links=reuse (BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE=1)"
  else
    echo "[capture] tag_links=clean-slate"
  fi

  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 scripts/run_dual_master_tdma_capture.py \
      --anchor-port "${BIOSPUR_ANCHOR_PORT}" \
      --anchor-snr "${BIOSPUR_ANCHOR_SNR}" \
      --tag-port "${BIOSPUR_TAG_PORT}" \
      --tag-snr "${BIOSPUR_TAG_SNR}" \
      "${preflight_args[@]}" \
      "${tag_link_args[@]}" \
      --duration "${duration}" \
      --targets "${targets}" \
      --tr-hz 10 \
      --tdma-profile "${tdma_profile}" \
      --tag-cir "${tag_cir}" \
      --full-cir-duration-s "${full_cir_duration}" \
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
    _bio_csv_escape "duration_s=${duration}; targets=${targets}; cir=${tag_cir}; full_cir_duration_s=${full_cir_duration}; rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"

  echo "[capture] rc=${rc}"
  echo "[capture] final_path=${final_path}"
  return "${rc}"
}

static() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: static -id ID01 [-s 120] [-cir off|compact|full] [-cir-s 30]"
    return 2
  }
  _bio_run_capture "static" "${BIO_ID}" "${BIO_DURATION}" "BSF66F" "${BIO_CIR}" "${BIO_FULL_CIR_DURATION}"
}

roto() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: roto -id R01 [-s 120] [-cir off|compact|full] [-cir-s 30]"
    return 2
  }
  _bio_run_capture "roto" "${BIO_ID}" "${BIO_DURATION}" "BS2DCE,BSDC91" "${BIO_CIR}" "${BIO_FULL_CIR_DURATION}"
}

wand() {
  _bio_parse_common_capture_args "$@" || {
    [[ $? -eq 9 ]] && echo "Usage: wand -id W01 [-s 120] [-cir off|compact|full] [-cir-s 30]"
    return 2
  }
  _bio_run_capture "wand3" "${BIO_ID}" "${BIO_DURATION}" "BS9336,BS955A,BSCCF4" "${BIO_CIR}" "${BIO_FULL_CIR_DURATION}"
}

free() {
  local id=""
  local duration="120"
  local targets=""
  local tag_cir="off"
  local full_cir_duration="30"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -id|--id)
        id="${2:-}"
        shift 2
        ;;
      -s|--seconds|--duration)
        duration="${2:-}"
        shift 2
        ;;
      -targets|--targets)
        targets="${2:-}"
        shift 2
        ;;
      -cir|--cir|--tag-cir)
        tag_cir="${2:-}"
        shift 2
        ;;
      -cir-s|--cir-seconds|--full-cir-duration-s)
        full_cir_duration="${2:-}"
        shift 2
        ;;
      -h|--help)
        echo "Usage: free -id F01 -targets BSF66F,BS2DCE [-s 120] [-cir off|compact|full] [-cir-s 30]"
        return 2
        ;;
      *)
        echo "[ERR] Unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  if [[ -z "${id}" ]]; then
    echo "[ERR] Missing -id, for example: free -id F01 -targets BSF66F,BS2DCE" >&2
    return 2
  fi
  if [[ -z "${targets}" ]]; then
    echo "[ERR] Missing -targets, for example: free -id F01 -targets BSF66F,BS2DCE" >&2
    return 2
  fi
  case "${tag_cir}" in
    off|compact|full) ;;
    *)
      echo "[ERR] Invalid CIR mode: ${tag_cir} (expected off, compact, or full)" >&2
      return 2
      ;;
  esac
  _bio_run_capture "free" "${id}" "${duration}" "${targets}" "${tag_cir}" "${full_cir_duration}"
}

sweep() {
  local id=""
  local sw_sets="1000"
  local prewarm_sets="10"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -id|--id)
        id="${2:-}"
        shift 2
        ;;
      -n|--sets|--sw-sets)
        sw_sets="${2:-}"
        shift 2
        ;;
      -p|--prewarm|--prewarm-sw-sets)
        prewarm_sets="${2:-}"
        shift 2
        ;;
      -h|--help)
        echo "Usage: sweep -id SW01 [-n 1000] [-p 10]"
        return 2
        ;;
      *)
        echo "[ERR] Unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  [[ -n "${id}" ]] || { echo "[ERR] Missing -id, for example: sweep -id SW01" >&2; return 2; }
  [[ "${sw_sets}" =~ ^[0-9]+$ && "${sw_sets}" -gt 0 ]] || { echo "[ERR] Invalid sweep sets: ${sw_sets}" >&2; return 2; }
  [[ "${prewarm_sets}" =~ ^[0-9]+$ ]] || { echo "[ERR] Invalid prewarm sets: ${prewarm_sets}" >&2; return 2; }
  if [[ -z "${BIOSPUR_SESSION_ROOT:-}" ]]; then
    echo "[ERR] Run bio_setup first." >&2
    return 2
  fi
  if [[ ! -d "${BIOSPUR_BCAST_DIR}" ]]; then
    echo "[ERR] Broadcast directory not found: ${BIOSPUR_BCAST_DIR}" >&2
    return 2
  fi
  if [[ "${BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP}" = "1" ]]; then
    echo "[sweep] reset Master_Anchor before AutoPos sweep"
    _bio_jlink_reset_snr "${BIOSPUR_ANCHOR_SNR}" "Master_Anchor" || return $?
    sleep 2
    _bio_wait_for_path "Master_Anchor" "${BIOSPUR_ANCHOR_PORT}" 25 || return $?
  fi
  _bio_need_setup || return $?

  local out="${BIOSPUR_SESSION_ROOT}/sweep_${id}_${sw_sets}_prewarm${prewarm_sets}_$(_bio_ts)"
  mkdir -p "${out}"
  echo "[sweep] id=${id}"
  echo "[sweep] sw_sets=${sw_sets}"
  echo "[sweep] prewarm=${prewarm_sets}"
  echo "[sweep] out=${out}"
  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 scripts/run_autopos_sweep_loop.py \
      --port "${BIOSPUR_ANCHOR_PORT}" \
      --order ABCDEFGH \
      --sw-sets "${sw_sets}" \
      --prewarm-sw-sets "${prewarm_sets}" \
      --round-retries 1 \
      --out-dir "${out}/sweep${sw_sets}" \
      --verbose 1 2>&1 | tee "${out}/sweep${sw_sets}.console.log"
  )
  local rc=$?
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ',sweep,'
    _bio_csv_escape "${out}"
    printf ','
    _bio_csv_escape "sw_sets=${sw_sets}; prewarm=${prewarm_sets}; rc=${rc}"
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

  local out="${BIOSPUR_SESSION_ROOT}/us_${id}_FGH_US30_$(_bio_ts)"
  mkdir -p "${out}"
  echo "[us30] id=${id}"
  echo "[us30] out=${out}"
  (
    cd "${BIOSPUR_BCAST_DIR}" && \
    python3 - \
      "${BIOSPUR_ANCHOR_PORT}" \
      "${out}" \
      "${BIOSPUR_F_UUID}" "${BIOSPUR_US_F_ANTENNA_CENTER_OFFSET_MM}" \
      "${BIOSPUR_G_UUID}" "${BIOSPUR_US_G_ANTENNA_CENTER_OFFSET_MM}" \
      "${BIOSPUR_H_UUID}" "${BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM}" <<'PY'
import json
import sys
import time
from pathlib import Path
import serial

import scripts.run_autopos_ultrasound_motion_triplet as usmod
from scripts.run_autopos_ultrasound_motion_triplet import (
    master_anchor_us_cmd,
    parse_us_status,
    wait_for_us_done,
    write_us_csv,
)

anchor_port, out_dir_s = sys.argv[1], sys.argv[2]
out_dir = Path(out_dir_s)
anchors = [
    ("F", "BS928B", sys.argv[3], float(sys.argv[4])),
    ("G", "BSEC88", sys.argv[5], float(sys.argv[6])),
    ("H", "BS506D", sys.argv[7], float(sys.argv[8])),
]

def read_for(ser, seconds):
    end = time.time() + seconds
    chunks = []
    while time.time() < end:
        data = ser.read(4096)
        if data:
            chunks.append(data.decode("utf-8", "ignore"))
    return "".join(chunks)

with serial.Serial(anchor_port, 115200, timeout=0.2) as ser:
    text = read_for(ser, 0.5)
    ser.write(b"mode autopos\n")
    ser.flush()
    text += read_for(ser, 3.0)
    (out_dir / "mode_autopos.log").write_text(text, encoding="utf-8")

summary = {"success": True, "anchors": {}, "out_dir": str(out_dir)}
for label, bs, uuid, offset in anchors:
    anchor_dir = out_dir / f"anchor_{label}"
    anchor_dir.mkdir(parents=True, exist_ok=True)
    usmod.US_H_ANTENNA_CENTER_OFFSET_MM = int(round(offset))
    rows = []

    _, before = master_anchor_us_cmd(anchor_port, uuid, "US?", anchor_dir, "before", wait_s=1.8, setup_wait_s=10.0)
    before_row = parse_us_status(before)
    before_row.update({"cycle": "1", "phase": "before"})
    rows.append(before_row)

    _, resp = master_anchor_us_cmd(anchor_port, uuid, "USON 30", anchor_dir, "us_on", wait_s=2.0, setup_wait_s=10.0)
    row = parse_us_status(resp)
    row.update({"cycle": "1", "phase": "on"})
    rows.append(row)

    if not resp.startswith("OK USON"):
        master_anchor_us_cmd(anchor_port, uuid, "USOFF", anchor_dir, "us_off_after_failed_on", wait_s=1.5, setup_wait_s=5.0)
        csv_path = out_dir / f"ultrasound_{label}.csv"
        write_us_csv(csv_path, rows)
        summary["success"] = False
        summary["anchors"][label] = {
            "success": False,
            "bs": bs,
            "uuid": uuid,
            "offset_mm": offset,
            "error": "uson_failed",
            "response": resp,
            "csv": str(csv_path),
        }
        continue

    done, poll_rows = wait_for_us_done(anchor_port, uuid, 1, anchor_dir, 30)
    rows.extend(poll_rows)
    _, off_resp = master_anchor_us_cmd(anchor_port, uuid, "USOFF", anchor_dir, "us_off", wait_s=1.5, setup_wait_s=5.0)
    off_row = parse_us_status(off_resp)
    off_row.update({"cycle": "1", "phase": "off"})
    rows.append(off_row)

    csv_path = out_dir / f"ultrasound_{label}.csv"
    write_us_csv(csv_path, rows)
    last = poll_rows[-1] if poll_rows else rows[-1]
    ok = bool(done)
    summary["success"] = summary["success"] and ok
    summary["anchors"][label] = {
        "success": ok,
        "bs": bs,
        "uuid": uuid,
        "offset_mm": offset,
        "off_response": off_resp,
        "csv": str(csv_path),
        "last": last,
    }

(out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, indent=2))
raise SystemExit(0 if summary["success"] else 4)
PY
  )
  local rc=$?
  {
    printf '%s,' "$(date -Is)"
    _bio_csv_escape "${id}"
    printf ',us30,'
    _bio_csv_escape "${out}"
    printf ','
    _bio_csv_escape "anchors=F,G,H; duration_s=30; offsets_mm=F:${BIOSPUR_US_F_ANTENNA_CENTER_OFFSET_MM}/G:${BIOSPUR_US_G_ANTENNA_CENTER_OFFSET_MM}/H:${BIOSPUR_US_H_ANTENNA_CENTER_OFFSET_MM}; rc=${rc}"
    printf '\n'
  } >> "${BIOSPUR_SESSION_ROOT}/session_notes.csv"
  echo "[us30] rc=${rc}"
  echo "[us30] csv=${out}/ultrasound_F.csv"
  echo "[us30] csv=${out}/ultrasound_G.csv"
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
brief = {
    "success": d.get("success"),
    "out_dir": d.get("out_dir"),
    "error": d.get("error", ""),
    "tag_capture_success": (d.get("tag_capture") or {}).get("success"),
    "raw_log": (d.get("tag_capture") or {}).get("raw_log"),
    "tr_all_csv": (d.get("tag_capture") or {}).get("tr_all_csv"),
}
print(json.dumps(brief, indent=2))

def data_lines(path: Path, skip_prefixes=()):
    if not path or not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        if any(line.startswith(prefix) for prefix in skip_prefixes):
            continue
        out.append(line)
    return out

tag = d.get("tag_capture") or {}
tr_s = tag.get("tr_all_csv") or d.get("tr_all_csv") or ""
tr_path = Path(tr_s) if tr_s else None
if tr_path and tr_path.exists():
    rows = data_lines(tr_path, skip_prefixes=("host_elapsed_s,",))
    print("[check:data] tr_all_csv:", tr_path)
    print("[check:data] tr_data_rows:", len(rows))
    if rows:
        print("[check:data] tr_first:", rows[0])
        print("[check:data] tr_last:", rows[-1])

raw_s = tag.get("raw_log") or d.get("raw_log") or ""
raw_path = Path(raw_s) if raw_s else None
if raw_path and raw_path.exists():
    notify = [
        line for line in data_lines(raw_path)
        if " notify: TR;" in line or " notify: TS;" in line or " notify: SW-" in line
    ]
    print("[check:data] raw_log:", raw_path)
    print("[check:data] raw_notify_rows:", len(notify))
    if notify:
        print("[check:data] raw_first_notify:", notify[0])
        print("[check:data] raw_last_notify:", notify[-1])

# Sweep summaries live under .../sweep1000/summary.json and use a different
# schema. Always prove real data by showing actual SW-* lines, not only counts.
if p.parent.name == "sweep1000":
    sweep_dir = p.parent
    sw_rows = []
    for log in sorted(sweep_dir.glob("round_*/master.log")):
        for line in data_lines(log):
            if "[AUTOPOS] SW-" in line:
                sw_rows.append(line)
    if not sw_rows:
        console = sweep_dir.parent / "sweep1000.console.log"
        for line in data_lines(console):
            if "[AUTOPOS] SW-" in line:
                sw_rows.append(line)
    rounds = d.get("rounds") if isinstance(d.get("rounds"), dict) else {}
    print("[check:data] sweep_dir:", sweep_dir)
    print("[check:data] sweep_rounds_with_summary:", ",".join(sorted(rounds)) or "-")
    print("[check:data] sweep_sw_rows:", len(sw_rows))
    if sw_rows:
        print("[check:data] sweep_first:", sw_rows[0])
        print("[check:data] sweep_last:", sw_rows[-1])
PY
}

echo "[erlangen] helpers loaded from ${BASH_SOURCE[0]}"
echo "[erlangen] run: bio_setup"
