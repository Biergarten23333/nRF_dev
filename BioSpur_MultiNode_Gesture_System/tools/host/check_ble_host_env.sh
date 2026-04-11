#!/usr/bin/env bash
set -euo pipefail

PEER_NAME="${PEER_NAME:-BSGR_TX01}"
HCI_INDEX="${HCI_INDEX:-0}"
MCUMGR_BIN="${MCUMGR_BIN:-mcumgr}"
HCI_DEV="hci${HCI_INDEX}"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

info() {
  echo "[INFO] $*"
}

need() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

need "${MCUMGR_BIN}"
need bluetoothctl
need hciconfig
need rfkill

info "mcumgr path: $(command -v "${MCUMGR_BIN}")"
info "mcumgr version: $("${MCUMGR_BIN}" version || true)"

hciconfig "${HCI_DEV}" >/dev/null 2>&1 || die "Adapter ${HCI_DEV} not found"
info "${HCI_DEV} exists"

if rfkill list bluetooth | grep -q "Hard blocked: yes"; then
  die "Bluetooth hard blocked"
fi
if rfkill list bluetooth | grep -q "Soft blocked: yes"; then
  die "Bluetooth soft blocked"
fi
info "rfkill state OK"

info "Quick scan for ${PEER_NAME}"
scan_out="$(bluetoothctl --timeout 10 scan on 2>&1 || true)"
if grep -q "${PEER_NAME}" <<<"${scan_out}"; then
  info "TX peer visible in scan"
else
  echo "[WARN] ${PEER_NAME} not seen in this scan window"
fi

info "Testing non-root mcumgr BLE image list"
set +e
mcumgr_out="$("${MCUMGR_BIN}" --conntype ble --hci "${HCI_INDEX}" --name "${PEER_NAME}" image list 2>&1)"
rc=$?
set -e

if [[ ${rc} -eq 0 ]]; then
  info "Non-root mcumgr BLE works"
  echo "${mcumgr_out}"
  exit 0
fi

echo "${mcumgr_out}"
if grep -q "can't down device: operation not permitted" <<<"${mcumgr_out}"; then
  echo "[RESULT] Host BLE privilege blocker detected."
  echo "[NEXT] Run: sudo ${MCUMGR_BIN} --conntype ble --hci ${HCI_INDEX} --name ${PEER_NAME} image list"
  exit 2
fi

echo "[RESULT] Non-root mcumgr failed for a different reason; inspect output above."
exit 3

