#!/usr/bin/env bash
set -euo pipefail

image="${1:-build-master-control-b120-m1/zephyr/merged_domains.hex}"
snr="${B120_SNR:-960148546}"
jlinkexe="${JLINKEXE:-JLinkExe}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root=""
search_dir="$script_dir"
while [ "$search_dir" != "/" ]; do
  if [ -e "$search_dir/.protec/noflash960148546" ] || [ -e "$search_dir/.protec/biospur_ports.env" ]; then
    repo_root="$search_dir"
    break
  fi
  search_dir="$(dirname "$search_dir")"
done
if [ -z "$repo_root" ]; then
  repo_root="$(cd "$script_dir/../../../../" && pwd)"
fi
protect_file="$repo_root/.protec/noflash960148546"
ports_file="$repo_root/.protec/biospur_ports.env"
assert_script="$script_dir/assert_b120_internal_osc_build.sh"

if [ -f "$ports_file" ]; then
  # shellcheck disable=SC1090
  source "$ports_file"
fi

if [ -f "$protect_file" ]; then
  # shellcheck disable=SC1090
  source "$protect_file"
fi

if [ ! -f "$image" ]; then
  echo "[error] image not found: $image" >&2
  exit 1
fi

logical_role="unknown"
if [ "${BIOSPUR_ANCHOR_SNR:-}" = "$snr" ] || [ "${BIOSPUR_PROTECTED_SNR:-}" = "$snr" ]; then
  logical_role="Master_Anchor"
elif [ "${BIOSPUR_TAG_SNR:-}" = "$snr" ]; then
  logical_role="Master_Tag"
fi

image_hint="$(realpath "$image" 2>/dev/null || printf '%s' "$image")"
image_lower="$(printf '%s' "$image_hint" | tr '[:upper:]' '[:lower:]')"

if [ "${BIOSPUR_FLASH_POLICY_REQUIRE_ROLE_BANNER:-1}" = "1" ]; then
  echo "[flash-guard] SNR=${snr} role=${logical_role} image=${image_hint}"
fi

if [ "${BIOSPUR_FLASH_POLICY_BLOCK_ROLE_MISMATCH:-1}" = "1" ]; then
  if [[ "$image_lower" == *master-tag* ]] && [ "$logical_role" = "Master_Anchor" ]; then
    echo "[error] role mismatch: refusing to flash master-tag image onto Master_Anchor (${snr})" >&2
    exit 9
  fi
  if [[ "$image_lower" == *master-anchor* ]] && [ "$logical_role" = "Master_Tag" ]; then
    echo "[error] role mismatch: refusing to flash master-anchor image onto Master_Tag (${snr})" >&2
    exit 10
  fi
fi

if [ "$snr" = "960148546" ] && [ -e "$protect_file" ] && \
   [ "${BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH:-0}" != "1" ]; then
  echo "[error] protected B120 SNR 960148546; refusing to flash because $protect_file exists" >&2
  echo "[hint] set B120_SNR=1050070698 for BioSpur_1 validation" >&2
  echo "[hint] for the approved ultrasound experiment only, set BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1" >&2
  exit 2
fi

if [ "$snr" = "960148546" ] && [ -e "$protect_file" ]; then
  echo "[flash-guard] OVERRIDE: BIOSPUR_ALLOW_PROTECTED_MASTER_ANCHOR_FLASH=1 permits approved ultrasound Master_Anchor flash"
fi

"$assert_script" "$image"

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
