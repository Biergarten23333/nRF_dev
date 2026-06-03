#!/usr/bin/env bash
set -euo pipefail

# Build an OTA-capable unified Anchor image with:
#   - runtime CIR=0: no CIR output; normal AutoPos sweep stays fast
#   - runtime CIR=COMPACT: lightweight ACRX diagnostics on BLE and CDC
#   - runtime CIR=FULL: full DW1000 accumulator output on local J-Link CDC
#
# This script only builds the Anchor OTA payload and matching control image.
# It does not flash the eight Anchors directly.

ANCHOR_BUILD_DIR="${1:-build-anchor-unified-ota-cir}"
CONTROL_BUILD_DIR="${2:-build-master-control-anchor-ota-cir}"
FW_MARKER="${3:-anchor-cir-$(date +%Y%m%d-%H%M%S)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export ANCHOR_EXTRA_CMAKE_ARGS="${ANCHOR_EXTRA_CMAKE_ARGS:-} \
-DAPP_ANCHOR_CIR_FEATURE_OUTPUT_ENABLE=1 \
-DAPP_ANCHOR_CIR_FEATURE_OUTPUT_BLE_ENABLE=1 \
-DAPP_ANCHOR_CIR_FEATURE_OUTPUT_CDC_ENABLE=1 \
-DAPP_ANCHOR_CIR_FULL_OUTPUT_ENABLE=1 \
-DAPP_ANCHOR_CIR_FULL_OUTPUT_CDC_ENABLE=1 \
-DAPP_ANCHOR_CIR_FULL_CHUNK_BYTES=48 \
-DAPP_ANCHOR_RESPONDER_PRINTK_ENABLE=0 \
-DAPP_ANCHOR_VERBOSE_RESPONDER=0 \
-DAPP_ANCHOR_VERBOSE_RESPONDER_ERRORS=0"

echo "[build-anchor-cir-ota] marker=${FW_MARKER}"
echo "[build-anchor-cir-ota] anchor_build_dir=${ANCHOR_BUILD_DIR}"
echo "[build-anchor-cir-ota] control_build_dir=${CONTROL_BUILD_DIR}"
echo "[build-anchor-cir-ota] ANCHOR_EXTRA_CMAKE_ARGS=${ANCHOR_EXTRA_CMAKE_ARGS}"

"${SCRIPT_DIR}/build_anchor_ota_control_bundle.sh" \
  "${ANCHOR_BUILD_DIR}" \
  "${CONTROL_BUILD_DIR}" \
  "${FW_MARKER}"
