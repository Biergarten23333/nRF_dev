#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <snr> <device> [speed_khz]" >&2
  exit 2
fi

snr="$1"
device="$2"
speed_khz="${3:-4000}"

if ! command -v JLinkExe >/dev/null 2>&1; then
  echo "[error] JLinkExe not found in PATH" >&2
  exit 1
fi

cmdfile="$(mktemp)"
trap 'rm -f "$cmdfile"' EXIT

cat >"$cmdfile" <<EOF
si 1
speed ${speed_khz}
device ${device}
if SWD
r
g
exit
EOF

echo "tool=jlink_reset_by_snr snr=${snr} device=${device} speed_khz=${speed_khz}"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "$snr" -CommanderScript "$cmdfile"
echo "tool=jlink_reset_by_snr action=ok snr=${snr}"
