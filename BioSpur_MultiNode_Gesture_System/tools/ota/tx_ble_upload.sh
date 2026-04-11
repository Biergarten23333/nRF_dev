#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tx_ble_common.sh
source "${SCRIPT_DIR}/tx_ble_common.sh"

IMAGE_PATH=""

usage() {
  cat <<'EOF'
Upload TX signed image over BLE mcumgr.

Usage:
  tx_ble_upload.sh [--peer BSGR_TX01] [--hci 0] [--image /abs/path/zephyr.signed.bin] [--sudo]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer) PEER_NAME="$2"; shift 2 ;;
    --hci) HCI_INDEX="$2"; shift 2 ;;
    --mcumgr) MCUMGR_BIN="$2"; shift 2 ;;
    --image) IMAGE_PATH="$2"; shift 2 ;;
    --sudo) USE_SUDO=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

check_prereqs_basic
check_hci_ready

if [[ -n "${IMAGE_PATH}" ]]; then
  IMG="$(resolve_tx_image_path "${IMAGE_PATH}")"
else
  print_cmd "${SCRIPT_DIR}/tx_ble_resolve_artifact.sh"
  IMG="$("${SCRIPT_DIR}/tx_ble_resolve_artifact.sh")"
fi

info "Uploading image: ${IMG}"
run_mcumgr_checked image upload "${IMG}"

