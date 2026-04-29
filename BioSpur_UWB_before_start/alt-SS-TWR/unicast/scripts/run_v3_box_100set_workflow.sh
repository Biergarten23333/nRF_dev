#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PORT="${PORT:-/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00}"
ORDER="${ORDER:-ABCDEFGH}"
SW_SETS="${SW_SETS:-100}"
TIMEOUT_S="${TIMEOUT_S:-7200}"
QUIET_TAG_NAME="${QUIET_TAG_NAME:--}"
CAPTURE_TAG115="${CAPTURE_TAG115:-1}"
TAG_NAME="${TAG_NAME:-BSF66F}"
CM_LINES="${CM_LINES:-80}"
REF_SESSION="${REF_SESSION:-logs/tag115_cm_fresh_20260416_154100}"
REF_MIN_CM_LINES="${REF_MIN_CM_LINES:-80}"
OUT_DIR="${OUT_DIR:-logs/v3_box_100set_with115_$(date +%Y%m%d_%H%M%S)}"

echo "REPO_ROOT=$REPO_ROOT"
echo "PORT=$PORT"
echo "OUT_DIR=$OUT_DIR"

CMD=(
python3 scripts/run_autopos_sweep_and_solve_v3_box.py
  --port "$PORT" \
  --order "$ORDER" \
  --sw-sets "$SW_SETS" \
  --timeout-s "$TIMEOUT_S" \
  --warmup-min-quality 0 \
  --quiet-tag-name "$QUIET_TAG_NAME" \
  --no-bootstrap-autopos-reset \
  --floating-reference-z-prior-mm 820 \
  --floating-reference-z-sigma-mm 80 \
  --out-dir "$OUT_DIR"
)

if [[ "$CAPTURE_TAG115" == "1" ]]; then
  CMD+=(
    --capture-tag115
    --tag-name "$TAG_NAME"
    --cm-lines "$CM_LINES"
  )
else
  CMD+=(
    --floating-reference-session "$REF_SESSION"
    --floating-reference-min-cm-lines "$REF_MIN_CM_LINES"
  )
fi

"${CMD[@]}"

python3 scripts/summarize_anchor_layout_result.py \
  --layout-json "$OUT_DIR/solve_v3_box/anchor_layout_v3_box.json" \
  --output-md "$OUT_DIR/solve_v3_box/result_summary_v3_box.md" \
  --title "V3-box 100-set Workflow Result"

python3 scripts/plot_anchor_layout.py \
  --layout-json "$OUT_DIR/solve_v3_box/anchor_layout_v3_box.json" \
  --output "$OUT_DIR/solve_v3_box/anchor_layout_v3_box_plot.png" \
  --title "V3-box 100-set Workflow Layout"

echo "[ok] workflow complete"
echo "[ok] result dir: $OUT_DIR"
echo "[ok] pairs csv: $OUT_DIR/solve_v3_box/pairs_all.csv"
echo "[ok] layout json: $OUT_DIR/solve_v3_box/anchor_layout_v3_box.json"
echo "[ok] summary md: $OUT_DIR/solve_v3_box/result_summary_v3_box.md"
echo "[ok] layout png: $OUT_DIR/solve_v3_box/anchor_layout_v3_box_plot.png"
