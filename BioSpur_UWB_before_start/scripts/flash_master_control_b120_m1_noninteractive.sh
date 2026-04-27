#!/usr/bin/env bash
set -euo pipefail

image="${1:-build-master-control-b120-m1/zephyr/merged_domains.hex}"
snr="${B120_SNR:-960148546}"
jlinkexe="${JLINKEXE:-JLinkExe}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
protect_file="$repo_root/.protec/noflash960148546"

if [ ! -f "$image" ]; then
  echo "[error] image not found: $image" >&2
  exit 1
fi

if [ "$snr" = "960148546" ] && [ -e "$protect_file" ]; then
  echo "[error] protected B120 SNR 960148546; refusing to flash because $protect_file exists" >&2
  echo "[hint] set B120_SNR=1050070698 for BioSpur_1 validation" >&2
  exit 2
fi

"$repo_root/scripts/assert_b120_internal_osc_build.sh" "$image"

if ! command -v "$jlinkexe" >/dev/null 2>&1; then
  echo "[error] JLinkExe not found in PATH" >&2
  exit 1
fi

cmdfile="$(mktemp)"
trap 'rm -f "$cmdfile"' EXIT

cat > "$cmdfile" <<EOF
r
h
loadfile $image
r
g
q
EOF

echo "tool=flash_master_control_b120_m1_noninteractive snr=${snr} image=${image} mode=jlinkexe_nogui policy=no_popup_explicit_snr"
"$jlinkexe" \
  -NoGui 1 \
  -SelectEmuBySN "$snr" \
  -device NRF5340_XXAA_APP \
  -if SWD \
  -speed 4000 \
  -autoconnect 1 \
  -CommanderScript "$cmdfile"

echo "tool=flash_master_control_b120_m1_noninteractive action=ok snr=${snr}"
