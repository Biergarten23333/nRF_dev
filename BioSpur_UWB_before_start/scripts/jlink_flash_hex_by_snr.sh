#!/usr/bin/env bash
set -euo pipefail

# Headless flashing helper that never triggers interactive probe selection.
# Usage:
#   scripts/jlink_flash_hex_by_snr.sh <snr> <device> <image.hex> [speed_khz]
#
# Examples:
#   scripts/jlink_flash_hex_by_snr.sh 683234364 nRF52840_xxAA build-master-control/merged.hex
#   scripts/jlink_flash_hex_by_snr.sh 760186071 nRF52840_xxAA build-anchor-unified-ota-quality-startup/merged.hex

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "Usage: $0 <snr> <device> <image.hex> [speed_khz]" >&2
  exit 2
fi

SNR="$1"
DEVICE="$2"
IMAGE="$3"
SPEED_KHZ="${4:-4000}"

if [ ! -f "$IMAGE" ]; then
  echo "[error] image not found: $IMAGE" >&2
  exit 3
fi

tmp="$(mktemp -t jlink_flash_${SNR}_XXXXXX.jlink)"
cleanup() { rm -f "$tmp" || true; }
trap cleanup EXIT

cat >"$tmp" <<EOF
si 1
speed ${SPEED_KHZ}
device ${DEVICE}
if SWD
r
erase
loadfile ${IMAGE}
r
g
exit
EOF

echo "tool=jlink_flash_hex_by_snr snr=${SNR} device=${DEVICE} image=${IMAGE} speed_khz=${SPEED_KHZ}"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$tmp"

