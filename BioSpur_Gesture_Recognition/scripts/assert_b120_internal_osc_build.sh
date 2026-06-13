#!/usr/bin/env bash
set -euo pipefail

usage() {
	echo "usage: $0 <central_b120-build-dir-or-merged.hex>" >&2
}

if [ "$#" -ne 1 ]; then
	usage
	exit 2
fi

target="$1"
if [ -f "$target" ]; then
	build_dir="$(dirname "$(readlink -f "$target")")"
else
	build_dir="$(readlink -f "$target")"
fi

app_config="${build_dir}/central_b120/zephyr/.config"
net_config="${build_dir}/ipc_radio/zephyr/.config"

need_line() {
	local file="$1"
	local pattern="$2"

	if ! grep -qxF "$pattern" "$file"; then
		echo "missing required config in ${file}: ${pattern}" >&2
		exit 1
	fi
}

need_absent() {
	local file="$1"
	local pattern="$2"

	if grep -qxF "$pattern" "$file"; then
		echo "forbidden config enabled in ${file}: ${pattern}" >&2
		exit 1
	fi
}

if [ ! -f "$app_config" ] || [ ! -f "$net_config" ]; then
	echo "cannot find sysbuild configs under ${build_dir}" >&2
	echo "expected:" >&2
	echo "  ${app_config}" >&2
	echo "  ${net_config}" >&2
	exit 1
fi

need_line "$app_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y"
need_line "$app_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y"
need_line "$app_config" "# CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL is not set"
need_line "$app_config" "# CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH is not set"
need_absent "$app_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL=y"
need_absent "$app_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH=y"

need_line "$net_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y"
need_line "$net_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y"
need_line "$net_config" "# CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL is not set"
need_line "$net_config" "# CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH is not set"
need_line "$net_config" "CONFIG_BT_CTLR_PHY_2M=y"
need_absent "$net_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_XTAL=y"
need_absent "$net_config" "CONFIG_CLOCK_CONTROL_NRF_K32SRC_SYNTH=y"

echo "B120 internal LFRC build check OK: ${build_dir}"
