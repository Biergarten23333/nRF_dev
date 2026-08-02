#!/usr/bin/env bash
set -euo pipefail

# Destructive, one-shot-per-authorized-batch FINAL relay8-payload Master_Tag carrier
# ceremony.
# Every J-Link process is locked to probe 1050070698 and an explicit core.

readonly EXPECTED_SNR="1050070698"
readonly CARRIER_MARKER="master-tag-carrier-v2-fix14-relay8"
readonly PAYLOAD_MARKER="tag-fusion-link-relay8"
readonly EXPECTED_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly EXPECTED_APP_SHA="093c129756ae6d37b91bef9a8e8bbf2e76af4d894b70d6c6cd1d3ef8e1252e89"
readonly EXPECTED_PAYLOAD_SHA="e332fc594a0a5d08cf05f4163d1a4102d825bf32376bb5e672fa0e802d9fd2a1"
readonly EXPECTED_ZIP_SHA="41699cad411cc543095f5f974f968d915140c285f989f1523decf7c416575307"
readonly EXPECTED_ARCHIVE_SHA="93c95dbe975a3a256fe630e4eb53a0b6ed23dcd3b1a67d163e6aa8faacf82d37"
readonly EXPECTED_FIX12_APP_SHA="65407cd882c88870a4c04df33d4f68979bfac8819dc2286fbac6041cb29c04c6"
readonly EXPECTED_FIX12_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly MARKER_ADDR="0x0002970A"
# J-Link Commander parses bare numeric command arguments as hexadecimal.
# 35 decimal bytes (34 visible bytes plus NUL) is 0x23.
readonly MARKER_SIZE="0x23"

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
	echo "Usage: $0 1050070698 [relay8_final_20260801]" >&2
	exit 2
fi
if [ "$1" != "$EXPECTED_SNR" ]; then
	echo "[error] target locked to Master_Tag SNR $EXPECTED_SNR; got $1" >&2
	exit 3
fi
ceremony_id="${2:-relay8_final_20260801}"
case "$ceremony_id" in
relay8_final_20260801) ;;
*)
	echo "[error] ceremony id is not authorized: $ceremony_id" >&2
	exit 3
	;;
esac

snr="$1"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$repo_root/../builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix14-relay8"
net_hex="$build_dir/hci_ipc/zephyr/merged_CPUNET.hex"
app_hex="$build_dir/zephyr/merged.hex"
app_elf="$build_dir/zephyr/zephyr.elf"
payload_bin="$repo_root/../builds/tag-fusion-link-relay8/tag/zephyr/zephyr.signed.bin"
payload_zip="$repo_root/../builds/tag-fusion-link-relay8/dfu_application.zip"
payload_manifest="$build_dir/active_ota_payload.json"
marker_registry="$repo_root/src/master_carrier_marker_registry.json"
marker_guard="$repo_root/src/scripts/master_carrier_marker_reuse_guard.py"
archive_root="$repo_root/../logs/batchE_bs065f_20260729/archive"
archive_tar="$archive_root/master-tag-carrier-v2-fix7-relay4-restore.tar.gz"
archive_sums="$archive_root/master-tag-carrier-v2-fix7-relay4-restore/SHA256SUMS"
fix12_build="$repo_root/../builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix12-relay7"
fix12_app_hex="$fix12_build/zephyr/merged.hex"
fix12_net_hex="$fix12_build/hci_ipc/zephyr/merged_CPUNET.hex"
log_root="$repo_root/../logs/relay8_20260801/carrier_fix14_relay8/ceremony"
log_dir="$log_root/run_$(date +%Y%m%d_%H%M%S)"
one_shot_guard="$log_root/CEREMONY_STARTED"
speed_khz=4000
mkdir -p "$log_dir"

for artifact in \
	"$net_hex" "$app_hex" "$app_elf" "$payload_bin" "$payload_zip" \
	"$payload_manifest" "$marker_registry" "$marker_guard" \
	"$archive_tar" "$archive_sums" "$fix12_app_hex" "$fix12_net_hex"; do
	[ -f "$artifact" ] || {
		echo "[error] missing artifact: $artifact" >&2
		exit 4
	}
done

actual_net_sha="$(sha256sum "$net_hex" | awk '{print $1}')"
actual_app_sha="$(sha256sum "$app_hex" | awk '{print $1}')"
actual_payload_sha="$(sha256sum "$payload_bin" | awk '{print $1}')"
actual_zip_sha="$(sha256sum "$payload_zip" | awk '{print $1}')"
actual_archive_sha="$(sha256sum "$archive_tar" | awk '{print $1}')"
actual_fix12_app_sha="$(sha256sum "$fix12_app_hex" | awk '{print $1}')"
actual_fix12_net_sha="$(sha256sum "$fix12_net_hex" | awk '{print $1}')"
[ "$actual_net_sha" = "$EXPECTED_NET_SHA" ] || {
	echo "[error] CPUNET SHA mismatch: expected=$EXPECTED_NET_SHA actual=$actual_net_sha" >&2
	exit 5
}
[ "$actual_app_sha" = "$EXPECTED_APP_SHA" ] || {
	echo "[error] CPUAPP SHA mismatch: expected=$EXPECTED_APP_SHA actual=$actual_app_sha" >&2
	exit 6
}
[ "$actual_payload_sha" = "$EXPECTED_PAYLOAD_SHA" ] || {
	echo "[error] signed OTA payload SHA mismatch: expected=$EXPECTED_PAYLOAD_SHA actual=$actual_payload_sha" >&2
	exit 7
}
[ "$actual_zip_sha" = "$EXPECTED_ZIP_SHA" ] || {
	echo "[error] canonical OTA ZIP SHA mismatch: expected=$EXPECTED_ZIP_SHA actual=$actual_zip_sha" >&2
	exit 8
}
[ "$actual_archive_sha" = "$EXPECTED_ARCHIVE_SHA" ] || {
	echo "[error] fix7-relay4 restore archive SHA mismatch" >&2
	exit 9
}
[ "$actual_fix12_app_sha" = "$EXPECTED_FIX12_APP_SHA" ] || {
	echo "[error] resident fix12-relay7 CPUAPP restore SHA mismatch" >&2
	exit 9
}
[ "$actual_fix12_net_sha" = "$EXPECTED_FIX12_NET_SHA" ] || {
	echo "[error] resident fix12-relay7 CPUNET restore SHA mismatch" >&2
	exit 9
}
grep -Fq "\"fw_marker\": \"$PAYLOAD_MARKER\"" "$payload_manifest" || {
	echo "[error] relay8 marker missing from payload manifest" >&2
	exit 10
}
grep -Fq "\"signed_bin_sha256\": \"$EXPECTED_PAYLOAD_SHA\"" "$payload_manifest" || {
	echo "[error] relay8 signed-bin SHA missing from payload manifest" >&2
	exit 11
}
grep -Fq "\"dfu_zip_sha256\": \"$EXPECTED_ZIP_SHA\"" "$payload_manifest" || {
	echo "[error] relay8 ZIP SHA missing from payload manifest" >&2
	exit 12
}
python3 "$marker_guard" \
	--elf "$app_elf" \
	--artifact "$app_hex" \
	--registry "$marker_registry"
command -v JLinkExe >/dev/null 2>&1 || {
	echo "[error] JLinkExe not found" >&2
	exit 13
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
		return 14
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
		return 15
	fi
	grep -q 'Read AP register 3 = 0x12880000' "$output_file" || {
		echo "[error] CTRL-AP $label IDR was not 0x12880000" >&2
		return 16
	}
	last_status="$(
		grep 'Read AP register 2 = ' "$output_file" | tail -n 1 |
			sed -E 's/.*= (0x[0-9A-Fa-f]+).*/\1/'
	)"
	[ "$last_status" = "0x00000000" ] || {
		echo "[error] CTRL-AP $label ERASEALLSTATUS=${last_status:-missing}" >&2
		return 17
	}
	echo "[ok] CTRL-AP $label ERASEALLSTATUS=0"
}

count_erased_words() {
	grep -E '^[[:xdigit:]]{8} = ' "$1" | grep -o 'FFFFFFFF' | wc -l
}

# Authorization is exactly one destructive attempt per named batch. A failed
# attempt follows the documented fix7-relay4 restore ladder; the same batch id
# must not be rerun.
if [ -e "$one_shot_guard" ]; then
	echo "[error] one-shot ceremony already started: $one_shot_guard" >&2
	exit 18
fi
set -o noclobber
: >"$one_shot_guard"
set +o noclobber

{
	echo "timestamp=$(date --iso-8601=seconds)"
	echo "ceremony_id=$ceremony_id"
	echo "probe_location=Master_Tag"
	echo "probe_snr=$snr"
	echo "carrier=$CARRIER_MARKER"
	echo "payload=$PAYLOAD_MARKER"
	echo "payload_signed_bin_sha256=$actual_payload_sha"
	echo "payload_dfu_zip_sha256=$actual_zip_sha"
	echo "cpunet_sha256=$actual_net_sha"
	echo "cpuapp_sha256=$actual_app_sha"
	echo "fix7_restore_archive_sha256=$actual_archive_sha"
	echo "fix12_restore_cpuapp_sha256=$actual_fix12_app_sha"
	echo "fix12_restore_cpunet_sha256=$actual_fix12_net_sha"
} | tee "$log_dir/manifest.txt"

echo "[1/9] CPUNET CTRL-AP erase, APSEL=3"
ctrlap_erase network 3 nrf5340_xxaa_net

echo "[2/9] CPUAPP CTRL-AP erase, APSEL=2"
ctrlap_erase application 2 nrf5340_xxaa_app

echo "[3/9] fresh-session blank proof: CPUAPP settings/NVS @ 0x000f8000"
run_jlink blank_app_settings nrf5340_xxaa_app "halt" "mem32 0x000f8000 8"
[ "$(count_erased_words "$log_dir/blank_app_settings.log")" -ge 8 ] || {
	echo "[error] CPUAPP settings erase not proven" >&2
	exit 19
}

echo "[4/9] fresh-session blank proof: CPUNET vector @ 0x01000000"
run_jlink blank_net_vector nrf5340_xxaa_net "halt" "mem32 0x01000000 2"
[ "$(count_erased_words "$log_dir/blank_net_vector.log")" -ge 2 ] || {
	echo "[error] CPUNET vector erase not proven" >&2
	exit 20
}

echo "[5/9] program CPUNET first"
run_jlink program_net nrf5340_xxaa_net "loadfile $net_hex"
grep -q 'O.K.' "$log_dir/program_net.log" || {
	echo "[error] CPUNET Program & Verify proof missing" >&2
	exit 21
}

echo "[6/9] program CPUAPP second"
run_jlink program_app nrf5340_xxaa_app "loadfile $app_hex"
grep -q 'O.K.' "$log_dir/program_app.log" || {
	echo "[error] CPUAPP Program & Verify proof missing" >&2
	exit 22
}

echo "[7/9] fresh-session CPUAPP persistence proof"
run_jlink verify_app_vector nrf5340_xxaa_app "halt" "mem32 0x00000000 2"
[ "$(count_erased_words "$log_dir/verify_app_vector.log")" -lt 2 ] || {
	echo "[error] CPUAPP vector remains blank after programming" >&2
	exit 23
}

echo "[8/9] read back exact carrier marker from CPUAPP flash"
marker_readback="$log_dir/carrier_marker_readback.bin"
marker_expected="$log_dir/carrier_marker_expected.bin"
printf '%s\0' "$CARRIER_MARKER" >"$marker_expected"
run_jlink readback_carrier_marker nrf5340_xxaa_app \
	"halt" "savebin $marker_readback $MARKER_ADDR $MARKER_SIZE"
cmp "$marker_expected" "$marker_readback" || {
	echo "[error] on-device carrier marker readback mismatch" >&2
	exit 24
}
echo "[ok] readback marker=$CARRIER_MARKER address=$MARKER_ADDR"

echo "[9/9] CPUNET persistence will be proved by cold-boot NET_BOOT/NET_WDT"
echo "[ok] fix14-relay8 dual-core ceremony programmed and verified"
echo "[record] logs=$log_dir"
echo
echo "================================================================"
echo "POWER CYCLE REQUIRED: physically remove Master_Tag power,"
echo "wait at least 5 seconds, restore power, then type POWER CYCLED."
echo "J-Link reset is NOT accepted."
echo "================================================================"
