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
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.models import Frame, SolverConfig


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


def percentile(vals: list[float], pct: float) -> float:
    if not vals:
        return float("nan")
    vals = sorted(vals)
    pos = (pct / 100.0) * (len(vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if hi >= len(vals):
        hi = len(vals) - 1
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def median(vals: list[float]) -> float:
    return percentile(vals, 50.0)


def load_static_captures(data: Path, max_frames_per_capture: int = 0) -> dict[str, list[Frame]]:
    captures: dict[str, list[Frame]] = {}
    for cap in sorted((data / "Static_Test").glob("ID*/tr_all.csv")):
        cid = cap.parent.name.split("_", 1)[0]
        cap_frames = read_tr_all_frames(cap, min_anchors=4)
        if max_frames_per_capture > 0:
            cap_frames = cap_frames[:max_frames_per_capture]
        captures[cid] = sorted(cap_frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))
    return captures


def load_roto_captures(data: Path, max_frames_per_capture: int = 0) -> dict[str, list[Frame]]:
    captures: dict[str, list[Frame]] = {}
    for cap in sorted((data / "Roto_Test").glob("ID*/tr_all.csv")):
        cid = cap.parent.name.split("_", 1)[0]
        cap_frames = read_tr_all_frames(cap, min_anchors=4)
        if max_frames_per_capture > 0:
            cap_frames = cap_frames[:max_frames_per_capture]
        captures[cid] = sorted(cap_frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))
    return captures


def keep_frame(frame: Frame, keep_k: int, rng: random.Random) -> Frame | None:
    obs = list(frame.observations)
    if len(obs) < keep_k:
        return None
    chosen = sorted(rng.sample(obs, keep_k), key=lambda o: o.anchor_id)
    return Frame(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=tuple(chosen),
    )


def solve_positions_for_condition(layout, frames: list[Frame], method: str) -> list[tuple[float, float, float, float, int, int | None]]:
    solver = TagPositionSolver(layout, SolverConfig(method=method))  # type: ignore[arg-type]
    out = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is None:
            continue
        out.append((
            result.x_mm,
            result.y_mm,
            result.z_mm,
            result.residual_rms_mm,
            result.anchors_used,
            result.rejected_anchor_id,
        ))
    return out


def summarize_positions(rows: list[tuple[float, float, float, float, int, int | None]]) -> dict:
    if not rows:
        return {
            "solved": 0,
            "x_std_mm": float("nan"),
            "y_std_mm": float("nan"),
            "z_std_mm": float("nan"),
            "d3_std_mm": float("nan"),
            "residual_rms_median_mm": float("nan"),
            "residual_rms_p95_mm": float("nan"),
            "rejected_frames": 0,
        }
    xs = [r[0] for r in rows]
    ys = [r[1] for r in rows]
    zs = [r[2] for r in rows]
    residuals = [r[3] for r in rows]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    mean_z = sum(zs) / len(zs)
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    dz = [z - mean_z for z in zs]
    d3 = [math.sqrt(a * a + b * b + c * c) for a, b, c in zip(dx, dy, dz)]
    std = lambda vals: math.sqrt(sum(v * v for v in vals) / max(1, len(vals)))
    return {
        "solved": len(rows),
        "x_std_mm": std(dx),
        "y_std_mm": std(dy),
        "z_std_mm": std(dz),
        "d3_std_mm": std(d3),
        "residual_rms_median_mm": median(residuals),
        "residual_rms_p95_mm": percentile(residuals, 95.0),
        "rejected_frames": sum(1 for r in rows if r[5] is not None),
    }


def summarize_capture_set(capture_rows: list[dict]) -> dict:
    ok = [r for r in capture_rows if int(r["solved"]) >= 10 and math.isfinite(float(r["d3_std_mm"]))]
    if not ok:
        return {
            "captures_ok": 0,
            "solved_total": 0,
            "x_std_mm_median": float("nan"),
            "y_std_mm_median": float("nan"),
            "z_std_mm_median": float("nan"),
            "d3_std_mm_median": float("nan"),
            "d3_std_mm_p95": float("nan"),
            "residual_rms_median_mm": float("nan"),
            "residual_rms_p95_mm": float("nan"),
            "rejected_frames_total": 0,
        }
    return {
        "captures_ok": len(ok),
        "solved_total": sum(int(r["solved"]) for r in ok),
        "x_std_mm_median": median([float(r["x_std_mm"]) for r in ok]),
        "y_std_mm_median": median([float(r["y_std_mm"]) for r in ok]),
        "z_std_mm_median": median([float(r["z_std_mm"]) for r in ok]),
        "d3_std_mm_median": median([float(r["d3_std_mm"]) for r in ok]),
        "d3_std_mm_p95": percentile([float(r["d3_std_mm"]) for r in ok], 95),
        "residual_rms_median_mm": median([float(r["residual_rms_median_mm"]) for r in ok]),
        "residual_rms_p95_mm": median([float(r["residual_rms_p95_mm"]) for r in ok]),
        "rejected_frames_total": sum(int(r["rejected_frames"]) for r in ok),
    }


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return {"N_frames": int(pts.shape[0]), "status": "insufficient"}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    total = np.sqrt(radial * radial + zplane * zplane)
    center3 = center0 + cx * e1 + cy * e2
    theta = np.unwrap(np.arctan2(y - cy, x - cx))
    if theta.size and theta[-1] < theta[0]:
        theta = -theta
    return {
        "status": "ok",
        "N_frames": int(pts.shape[0]),
        "radius": float(radius),
        "radial_std": float(np.std(radial, ddof=1)),
        "z_plane_std": float(np.std(zplane, ddof=1)),
        "circle_thickness_rms_diagnostic": float(np.sqrt(np.mean(total * total))),
        "circle_thickness_p95_diagnostic": float(np.percentile(total, 95)),
        "center_x": float(center3[0]),
        "center_y": float(center3[1]),
        "center_z": float(center3[2]),
        "_theta": theta,
    }


def per_turn_center_stats(points: np.ndarray, theta: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    th = np.asarray(theta, dtype=float)
    if pts.shape[0] < 80 or th.size != pts.shape[0]:
        return {"turn_count": 0}
    th = th - th[0]
    estimated_turns = float((th[-1] - th[0]) / (2.0 * math.pi))
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    radii = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_circle_3d(pts[idx])
        if fit.get("status") == "ok":
            centers.append([fit["center_x"], fit["center_y"], fit["center_z"]])
            radii.append(float(fit["radius"]))
    if len(centers) < 2:
        return {"turn_count": len(centers), "estimated_turns": estimated_turns}
    c = np.asarray(centers, dtype=float)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean, axis=1)
    std = np.std(c, axis=0, ddof=1)
    return {
        "turn_count": int(len(centers)),
        "estimated_turns": estimated_turns,
        "turn_center_rms_3d_mm": float(np.sqrt(np.mean(dist * dist))),
        "turn_center_p95_3d_mm": float(np.percentile(dist, 95)),
        "turn_center_x_std_mm": float(std[0]),
        "turn_center_y_std_mm": float(std[1]),
        "turn_center_z_std_mm": float(std[2]),
        "turn_radius_std_mm": float(np.std(radii, ddof=1)) if len(radii) > 1 else float("nan"),
    }


def summarize_roto_points(rows: list[tuple[float, float, float, float, int, int | None]]) -> dict:
    if len(rows) < 80:
        return {"status": "insufficient", "solved": len(rows), "turn_count": 0}
    pts = np.asarray([[r[0], r[1], r[2]] for r in rows], dtype=float)
    fit = fit_circle_3d(pts)
    if fit.get("status") != "ok":
        return {"status": "fit_failed", "solved": len(rows), "turn_count": 0}
    theta = fit.pop("_theta", None)
    turn = per_turn_center_stats(pts, theta) if theta is not None else {"turn_count": 0}
    return {
        "status": "ok",
        "solved": len(rows),
        "radius_mm": fit.get("radius", float("nan")),
        "circle_thickness_rms_mm": fit.get("circle_thickness_rms_diagnostic", float("nan")),
        "circle_thickness_p95_mm": fit.get("circle_thickness_p95_diagnostic", float("nan")),
        **turn,
    }


def summarize_roto_set(roto_rows: list[dict]) -> dict:
    ok = [
        r for r in roto_rows
        if r.get("status") == "ok"
        and int(r.get("turn_count") or 0) >= 2
        and math.isfinite(float(r.get("turn_center_rms_3d_mm", float("nan"))))
    ]
    if not ok:
        return {
            "roto_tracks_ok": 0,
            "solved_total": 0,
            "turn_center_rms_3d_mm_median": float("nan"),
            "turn_center_p95_3d_mm_median": float("nan"),
            "circle_thickness_rms_mm_median": float("nan"),
            "radius_mm_median": float("nan"),
            "turn_count_median": float("nan"),
            "rejected_frames_total": 0,
        }
    return {
        "roto_tracks_ok": len(ok),
        "solved_total": sum(int(r["solved"]) for r in ok),
        "turn_center_rms_3d_mm_median": median([float(r["turn_center_rms_3d_mm"]) for r in ok]),
        "turn_center_p95_3d_mm_median": median([float(r["turn_center_p95_3d_mm"]) for r in ok]),
        "circle_thickness_rms_mm_median": median([float(r["circle_thickness_rms_mm"]) for r in ok]),
        "radius_mm_median": median([float(r["radius_mm"]) for r in ok]),
        "turn_count_median": median([float(r["turn_count"]) for r in ok]),
        "rejected_frames_total": sum(int(r.get("rejected_frames") or 0) for r in ok),
    }


def plot_summary(summary_csv: Path, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    with summary_csv.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    methods = ["T1", "T2", "T3", "T4"]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), constrained_layout=True)
    for method in methods:
        mrows = [r for r in rows if r["method"] == method]
        xs = [int(r["keep_k"]) for r in mrows]
        d3 = [float(r["d3_std_mm_median"]) for r in mrows]
        z = [float(r["z_std_mm_median"]) for r in mrows]
        axes[0].plot(xs, d3, marker="o", label=method)
        axes[1].plot(xs, z, marker="o", label=method)
    axes[0].set_title("3D Repeatability vs Keep-k")
    axes[1].set_title("Z Repeatability vs Keep-k")
    for ax in axes:
        ax.set_xlabel("Kept anchors")
        ax.set_ylabel("median per-capture std (mm)")
        ax.set_xticks([4, 5, 6, 7, 8])
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.invert_xaxis()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def plot_roto_summary(summary_csv: Path, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = []
    with summary_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    methods = ["T1", "T2", "T3", "T4"]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), constrained_layout=True)
    for method in methods:
        mrows = [r for r in rows if r["method"] == method]
        xs = [int(r["keep_k"]) for r in mrows]
        center = [float(r["turn_center_rms_3d_mm_median"]) for r in mrows]
        thick = [float(r["circle_thickness_rms_mm_median"]) for r in mrows]
        axes[0].plot(xs, center, marker="o", label=method)
        axes[1].plot(xs, thick, marker="o", label=method)
    axes[0].set_title("Roto Turn-Center Repeatability")
    axes[1].set_title("Roto Circle Thickness")
    for ax in axes:
        ax.set_xlabel("Kept anchors")
        ax.set_ylabel("median metric (mm)")
        ax.set_xticks([4, 5, 6, 7, 8])
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.invert_xaxis()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare T-series robustness under random keep-k anchor reduction.")
    ap.add_argument("--data", default=str(REPO / "autopos_pipeline/outdoor_20260513"))
    ap.add_argument("--out", default=str(ROOT / "validation_outputs/outdoor_20260513_keepk"))
    ap.add_argument("--repeats", type=int, default=80)
    ap.add_argument("--seed", type=int, default=20260524)
    ap.add_argument("--max-frames-per-capture", type=int, default=0)
    ap.add_argument("--skip-static", action="store_true")
    ap.add_argument("--skip-roto", action="store_true")
    args = ap.parse_args()

    data = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout_path = data / "FULL-COMPARE-1000/v4-io/layout.json"
    sigma_path = ROOT / "validation_outputs/outdoor_20260513/official_anchor_sigma.json"
    if not sigma_path.exists():
        sigma_path = data / "FULL-COMPARE-1000/tables/anchor_sigma.json"
    layout = load_layout_json(layout_path, sigma_path)
    captures = load_static_captures(data, args.max_frames_per_capture) if not args.skip_static else {}
    roto_captures = load_roto_captures(data, args.max_frames_per_capture) if not args.skip_roto else {}

    detail_rows: list[dict] = []
    aggregate: dict[tuple[str, int], list[dict]] = defaultdict(list)
    rng = random.Random(args.seed)
    if captures:
        for method in ["T1", "T2", "T3", "T4"]:
            for keep_k in [8, 7, 6, 5, 4]:
                for repeat in range(args.repeats):
                    capture_summaries = []
                    input_total = 0
                    for capture_id, frames in captures.items():
                        kept = []
                        if keep_k == 8:
                            kept = [frame for frame in frames if len(frame.observations) >= 8]
                        else:
                            for frame in frames:
                                kf = keep_frame(frame, keep_k, rng)
                                if kf is not None:
                                    kept.append(kf)
                        input_total += len(kept)
                        rows = solve_positions_for_condition(layout, kept, method)
                        s_cap = {"capture": capture_id, **summarize_positions(rows)}
                        capture_summaries.append(s_cap)
                        detail_rows.append({
                            "method": method,
                            "keep_k": keep_k,
                            "repeat": repeat,
                            "capture": capture_id,
                            "input_frames": len(kept),
                            **s_cap,
                        })
                    s = summarize_capture_set(capture_summaries)
                    detail = {
                        "method": method,
                        "keep_k": keep_k,
                        "repeat": repeat,
                        "input_frames": input_total,
                        **s,
                    }
                    aggregate[(method, keep_k)].append(detail)
                    print(
                        f"[keepk-static] method={method} keep={keep_k} repeat={repeat+1}/{args.repeats} "
                        f"captures={s['captures_ok']} solved={s['solved_total']} "
                        f"d3={s['d3_std_mm_median']:.1f} z={s['z_std_mm_median']:.1f}",
                        flush=True,
                    )

    summary_rows: list[dict] = []
    for (method, keep_k), items in sorted(aggregate.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        for_metric = {
            key: [float(item[key]) for item in items if math.isfinite(float(item[key]))]
            for key in [
                "x_std_mm_median",
                "y_std_mm_median",
                "z_std_mm_median",
                "d3_std_mm_median",
                "d3_std_mm_p95",
                "residual_rms_median_mm",
                "residual_rms_p95_mm",
            ]
        }
        summary_rows.append({
            "method": method,
            "keep_k": keep_k,
            "repeats": len(items),
            "input_frames_median": median([float(item["input_frames"]) for item in items]),
            "captures_ok_median": median([float(item["captures_ok"]) for item in items]),
            "solved_total_median": median([float(item["solved_total"]) for item in items]),
            "x_std_mm_median": median(for_metric["x_std_mm_median"]),
            "y_std_mm_median": median(for_metric["y_std_mm_median"]),
            "z_std_mm_median": median(for_metric["z_std_mm_median"]),
            "d3_std_mm_median": median(for_metric["d3_std_mm_median"]),
            "d3_std_mm_p95": median(for_metric["d3_std_mm_p95"]),
            "residual_rms_median_mm": median(for_metric["residual_rms_median_mm"]),
            "residual_rms_p95_mm": median(for_metric["residual_rms_p95_mm"]),
            "rejected_frames_median": median([float(item["rejected_frames_total"]) for item in items]),
        })

    detail_csv = out_dir / "keepk_detail.csv"
    summary_csv = out_dir / "keepk_summary.csv"
    plot_png = out_dir / "keepk_t1_t2_t3_repeatability.png"
    if captures:
        write_csv(detail_csv, detail_rows)
        write_csv(summary_csv, summary_rows)
        plot_summary(summary_csv, plot_png)

    roto_detail_rows: list[dict] = []
    roto_aggregate: dict[tuple[str, int], list[dict]] = defaultdict(list)
    if roto_captures:
        for method in ["T1", "T2", "T3", "T4"]:
            for keep_k in [8, 7, 6, 5, 4]:
                for repeat in range(args.repeats):
                    track_summaries = []
                    input_total = 0
                    for capture_id, frames in roto_captures.items():
                        kept = []
                        if keep_k == 8:
                            kept = [frame for frame in frames if len(frame.observations) >= 8]
                        else:
                            for frame in frames:
                                kf = keep_frame(frame, keep_k, rng)
                                if kf is not None:
                                    kept.append(kf)
                        input_total += len(kept)
                        by_tag: dict[str, list[Frame]] = defaultdict(list)
                        for frame in kept:
                            by_tag[frame.tag].append(frame)
                        for tag, tag_frames in sorted(by_tag.items()):
                            rows = solve_positions_for_condition(layout, tag_frames, method)
                            s_track = {
                                "capture": capture_id,
                                "tag": tag,
                                **summarize_roto_points(rows),
                                "rejected_frames": sum(1 for row in rows if row[5] is not None),
                            }
                            track_summaries.append(s_track)
                            roto_detail_rows.append({
                                "method": method,
                                "keep_k": keep_k,
                                "repeat": repeat,
                                "input_frames": len(tag_frames),
                                **s_track,
                            })
                    s = summarize_roto_set(track_summaries)
                    detail = {
                        "method": method,
                        "keep_k": keep_k,
                        "repeat": repeat,
                        "input_frames": input_total,
                        **s,
                    }
                    roto_aggregate[(method, keep_k)].append(detail)
                    print(
                        f"[keepk-roto] method={method} keep={keep_k} repeat={repeat+1}/{args.repeats} "
                        f"tracks={s['roto_tracks_ok']} solved={s['solved_total']} "
                        f"center={s['turn_center_rms_3d_mm_median']:.1f} thick={s['circle_thickness_rms_mm_median']:.1f}",
                        flush=True,
                    )

    roto_summary_rows: list[dict] = []
    for (method, keep_k), items in sorted(roto_aggregate.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        metrics = {
            key: [float(item[key]) for item in items if math.isfinite(float(item[key]))]
            for key in [
                "turn_center_rms_3d_mm_median",
                "turn_center_p95_3d_mm_median",
                "circle_thickness_rms_mm_median",
                "radius_mm_median",
                "turn_count_median",
            ]
        }
        roto_summary_rows.append({
            "method": method,
            "keep_k": keep_k,
            "repeats": len(items),
            "input_frames_median": median([float(item["input_frames"]) for item in items]),
            "roto_tracks_ok_median": median([float(item["roto_tracks_ok"]) for item in items]),
            "solved_total_median": median([float(item["solved_total"]) for item in items]),
            "turn_center_rms_3d_mm_median": median(metrics["turn_center_rms_3d_mm_median"]),
            "turn_center_p95_3d_mm_median": median(metrics["turn_center_p95_3d_mm_median"]),
            "circle_thickness_rms_mm_median": median(metrics["circle_thickness_rms_mm_median"]),
            "radius_mm_median": median(metrics["radius_mm_median"]),
            "turn_count_median": median(metrics["turn_count_median"]),
            "rejected_frames_median": median([float(item["rejected_frames_total"]) for item in items]),
        })

    roto_detail_csv = out_dir / "roto_keepk_detail.csv"
    roto_summary_csv = out_dir / "roto_keepk_summary.csv"
    roto_plot_png = out_dir / "roto_keepk_t1_t2_t3_center_repeatability.png"
    if roto_captures:
        write_csv(roto_detail_csv, roto_detail_rows)
        write_csv(roto_summary_csv, roto_summary_rows)
        plot_roto_summary(roto_summary_csv, roto_plot_png)

    print(json.dumps({
        "captures": len(captures),
        "frames_total": sum(len(v) for v in captures.values()),
        "static_detail_csv": str(detail_csv) if captures else "",
        "static_summary_csv": str(summary_csv) if captures else "",
        "static_plot_png": str(plot_png) if captures else "",
        "roto_captures": len(roto_captures),
        "roto_frames_total": sum(len(v) for v in roto_captures.values()),
        "roto_detail_csv": str(roto_detail_csv) if roto_captures else "",
        "roto_summary_csv": str(roto_summary_csv) if roto_captures else "",
        "roto_plot_png": str(roto_plot_png) if roto_captures else "",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
