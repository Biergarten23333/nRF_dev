#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <snr> <hex_path>" >&2
  exit 1
fi

snr="$1"
hex_path="$2"

echo "[reset-before-flash] $snr"
nrfjprog --reset -f NRF52 --snr "$snr" || true

echo "[flash] $snr $hex_path"
nrfjprog --program "$hex_path" --sectorerase --verify -f NRF52 --snr "$snr"

echo "[reset-after-flash] $snr"
nrfjprog --reset -f NRF52 --snr "$snr"
