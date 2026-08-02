#!/usr/bin/env bash
set -euo pipefail

usage() {
	echo "usage: bash $0 <board2|board3> <dwm|b306> 1050070698 <log-directory>" >&2
	exit 2
}

[[ $# -eq 4 ]] || usage
board="$1"
target="$2"
snr="$3"
log_dir="$4"

[[ "$board" == "board2" || "$board" == "board3" ]] || usage
[[ "$target" == "dwm" || "$target" == "b306" ]] || usage
[[ "$snr" == "1050070698" ]] || {
	echo "REFUSED: bring-up authority in this procedure is SNR 1050070698" >&2
	exit 3
}

repo="/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion"
if [[ "$target" == "dwm" ]]; then
	build_dir="$repo/UWB_Part/builds/tag-fusion-link-relay3/tag"
	image="$repo/UWB_Part/builds/tag-fusion-link-relay3/merged.hex"
	expected="3c538c787478f86abb0a2eb78c6502ca4bf0d071fe72048939196514b8e11f09"
else
	build_dir="$repo/B306_Part/builds/b306-imu-relay-v26/firmware"
	image="$repo/B306_Part/builds/b306-imu-relay-v26/merged.hex"
	expected="474adb8874b3549c1363004998a077819895b35b4a3e8d6a971fe6891a568e25"
fi

actual="$(sha256sum "$image" | awk '{print $1}')"
if [[ "$actual" != "$expected" ]]; then
	echo "REFUSED: $target image SHA mismatch: got $actual expected $expected" >&2
	exit 4
fi

mkdir -p "$log_dir"
log_file="$log_dir/${board}_${target}_flash_1050070698.log"
{
	echo "BOARD=$board"
	echo "TARGET=$target"
	echo "PROBE_SNR=$snr"
	echo "IMAGE=$image"
	echo "SHA256=$actual"
	date --iso-8601=seconds
} | tee "$log_file"

cd "$repo"
LD_PRELOAD=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/lib/x86_64-linux-gnu/libffi.so.7 \
PYTHONNOUSERSITE=1 \
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages \
ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr \
ZEPHYR_TOOLCHAIN_VARIANT=zephyr \
ZEPHYR_SDK_INSTALL_DIR=/home/zekaixiao/ncs/toolchains/b81a7cd864/opt/zephyr-sdk \
/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/bin/python3 -m west \
	flash --skip-rebuild \
	-d "$build_dir" \
	-r jlink \
	--dev-id "$snr" \
	--erase \
	-f "$image" 2>&1 | tee -a "$log_file"
