#!/usr/bin/env bash
set -euo pipefail

tag_id="${1:-0}"
build_dir="${2:-build-tag-ble-lite}"
tag_logical_id="${TAG_LOGICAL_ID:-}"

if [ -z "${tag_logical_id}" ]; then
  case "${tag_id}" in
    113) tag_logical_id="3" ;;
    127) tag_logical_id="2" ;;
    *)
      if [[ "${tag_id}" =~ ^[0-9]+$ ]] && [ "${tag_id}" -lt 10 ]; then
        tag_logical_id="${tag_id}"
      else
        tag_logical_id="0"
      fi
      ;;
  esac
fi

west build \
  -b decawave_dwm1001_dev/nrf52832 \
  -s apps/tag_ble_lite \
  -d "${build_dir}" \
  --pristine=always \
  -- \
  -DAPP_TAG_ID="${tag_logical_id}" \
  -DAPP_TAG_BLE_TOKEN_ID="${tag_id}" \
  -DAPP_TAG_BLE_ENABLE=1 \
  -DAPP_TAG_BLE_OTA_ENABLE=0 \
  -DAPP_TAG_MCUBOOT_ENABLE=0
