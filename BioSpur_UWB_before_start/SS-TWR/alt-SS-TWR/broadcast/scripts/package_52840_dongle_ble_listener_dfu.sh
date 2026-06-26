#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${1:-build-52840-dongle-ble-listener}"
APP_VERSION="${2:-1}"
ZIP_OUT="${3:-biospur_ble_listener_dongle.zip}"

NRFUTIL_BIN="${NRFUTIL_BIN:-}"
if [[ -z "${NRFUTIL_BIN}" ]]; then
  if [[ -x "${HOME}/.cache/biospur_nrfutil_venv/bin/nrfutil" ]]; then
    NRFUTIL_BIN="${HOME}/.cache/biospur_nrfutil_venv/bin/nrfutil"
  elif [[ -x "${HOME}/.nrfutil/bin/nrfutil" ]]; then
    NRFUTIL_BIN="${HOME}/.nrfutil/bin/nrfutil"
  elif [[ -x "${REPO_ROOT}/scripts/nrfutil" ]]; then
    NRFUTIL_BIN="${REPO_ROOT}/scripts/nrfutil"
  elif command -v nrfutil >/dev/null 2>&1; then
    NRFUTIL_BIN="$(command -v nrfutil)"
  else
    echo "[err] nrfutil not found in PATH" >&2
    exit 1
  fi
fi

cd "${REPO_ROOT}"

HEX_PATH="${REPO_ROOT}/${BUILD_DIR}/zephyr/zephyr.hex"
if [[ ! -f "${HEX_PATH}" ]]; then
  echo "[err] missing hex: ${HEX_PATH}" >&2
  echo "[hint] run: scripts/build_52840_dongle_ble_listener.sh ${BUILD_DIR}" >&2
  exit 1
fi

"${NRFUTIL_BIN}" pkg generate \
  --hw-version 52 \
  --sd-req=0x00 \
  --application "${HEX_PATH}" \
  --application-version "${APP_VERSION}" \
  "${ZIP_OUT}"

echo "[ok] package: ${REPO_ROOT}/${ZIP_OUT}"
