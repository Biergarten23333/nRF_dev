#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

TAG_SNR="${TAG_SNR:-760186115}"
TAG_PORT="${TAG_PORT:-/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00}"
CAPTURE_DURATION_S="${CAPTURE_DURATION_S:-180}"
SKIP_SWEEPS="${SKIP_SWEEPS:-5}"
TARGET_POINTS="${TARGET_POINTS:-200}"

BASE_SOLVE_DIR="${BASE_SOLVE_DIR:-logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_154100_tag115cm}"
V1_LAYOUT="${V1_LAYOUT:-${BASE_SOLVE_DIR}/v1/anchor_layout_v1_soft_iterative.json}"
V2_LAYOUT="${V2_LAYOUT:-${BASE_SOLVE_DIR}/v2/v2_fused/anchor_layout_v2_iterative.json}"
V3_LAYOUT="${V3_LAYOUT:-${BASE_SOLVE_DIR}/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json}"
V3FULL_LAYOUT="${V3FULL_LAYOUT:-${BASE_SOLVE_DIR}/v3_full_tag115_cm/anchor_layout_v3_full.json}"
V3FULL_NO115_LAYOUT="${V3FULL_NO115_LAYOUT:-${BASE_SOLVE_DIR}/v3_full_no115/anchor_layout_v3_full.json}"

SRC_LAYOUT_C="src/uwb_anchor_layout.c"
RUNTIME_LAYOUT_JSON="data/anchor_layout_ah_calibrated.json"
OUT_ROOT="${OUT_ROOT:-logs/tag115_online_rms_v5}"
STAMP="$(date +%Y%m%d_%H%M%S)"
RUN_DIR="${OUT_ROOT}/run_${STAMP}"
mkdir -p "$RUN_DIR"

BACKUP_C="${RUN_DIR}/uwb_anchor_layout.c.backup"
BACKUP_JSON="${RUN_DIR}/anchor_layout_ah_calibrated.json.backup"
cp "$SRC_LAYOUT_C" "$BACKUP_C"
cp "$RUNTIME_LAYOUT_JSON" "$BACKUP_JSON"

restore_layouts() {
  cp "$BACKUP_C" "$SRC_LAYOUT_C" || true
  cp "$BACKUP_JSON" "$RUNTIME_LAYOUT_JSON" || true
}
trap restore_layouts EXIT

apply_layout_to_source() {
  local layout_path="$1"
  cp "$layout_path" "$RUNTIME_LAYOUT_JSON"
  python3 - "$layout_path" "$SRC_LAYOUT_C" <<'PY'
import json
import re
import sys
from pathlib import Path

anchors = "ABCDEFGH"
layout_path = Path(sys.argv[1])
source_path = Path(sys.argv[2])

raw = json.loads(layout_path.read_text(encoding="utf-8"))
data = raw.get("anchors")
if data is None:
    raise SystemExit(f"[error] no anchors in {layout_path}")

xyz = {}
if isinstance(data, dict):
    units = str(raw.get("units") or "m").lower()
    scale = 1000.0 if units != "mm" else 1.0
    for k in anchors:
        v = data.get(k)
        if not isinstance(v, list) or len(v) < 3:
            raise SystemExit(f"[error] anchor {k} missing in {layout_path}")
        xyz[k] = [int(round(float(v[0]) * scale)), int(round(float(v[1]) * scale)), int(round(float(v[2]) * scale))]
elif isinstance(data, list):
    for e in data:
        if not isinstance(e, dict):
            continue
        label = str(e.get("label", "")).strip().upper()
        if label not in anchors:
            continue
        if "x_mm" in e:
            xyz[label] = [int(round(float(e["x_mm"]))), int(round(float(e["y_mm"]))), int(round(float(e["z_mm"])))]
        else:
            units = str(raw.get("units") or "m").lower()
            scale = 1000.0 if units != "mm" else 1.0
            xyz[label] = [int(round(float(e.get("x", 0.0)) * scale)), int(round(float(e.get("y", 0.0)) * scale)), int(round(float(e.get("z", 0.0)) * scale))]
else:
    raise SystemExit(f"[error] unsupported anchors format in {layout_path}")

for k in anchors:
    if k not in xyz:
        raise SystemExit(f"[error] anchor {k} missing in {layout_path}")

entries = []
for idx, label in enumerate(anchors):
    x, y, z = xyz[label]
    entries.append(f"    {{{idx}U, '{label}', {x}, {y}, {z}}},")
replacement = (
    "static const struct uwb_anchor_pose_mm uwb_anchor_layout[UWB_MAX_ANCHORS] = {\n"
    + "\n".join(entries)
    + "\n};"
)

text = source_path.read_text(encoding="utf-8")
pat = r"static const struct uwb_anchor_pose_mm uwb_anchor_layout\[UWB_MAX_ANCHORS\] = \{\n.*?\n\};"
if re.search(pat, text, flags=re.S) is None:
    raise SystemExit(f"[error] layout table pattern not found in {source_path}")
text2 = re.sub(
    pat,
    replacement,
    text,
    flags=re.S,
)
source_path.write_text(text2, encoding="utf-8")
print(f"[ok] applied layout to {source_path}")
PY
}

run_one() {
  local name="$1"
  local layout="$2"
  local build_tag="build-tag-ota-rms-${name,,}-${STAMP}"
  local build_master="build-master-ota-rms-${name,,}-${STAMP}"
  local session="tag115_online_${name,,}_${STAMP}"
  local out_summary="${RUN_DIR}/summary_${name}.json"

  echo "[run] ${name}: applying layout ${layout}"
  apply_layout_to_source "$layout"

  echo "[run] ${name}: building motion tag OTA image"
  TAG_DEVICE_NAME=BSF66F \
  TAG_SIGN_VERSION="0.0.9+$(date +%s)" \
  TAG_CMAKE_ARGS="-DAPP_TAG_FIXED_MODE=0 -DAPP_TAG_MULTITAG_PLAN_MODE=0 -DAPP_TAG_BLE_SETTINGS_ENABLE=0 -DAPP_TAG_STREAM_FORCE_OFF_AT_BOOT=0 -DAPP_TAG_SUMMARY_PERIOD=1 -DAPP_TAG_PENDING_PRINT_PERIOD=1 -DAPP_TAG_CONSOLE_SUMMARY_ENABLE=1 -DAPP_TAG_LOC_MIN_QUALITY_PERCENT=20" \
  scripts/build_motion_tag_ota_profile.sh 115 "$build_tag" "$build_master"

  echo "[run] ${name}: flashing tag via non-interactive SN-pinned flow"
  scripts/reset_then_flash.sh "$TAG_SNR" "${build_tag}/merged.hex"

  echo "[run] ${name}: capture TS session (duration=${CAPTURE_DURATION_S}s)"
  python3 scripts/capture_tag_session.py \
    "$TAG_SNR" \
    "$TAG_PORT" \
    --duration "$CAPTURE_DURATION_S" \
    --skip-sweeps "$SKIP_SWEEPS" \
    --no-reset \
    --session-name "$session" \
    --out-dir "$RUN_DIR"

  cp "${RUN_DIR}/${session}/summary.json" "$out_summary"
}

run_one "V1" "$V1_LAYOUT"
run_one "V2" "$V2_LAYOUT"
run_one "V3-lite" "$V3_LAYOUT"
run_one "V3-full+Tag115" "$V3FULL_LAYOUT"
run_one "V3-full-no115" "$V3FULL_NO115_LAYOUT"

export RUN_DIR TARGET_POINTS
python3 - <<'PY'
import csv
import json
import math
import os
from pathlib import Path

run_dir = Path(os.environ["RUN_DIR"])
target_points = int(os.environ["TARGET_POINTS"])
rows = []

mapping = [
    ("V1", "tag115_online_v1"),
    ("V2", "tag115_online_v2"),
    ("V3-lite", "tag115_online_v3-lite"),
    ("V3-full+Tag115", "tag115_online_v3-full+tag115"),
    ("V3-full-no115", "tag115_online_v3-full-no115"),
]

def parse_points(csv_path: Path):
    pts = []
    with csv_path.open("r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            try:
                pts.append({
                    "x": float(row["x_mm"]),
                    "y": float(row["y_mm"]),
                    "z": float(row["z_mm"]),
                    "rms": float(row["rms_mm"]) if row["rms_mm"] else None,
                    "max": float(row["max_mm"]) if row["max_mm"] else None,
                })
            except Exception:
                continue
    return pts

for name, pref in mapping:
    summary_path = run_dir / f"summary_{name}.json"
    obj = json.loads(summary_path.read_text(encoding="utf-8"))
    session_dir = Path(obj["session_dir"])
    pts = parse_points(session_dir / "positions.csv")
    n_total = len(pts)
    pts = pts[:target_points]
    n = len(pts)
    if n == 0:
        rows.append({
            "layout": name,
            "samples_total": n_total,
            "samples_used": 0,
            "error": "no points",
            "session_dir": str(session_dir),
        })
        continue

    xs = [p["x"] for p in pts]
    ys = [p["y"] for p in pts]
    zs = [p["z"] for p in pts]
    rms_vals = [p["rms"] for p in pts if p["rms"] is not None]
    max_vals = [p["max"] for p in pts if p["max"] is not None]

    def mean(a):
        return sum(a) / len(a) if a else None
    def pstdev(a):
        if len(a) < 2:
            return 0.0
        m = mean(a)
        return math.sqrt(sum((x - m) ** 2 for x in a) / len(a))

    sx = pstdev(xs)
    sy = pstdev(ys)
    sz = pstdev(zs)
    s3 = math.sqrt(sx * sx + sy * sy + sz * sz)

    rows.append({
        "layout": name,
        "samples_total": n_total,
        "samples_used": n,
        "pos_std_x_mm": sx,
        "pos_std_y_mm": sy,
        "pos_std_z_mm": sz,
        "pos_std_3d_mm": s3,
        "residual_mean_rms_mm": mean(rms_vals),
        "residual_mean_max_mm": mean(max_vals),
        "session_dir": str(session_dir),
    })

out_json = run_dir / "compare_v5_online_ts_200pts.json"
out_md = run_dir / "compare_v5_online_ts_200pts.md"

out_json.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

lines = []
lines.append("# Tag115 Online TS Compare (5 Layouts, First 200 Points)")
lines.append("")
lines.append("| Layout | samples_total | samples_used | pos_std_x(mm) | pos_std_y(mm) | pos_std_z(mm) | pos_std_3d(mm) | residual_mean_rms(mm) | residual_mean_max(mm) | session_dir |")
lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
for r in rows:
    if "error" in r:
        lines.append(f"| {r['layout']} | {r['samples_total']} | 0 | - | - | - | - | - | - | `{r['session_dir']}` |")
        continue
    lines.append(
        f"| {r['layout']} | {r['samples_total']} | {r['samples_used']} | "
        f"{r['pos_std_x_mm']:.3f} | {r['pos_std_y_mm']:.3f} | {r['pos_std_z_mm']:.3f} | {r['pos_std_3d_mm']:.3f} | "
        f"{r['residual_mean_rms_mm']:.3f} | {r['residual_mean_max_mm']:.3f} | `{r['session_dir']}` |"
    )

out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"[ok] wrote {out_json}")
print(f"[ok] wrote {out_md}")
PY

echo "[done] run_dir=${RUN_DIR}"
