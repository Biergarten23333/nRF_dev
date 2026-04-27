#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${1:-${ROOT_DIR}/build-uwb-listener-ultrace/merged.hex}"
SNR="${BIOSPUR_LISTENER_SN:-760185886}"
SNR_PADDED="$(printf '%012d' "${SNR}")"
SERIAL_LINK="/dev/serial/by-id/usb-SEGGER_J-Link_${SNR_PADDED}-if00"
JLINK_TIMEOUT_S="${JLINK_TIMEOUT_S:-90}"

if [[ ! -f "${IMAGE}" ]]; then
  echo "missing image: ${IMAGE}" >&2
  exit 2
fi

if [[ ! -e "${SERIAL_LINK}" ]]; then
  echo "warning: no by-id serial link for J-Link SNR ${SNR}: expected ${SERIAL_LINK}" >&2
  echo "available SEGGER ports:" >&2
  ls -1 /dev/serial/by-id/usb-SEGGER_J-Link_*-if00 2>/dev/null >&2 || true
  echo "continuing; JLinkExe will still select the probe by SNR ${SNR}" >&2
fi

cmd_file="$(mktemp)"
trap 'rm -f "${cmd_file}"' EXIT

cat >"${cmd_file}" <<EOF
r
h
loadfile ${IMAGE}
r
g
q
EOF

echo "tool=flash_uwb_listener_jlink snr=${SNR} image=${IMAGE} mode=jlinkexe_nogui"
timeout "${JLINK_TIMEOUT_S}" JLinkExe \
  -NoGui 1 \
  -SelectEmuBySN "${SNR}" \
  -device NRF52832_XXAA \
  -if SWD \
  -speed 4000 \
  -autoconnect 1 \
  -CommanderScript "${cmd_file}"
echo "tool=flash_uwb_listener_jlink action=ok snr=${SNR}"
