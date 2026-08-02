#!/usr/bin/env bash
set -euo pipefail

# Phase-H one-ceremony flasher for the Master_Tag B120 only.
# Every J-Link session selects SNR 1050070698 explicitly. The two CTRL-AP
# recover operations and all readbacks run in fresh sessions so J-Link's
# post-load cache cannot masquerade as persistent flash.

readonly EXPECTED_SNR="1050070698"
readonly EXPECTED_NET_SHA="c9003e16d921a4f8183bd7882e52fca3eb64da8f0a42285dfd239e76bfef429f"
readonly EXPECTED_APP_SHA="e6ddcbbc48ae9162feb03a2f5940032427e2781b3b06960273f9f84dde372c79"

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 1050070698" >&2
  exit 2
fi

snr="$1"
if [ "$snr" != "$EXPECTED_SNR" ]; then
  echo "[error] this ceremony is locked to Master_Tag SNR $EXPECTED_SNR; got $snr" >&2
  exit 3
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
build_dir="$repo_root/../builds/master-control-b120-m1-master-tag-lfrc-carrier-v2-fix1-relay2"
net_hex="$build_dir/hci_ipc/zephyr/merged_CPUNET.hex"
app_hex="$build_dir/zephyr/merged.hex"
speed_khz=4000

for image in "$net_hex" "$app_hex"; do
  [ -f "$image" ] || {
    echo "[error] missing artifact: $image" >&2
    exit 4
  }
done

actual_net_sha="$(sha256sum "$net_hex" | awk '{print $1}')"
actual_app_sha="$(sha256sum "$app_hex" | awk '{print $1}')"
if [ "$actual_net_sha" != "$EXPECTED_NET_SHA" ]; then
  echo "[error] CPUNET SHA mismatch: expected=$EXPECTED_NET_SHA actual=$actual_net_sha" >&2
  exit 5
fi
if [ "$actual_app_sha" != "$EXPECTED_APP_SHA" ]; then
  echo "[error] CPUAPP SHA mismatch: expected=$EXPECTED_APP_SHA actual=$actual_app_sha" >&2
  exit 6
fi

command -v JLinkExe >/dev/null 2>&1 || {
  echo "[error] JLinkExe not found" >&2
  exit 7
}

run_jlink() {
  local device="$1"
  shift
  local command_file output_file rc
  command_file="$(mktemp -t master_tag_v2_XXXXXX.jlink)"
  output_file="$(mktemp -t master_tag_v2_XXXXXX.log)"
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
    -SelectEmuBySN "$snr" -CommanderScript "$command_file" 2>&1 |
    tee "$output_file"
  rc=${PIPESTATUS[0]}
  set -e
  if [ "$rc" -ne 0 ] ||
    grep -qiE 'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' "$output_file"; then
    echo "[error] J-Link command session failed closed: device=$device rc=$rc" >&2
    rm -f "$command_file" "$output_file"
    return 12
  fi
  rm -f "$command_file" "$output_file"
}

# nRF5340 exposes application and network CTRL-APs at APSEL 2 and 3.
# J-Link Commander 9.24a has no Nordic `recover` command, so drive ERASEALL
# through the SWD DP/AP registers. This exact sequence was proven on this
# probe and board during H1 recovery on 2026-07-24.
ctrlap_erase() {
  local label="$1"
  local apsel="$2"
  local select_idr select_bank command_file output_file rc last_status
  select_idr="$(printf '0x%02X0000F0' "$apsel")"
  select_bank="$(printf '0x%02X000000' "$apsel")"
  command_file="$(mktemp -t master_tag_v2_ctrlap_XXXXXX.jlink)"
  output_file="$(mktemp -t master_tag_v2_ctrlap_XXXXXX.log)"
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
  JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "$snr" \
    -CommanderScript "$command_file" 2>&1 | tee "$output_file"
  rc=${PIPESTATUS[0]}
  set -e
  if [ "$rc" -ne 0 ] ||
    grep -qiE 'Unknown command|Command failed|FATAL ERROR|Could not connect|Cannot connect' "$output_file"; then
    echo "[error] CTRL-AP $label session failed closed: rc=$rc" >&2
    rm -f "$command_file" "$output_file"
    return 13
  fi
  if ! grep -q 'Read AP register 3 = 0x12880000' "$output_file"; then
    echo "[error] CTRL-AP $label IDR was not 0x12880000" >&2
    rm -f "$command_file" "$output_file"
    return 14
  fi
  last_status="$(
    grep 'Read AP register 2 = ' "$output_file" | tail -n 1 |
      sed -E 's/.*= (0x[0-9A-Fa-f]+).*/\1/'
  )"
  if [ "$last_status" != "0x00000000" ]; then
    echo "[error] CTRL-AP $label ERASEALLSTATUS did not reach zero: ${last_status:-missing}" >&2
    rm -f "$command_file" "$output_file"
    return 15
  fi
  echo "[ok] CTRL-AP $label ERASEALLSTATUS=0"
  rm -f "$command_file" "$output_file"
}

count_erased_words() {
  grep -E '^[[:xdigit:]]{8} = ' | grep -o 'FFFFFFFF' | wc -l
}

echo "[guard] target=Master_Tag SNR=$snr"
echo "[guard] CPUNET sha256=$actual_net_sha"
echo "[guard] CPUAPP sha256=$actual_app_sha"

echo "[1/8] CPUNET CTRL-AP recover"
ctrlap_erase network 3

echo "[2/8] CPUAPP CTRL-AP recover"
ctrlap_erase application 2

echo "[3/8] fresh-session blank proof: CPUAPP settings/NVS @ 0x000f8000"
app_blank_output="$(run_jlink nrf5340_xxaa_app "halt" "mem32 0x000f8000 8")"
printf '%s\n' "$app_blank_output"
app_blank_words="$(printf '%s\n' "$app_blank_output" | count_erased_words)"
if [ "$app_blank_words" -lt 8 ]; then
  echo "[error] CPUAPP settings erase not proven: erased_words=$app_blank_words expected>=8" >&2
  exit 8
fi

echo "[4/8] fresh-session blank proof: CPUNET vector @ 0x01000000"
net_blank_output="$(run_jlink nrf5340_xxaa_net "halt" "mem32 0x01000000 2")"
printf '%s\n' "$net_blank_output"
net_blank_words="$(printf '%s\n' "$net_blank_output" | count_erased_words)"
if [ "$net_blank_words" -lt 2 ]; then
  echo "[error] CPUNET erase not proven: erased_words=$net_blank_words expected>=2" >&2
  exit 9
fi

echo "[5/8] program CPUNET first"
run_jlink nrf5340_xxaa_net "loadfile $net_hex"

echo "[6/8] program CPUAPP second"
run_jlink nrf5340_xxaa_app "loadfile $app_hex"

echo "[7/8] fresh-session CPUAPP persistence proof"
app_program_output="$(run_jlink nrf5340_xxaa_app "halt" "mem32 0x00000000 2")"
printf '%s\n' "$app_program_output"
if [ "$(printf '%s\n' "$app_program_output" | count_erased_words)" -ge 2 ]; then
  echo "[error] CPUAPP vector remains blank after programming" >&2
  exit 11
fi

echo "[8/8] CPUNET program-and-verify recorded; cold-boot NET_BOOT/NET_WDT is the persistence proof"
# Do not open a fresh NRF5340_XXAA_NET debug session here. On this board,
# J-Link 9.24a reports the network core secured and automatically mass-erases
# both cores to unsecure it. The loadfile operation above already performed
# Program & Verify; final CPUNET persistence is proved non-destructively by
# its boot/WDT banner after the mandatory physical cold power cycle.

echo "[ok] carrier-v2-fix1 dual-core programming and fresh-session readback passed"
echo "[HARD GATE] DO NOT use J-Link reset. Physically cold power-cycle Master_Tag."
