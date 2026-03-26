#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <snr> <hex_path>" >&2
  exit 1
fi

snr="$1"
hex_path="$(realpath "$2")"

echo "[reset-before-flash] $snr"
echo "[flash] $snr $hex_path"
if command -v JLinkExe >/dev/null 2>&1; then
  jlink_cmd="$(mktemp)"
  trap 'rm -f "$jlink_cmd"' EXIT
  cat >"$jlink_cmd" <<EOF
Device nRF52832_XXAA
SelectInterface SWD
Speed 4000
Connect
LoadFile $hex_path
Reset
Go
Exit
EOF
  JLinkExe -SelectEmuBySN "$snr" -CommanderScript "$jlink_cmd"
else
  echo "[warn] JLinkExe not found, falling back to nrfjprog" >&2
  nrfjprog --reset -f NRF52 --snr "$snr" || true
  nrfjprog --program "$hex_path" --sectorerase --verify -f NRF52 --snr "$snr"
  nrfjprog --reset -f NRF52 --snr "$snr"
  exit 0
fi

echo "[reset-after-flash] $snr"
