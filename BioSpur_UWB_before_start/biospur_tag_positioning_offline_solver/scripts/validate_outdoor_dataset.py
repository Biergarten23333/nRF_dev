#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver, build_c_core
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.models import SolverConfig
from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory, write_trajectory_json


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def compare_reference_sources() -> list[dict]:
    pairs = [
        (
            REPO / "autopos_pipeline/erlangen_20260528_mocap/solver/scripts/export_capture_trajectory.py",
            ROOT / "reference_current_implementations/ui_realtime_trajectory_solver_20052026/export_capture_trajectory.py",
            "ui_export_trajectory",
        ),
        (
            REPO / "autopos_pipeline/outdoor_20260513/run_clean_full_compare.py",
            ROOT / "reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py",
            "official_run_clean_full_compare",
        ),
        (
            REPO / "autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py",
            ROOT / "reference_current_implementations/official_report_field_solver_13052026/run_v4io_field_check.py",
            "official_field_check_wrapper",
        ),
    ]
    rows = []
    for original, copied, name in pairs:
        rows.append({
            "name": name,
            "original": str(original),
            "copied": str(copied),
            "exact_copy": original.read_bytes() == copied.read_bytes(),
        })
    return rows


def write_official_sigma(data: Path, out_dir: Path) -> Path:
    run_clean = import_module(REPO / "autopos_pipeline/outdoor_20260513/run_clean_full_compare.py", "tagpos_ref_sigma_run_clean")
    eval_mod = run_clean.load_eval_module()
    path = out_dir / "official_anchor_sigma.json"
    labels = "ABCDEFGH"
    obj = {labels[int(k)]: float(v) for k, v in eval_mod.ANCHOR_SIGMA.items()}
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return path


def c_vs_official_reference(data: Path, max_frames: int, sigma_path: Path) -> dict:
    layout_path = data / "FULL-COMPARE-1000/v4-io/layout.json"
    capture_path = data / "Static_Test/ID02_20260513_153316/tr_all.csv"
    run_clean = import_module(REPO / "autopos_pipeline/outdoor_20260513/run_clean_full_compare.py", "tagpos_ref_run_clean")
    eval_mod = run_clean.load_eval_module()
    layout_obj = json.loads(layout_path.read_text(encoding="utf-8"))
    anchor_ids = [int(v) for v in layout_obj.get("anchor_ids", list(range(8)))]
    x = []
    dly = []
    for item in layout_obj["anchors"]:
        x.append([float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])])
        dly.append(float(item.get("d_anchor_mm") or 0.0))
    import numpy as np

    ref_layout = run_clean.Layout("v4-io", "V4-io", np.asarray(x, dtype=float), np.asarray(dly, dtype=float), {}, float(layout_obj.get("tag_delay_mm") or 0.0))
    by_peer = run_clean.load_frames_by_peer(capture_path)
    frames = []
    for peer_frames in by_peer.values():
        frames.extend(peer_frames)
    frames = sorted(frames, key=lambda r: (r["t"], r["sweep"]))[:max_frames]
    ref_pos, _t, _counts = run_clean.solve_positions(eval_mod, frames, ref_layout, anchor_ids)

    layout = load_layout_json(layout_path, sigma_path)
    c_frames = read_tr_all_frames(capture_path, min_anchors=4)[:max_frames]
    solver = TagPositionSolver(layout, SolverConfig(method="T1"))
    c_rows = []
    for frame in c_frames:
        out = solver.solve_frame(frame)
        if out is not None:
            c_rows.append([out.x_mm, out.y_mm, out.z_mm])
    c_pos = np.asarray(c_rows, dtype=float)
    n = min(len(ref_pos), len(c_pos))
    if n == 0:
        return {"status": "failed", "reason": "no comparable frames"}
    diff = ref_pos[:n] - c_pos[:n]
    dist = np.linalg.norm(diff, axis=1)
    return {
        "status": "ok",
        "frames_compared": int(n),
        "max_3d_diff_mm": float(np.max(dist)),
        "median_3d_diff_mm": float(np.median(dist)),
        "rms_3d_diff_mm": float(np.sqrt(np.mean(dist * dist))),
    }


def run_t_methods(data: Path, out_dir: Path, max_frames: int, sigma_path: Path) -> list[dict]:
    layout = data / "FULL-COMPARE-1000/v4-io/layout.json"
    capture = data / "Static_Test/ID02_20260513_153316/tr_all.csv"
    rows = []
    for method in ["T1", "T2", "T3", "T4"]:
        out = out_dir / f"ID02_{method}_trajectory.json"
        result = solve_capture_trajectory(layout, capture, method=method, anchor_sigma_path=sigma_path, max_frames=max_frames)
        write_trajectory_json(result, out)
        residuals = [row.residual_rms_mm for row in result.results if math.isfinite(row.residual_rms_mm)]
        rejections = sum(1 for row in result.results if row.rejected_anchor_id is not None)
        rows.append({
            "method": method,
            "frames_input": result.frames_input,
            "frames_solved": result.frames_solved,
            "median_residual_rms_mm": median(residuals),
            "rejected_frames": rejections,
            "out": str(out),
        })
    return rows


def median(vals: list[float]) -> float:
    if not vals:
        return float("nan")
    vals = sorted(vals)
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate T-series tag positioning solver on outdoor_20260513.")
    ap.add_argument("--data", default=str(REPO / "autopos_pipeline/outdoor_20260513"))
    ap.add_argument("--out", default=str(ROOT / "validation_outputs/outdoor_20260513"))
    ap.add_argument("--max-frames", type=int, default=300)
    args = ap.parse_args()

    data = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    build_c_core()

    source_rows = compare_reference_sources()
    sigma_path = write_official_sigma(data, out_dir)
    method_rows = run_t_methods(data, out_dir, args.max_frames, sigma_path)
    ref_compare = c_vs_official_reference(data, args.max_frames, sigma_path)
    write_csv(out_dir / "reference_source_copy_check.csv", source_rows)
    write_csv(out_dir / "t_method_summary.csv", method_rows)
    (out_dir / "official_reference_compare.json").write_text(json.dumps(ref_compare, indent=2), encoding="utf-8")
    print(json.dumps({
        "out_dir": str(out_dir),
        "source_copy_check": source_rows,
        "official_reference_compare": ref_compare,
        "method_summary": method_rows,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
