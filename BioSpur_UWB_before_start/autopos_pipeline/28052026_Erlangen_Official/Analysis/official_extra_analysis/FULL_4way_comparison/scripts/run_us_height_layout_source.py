#!/usr/bin/env python3
"""Build a US30/FGH height-gauged layout source for the FULL rerun.

The generated layout directory mirrors
``solver/outputs/v1_to_v4_io_field_check`` but replaces every layout.json with
the F/G/H ultrasound-height-gauged coordinate frame.  Range-only tables are
copied because a rigid gauge transform does not change inter-anchor ranges.
The production static means are transformed into the same US gauge so
production-path static evaluation remains frame-consistent.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
THIS = Path(__file__).resolve()
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
US_SCRIPT = REPO_ROOT / "autopos_pipeline/erlangen_20260528_mocap/solver/scripts/apply_ultrasound_height_to_layout.py"
DEFAULT_SESSION = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack/us_US01_FGH_US30_20260528_144437"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def import_us_module():
    spec = importlib.util.spec_from_file_location("apply_ultrasound_height_to_layout", US_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {US_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def layout_points(path: Path) -> dict[str, np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for anchor in data["anchors"]:
        label = str(anchor["label"]).upper()
        out[label] = np.array([float(anchor["x_mm"]), float(anchor["y_mm"]), float(anchor["z_mm"])], dtype=float)
    return out


def fit_rigid(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1.0
        r = u @ vt
    t = dst_c - src_c @ r
    aligned = src @ r + t
    err = np.linalg.norm(aligned - dst, axis=1)
    return r, t, float(math.sqrt(float(np.mean(err * err))))


def copy_tables(src_tables: Path, out_tables: Path) -> None:
    out_tables.mkdir(parents=True, exist_ok=True)
    for path in src_tables.iterdir():
        if path.is_file():
            shutil.copy2(path, out_tables / path.name)


def transform_static_table(src_csv: Path, out_csv: Path, transforms: dict[str, tuple[np.ndarray, np.ndarray]]) -> list[dict]:
    df = pd.read_csv(src_csv)
    rows = []
    for _, row in df.iterrows():
        rec = row.to_dict()
        version = str(row["version"])
        if version in transforms and {"mean_x", "mean_y", "mean_z"}.issubset(df.columns):
            r, t = transforms[version]
            point = np.array([[float(row["mean_x"]), float(row["mean_y"]), float(row["mean_z"])]], dtype=float)
            transformed = point @ r + t
            rec["mean_x_raw_no_us"] = float(row["mean_x"])
            rec["mean_y_raw_no_us"] = float(row["mean_y"])
            rec["mean_z_raw_no_us"] = float(row["mean_z"])
            rec["mean_x"] = float(transformed[0, 0])
            rec["mean_y"] = float(transformed[0, 1])
            rec["mean_z"] = float(transformed[0, 2])
            for axis in ("X", "Y", "Z"):
                std_col = f"{axis}_std"
                if std_col in df.columns and np.isfinite(float(row[std_col])):
                    # Approximate axis-SD rotation from diagonal covariance. D3_std is invariant.
                    cov = np.diag(
                        [
                            float(row.get("X_std", 0.0)) ** 2,
                            float(row.get("Y_std", 0.0)) ** 2,
                            float(row.get("Z_std", 0.0)) ** 2,
                        ]
                    )
                    cov_new = r.T @ cov @ r
                    rec["X_std"], rec["Y_std"], rec["Z_std"] = [float(math.sqrt(max(0.0, cov_new[i, i]))) for i in range(3)]
                    break
            rec["coordinate_gauge"] = "US30_FGH_height"
        rows.append(rec)
    write_csv(out_csv, rows)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate US30/FGH height-gauged FULL layout source.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT))
    parser.add_argument("--source-layout-dir", default=None)
    parser.add_argument("--out-layout-dir", default=None)
    parser.add_argument("--session", default=str(DEFAULT_SESSION))
    parser.add_argument("--anchors", default="F,G,H")
    parser.add_argument("--versions", default=",".join(LAYOUT_VERSIONS))
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    source_layout_dir = (
        Path(args.source_layout_dir).resolve()
        if args.source_layout_dir
        else official_root / "solver/outputs/v1_to_v4_io_field_check"
    )
    out_layout_dir = (
        Path(args.out_layout_dir).resolve()
        if args.out_layout_dir
        else official_root / "solver/outputs/v1_to_v4_io_field_check_US"
    )
    session = Path(args.session).resolve()
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    anchors_used = tuple(a.strip().upper() for a in args.anchors.split(",") if a.strip())

    if not session.exists():
        raise FileNotFoundError(session)
    if not source_layout_dir.exists():
        raise FileNotFoundError(source_layout_dir)

    us_mod = import_us_module()
    ultrasound = {}
    for anchor in anchors_used:
        csv_path = us_mod.find_ultrasound_csv(session, anchor)
        if csv_path is None:
            raise FileNotFoundError(f"ultrasound CSV missing for {anchor} in {session}")
        ultrasound[anchor] = us_mod.read_ultrasound_height(csv_path)

    if out_layout_dir.exists():
        shutil.rmtree(out_layout_dir)
    out_layout_dir.mkdir(parents=True, exist_ok=True)
    copy_tables(source_layout_dir / "tables", out_layout_dir / "tables")

    summary_rows = []
    transform_rows = []
    transforms: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for version in versions:
        src_layout = source_layout_dir / version / "layout.json"
        out_layout = out_layout_dir / version / "layout.json"
        meta = us_mod.apply_height(src_layout, ultrasound, out_layout, anchors_used)
        src_pts_by_label = layout_points(src_layout)
        us_pts_by_label = layout_points(out_layout)
        src_pts = np.vstack([src_pts_by_label[a] for a in ANCHORS])
        us_pts = np.vstack([us_pts_by_label[a] for a in ANCHORS])
        r, t, rms = fit_rigid(src_pts, us_pts)
        transforms[version] = (r, t)
        summary_rows.append(
            {
                "version": version,
                "status": meta["status"],
                "anchors_used": ",".join(anchors_used),
                "rms_residual_mm": float(meta["rms_residual_mm"]),
                "max_residual_mm": float(meta["max_residual_mm"]),
                "lower_mean_aligned_z_mm": float(meta["lower_mean_aligned_z_mm"]),
                "upper_mean_aligned_z_mm": float(meta["upper_mean_aligned_z_mm"]),
                "min_aligned_z_mm": float(meta["min_aligned_z_mm"]),
                "raw_to_us_rigid_rms_mm": rms,
                "z_axis_unit_in_raw_frame": json.dumps(meta["z_axis_unit_in_raw_frame"]),
                "z_shift_mm": float(meta["z_shift_mm"]),
                "out_layout_json": str(out_layout),
            }
        )
        for label in ANCHORS:
            raw = src_pts_by_label[label]
            corrected = us_pts_by_label[label]
            transform_rows.append(
                {
                    "version": version,
                    "anchor": label,
                    "raw_x_mm": float(raw[0]),
                    "raw_y_mm": float(raw[1]),
                    "raw_z_mm": float(raw[2]),
                    "us_x_mm": float(corrected[0]),
                    "us_y_mm": float(corrected[1]),
                    "us_z_mm": float(corrected[2]),
                    "delta_3d_mm": float(np.linalg.norm(corrected - raw)),
                }
            )

    transform_static_table(
        source_layout_dir / "tables/static_all_captures.csv",
        out_layout_dir / "tables/static_all_captures.csv",
        transforms,
    )
    write_csv(out_layout_dir / "tables/us_height_layout_summary.csv", summary_rows)
    write_csv(out_layout_dir / "tables/us_height_anchor_transform.csv", transform_rows)
    (out_layout_dir / "us_height_generation_meta.json").write_text(
        json.dumps(
            {
                "generated_utc": datetime.now(UTC).isoformat(),
                "source_layout_dir": str(source_layout_dir),
                "out_layout_dir": str(out_layout_dir),
                "session": str(session),
                "anchors_used": list(anchors_used),
                "ultrasound": ultrasound,
                "note": "US30/FGH height-gauged deployment coordinate source. Inter-anchor ranges are unchanged; static means are rigidly transformed into the US gauge.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[us-layout] wrote {out_layout_dir}")
    for row in summary_rows:
        print(
            f"[us-layout] {row['version']} status={row['status']} "
            f"rms={row['rms_residual_mm']:.3f} max={row['max_residual_mm']:.3f} "
            f"raw_to_us_rms={row['raw_to_us_rigid_rms_mm']:.6f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
