#!/usr/bin/env bash
set -euo pipefail

usage() {
	echo "usage: $0 <jlink-snr> <central_b120-build-dir> [speed-khz]" >&2
}

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	usage
	exit 2
fi

snr="$1"
build_dir="$(readlink -f "$2")"
speed="${3:-1000}"
JLINK_EXE="${JLINK_EXE:-JLinkExe}"

app_image="${build_dir}/merged.hex"
net_image="${build_dir}/merged_CPUNET.hex"

if [ ! -f "$app_image" ]; then
	echo "app image not found: ${app_image}" >&2
	exit 1
fi

if [ ! -f "$net_image" ]; then
	echo "network-core image not found: ${net_image}" >&2
	exit 1
fi

"$(dirname "${BASH_SOURCE[0]}")/assert_b120_internal_osc_build.sh" "$build_dir"

"${JLINK_EXE}" -device NRF5340_XXAA_APP -if SWD -speed "${speed}" -SelectEmuBySN "${snr}" <<EOF
connect
h
loadfile ${app_image}
r
g
q
EOF

"${JLINK_EXE}" -device NRF5340_XXAA_NET -if SWD -speed "${speed}" -SelectEmuBySN "${snr}" <<EOF
connect
h
loadfile ${net_image}
r
g
q
EOF

# Leave the application core running after the network core is flashed/reset.
"${JLINK_EXE}" -device NRF5340_XXAA_APP -if SWD -speed "${speed}" -SelectEmuBySN "${snr}" <<EOF
connect
r
g
q
EOF
