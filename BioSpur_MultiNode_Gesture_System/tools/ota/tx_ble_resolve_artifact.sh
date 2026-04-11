#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=tx_ble_common.sh
source "${SCRIPT_DIR}/tx_ble_common.sh"

IMAGE_PATH=""

usage() {
  cat <<'EOF'
Resolve TX BLE OTA image path.

Usage:
  tx_ble_resolve_artifact.sh [--image /abs/path/zephyr.signed.bin]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      [[ $# -ge 2 ]] || die "--image requires a value"
      IMAGE_PATH="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

resolve_tx_image_path "${IMAGE_PATH}"

