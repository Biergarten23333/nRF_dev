#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DEFAULT_PEER_NAME="BSGR_TX01"
DEFAULT_HCI_INDEX="0"
DEFAULT_MCUMGR_BIN="${MCUMGR_BIN:-mcumgr}"
DEFAULT_TX_BIN="${REPO_ROOT}/tx_node/build/tx_node/zephyr/zephyr.signed.bin"

PEER_NAME="${DEFAULT_PEER_NAME}"
HCI_INDEX="${DEFAULT_HCI_INDEX}"
MCUMGR_BIN="${DEFAULT_MCUMGR_BIN}"
USE_SUDO=0

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

info() {
  echo "[INFO] $*"
}

print_cmd() {
  printf "[CMD] "
  printf "%q " "$@"
  printf "\n"
}

require_cmd() {
  local cmd="$1"
  command -v "${cmd}" >/dev/null 2>&1 || die "Missing required command: ${cmd}"
}

check_prereqs_basic() {
  require_cmd "${MCUMGR_BIN}"
  require_cmd bluetoothctl
  require_cmd hciconfig
  require_cmd rfkill
}

check_hci_ready() {
  local hci_dev="hci${HCI_INDEX}"
  hciconfig "${hci_dev}" >/dev/null 2>&1 || die "Bluetooth adapter ${hci_dev} not found"
  if ! rfkill list bluetooth | grep -q "Soft blocked: no"; then
    die "Bluetooth appears soft-blocked by rfkill"
  fi
}

parse_common_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --peer)
        [[ $# -ge 2 ]] || die "--peer requires a value"
        PEER_NAME="$2"
        shift 2
        ;;
      --hci)
        [[ $# -ge 2 ]] || die "--hci requires a value"
        HCI_INDEX="$2"
        shift 2
        ;;
      --mcumgr)
        [[ $# -ge 2 ]] || die "--mcumgr requires a value"
        MCUMGR_BIN="$2"
        shift 2
        ;;
      --sudo)
        USE_SUDO=1
        shift
        ;;
      --help|-h)
        return 10
        ;;
      --)
        shift
        break
        ;;
      *)
        break
        ;;
    esac
  done

  echo "$@"
}

run_mcumgr() {
  local cmd=()
  if [[ "${USE_SUDO}" -eq 1 ]]; then
    cmd+=(sudo)
  fi
  cmd+=("${MCUMGR_BIN}" --conntype ble --hci "${HCI_INDEX}" --name "${PEER_NAME}" "$@")
  print_cmd "${cmd[@]}"
  "${cmd[@]}"
}

run_mcumgr_checked() {
  local output rc
  set +e
  output="$(run_mcumgr "$@" 2>&1)"
  rc=$?
  set -e
  if [[ ${rc} -ne 0 ]]; then
    echo "${output}" >&2
    if grep -q "can't down device: operation not permitted" <<<"${output}"; then
      die "Host privilege blocked BLE HCI control. Retry with --sudo in an interactive password session."
    fi
    if grep -q "can't init hci" <<<"${output}"; then
      die "HCI initialization failed. Check adapter state and privileges."
    fi
    die "mcumgr command failed"
  fi
  echo "${output}"
}

resolve_tx_image_path() {
  local image_path="${1:-${DEFAULT_TX_BIN}}"
  [[ -f "${image_path}" ]] && { echo "${image_path}"; return 0; }
  die "Signed TX image not found at ${image_path}. Build first: west build -s ${REPO_ROOT}/tx_node -b nrf52840dk/nrf52840 --sysbuild -d ${REPO_ROOT}/tx_node/build"
}

