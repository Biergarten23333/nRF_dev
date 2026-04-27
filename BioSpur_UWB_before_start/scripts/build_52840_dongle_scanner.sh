#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-build-52840_dongle_scanner}"

cd "${REPO_ROOT}"

west build --no-sysbuild -b nrf52840dongle/nrf52840 -s 52840_dongle_scanner/firmware -d "${BUILD_DIR}" --pristine=always

echo "[ok] build dir: ${REPO_ROOT}/${BUILD_DIR}"
echo "[ok] hex: ${REPO_ROOT}/${BUILD_DIR}/zephyr/zephyr.hex"
