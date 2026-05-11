#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${1:-${REPO_ROOT}/build-anchor-unified-ota}"

declare -A SNR=(
  [A]=760186071
  [B]=760185876
  [C]=760185878
  [D]=760184974
  [E]=760185904
  [F]=760186124
  [G]=760185889
  [H]=760184753
)

for anchor in A B C D E F G H; do
  echo "==== Flash ${anchor} (${SNR[$anchor]}) with ${BUILD_DIR} ===="
  "${SCRIPT_DIR}/flash_anchor_auto.sh" "${BUILD_DIR}" "${SNR[$anchor]}"
done

echo "All anchors flashed."
