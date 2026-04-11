#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tx_ble_common.sh
source "${SCRIPT_DIR}/tx_ble_common.sh"

IMAGE_PATH=""
TEST_HASH=""
DO_CONFIRM=0
RESET_SETTLE_SEC=8

usage() {
  cat <<'EOF'
Run TX BLE OTA cycle: list -> upload -> list -> test -> reset -> post-reset list.

Usage:
  tx_ble_full_cycle.sh [--peer BSGR_TX01] [--hci 0] [--image /abs/path/zephyr.signed.bin]
                       [--hash <slot1_hash>] [--confirm] [--sudo]
EOF
}

extract_slot1_hash() {
  awk '
    /slot=1/ {slot1=1}
    slot1 && /hash:/ {print $2; exit}
  '
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer) PEER_NAME="$2"; shift 2 ;;
    --hci) HCI_INDEX="$2"; shift 2 ;;
    --mcumgr) MCUMGR_BIN="$2"; shift 2 ;;
    --image) IMAGE_PATH="$2"; shift 2 ;;
    --hash) TEST_HASH="$2"; shift 2 ;;
    --confirm) DO_CONFIRM=1; shift ;;
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

info "Step 1: image list (before upload)"
before_list="$(run_mcumgr_checked image list)"
echo "${before_list}"

info "Step 2: upload ${IMG}"
run_mcumgr_checked image upload "${IMG}" >/dev/null

info "Step 3: image list (after upload)"
after_upload_list="$(run_mcumgr_checked image list)"
echo "${after_upload_list}"

if [[ -z "${TEST_HASH}" ]]; then
  TEST_HASH="$(echo "${after_upload_list}" | extract_slot1_hash || true)"
fi
[[ -n "${TEST_HASH}" ]] || die "Could not auto-detect slot1 hash. Re-run with --hash <slot1_hash>."

info "Step 4: image test ${TEST_HASH}"
run_mcumgr_checked image test "${TEST_HASH}" >/dev/null

info "Step 5: reset target"
run_mcumgr_checked reset >/dev/null

info "Waiting ${RESET_SETTLE_SEC}s for re-advertise..."
sleep "${RESET_SETTLE_SEC}"

info "Step 6: image list (post-reset)"
post_reset_list="$(run_mcumgr_checked image list)"
echo "${post_reset_list}"

if [[ "${DO_CONFIRM}" -eq 1 ]]; then
  info "Step 7: image confirm"
  run_mcumgr_checked image confirm
else
  info "Skip confirm by default. Confirm manually after stability check:"
  echo "  ${SCRIPT_DIR}/tx_ble_confirm.sh --peer ${PEER_NAME} --hci ${HCI_INDEX} $([[ ${USE_SUDO} -eq 1 ]] && echo --sudo)"
fi

