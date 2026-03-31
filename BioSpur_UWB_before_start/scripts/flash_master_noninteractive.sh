#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <image.hex>" >&2
  exit 1
fi

SN="683234364"
IMAGE="$1"
script_dir="$(cd "$(dirname "$0")" && pwd)"

# Prevent VSCode Nordic background hotplug scanner from racing J-Link access
# and triggering interactive probe-selection dialogs.
pkill -f "nrfutil-device --json list --hotplug" >/dev/null 2>&1 || true
sleep 0.2

if [ ! -f "$IMAGE" ]; then
  echo "Image not found: $IMAGE" >&2
  exit 2
fi

IDS="$(nrfjprog --ids || true)"
if ! printf '%s\n' "$IDS" | rg -q "^${SN}$"; then
  echo "Required probe SN ${SN} not present." >&2
  echo "Detected probes:" >&2
  printf '%s\n' "$IDS" >&2
  exit 3
fi

echo "tool=reset_then_flash snr=${SN} image=${IMAGE} action=begin"
"${script_dir}/reset_then_flash.sh" "${SN}" "${IMAGE}"
echo "tool=reset_then_flash snr=${SN} image=${IMAGE} action=ok"
