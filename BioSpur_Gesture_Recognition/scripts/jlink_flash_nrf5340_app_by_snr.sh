#!/usr/bin/env bash
set -euo pipefail

usage() {
	echo "usage: $0 <jlink-snr> <app-image.hex> [speed-khz]" >&2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	usage
	exit 2
fi

snr="$1"
image="$(readlink -f "$2")"
speed="${3:-1000}"
JLINK_EXE="${JLINK_EXE:-JLinkExe}"

if [ ! -f "$image" ]; then
	echo "image not found: ${image}" >&2
	exit 1
fi

"${JLINK_EXE}" -device NRF5340_XXAA_APP -if SWD -speed "${speed}" -SelectEmuBySN "${snr}" <<EOF
connect
h
loadfile ${image}
r
g
q
EOF
