#!/usr/bin/env bash
set -euo pipefail

MCUMGR_BIN="${MCUMGR_BIN:-$(command -v mcumgr || true)}"
APPLY=0

usage() {
  cat <<'EOF'
Example helper for capability-based mcumgr BLE access.

Default mode is DRY RUN (no changes).
Use --apply only in an interactive sudo session.

Usage:
  setcap_mcumgr_example.sh [--apply] [--mcumgr /path/to/mcumgr]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --mcumgr) MCUMGR_BIN="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -n "${MCUMGR_BIN}" ]] || { echo "mcumgr not found" >&2; exit 1; }

echo "[INFO] Target binary: ${MCUMGR_BIN}"
echo "[INFO] Command:"
echo "  sudo setcap cap_net_admin,cap_net_raw+eip \"${MCUMGR_BIN}\""
echo "  getcap \"${MCUMGR_BIN}\""
echo "  mcumgr --conntype ble --hci 0 --name BSGR_TX01 image list"

if [[ "${APPLY}" -eq 1 ]]; then
  sudo setcap cap_net_admin,cap_net_raw+eip "${MCUMGR_BIN}"
  getcap "${MCUMGR_BIN}"
fi

