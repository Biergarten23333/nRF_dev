#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tx_ble_common.sh
source "${SCRIPT_DIR}/tx_ble_common.sh"

SCAN_TIMEOUT=15

usage() {
  cat <<'EOF'
Probe TX BLE advertising and verify SMP/NUS services.

Usage:
  tx_ble_probe.sh [--peer BSGR_TX01] [--hci 0] [--scan-timeout 15]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --peer)
      PEER_NAME="$2"
      shift 2
      ;;
    --hci)
      HCI_INDEX="$2"
      shift 2
      ;;
    --scan-timeout)
      SCAN_TIMEOUT="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

check_prereqs_basic
check_hci_ready
require_cmd python3

info "Scanning for ${PEER_NAME}"
print_cmd bluetoothctl --timeout "${SCAN_TIMEOUT}" scan on
scan_out="$(bluetoothctl --timeout "${SCAN_TIMEOUT}" scan on 2>&1 || true)"
echo "${scan_out}"
grep -q "${PEER_NAME}" <<<"${scan_out}" || die "TX not advertising (${PEER_NAME} not seen in scan output)"

info "Probing GATT services for ${PEER_NAME}"
python3 - <<'PY' "${PEER_NAME}"
import asyncio
import sys
from bleak import BleakScanner, BleakClient

target_name = sys.argv[1]
smp_uuid = "8d53dc1d-1db7-4cd3-868b-8a527460aa84"
nus_uuid = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"

async def main():
    devs = await BleakScanner.discover(timeout=12.0)
    tgt = next((d for d in devs if (d.name or "") == target_name), None)
    if tgt is None:
        print("[ERROR] Peer not reachable in Bleak scan")
        raise SystemExit(2)
    print(f"[INFO] Found peer: {tgt.address} {tgt.name}")
    async with BleakClient(tgt.address, timeout=15.0) as client:
        uuids = [s.uuid.lower() for s in client.services]
        has_smp = smp_uuid in uuids
        has_nus = nus_uuid in uuids
        print(f"[INFO] HAS_SMP={has_smp}")
        print(f"[INFO] HAS_NUS={has_nus}")
        if not has_smp:
            print("[ERROR] SMP DFU service missing")
            raise SystemExit(3)

asyncio.run(main())
PY

info "Probe successful: ${PEER_NAME} advertising and SMP present"

