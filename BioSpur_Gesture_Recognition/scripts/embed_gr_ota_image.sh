#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIGNED_BIN="${1:-${ROOT}/build/gr_module/gr_module/zephyr/zephyr.signed.bin}"
OUT_INC="${2:-${ROOT}/central_b120/generated/gr_ota_image.inc}"

if [ ! -f "$SIGNED_BIN" ]; then
	echo "signed GR OTA payload not found: ${SIGNED_BIN}" >&2
	echo "build it first with: scripts/build_gr_module.sh" >&2
	exit 1
fi

mkdir -p "$(dirname "$OUT_INC")"
python3 "${ROOT}/scripts/gen_ota_image_inc.py" \
	"$SIGNED_BIN" \
	"$OUT_INC" \
	--symbol-prefix gr_ota_image

echo "embedded GR OTA image: ${OUT_INC}"
