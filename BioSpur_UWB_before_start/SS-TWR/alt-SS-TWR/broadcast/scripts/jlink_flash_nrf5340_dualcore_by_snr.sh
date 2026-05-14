#!/usr/bin/env bash
set -euo pipefail

# Non-interactive dual-core flash helper for nRF5340.
#
# Usage:
#   scripts/jlink_flash_nrf5340_dualcore_by_snr.sh <snr> <build_dir> [speed_khz]
#
# It expects these standard artifacts inside <build_dir>:
#   - hci_ipc/zephyr/merged_CPUNET.hex
#   - zephyr/merged.hex

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "Usage: $0 <snr> <build_dir> [speed_khz]" >&2
  exit 2
fi

SNR="$1"
BUILD_DIR="$2"
SPEED_KHZ="${3:-4000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT=""
SEARCH_DIR="$SCRIPT_DIR"
while [ "$SEARCH_DIR" != "/" ]; do
  if [ -e "$SEARCH_DIR/.protec/noflash960148546" ] || [ -e "$SEARCH_DIR/.protec/biospur_ports.env" ]; then
    REPO_ROOT="$SEARCH_DIR"
    break
  fi
  SEARCH_DIR="$(dirname "$SEARCH_DIR")"
done
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../../../" && pwd)"
fi
PROTECT_FILE="$REPO_ROOT/.protec/noflash960148546"
PORTS_FILE="$REPO_ROOT/.protec/biospur_ports.env"
ASSERT_SCRIPT="$SCRIPT_DIR/assert_b120_internal_osc_build.sh"

if [ -f "$PORTS_FILE" ]; then
  # shellcheck disable=SC1090
  source "$PORTS_FILE"
fi

if [ -f "$PROTECT_FILE" ]; then
  # shellcheck disable=SC1090
  source "$PROTECT_FILE"
fi

NET_HEX="${BUILD_DIR}/hci_ipc/zephyr/merged_CPUNET.hex"
APP_HEX="${BUILD_DIR}/zephyr/merged.hex"

if [ ! -f "$NET_HEX" ]; then
  echo "[error] CPUNET image not found: $NET_HEX" >&2
  exit 3
fi

if [ ! -f "$APP_HEX" ]; then
  echo "[error] CPUAPP image not found: $APP_HEX" >&2
  exit 4
fi

LOGICAL_ROLE="unknown"
if [ "${BIOSPUR_ANCHOR_SNR:-}" = "$SNR" ] || [ "${BIOSPUR_PROTECTED_SNR:-}" = "$SNR" ]; then
  LOGICAL_ROLE="Master_Anchor"
elif [ "${BIOSPUR_TAG_SNR:-}" = "$SNR" ]; then
  LOGICAL_ROLE="Master_Tag"
fi

BUILD_HINT="$(realpath "$BUILD_DIR" 2>/dev/null || printf '%s' "$BUILD_DIR")"
BUILD_LOWER="$(printf '%s' "$BUILD_HINT" | tr '[:upper:]' '[:lower:]')"

if [ "${BIOSPUR_FLASH_POLICY_REQUIRE_ROLE_BANNER:-1}" = "1" ]; then
  echo "[flash-guard] SNR=${SNR} role=${LOGICAL_ROLE} image=${BUILD_HINT}"
fi

if [ "${BIOSPUR_FLASH_POLICY_BLOCK_ROLE_MISMATCH:-1}" = "1" ]; then
  if [[ "$BUILD_LOWER" == *master-tag* ]] && [ "$LOGICAL_ROLE" = "Master_Anchor" ]; then
    echo "[error] role mismatch: refusing to flash master-tag build onto Master_Anchor (${SNR})" >&2
    exit 8
  fi
  if [[ "$BUILD_LOWER" == *master-anchor* ]] && [ "$LOGICAL_ROLE" = "Master_Tag" ]; then
    echo "[error] role mismatch: refusing to flash master-anchor build onto Master_Tag (${SNR})" >&2
    exit 9
  fi
fi

if [ "$SNR" = "960148546" ] && [ -e "$PROTECT_FILE" ]; then
  echo "[error] protected B120 SNR 960148546; refusing to flash because $PROTECT_FILE exists" >&2
  exit 5
fi

"$ASSERT_SCRIPT" "$BUILD_DIR"

tmp_net="$(mktemp -t jlink_nrf5340_net_${SNR}_XXXXXX.jlink)"
tmp_app="$(mktemp -t jlink_nrf5340_app_${SNR}_XXXXXX.jlink)"

cleanup() {
  rm -f "$tmp_net" "$tmp_app" || true
}
trap cleanup EXIT

cat >"$tmp_net" <<EOF
si 1
speed ${SPEED_KHZ}
device nrf5340_xxaa_net
if SWD
r
loadfile ${NET_HEX}
r
g
exit
EOF

cat >"$tmp_app" <<EOF
si 1
speed ${SPEED_KHZ}
device nrf5340_xxaa_app
if SWD
r
loadfile ${APP_HEX}
r
g
exit
EOF

echo "tool=jlink_flash_nrf5340_dualcore_by_snr snr=${SNR} build_dir=${BUILD_DIR} speed_khz=${SPEED_KHZ}"
echo "cpunet=${NET_HEX}"
echo "cpuapp=${APP_HEX}"

echo "[1/2] flash CPUNET"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$tmp_net"

echo "[2/2] flash CPUAPP"
JLinkExe -NoGui 1 -ExitOnError 1 -SelectEmuBySN "${SNR}" -CommanderScript "$tmp_app"

echo "[ok] dual-core flash done"
