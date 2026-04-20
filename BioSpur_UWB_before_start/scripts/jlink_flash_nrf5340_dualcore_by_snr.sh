#!/usr/bin/env bash
set -euo pipefail

# Non-interactive dual-core flash helper for nRF5340.
#
# Usage:
#   scripts/jlink_flash_nrf5340_dualcore_by_snr.sh <snr> <build_dir> [speed_khz]
#
# It expects these standard artifacts inside <build_dir>:
#   - hci_ipc/zephyr/merged_CPUNET.hex
#   - zephyr/merged.hex

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <snr> <build_dir> [speed_khz]" >&2
  exit 2
fi

SNR="$1"
BUILD_DIR="$2"
SPEED_KHZ="${3:-4000}"

NET_HEX="${BUILD_DIR}/hci_ipc/zephyr/merged_CPUNET.hex"
APP_HEX="${BUILD_DIR}/zephyr/merged.hex"

if [ ! -f "$NET_HEX" ]; then
  echo "[error] CPUNET image not found: $NET_HEX" >&2
  exit 3
fi

if [ ! -f "$APP_HEX" ]; then
  echo "[error] CPUAPP image not found: $APP_HEX" >&2
  exit 4
fi

tmp_net="$(mktemp -t jlink_nrf5340_net_${SNR}_XXXXXX.jlink)"
tmp_app="$(mktemp -t jlink_nrf5340_app_${SNR}_XXXXXX.jlink)"

cleanup() {
  rm -f "$tmp_net" "$tmp_app" || true
}
trap cleanup EXIT

cat >"$tmp_net" <<EOF
si 1
speed ${SPEED_KHZ}
device nrf5340_xxaa_net
if SWD
r
loadfile ${NET_HEX}
r
g
exit
EOF

cat >"$tmp_app" <<EOF
si 1
speed ${SPEED_KHZ}
device nrf5340_xxaa_app
if SWD
r
loadfile ${APP_HEX}
r
g
exit
EOF

echo "tool=jlink_flash_nrf5340_dualcore_by_snr snr=${SNR} build_dir=${BUILD_DIR} speed_khz=${SPEED_KHZ}"
echo "cpunet=${NET_HEX}"
echo "cpuapp=${APP_HEX}"

echo "[1/2] flash CPUNET"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$tmp_net"

echo "[2/2] flash CPUAPP"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$tmp_app"

echo "[ok] dual-core flash done"
