#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG_SNR="${TAG_SNR:-760186115}"
TAG_PORT="${TAG_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00}"
DURATION_S="${DURATION_S:-90}"
SKIP_SWEEPS="${SKIP_SWEEPS:-5}"
OUT_ROOT="${OUT_ROOT:-logs/tag_sessions}"

V1_LAYOUT="${V1_LAYOUT:-autopos_V3/logs/v123_from_baseline_20260415_110121/v1/anchor_layout_v1_soft_iterative.json}"
V2_LAYOUT="${V2_LAYOUT:-autopos_V3/logs/v123_from_baseline_20260415_110121/v2/v2_fused/anchor_layout_v2_iterative.json}"

RUNTIME_LAYOUT="data/anchor_layout_ah_calibrated.json"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${OUT_ROOT}/tag115_online_rms_v1v2_${STAMP}"
mkdir -p "$RUN_DIR"

BACKUP_LAYOUT="${RUN_DIR}/anchor_layout_ah_calibrated.backup.json"
cp "$RUNTIME_LAYOUT" "$BACKUP_LAYOUT"

cleanup() {
  cp "$BACKUP_LAYOUT" "$RUNTIME_LAYOUT" || true
}
trap cleanup EXIT

run_one() {
  local name="$1"
  local layout="$2"
  local build_tag="build-tag-ota-rms-${name,,}"
  local build_master="build-master-ota-rms-${name,,}"
  local session="tag115_online_rms_${name,,}_${STAMP}"

  echo "[run] ${name}: apply layout ${layout}"
  cp "$layout" "$RUNTIME_LAYOUT"

  echo "[run] ${name}: build tag profile"
  # MCUboot image build number is 32-bit unsigned; keep it safely bounded.
  # Use unix epoch seconds to avoid overflow from long timestamp strings.
  TAG_DEVICE_NAME=BSF66F \
  TAG_SIGN_VERSION="0.0.3+$(date +%s)" \
  scripts/build_motion_tag_ota_profile.sh 115 "$build_tag" "$build_master"

  echo "[run] ${name}: flash tag snr=${TAG_SNR}"
  nrfjprog --snr "$TAG_SNR" --program "${build_tag}/merged.hex" --chiperase --verify --reset

  echo "[run] ${name}: capture session"
  python3 scripts/capture_tag_session.py \
    "$TAG_SNR" \
    "$TAG_PORT" \
    --duration "$DURATION_S" \
    --skip-sweeps "$SKIP_SWEEPS" \
    --session-name "$session" \
    --out-dir "$OUT_ROOT"

  cp "${OUT_ROOT}/${session}/summary.json" "${RUN_DIR}/summary_${name}.json"
}

run_one "V1" "$V1_LAYOUT"
run_one "V2" "$V2_LAYOUT"

export RUN_DIR
python3 - <<'PY'
import json
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
rows = []
for name in ("V1", "V2"):
    p = run_dir / f"summary_{name}.json"
    obj = json.loads(p.read_text(encoding="utf-8"))
    rows.append({
        "name": name,
        "session_dir": obj.get("session_dir"),
        "position_samples": obj.get("position_samples"),
        "residual_rms_mm": (obj.get("residual_mean_mm") or {}).get("rms"),
        "residual_max_mm": (obj.get("residual_mean_mm") or {}).get("max"),
    })

out_json = run_dir / "compare_v1_v2_online_rms.json"
out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

lines = []
lines.append("# Tag115 Online RMS Compare (V1 vs V2)")
lines.append("")
lines.append("| Layout | position_samples | residual_mean_mm.rms | residual_mean_mm.max | session_dir |")
lines.append("|---|---:|---:|---:|---|")
for r in rows:
    lines.append(
        f"| {r['name']} | {r['position_samples']} | {r['residual_rms_mm']} | {r['residual_max_mm']} | `{r['session_dir']}` |"
    )

out_md = run_dir / "compare_v1_v2_online_rms.md"
out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

print(f"[ok] wrote {out_json}")
print(f"[ok] wrote {out_md}")
PY

echo "[done] run_dir=${RUN_DIR}"
