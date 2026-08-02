#!/usr/bin/env bash
set -euo pipefail

# Fail-closed, one-shot Master_Tag carrier ceremony for the relay8.1 payload.
# Every J-Link session selects probe 1050070698 and an explicit nRF5340 core.

readonly EXPECTED_SNR="1050070698"
readonly CEREMONY_ID="relay8_1_20260801"
readonly CARRIER_MARKER="master-tag-carrier-v2-fix15-relay8.1"
readonly PAYLOAD_MARKER="tag-fusion-link-relay8.1"
readonly EXPECTED_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly EXPECTED_APP_SHA="c5b3c5f116fcc12e49e6569fd7664251fc192be2162fc1eded75a1fa9c0783b2"
readonly EXPECTED_PAYLOAD_SHA="e2a6a37270e6c2e4bee1e90f46e925512ea429c8fc57ab68cd4561fd8fe2337d"
readonly EXPECTED_ZIP_SHA="4921eaacc02741f16823e21ac4ec900210a6815a9ece76807048ec1b06f4aa0a"
readonly EXPECTED_FIX14_APP_SHA="093c129756ae6d37b91bef9a8e8bbf2e76af4d894b70d6c6cd1d3ef8e1252e89"
readonly EXPECTED_FIX14_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly MARKER_ADDR="0x0002972B"
readonly MARKER_SIZE="0x25"
readonly SPEED_KHZ="4000"

if [ "$#" -ne 2 ]; then
	echo "Usage: $0 1050070698 relay8_1_20260801" >&2
	exit 2
fi
if [ "$1" != "$EXPECTED_SNR" ] || [ "$2" != "$CEREMONY_ID" ]; then
	echo "[error] authorization mismatch: expected SNR=$EXPECTED_SNR ceremony=$CEREMONY_ID" >&2
	exit 3
fi

snr="$1"
src_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uwb_root="$(cd "$src_root/../.." && pwd)"
build_dir="$uwb_root/builds/master-control-b120-m1-master-tag-lfrc-fix15-relay8.1-a"
rollback_dir="$uwb_root/builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix14-relay8"
net_hex="$build_dir/hci_ipc/zephyr/merged_CPUNET.hex"
app_hex="$build_dir/zephyr/merged.hex"
app_elf="$build_dir/zephyr/zephyr.elf"
payload_bin="$uwb_root/builds/tag-fusion-link-relay8.1-a/tag/zephyr/zephyr.signed.bin"
payload_zip="$uwb_root/builds/tag-fusion-link-relay8.1-a/dfu_application.zip"
payload_manifest="$src_root/apps/master_ota/generated/active_ota_payload.json"
marker_registry="$src_root/master_carrier_marker_registry.json"
marker_guard="$src_root/scripts/master_carrier_marker_reuse_guard.py"
rollback_app="$rollback_dir/zephyr/merged.hex"
rollback_net="$rollback_dir/hci_ipc/zephyr/merged_CPUNET.hex"
log_root="$uwb_root/logs/relay8_1_20260801/carrier_fix15_relay8_1/ceremony"
log_dir="$log_root/run_$(date +%Y%m%d_%H%M%S)"
one_shot_guard="$log_root/CEREMONY_STARTED"
mkdir -p "$log_dir"

for artifact in "$net_hex" "$app_hex" "$app_elf" "$payload_bin" \
	"$payload_zip" "$payload_manifest" "$marker_registry" "$marker_guard" \
	"$rollback_app" "$rollback_net"; do
	[ -f "$artifact" ] || { echo "[error] missing artifact: $artifact" >&2; exit 4; }
done
command -v JLinkExe >/dev/null 2>&1 || {
	echo "[error] JLinkExe not found" >&2
	exit 4
}

check_sha() {
	local path="$1" expected="$2" label="$3" actual
	actual="$(sha256sum "$path" | awk '{print $1}')"
	[ "$actual" = "$expected" ] || {
		echo "[error] $label SHA mismatch expected=$expected actual=$actual" >&2
		exit 5
	}
}

check_sha "$net_hex" "$EXPECTED_NET_SHA" CPUNET
check_sha "$app_hex" "$EXPECTED_APP_SHA" CPUAPP
check_sha "$payload_bin" "$EXPECTED_PAYLOAD_SHA" payload
check_sha "$payload_zip" "$EXPECTED_ZIP_SHA" OTA_ZIP
check_sha "$rollback_app" "$EXPECTED_FIX14_APP_SHA" rollback_CPUAPP
check_sha "$rollback_net" "$EXPECTED_FIX14_NET_SHA" rollback_CPUNET
grep -Fq "\"fw_marker\": \"$PAYLOAD_MARKER\"" "$payload_manifest" || {
	echo "[error] payload marker missing from active manifest" >&2
	exit 6
}
grep -Fq "\"signed_bin_sha256\": \"$EXPECTED_PAYLOAD_SHA\"" "$payload_manifest" || {
	echo "[error] payload SHA missing from active manifest" >&2
	exit 6
}
python3 "$marker_guard" --elf "$app_elf" --artifact "$app_hex" \
	--registry "$marker_registry"

run_jlink() {
	local label="$1" device="$2" command_file output_file rc
	shift 2
	command_file="$log_dir/$label.jlink"
	output_file="$log_dir/$label.log"
	{
		echo "si 1"
		echo "speed $SPEED_KHZ"
		echo "device $device"
		echo "if SWD"
		echo "connect"
		for command in "$@"; do echo "$command"; done
		echo "exit"
	} >"$command_file"
	set +e
	JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "$snr" \
		-Device "$device" -If SWD -Speed "$SPEED_KHZ" \
		-CommanderScript "$command_file" 2>&1 | tee "$output_file"
	rc=${PIPESTATUS[0]}
	set -e
	if [ "$rc" -ne 0 ] || grep -qiE \
		'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' \
		"$output_file"; then
		echo "[error] J-Link session failed closed: $label rc=$rc" >&2
		return 7
	fi
}

ctrlap_erase() {
	local label="$1" apsel="$2" device="$3"
	local idr bank command_file output_file rc last_status
	idr="$(printf '0x%02X0000F0' "$apsel")"
	bank="$(printf '0x%02X000000' "$apsel")"
	command_file="$log_dir/erase_$label.jlink"
	output_file="$log_dir/erase_$label.log"
	{
		echo "si 1"
		echo "speed $SPEED_KHZ"
		echo "SWDSelect"
		echo "SWDWriteDP 1 0x50000000"
		echo "SWDWriteDP 2 $idr"
		echo "SWDReadAP 3"
		echo "SWDReadAP 3"
		echo "SWDWriteDP 2 $bank"
		echo "SWDWriteAP 1 1"
		for _ in $(seq 1 20); do echo "Sleep 100"; echo "SWDReadAP 2"; echo "SWDReadAP 2"; done
		echo "exit"
	} >"$command_file"
	set +e
	JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "$snr" \
		-Device "$device" -If SWD -Speed "$SPEED_KHZ" \
		-CommanderScript "$command_file" 2>&1 | tee "$output_file"
	rc=${PIPESTATUS[0]}
	set -e
	[ "$rc" -eq 0 ] || return 8
	grep -q 'Read AP register 3 = 0x12880000' "$output_file" || return 8
	last_status="$(grep 'Read AP register 2 = ' "$output_file" | tail -n 1 | sed -E 's/.*= (0x[0-9A-Fa-f]+).*/\1/')"
	[ "$last_status" = "0x00000000" ] || return 8
}

count_erased_words() {
	grep -E '^[[:xdigit:]]{8} = ' "$1" | grep -o 'FFFFFFFF' | wc -l
}

if [ -e "$one_shot_guard" ]; then
	echo "[error] one-shot ceremony already started: $one_shot_guard" >&2
	exit 9
fi
set -o noclobber
: >"$one_shot_guard"
set +o noclobber

{
	echo "timestamp=$(date --iso-8601=seconds)"
	echo "ceremony_id=$CEREMONY_ID"
	echo "probe_location=Master_Tag"
	echo "probe_snr=$snr"
	echo "carrier=$CARRIER_MARKER"
	echo "payload=$PAYLOAD_MARKER"
	echo "payload_signed_bin_sha256=$EXPECTED_PAYLOAD_SHA"
	echo "payload_dfu_zip_sha256=$EXPECTED_ZIP_SHA"
	echo "cpunet_sha256=$EXPECTED_NET_SHA"
	echo "cpuapp_sha256=$EXPECTED_APP_SHA"
	echo "rollback_fix14_cpuapp_sha256=$EXPECTED_FIX14_APP_SHA"
	echo "rollback_fix14_cpunet_sha256=$EXPECTED_FIX14_NET_SHA"
} | tee "$log_dir/manifest.txt"

echo "[1/8] CPUNET CTRL-AP erase (APSEL 3)"
ctrlap_erase network 3 nrf5340_xxaa_net
echo "[2/8] CPUAPP CTRL-AP erase (APSEL 2)"
ctrlap_erase application 2 nrf5340_xxaa_app
echo "[3/8] CPUAPP blank proof"
run_jlink blank_app nrf5340_xxaa_app halt "mem32 0x000f8000 8"
[ "$(count_erased_words "$log_dir/blank_app.log")" -ge 8 ] || exit 10
echo "[4/8] CPUNET blank proof"
run_jlink blank_net nrf5340_xxaa_net halt "mem32 0x01000000 2"
[ "$(count_erased_words "$log_dir/blank_net.log")" -ge 2 ] || exit 10
echo "[5/8] program and verify CPUNET first"
run_jlink program_net nrf5340_xxaa_net "loadfile $net_hex"
grep -q 'O.K.' "$log_dir/program_net.log" || exit 11
echo "[6/8] program and verify CPUAPP second"
run_jlink program_app nrf5340_xxaa_app "loadfile $app_hex"
grep -q 'O.K.' "$log_dir/program_app.log" || exit 11
echo "[7/8] CPUAPP persistence proof"
run_jlink verify_app nrf5340_xxaa_app halt "mem32 0x00000000 2"
[ "$(count_erased_words "$log_dir/verify_app.log")" -lt 2 ] || exit 12
echo "[8/8] exact carrier-marker readback"
marker_expected="$log_dir/carrier_marker_expected.bin"
marker_readback="$log_dir/carrier_marker_readback.bin"
printf '%s\0' "$CARRIER_MARKER" >"$marker_expected"
run_jlink marker nrf5340_xxaa_app halt \
	"savebin $marker_readback $MARKER_ADDR $MARKER_SIZE"
cmp "$marker_expected" "$marker_readback" || exit 13

echo "[ok] programmed $CARRIER_MARKER with $PAYLOAD_MARKER"
echo "[record] $log_dir"
echo
echo "================================================================"
echo "POWER CYCLE REQUIRED: physically remove Master_Tag power,"
echo "wait at least 5 seconds, restore power, then type POWER CYCLED."
echo "J-Link reset is NOT accepted."
echo "================================================================"
