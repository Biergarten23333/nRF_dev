#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_DIR="${ROOT_DIR}/UWB_listener"
BUILD_DIR="${ROOT_DIR}/build-uwb-listener"
SERIAL_ID="${BIOSPUR_LISTENER_SN:-760185886}"

west build -p always -b decawave_dwm1001_dev "${APP_DIR}" -d "${BUILD_DIR}"
west flash -d "${BUILD_DIR}" --dev-id "${SERIAL_ID}"
