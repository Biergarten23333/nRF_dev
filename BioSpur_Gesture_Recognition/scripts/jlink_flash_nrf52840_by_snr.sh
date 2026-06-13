#!/usr/bin/env bash
set -euo pipefail

usage() {
	echo "EMERGENCY ONLY: direct J-Link flash of the GR nRF52840 is disabled by default." >&2
	echo "usage: GR_EMERGENCY_DIRECT_FLASH=YES $0 <jlink-snr> <image.hex> [speed-khz]" >&2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	usage
	exit 2
fi

if [ "${GR_EMERGENCY_DIRECT_FLASH:-}" != "YES" ]; then
	usage
	echo "" >&2
	echo "Normal GR module updates must use BLE OTA through BioSpur-GR/B120." >&2
	echo "Use this only when OTA/bootloader recovery is broken and the user explicitly approves direct probe flashing." >&2
	exit 1
fi

snr="$1"
image="$(readlink -f "$2")"
speed="${3:-4000}"
JLINK_EXE="${JLINK_EXE:-JLinkExe}"

if [ ! -f "$image" ]; then
	echo "image not found: ${image}" >&2
	exit 1
fi

"${JLINK_EXE}" -device NRF52840_XXAA -if SWD -speed "${speed}" -SelectEmuBySN "${snr}" <<EOF
connect
r
h
loadfile ${image}
r
g
q
EOF
