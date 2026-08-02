#!/usr/bin/env bash
set -euo pipefail

# Phase-H fix3 CPUAPP-only update. CPUNET is byte-identical to fix1/fix2 and is
# not opened here: J-Link 9.24a may mass-erase this board when a fresh CPUNET
# debug session attempts to unsecure the network core.

readonly EXPECTED_SNR="1050070698"
readonly EXPECTED_APP_SHA="d3950097c859f8006044de24e1350cc80128821d372644bd5e9a67e04d2142af"
readonly EXPECTED_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"

if [ "$#" -ne 1 ]; then
	echo "Usage: $0 1050070698" >&2
	exit 2
fi
if [ "$1" != "$EXPECTED_SNR" ]; then
	echo "[error] target locked to Master_Tag SNR $EXPECTED_SNR; got $1" >&2
	exit 3
fi

snr="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$repo_root/../builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix3-relay2"
app_hex="$build_dir/zephyr/merged.hex"
net_hex="$build_dir/hci_ipc/zephyr/merged_CPUNET.hex"

for image in "$app_hex" "$net_hex"; do
	[ -f "$image" ] || {
		echo "[error] missing artifact: $image" >&2
		exit 4
	}
done

actual_app_sha="$(sha256sum "$app_hex" | awk '{print $1}')"
actual_net_sha="$(sha256sum "$net_hex" | awk '{print $1}')"
[ "$actual_app_sha" = "$EXPECTED_APP_SHA" ] || {
	echo "[error] CPUAPP SHA mismatch: expected=$EXPECTED_APP_SHA actual=$actual_app_sha" >&2
	exit 5
}
[ "$actual_net_sha" = "$EXPECTED_NET_SHA" ] || {
	echo "[error] CPUNET SHA mismatch: expected=$EXPECTED_NET_SHA actual=$actual_net_sha" >&2
	exit 6
}

command_file="$(mktemp -t master_tag_fix3_XXXXXX.jlink)"
output_file="$(mktemp -t master_tag_fix3_XXXXXX.log)"
trap 'rm -f "$command_file" "$output_file"' EXIT

{
	echo "si 1"
	echo "speed 4000"
	echo "device nrf5340_xxaa_app"
	echo "if SWD"
	echo "connect"
	echo "halt"
	echo "loadfile $app_hex"
	echo "mem32 0x00000000 2"
	echo "exit"
} >"$command_file"

echo "[guard] target=Master_Tag CPUAPP SNR=$snr"
echo "[guard] marker=master-tag-carrier-v2-fix3"
echo "[guard] CPUAPP sha256=$actual_app_sha"
echo "[guard] CPUNET unchanged sha256=$actual_net_sha"
echo "[guard] capture=must_be_idle CPUNET_session=forbidden"

set +e
JLinkExe -NoGui 1 -ExitOnError 1 \
	-SelectEmuBySN "$snr" -CommanderScript "$command_file" 2>&1 |
	tee "$output_file"
rc=${PIPESTATUS[0]}
set -e

if [ "$rc" -ne 0 ] ||
	grep -qiE 'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' \
		"$output_file"; then
	echo "[error] explicit-SNR CPUAPP programming failed closed: rc=$rc" >&2
	exit 7
fi
if ! grep -q 'O.K.' "$output_file"; then
	echo "[error] J-Link did not report successful programming/verification" >&2
	exit 8
fi

echo "[ok] carrier-v2-fix3 CPUAPP programmed and verified"
echo "[HARD GATE] Physically cold power-cycle Master_Tag; J-Link reset is not accepted."
