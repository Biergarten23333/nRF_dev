#!/usr/bin/env bash
set -euo pipefail

# Destructive relay4-payload Master_Tag ceremony. Every J-Link process is
# locked to probe 1050070698 and an explicit nRF5340 core device.

readonly EXPECTED_SNR="1050070698"
readonly EXPECTED_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly EXPECTED_APP_SHA="cbb5228e87f7b2e5a9afe96275f1f70254cd880235063af5babc5a47c561e472"
readonly EXPECTED_PAYLOAD_SHA="d0704307ce0ae12af48df7d38524117f3ee88501b831cec1a5fcc79e8abe4b1a"

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
build_dir="$repo_root/../builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix7-relay4"
net_hex="$build_dir/hci_ipc/zephyr/merged_CPUNET.hex"
app_hex="$build_dir/zephyr/merged.hex"
payload_bin="$repo_root/../builds/tag-fusion-link-relay4/tag/zephyr/zephyr.signed.bin"
payload_manifest="$build_dir/active_ota_payload.json"
log_dir="$repo_root/../logs/tag_selfconfirm_20260728/master_carrier_relay4_ceremony_$(date +%Y%m%d_%H%M%S)"
speed_khz=4000
mkdir -p "$log_dir"

for image in "$net_hex" "$app_hex" "$payload_bin" "$payload_manifest"; do
	[ -f "$image" ] || {
		echo "[error] missing artifact: $image" >&2
		exit 4
	}
done

actual_net_sha="$(sha256sum "$net_hex" | awk '{print $1}')"
actual_app_sha="$(sha256sum "$app_hex" | awk '{print $1}')"
actual_payload_sha="$(sha256sum "$payload_bin" | awk '{print $1}')"
[ "$actual_net_sha" = "$EXPECTED_NET_SHA" ] || {
	echo "[error] CPUNET SHA mismatch: expected=$EXPECTED_NET_SHA actual=$actual_net_sha" >&2
	exit 5
}
[ "$actual_app_sha" = "$EXPECTED_APP_SHA" ] || {
	echo "[error] CPUAPP SHA mismatch: expected=$EXPECTED_APP_SHA actual=$actual_app_sha" >&2
	exit 6
}
[ "$actual_payload_sha" = "$EXPECTED_PAYLOAD_SHA" ] || {
	echo "[error] OTA payload SHA mismatch: expected=$EXPECTED_PAYLOAD_SHA actual=$actual_payload_sha" >&2
	exit 7
}
grep -q '"fw_marker": "tag-fusion-link-v2-relay4"' "$payload_manifest" || {
	echo "[error] relay4 marker missing from built payload manifest" >&2
	exit 8
}
grep -q "\"signed_bin_sha256\": \"$EXPECTED_PAYLOAD_SHA\"" "$payload_manifest" || {
	echo "[error] relay4 SHA missing from built payload manifest" >&2
	exit 9
}
command -v JLinkExe >/dev/null 2>&1 || {
	echo "[error] JLinkExe not found" >&2
	exit 10
}

run_jlink() {
	local label="$1"
	local device="$2"
	shift 2
	local command_file="$log_dir/$label.jlink"
	local output_file="$log_dir/$label.log"
	local rc
	{
		echo "si 1"
		echo "speed $speed_khz"
		echo "device $device"
		echo "if SWD"
		echo "connect"
		for command in "$@"; do
			echo "$command"
		done
		echo "exit"
	} >"$command_file"
	set +e
	JLinkExe -NoGui 1 -ExitOnError 1 \
		-SelectEmuBySN "$snr" -Device "$device" -If SWD -Speed "$speed_khz" \
		-CommanderScript "$command_file" 2>&1 | tee "$output_file"
	rc=${PIPESTATUS[0]}
	set -e
	if [ "$rc" -ne 0 ] ||
		grep -qiE 'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' \
			"$output_file"; then
		echo "[error] J-Link session failed closed: label=$label device=$device rc=$rc" >&2
		return 12
	fi
}

ctrlap_erase() {
	local label="$1"
	local apsel="$2"
	local device="$3"
	local select_idr select_bank command_file output_file rc last_status
	select_idr="$(printf '0x%02X0000F0' "$apsel")"
	select_bank="$(printf '0x%02X000000' "$apsel")"
	command_file="$log_dir/erase_$label.jlink"
	output_file="$log_dir/erase_$label.log"
	{
		echo "si 1"
		echo "speed $speed_khz"
		echo "SWDSelect"
		echo "SWDWriteDP 1 0x50000000"
		echo "SWDWriteDP 2 $select_idr"
		echo "SWDReadAP 3"
		echo "SWDReadAP 3"
		echo "SWDWriteDP 2 $select_bank"
		echo "SWDWriteAP 1 1"
		for _ in $(seq 1 20); do
			echo "Sleep 100"
			echo "SWDReadAP 2"
			echo "SWDReadAP 2"
		done
		echo "exit"
	} >"$command_file"
	set +e
	JLinkExe -NoGui 1 -ExitOnError 1 \
		-SelectEmuBySN "$snr" -Device "$device" -If SWD -Speed "$speed_khz" \
		-CommanderScript "$command_file" 2>&1 | tee "$output_file"
	rc=${PIPESTATUS[0]}
	set -e
	if [ "$rc" -ne 0 ] ||
		grep -qiE 'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' \
			"$output_file"; then
		echo "[error] CTRL-AP $label session failed closed: rc=$rc" >&2
		return 13
	fi
	grep -q 'Read AP register 3 = 0x12880000' "$output_file" || {
		echo "[error] CTRL-AP $label IDR was not 0x12880000" >&2
		return 14
	}
	last_status="$(
		grep 'Read AP register 2 = ' "$output_file" | tail -n 1 |
			sed -E 's/.*= (0x[0-9A-Fa-f]+).*/\1/'
	)"
	[ "$last_status" = "0x00000000" ] || {
		echo "[error] CTRL-AP $label ERASEALLSTATUS=${last_status:-missing}" >&2
		return 15
	}
	echo "[ok] CTRL-AP $label ERASEALLSTATUS=0"
}

count_erased_words() {
	grep -E '^[[:xdigit:]]{8} = ' "$1" | grep -o 'FFFFFFFF' | wc -l
}

{
	echo "timestamp=$(date --iso-8601=seconds)"
	echo "probe_location=Master_Tag"
	echo "probe_snr=$snr"
	echo "carrier=master-tag-carrier-v2-fix7-relay4"
	echo "payload=tag-fusion-link-v2-relay4"
	echo "payload_sha256=$actual_payload_sha"
	echo "cpunet_sha256=$actual_net_sha"
	echo "cpuapp_sha256=$actual_app_sha"
} | tee "$log_dir/manifest.txt"

echo "[1/8] CPUNET CTRL-AP erase, APSEL=3"
ctrlap_erase network 3 nrf5340_xxaa_net

echo "[2/8] CPUAPP CTRL-AP erase, APSEL=2"
ctrlap_erase application 2 nrf5340_xxaa_app

echo "[3/8] fresh-session blank proof: CPUAPP settings/NVS @ 0x000f8000"
run_jlink blank_app_settings nrf5340_xxaa_app \
	"halt" "mem32 0x000f8000 8"
[ "$(count_erased_words "$log_dir/blank_app_settings.log")" -ge 8 ] || {
	echo "[error] CPUAPP settings erase not proven" >&2
	exit 16
}

echo "[4/8] fresh-session blank proof: CPUNET vector @ 0x01000000"
run_jlink blank_net_vector nrf5340_xxaa_net \
	"halt" "mem32 0x01000000 2"
[ "$(count_erased_words "$log_dir/blank_net_vector.log")" -ge 2 ] || {
	echo "[error] CPUNET vector erase not proven" >&2
	exit 17
}

echo "[5/8] program CPUNET first"
run_jlink program_net nrf5340_xxaa_net "loadfile $net_hex"
grep -q 'O.K.' "$log_dir/program_net.log" || {
	echo "[error] CPUNET Program & Verify proof missing" >&2
	exit 18
}

echo "[6/8] program CPUAPP second"
run_jlink program_app nrf5340_xxaa_app "loadfile $app_hex"
grep -q 'O.K.' "$log_dir/program_app.log" || {
	echo "[error] CPUAPP Program & Verify proof missing" >&2
	exit 19
}

echo "[7/8] fresh-session CPUAPP persistence proof"
run_jlink verify_app_vector nrf5340_xxaa_app \
	"halt" "mem32 0x00000000 2"
[ "$(count_erased_words "$log_dir/verify_app_vector.log")" -lt 2 ] || {
	echo "[error] CPUAPP vector remains blank after programming" >&2
	exit 20
}

echo "[8/8] CPUNET persistence will be proved by cold-boot NET_BOOT/NET_WDT"
echo "[ok] relay4-payload dual-core ceremony programmed and verified"
echo "[record] logs=$log_dir"
echo
echo "================================================================"
echo "POWER CYCLE REQUIRED: physically remove Master_Tag power,"
echo "wait at least 5 seconds, restore power, then type POWER CYCLED."
echo "J-Link reset is NOT accepted."
echo "================================================================"
