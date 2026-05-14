#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUILD_DIR="${1:-build-master-control}"
MASTER_SNR="683234364"

cd "${REPO_ROOT}"

bash "${SCRIPT_DIR}/build_master_control.sh" "${BUILD_DIR}"
"${SCRIPT_DIR}/reset_then_flash.sh" "${MASTER_SNR}" "${REPO_ROOT}/${BUILD_DIR}/zephyr/zephyr.hex"
