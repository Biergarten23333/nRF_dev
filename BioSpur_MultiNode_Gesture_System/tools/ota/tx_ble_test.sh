#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tx_ble_common.sh
source "${SCRIPT_DIR}/tx_ble_common.sh"

SLOT1_HASH=""

usage() {
  cat <<'EOF'
Mark uploaded TX image as test.

Usage:
  tx_ble_test.sh --hash <slot1_hash> [--peer BSGR_TX01] [--hci 0] [--sudo]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hash) SLOT1_HASH="$2"; shift 2 ;;
    --peer) PEER_NAME="$2"; shift 2 ;;
    --hci) HCI_INDEX="$2"; shift 2 ;;
    --mcumgr) MCUMGR_BIN="$2"; shift 2 ;;
    --sudo) USE_SUDO=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

[[ -n "${SLOT1_HASH}" ]] || die "--hash is required"
check_prereqs_basic
check_hci_ready
run_mcumgr_checked image test "${SLOT1_HASH}"

