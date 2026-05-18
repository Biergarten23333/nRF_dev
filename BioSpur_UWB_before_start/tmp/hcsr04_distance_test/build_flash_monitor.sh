#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$APP_DIR/../.." && pwd)"
BUILD_DIR="$APP_DIR/build"
SNR="${1:-760184500}"

cd "$REPO_ROOT"

west build -p always -b decawave_dwm1001_dev "$APP_DIR" -d "$BUILD_DIR"
west flash -d "$BUILD_DIR" --dev-id "$SNR"

echo
echo "Flash done for SNR $SNR."
echo "Now open the CDC serial port, for example:"
echo "  ls -l /dev/serial/by-id/"
echo "  picocom -b 115200 /dev/serial/by-id/<DWM1001C-PORT>"
