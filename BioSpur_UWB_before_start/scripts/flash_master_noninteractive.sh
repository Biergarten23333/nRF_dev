#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <image.hex>" >&2
  exit 1
fi

SN="683234364"
IMAGE="$1"

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

echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=eraseall result=begin"
nrfjprog -f NRF52 --snr "${SN}" --eraseall
echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=eraseall result=ok"

echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=program_verify result=begin"
nrfjprog -f NRF52 --snr "${SN}" --program "${IMAGE}" --verify
echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=program_verify result=ok"

echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=reset result=begin"
nrfjprog -f NRF52 --snr "${SN}" --reset
echo "tool=nrfjprog snr=${SN} image=${IMAGE} action=reset result=ok"
