#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.models import Frame, SolverConfig


def percentile(vals: list[float], pct: float) -> float:
    vals = [float(v) for v in vals if math.isfinite(float(v))]
    if not vals:
        return float("nan")
    vals.sort()
    pos = (pct / 100.0) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = min(len(vals) - 1, int(math.ceil(pos)))
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def median(vals: list[float]) -> float:
    return percentile(vals, 50.0)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
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


def keep_frame(frame: Frame, keep_k: int, rng: random.Random) -> Frame | None:
    obs = list(frame.observations)
    if len(obs) < keep_k:
        return None
    if keep_k < len(obs):
        obs = rng.sample(obs, keep_k)
    return Frame(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=tuple(sorted(obs, key=lambda item: item.anchor_id)),
        imu=frame.imu,
    )


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 80:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    plane = (pts - center0) @ normal
    total = np.sqrt(radial * radial + plane * plane)
    center3 = center0 + cx * e1 + cy * e2
    theta = np.unwrap(np.arctan2(y - cy, x - cx))
    if theta.size and theta[-1] < theta[0]:
        theta = -theta
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "radius_mm": float(radius),
        "center_x": float(center3[0]),
        "center_y": float(center3[1]),
        "center_z": float(center3[2]),
        "radial_rms_mm": float(np.sqrt(np.mean(radial * radial))),
        "plane_rms_mm": float(np.sqrt(np.mean(plane * plane))),
        "circle_thickness_rms_mm": float(np.sqrt(np.mean(total * total))),
        "circle_thickness_p95_mm": float(np.percentile(total, 95)),
        "_theta": theta,
    }


def per_turn_center_stats(points: np.ndarray, theta: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    th = np.asarray(theta, dtype=float)
    if pts.shape[0] < 120 or th.size != pts.shape[0]:
        return {"turn_count": 0}
    th = th - th[0]
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    radii = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 40:
            continue
        fit = fit_circle_3d(pts[idx])
        if fit.get("status") == "ok":
            centers.append([fit["center_x"], fit["center_y"], fit["center_z"]])
            radii.append(float(fit["radius_mm"]))
    if len(centers) < 2:
        return {"turn_count": len(centers)}
    c = np.asarray(centers, dtype=float)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean, axis=1)
    return {
        "turn_count": int(len(centers)),
        "turn_center_rms_3d_mm": float(np.sqrt(np.mean(dist * dist))),
        "turn_center_p95_3d_mm": float(np.percentile(dist, 95)),
        "turn_radius_std_mm": float(np.std(radii, ddof=1)) if len(radii) > 1 else float("nan"),
    }


def solve_frames(layout, frames: list[Frame], method: str) -> list[dict]:
    solver = TagPositionSolver(layout, SolverConfig(method=method))  # type: ignore[arg-type]
    rows = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is None:
            continue
        rows.append({
            "tag": result.tag,
            "sweep": result.sweep,
            "x_mm": result.x_mm,
            "y_mm": result.y_mm,
            "z_mm": result.z_mm,
            "anchors_input": result.anchors_input,
            "anchors_used": result.anchors_used,
            "residual_rms_mm": result.residual_rms_mm,
            "imu_std_mg": result.imu_acc_norm_std_mg,
            "imu_prior_scale": result.imu_prior_scale,
            "temporal_prior_sigma_used_mm": result.temporal_prior_sigma_used_mm,
            "rejected_anchor_id": result.rejected_anchor_id,
        })
    return rows


def summarize_track(rows: list[dict]) -> dict:
    if len(rows) < 80:
        return {"status": "insufficient", "solved": len(rows)}
    pts = np.asarray([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in rows], dtype=float)
    fit = fit_circle_3d(pts)
    if fit.get("status") != "ok":
        return {"status": fit.get("status", "fit_failed"), "solved": len(rows)}
    theta = fit.pop("_theta")
    turn = per_turn_center_stats(pts, theta)
    residuals = [float(r["residual_rms_mm"]) for r in rows]
    prior = [float(r["imu_prior_scale"]) for r in rows if r.get("imu_prior_scale") is not None]
    imu_std = [float(r["imu_std_mg"]) for r in rows if r.get("imu_std_mg") is not None]
    return {
        "status": "ok",
        "solved": len(rows),
        "radius_mm": fit["radius_mm"],
        "center_x": fit["center_x"],
        "center_y": fit["center_y"],
        "center_z": fit["center_z"],
        "circle_thickness_rms_mm": fit["circle_thickness_rms_mm"],
        "circle_thickness_p95_mm": fit["circle_thickness_p95_mm"],
        "residual_rms_median_mm": median(residuals),
        "residual_rms_p95_mm": percentile(residuals, 95.0),
        "imu_prior_frames": len(prior),
        "imu_prior_scale_median": median(prior),
        "imu_prior_scale_min": min(prior) if prior else float("nan"),
        "imu_std_mg_median": median(imu_std),
        "imu_std_mg_p95": percentile(imu_std, 95.0),
        "rejected_frames": sum(1 for r in rows if r.get("rejected_anchor_id") is not None),
        **turn,
    }


def summarize_repeat(method: str, keep_k: int, repeat: int, rows_by_tag: dict[str, list[dict]]) -> dict:
    track_rows = {tag: summarize_track(rows) for tag, rows in rows_by_tag.items()}
    out = {
        "method": method,
        "keep_k": keep_k,
        "repeat": repeat,
        "tracks_ok": sum(1 for row in track_rows.values() if row.get("status") == "ok"),
    }
    for tag, row in sorted(track_rows.items()):
        prefix = tag.lower()
        for key, value in row.items():
            out[f"{prefix}_{key}"] = value
    if "BS2DCE" in track_rows and "BSDC91" in track_rows:
        inner = track_rows["BS2DCE"]
        outer = track_rows["BSDC91"]
        if inner.get("status") == "ok" and outer.get("status") == "ok":
            delta = float(outer["radius_mm"]) - float(inner["radius_mm"])
            ci = np.asarray([inner["center_x"], inner["center_y"], inner["center_z"]], dtype=float)
            co = np.asarray([outer["center_x"], outer["center_y"], outer["center_z"]], dtype=float)
            out.update({
                "delta_radius_mm": delta,
                "delta_radius_bias_vs_120_mm": delta - 120.0,
                "inner_outer_center_sep_mm": float(np.linalg.norm(co - ci)),
                "mean_circle_thickness_rms_mm": (
                    float(inner["circle_thickness_rms_mm"]) + float(outer["circle_thickness_rms_mm"])
                ) / 2.0,
                "mean_turn_center_rms_3d_mm": median([
                    float(inner.get("turn_center_rms_3d_mm", float("nan"))),
                    float(outer.get("turn_center_rms_3d_mm", float("nan"))),
                ]),
            })
    return out


def summarize_aggregate(rows: list[dict]) -> list[dict]:
    out = []
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["method"], int(row["keep_k"]))].append(row)
    metric_keys = [
        "delta_radius_bias_vs_120_mm",
        "inner_outer_center_sep_mm",
        "mean_circle_thickness_rms_mm",
        "mean_turn_center_rms_3d_mm",
        "bs2dce_circle_thickness_rms_mm",
        "bsdc91_circle_thickness_rms_mm",
        "bs2dce_turn_center_rms_3d_mm",
        "bsdc91_turn_center_rms_3d_mm",
        "bs2dce_residual_rms_median_mm",
        "bsdc91_residual_rms_median_mm",
        "bs2dce_imu_prior_scale_median",
        "bsdc91_imu_prior_scale_median",
    ]
    for (method, keep_k), items in sorted(grouped.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        row = {
            "method": method,
            "keep_k": keep_k,
            "repeats": len(items),
            "tracks_ok_median": median([float(item.get("tracks_ok", 0)) for item in items]),
        }
        for key in metric_keys:
            vals = [
                float(item[key])
                for item in items
                if key in item and str(item[key]) != "" and math.isfinite(float(item[key]))
            ]
            row[f"{key}_median"] = median(vals)
            row[f"{key}_p95"] = percentile(vals, 95.0)
        out.append(row)
    return out


def plot_summary(summary_csv: Path, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = list(csv.DictReader(summary_csv.open(newline="", encoding="utf-8")))
    methods = ["T4", "T4_V6_IMU_GATE"]
    fig, axes = plt.subplots(1, 3, figsize=(14.0, 4.0), constrained_layout=True)
    for method in methods:
        mrows = [r for r in rows if r["method"] == method]
        xs = [int(r["keep_k"]) for r in mrows]
        bias = [abs(float(r["delta_radius_bias_vs_120_mm_median"])) for r in mrows]
        thick = [float(r["mean_circle_thickness_rms_mm_median"]) for r in mrows]
        center = [float(r["mean_turn_center_rms_3d_mm_median"]) for r in mrows]
        axes[0].plot(xs, bias, marker="o", label=method)
        axes[1].plot(xs, thick, marker="o", label=method)
        axes[2].plot(xs, center, marker="o", label=method)
    axes[0].set_title("|Delta radius bias|")
    axes[1].set_title("Mean circle thickness RMS")
    axes[2].set_title("Mean turn-center RMS")
    for ax in axes:
        ax.set_xlabel("Kept anchors")
        ax.set_ylabel("mm")
        ax.set_xticks([4, 5, 6, 7, 8])
        ax.invert_xaxis()
        ax.grid(True, alpha=0.3)
        ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare T4 vs T4_V6_IMU_GATE under forced keep-k on one Roto capture.")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--capture", required=True, help="Capture directory or tr_all.csv")
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260525)
    ap.add_argument("--keep", default="8,7,6,5,4")
    ap.add_argument("--tags", default="BS2DCE,BSDC91")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    tags = {item.strip().upper() for item in args.tags.split(",") if item.strip()}
    keep_values = [int(item) for item in args.keep.split(",") if item.strip()]
    layout = load_layout_json(args.layout)
    frames = read_tr_all_frames(args.capture, tags=tags, min_anchors=4)
    by_tag: dict[str, list[Frame]] = defaultdict(list)
    for frame in frames:
        by_tag[frame.tag].append(frame)
    for tag in by_tag:
        by_tag[tag].sort(key=lambda item: (item.host_epoch_s, item.sweep))

    detail_rows = []
    rng = random.Random(args.seed)
    for keep_k in keep_values:
        repeat_count = 1 if keep_k == 8 else args.repeats
        for repeat in range(repeat_count):
            kept_by_tag: dict[str, list[Frame]] = defaultdict(list)
            for tag, tag_frames in by_tag.items():
                for frame in tag_frames:
                    kept = keep_frame(frame, keep_k, rng)
                    if kept is not None:
                        kept_by_tag[tag].append(kept)
            solved_by_method: dict[str, dict[str, list[dict]]] = {}
            for method in ["T4", "T4_V6_IMU_GATE"]:
                solved_by_tag = {
                    tag: solve_frames(layout, tag_frames, method)
                    for tag, tag_frames in kept_by_tag.items()
                }
                solved_by_method[method] = solved_by_tag
                summary = summarize_repeat(method, keep_k, repeat, solved_by_tag)
                summary["input_frames"] = sum(len(v) for v in kept_by_tag.values())
                detail_rows.append(summary)
                print(
                    f"[imu-keepk] keep={keep_k} repeat={repeat + 1}/{repeat_count} "
                    f"method={method} tracks={summary.get('tracks_ok')} "
                    f"dRbias={summary.get('delta_radius_bias_vs_120_mm', float('nan')):.2f} "
                    f"thick={summary.get('mean_circle_thickness_rms_mm', float('nan')):.2f}",
                    flush=True,
                )
    summary_rows = summarize_aggregate(detail_rows)
    detail_csv = out_dir / "imu_gate_keepk_detail.csv"
    summary_csv = out_dir / "imu_gate_keepk_summary.csv"
    plot_png = out_dir / "imu_gate_keepk_summary.png"
    write_csv(detail_csv, detail_rows)
    write_csv(summary_csv, summary_rows)
    plot_summary(summary_csv, plot_png)
    meta = {
        "layout": str(args.layout),
        "capture": str(args.capture),
        "out": str(out_dir),
        "frames_total": len(frames),
        "frames_by_tag": {tag: len(items) for tag, items in sorted(by_tag.items())},
        "repeats": args.repeats,
        "seed": args.seed,
        "detail_csv": str(detail_csv),
        "summary_csv": str(summary_csv),
        "plot_png": str(plot_png),
    }
    (out_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
