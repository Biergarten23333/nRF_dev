#!/usr/bin/env python3
"""Static FULL derivative ablations for known-anchor and scale-correction paths.

This script writes three clean experiment directories:

* FULL_AutoPos_align_to_Vicon
* FULL_AutoPos_scale_to_vicon
* FULL_AutoPos_one_baseline_scale_correction

All solved layouts are expressed directly in the OptiTrack frame before tag
solving.  For the original AutoPos condition this is a rigid/reflection
anchor-lock with no scale; for scale-to-Vicon this is each solver's own
similarity scale; for one-baseline correction this is each solver's own
single-baseline scale for all C(8,2) anchor pairs.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ANCHORS = list("ABCDEFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]

THIS = Path(__file__).resolve()
COMPARISON_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT_DEFAULT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
FULL_ROOT = EXTRA_ROOT / "FULL"
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"

sys.path.insert(0, str(FULL_ROOT / "scripts"))
sys.path.insert(0, str(SOLVER_ROOT))

from tag_ground_truth import load_corrected_static_truth  # noqa: E402
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_anchor_sigma  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Anchor, Frame, Layout, SolverConfig  # noqa: E402


@dataclass(frozen=True)
class Fit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float


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


def load_static_metadata(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    df = pd.read_csv(path)
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row.get("ID", "")).strip()
        if sid and sid not in out:
            out[sid] = {
                "location": row.get("location", ""),
                "height": row.get("height", ""),
                "facing": row.get("facing", ""),
            }
    return out


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name.split("_")[1]
    return path.parents[1].name


def capture_name_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name
    return path.parents[1].name


def load_layout_json_raw(path: Path) -> tuple[list[str], np.ndarray, dict[int, float], float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels = []
    coords = []
    delays: dict[int, float] = {}
    for item in data["anchors"]:
        aid = int(item["id"])
        label = item.get("label", ANCHORS[aid])
        labels.append(label)
        coords.append([float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])])
        delays[aid] = float(item.get("d_anchor_mm") or 0.0)
    for aid in range(len(ANCHORS)):
        delays.setdefault(aid, 0.0)
    return labels, np.asarray(coords, dtype=float), delays, float(data.get("tag_delay_mm") or 0.0)


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> Fit:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, svals, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(svals * d) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    return Fit(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)))


def apply_fit(points: np.ndarray, fit: Fit) -> np.ndarray:
    return fit.scale * points @ fit.rotation + fit.translation


def fit_with_fixed_scale(src: np.ndarray, dst: np.ndarray, rotation: np.ndarray, scale: float) -> Fit:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    t = dst_c - scale * src_c @ rotation
    return Fit(rotation=rotation, translation=t, scale=float(scale), det=float(np.linalg.det(rotation)))


def estimate_delaycal_from_points(points_by_label: dict[str, np.ndarray], pair_quality_csv: Path, source: str) -> tuple[dict[int, float], float, list[dict]]:
    df = pd.read_csv(pair_quality_csv)
    df = df[df["eval_set"] == "solve"].copy()
    design = []
    target = []
    rows = []
    for _, row in df.iterrows():
        a, b = str(row["pair"]).split("-")
        ia = ANCHORS.index(a)
        ib = ANCHORS.index(b)
        measured = float(row["median_all"])
        layout_dist = float(np.linalg.norm(points_by_label[a] - points_by_label[b]))
        bias = measured - layout_dist
        vec = np.zeros(len(ANCHORS), dtype=float)
        vec[ia] = 1.0
        vec[ib] = 1.0
        design.append(vec)
        target.append(bias)
        rows.append(
            {
                "source": source,
                "pair": f"{a}-{b}",
                "measured_median_mm": measured,
                "layout_pair_dist_mm": layout_dist,
                "pair_bias_mm": bias,
            }
        )
    m = np.vstack(design)
    y = np.asarray(target, dtype=float)
    delays, *_ = np.linalg.lstsq(m, y, rcond=None)
    for row in rows:
        a, b = row["pair"].split("-")
        pred = delays[ANCHORS.index(a)] + delays[ANCHORS.index(b)]
        row["delaycal_pair_predicted_bias_mm"] = float(pred)
        row["delaycal_pair_residual_mm"] = float(pred - row["pair_bias_mm"])
    tag_delay = float(np.nanmedian(delays))
    return {i: float(v) for i, v in enumerate(delays)}, tag_delay, rows


def estimate_delaycal(anchor_truth: dict[str, np.ndarray], pair_quality_csv: Path) -> tuple[dict[int, float], float, list[dict]]:
    return estimate_delaycal_from_points(anchor_truth, pair_quality_csv, "OptiTrack truth anchors")


def build_layout(
    *,
    name: str,
    labels: list[str],
    coords_opti_frame: np.ndarray,
    delays: dict[int, float],
    tag_delay_mm: float,
    sigma_by_id: dict[int, float],
    metadata: dict,
) -> Layout:
    anchors: dict[int, Anchor] = {}
    by_label = {label: coords_opti_frame[i] for i, label in enumerate(labels)}
    for aid, label in enumerate(ANCHORS):
        xyz = by_label[label]
        anchors[aid] = Anchor(
            id=aid,
            label=label,
            x_mm=float(xyz[0]),
            y_mm=float(xyz[1]),
            z_mm=float(xyz[2]),
            d_anchor_mm=float(delays.get(aid, 0.0)),
            sigma_mm=float(sigma_by_id.get(aid, 50.0)),
        )
    return Layout(path=name, anchors=anchors, tag_delay_mm=float(tag_delay_mm), metadata=metadata)


def filter_frames(frames: list[Frame], allowed_anchor_ids: set[int]) -> list[Frame]:
    out: list[Frame] = []
    for frame in frames:
        obs = tuple(o for o in frame.observations if o.anchor_id in allowed_anchor_ids)
        if len(obs) >= 4:
            out.append(
                Frame(
                    tag=frame.tag,
                    sweep=frame.sweep,
                    host_elapsed_s=frame.host_elapsed_s,
                    host_epoch_s=frame.host_epoch_s,
                    observations=obs,
                    imu=frame.imu,
                )
            )
    return out


def summarize_results(results: list, estimator: str) -> dict:
    if not results:
        return {"status": "no_solution", "frames_solved": 0}
    pts = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
    if estimator == "mean":
        p = np.nanmean(pts, axis=0)
    else:
        p = np.nanmedian(pts, axis=0)
    d = pts - p[None, :]
    d3 = np.linalg.norm(d, axis=1)
    residual = np.asarray([r.residual_rms_mm for r in results], dtype=float)
    anchors_input = np.asarray([r.anchors_input for r in results], dtype=float)
    anchors_used = np.asarray([r.anchors_used for r in results], dtype=float)
    return {
        "status": "ok",
        "frames_solved": int(len(results)),
        "x_mm": float(p[0]),
        "y_mm": float(p[1]),
        "z_mm": float(p[2]),
        "d3_std_mm": float(math.sqrt(np.nanmean(d3 * d3))),
        "d3_p95_mm": float(np.nanpercentile(d3, 95)),
        "residual_rms_median_mm": float(np.nanmedian(residual)),
        "residual_rms_p95_mm": float(np.nanpercentile(residual, 95)),
        "anchors_input_median": float(np.nanmedian(anchors_input)),
        "anchors_used_median": float(np.nanmedian(anchors_used)),
        "pct_frames_ge8_anchors_input": float(np.mean(anchors_input >= 8.0) * 100.0),
    }


def solve_one_job(job: dict) -> dict:
    layout = build_layout(
        name=job["layout_name"],
        labels=job["labels"],
        coords_opti_frame=np.asarray(job["coords_opti_frame"], dtype=float),
        delays={int(k): float(v) for k, v in job["delays"].items()},
        tag_delay_mm=float(job["tag_delay_mm"]),
        sigma_by_id={int(k): float(v) for k, v in job["sigma_by_id"].items()},
        metadata=job["metadata"],
    )
    solver = TagPositionSolver(layout, SolverConfig(method=job["tag_method"]))
    tag_truth = {k: np.asarray(v, dtype=float) for k, v in job["tag_truth"].items()}
    tag_truth_meta = job["tag_truth_meta"]
    metadata_by_id = job["static_metadata"]
    rows: list[dict] = []
    for path_s in job["static_files"]:
        path = Path(path_s)
        sid = session_id_from_path(path)
        frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        if job["max_frames"] > 0:
            frames = frames[: int(job["max_frames"])]
        frames = filter_frames(frames, set(range(8)))
        results = []
        for frame in frames:
            result = solver.solve_frame(frame)
            if result is not None and result.status == "ok":
                results.append(result)
        summary = summarize_results(results, job["point_estimator"])
        truth = tag_truth.get(sid)
        if truth is None or summary["status"] != "ok":
            continue
        solved = np.asarray([summary["x_mm"], summary["y_mm"], summary["z_mm"]], dtype=float)
        diff = solved - truth
        e3 = float(np.linalg.norm(diff))
        horiz = float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2]))
        vert = float(abs(diff[1]))
        meta = metadata_by_id.get(sid, {})
        truth_info = tag_truth_meta.get(sid, {})
        row = {
            **job["metadata"],
            "tag_method": job["tag_method"],
            "ID": sid,
            "capture": capture_name_from_path(path),
            "location": meta.get("location", ""),
            "height": meta.get("height", ""),
            "facing": meta.get("facing", ""),
            "frames_input": int(len(frames)),
            "frames_solved": int(summary["frames_solved"]),
            "solve_fraction": float(summary["frames_solved"] / len(frames)) if frames else 0.0,
            "point_estimator": job["point_estimator"],
            "solved_x_mm": float(solved[0]),
            "solved_y_vertical_mm": float(solved[1]),
            "solved_z_mm": float(solved[2]),
            "truth_x_mm": float(truth[0]),
            "truth_y_vertical_mm": float(truth[1]),
            "truth_z_mm": float(truth[2]),
            "err_x_mm": float(diff[0]),
            "err_y_vertical_mm": float(diff[1]),
            "err_z_mm": float(diff[2]),
            "err_3d_mm": e3,
            "err_horizontal_xz_mm": horiz,
            "err_vertical_y_mm": vert,
            "tag_truth_source": truth_info.get("tag_truth_source", ""),
            "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
            **{k: v for k, v in summary.items() if k not in {"x_mm", "y_mm", "z_mm", "status"}},
        }
        rows.append(row)
    return {"experiment": job["experiment"], "rows": rows}


def summarize(rows: list[dict], group_cols: list[str]) -> list[dict]:
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out: list[dict] = []
    for key, g in df.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        err = g["err_3d_mm"].to_numpy(dtype=float)
        horiz = g["err_horizontal_xz_mm"].to_numpy(dtype=float)
        vert = g["err_vertical_y_mm"].to_numpy(dtype=float)
        row = {col: key[i] for i, col in enumerate(group_cols)}
        row.update(
            {
                "n_sessions": int(len(g)),
                "frames_solved_total": int(g["frames_solved"].sum()),
                "frames_input_total": int(g["frames_input"].sum()),
                "err_3d_median_mm": float(np.nanmedian(err)),
                "err_3d_p75_mm": float(np.nanpercentile(err, 75)),
                "err_3d_p95_mm": float(np.nanpercentile(err, 95)),
                "err_3d_rms_mm": float(math.sqrt(np.nanmean(err * err))),
                "err_horizontal_xz_median_mm": float(np.nanmedian(horiz)),
                "err_horizontal_xz_p95_mm": float(np.nanpercentile(horiz, 95)),
                "err_vertical_y_median_mm": float(np.nanmedian(vert)),
                "err_vertical_y_p95_mm": float(np.nanpercentile(vert, 95)),
                "d3_std_median_mm": float(np.nanmedian(g["d3_std_mm"].to_numpy(dtype=float))),
                "residual_rms_median_mm": float(np.nanmedian(g["residual_rms_median_mm"].to_numpy(dtype=float))),
            }
        )
        out.append(row)
    return out


def write_markdown_summary(path: Path, title: str, summary_rows: list[dict], group_cols: list[str]) -> None:
    df = pd.DataFrame(summary_rows)
    lines = [f"# {title}\n\n"]
    lines.append(f"Generated {datetime.now(UTC).isoformat()}.\n\n")
    if df.empty:
        lines.append("No rows.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    best = df.sort_values(["err_3d_median_mm", "err_3d_p95_mm"]).head(20)
    cols = [*group_cols, "n_sessions", "err_3d_median_mm", "err_3d_p95_mm", "err_horizontal_xz_p95_mm", "err_vertical_y_p95_mm", "d3_std_median_mm"]
    lines.append("## Best Rows By 3D Median\n\n")
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for _, row in best.iterrows():
        vals = []
        for col in cols:
            val = row[col]
            if col == "scale_factor":
                vals.append(f"{float(val):.4f}")
            elif isinstance(val, float):
                vals.append(f"{val:.1f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    lines.append("\n## Full Summary\n\n")
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for _, row in df.sort_values(group_cols).iterrows():
        vals = []
        for col in cols:
            val = row[col]
            if col == "scale_factor":
                vals.append(f"{float(val):.4f}")
            elif isinstance(val, float):
                vals.append(f"{val:.1f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def make_variant_jobs(args: argparse.Namespace) -> tuple[list[dict], dict[str, list[str]], list[dict]]:
    official_root = Path(args.official_root).resolve()
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    static_table = layout_base / "tables/static_all_captures.csv"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"

    anchor_truth, tag_truth, tag_truth_meta, _corr = load_corrected_static_truth(opti_dir, ANCHORS, PRIMARY_IDS)
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    sigma_by_id = load_anchor_sigma(sigma_path)
    static_metadata = load_static_metadata(static_table)
    static_files = [str(p) for p in sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))]
    delaycal_delays, delaycal_tag_delay, delay_rows = estimate_delaycal(anchor_truth, pair_quality)

    jobs: list[dict] = []
    experiment_dirs = {
        "align_to_vicon": EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon",
        "scale_to_vicon": EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon",
        "one_baseline": EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction",
    }
    for out in experiment_dirs.values():
        for sub in ["scripts", "configs", "tables", "figs", "reports", "logs"]:
            (out / sub).mkdir(parents=True, exist_ok=True)

    tag_methods = TAG_METHODS if args.tag_methods == "all" else [x.strip().upper() for x in args.tag_methods.split(",") if x.strip()]
    layout_versions = LAYOUT_VERSIONS if args.layouts == "all" else [x.strip() for x in args.layouts.split(",") if x.strip()]
    for layout_name in layout_versions:
        labels, coords, solver_delays, solver_tag_delay = load_layout_json_raw(layout_base / layout_name / "layout.json")
        by_label = {label: coords[i] for i, label in enumerate(labels)}
        src = np.vstack([by_label[a] for a in ANCHORS])
        rigid = fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
        similarity = fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=True)
        original_coords = apply_fit(src, rigid)
        scaled_coords = apply_fit(src, similarity)
        scaled_by_label = {a: scaled_coords[i] for i, a in enumerate(ANCHORS)}
        scaled_delaycal_delays, scaled_delaycal_tag_delay, scaled_delay_rows = estimate_delaycal_from_points(
            scaled_by_label,
            pair_quality,
            f"{layout_name} full-similarity scaled layout",
        )
        delay_rows.extend(scaled_delay_rows)
        labels_all = ANCHORS[:]

        for tag_method in tag_methods:
            # Known-anchor/Vicon baseline: zero, solver delay, and Vicon delaycal.
            for delay_mode, delays, tag_delay in [
                ("zero_delay", {i: 0.0 for i in range(8)}, 0.0),
                ("solver_delay", solver_delays, solver_tag_delay),
                ("vicon_inter_anchor_delaycal", delaycal_delays, delaycal_tag_delay),
            ]:
                jobs.append(
                    {
                        "experiment": "align_to_vicon",
                        "layout_name": f"vicon_truth/{layout_name}/{delay_mode}",
                        "labels": labels_all,
                        "coords_opti_frame": truth_coords.tolist(),
                        "delays": delays,
                        "tag_delay_mm": tag_delay,
                        "sigma_by_id": sigma_by_id,
                        "tag_method": tag_method,
                        "static_files": static_files,
                        "tag_truth": {k: v.tolist() for k, v in tag_truth.items()},
                        "tag_truth_meta": tag_truth_meta,
                        "static_metadata": static_metadata,
                        "point_estimator": args.point_estimator,
                        "max_frames": args.max_frames,
                        "metadata": {
                            "experiment": "align_to_vicon",
                            "layout_solver": layout_name,
                            "layout_variant": "vicon_truth",
                            "delay_mode": delay_mode,
                            "scale_mode": "none_truth_anchor",
                            "scale_factor": 1.0,
                            "scale_source": "OptiTrack truth anchors",
                            "alignment_frame": "OptiTrack",
                        },
                    }
                )
            # Full scale ablation: original no-scale, solver-specific full similarity, Vicon truth.
            for variant, variant_coords, scale_mode, scale_factor, delays, tag_delay, delay_mode in [
                ("original_rigid_no_scale", original_coords, "rigid_no_scale", 1.0, solver_delays, solver_tag_delay, "solver_delay"),
                ("solver_similarity_scale_to_vicon", scaled_coords, "full_similarity", similarity.scale, solver_delays, solver_tag_delay, "solver_delay"),
                (
                    "solver_similarity_scale_to_vicon",
                    scaled_coords,
                    "full_similarity",
                    similarity.scale,
                    scaled_delaycal_delays,
                    scaled_delaycal_tag_delay,
                    "scaled_layout_inter_anchor_delaycal",
                ),
                ("vicon_truth", truth_coords, "none_truth_anchor", 1.0, delaycal_delays, delaycal_tag_delay, "vicon_inter_anchor_delaycal"),
            ]:
                jobs.append(
                    {
                        "experiment": "scale_to_vicon",
                        "layout_name": f"{variant}/{layout_name}",
                        "labels": labels_all,
                        "coords_opti_frame": variant_coords.tolist(),
                        "delays": delays,
                        "tag_delay_mm": tag_delay,
                        "sigma_by_id": sigma_by_id,
                        "tag_method": tag_method,
                        "static_files": static_files,
                        "tag_truth": {k: v.tolist() for k, v in tag_truth.items()},
                        "tag_truth_meta": tag_truth_meta,
                        "static_metadata": static_metadata,
                        "point_estimator": args.point_estimator,
                        "max_frames": args.max_frames,
                        "metadata": {
                            "experiment": "scale_to_vicon",
                            "layout_solver": layout_name,
                            "layout_variant": variant,
                            "delay_mode": delay_mode,
                            "scale_mode": scale_mode,
                            "scale_factor": float(scale_factor),
                            "scale_source": "all_anchor_similarity" if scale_mode == "full_similarity" else scale_mode,
                            "alignment_frame": "OptiTrack",
                        },
                    }
                )
            # One-baseline correction: all C(8,2), solver-specific pair scale.
            for a, b in itertools.combinations(ANCHORS, 2):
                ia, ib = ANCHORS.index(a), ANCHORS.index(b)
                d_auto = float(np.linalg.norm(src[ia] - src[ib]))
                d_true = float(np.linalg.norm(truth_coords[ia] - truth_coords[ib]))
                pair_scale = d_true / d_auto if d_auto > 0 else float("nan")
                pair_fit = fit_with_fixed_scale(src, truth_coords, rigid.rotation, pair_scale)
                pair_coords = apply_fit(src, pair_fit)
                pair_by_label = {label: pair_coords[i] for i, label in enumerate(ANCHORS)}
                pair_delaycal_delays, pair_delaycal_tag_delay, pair_delay_rows = estimate_delaycal_from_points(
                    pair_by_label,
                    pair_quality,
                    f"{layout_name} one-baseline {a}-{b} scaled layout",
                )
                delay_rows.extend(pair_delay_rows)
                for delays, tag_delay, delay_mode in [
                    (solver_delays, solver_tag_delay, "solver_delay"),
                    (pair_delaycal_delays, pair_delaycal_tag_delay, "one_baseline_layout_inter_anchor_delaycal"),
                ]:
                    jobs.append(
                        {
                            "experiment": "one_baseline",
                            "layout_name": f"one_baseline_{a}{b}/{layout_name}/{delay_mode}",
                            "labels": labels_all,
                            "coords_opti_frame": pair_coords.tolist(),
                            "delays": delays,
                            "tag_delay_mm": tag_delay,
                            "sigma_by_id": sigma_by_id,
                            "tag_method": tag_method,
                            "static_files": static_files,
                            "tag_truth": {k: v.tolist() for k, v in tag_truth.items()},
                            "tag_truth_meta": tag_truth_meta,
                            "static_metadata": static_metadata,
                            "point_estimator": args.point_estimator,
                            "max_frames": args.max_frames,
                            "metadata": {
                                "experiment": "one_baseline",
                                "layout_solver": layout_name,
                                "layout_variant": "one_baseline_scale",
                                "delay_mode": delay_mode,
                                "scale_mode": "one_baseline",
                                "scale_factor": float(pair_scale),
                                "scale_source": f"{a}-{b}",
                                "baseline_pair": f"{a}-{b}",
                                "baseline_autopos_dist_mm": d_auto,
                                "baseline_vicon_dist_mm": d_true,
                                "baseline_length_class": "long" if d_true >= np.nanmedian([np.linalg.norm(truth_coords[i] - truth_coords[j]) for i, j in itertools.combinations(range(8), 2)]) else "short",
                                "alignment_frame": "OptiTrack",
                            },
                        }
                    )
    return jobs, {k: [str(v)] for k, v in experiment_dirs.items()}, delay_rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Run static known-anchor and scale-correction ablations.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT_DEFAULT))
    parser.add_argument("--layouts", default="all")
    parser.add_argument("--tag-methods", default="all")
    parser.add_argument("--workers", type=int, default=max(1, min(10, os.cpu_count() or 1)))
    parser.add_argument("--point-estimator", choices=["median", "mean"], default="median")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--only", choices=["all", "align_to_vicon", "scale_to_vicon", "one_baseline"], default="all")
    args = parser.parse_args()

    t0 = time.time()
    jobs, exp_dirs_raw, delay_rows = make_variant_jobs(args)
    exp_dirs = {k: Path(v[0]) for k, v in exp_dirs_raw.items()}
    if args.only != "all":
        jobs = [j for j in jobs if j["experiment"] == args.only]
    print(f"[static-ablation] jobs={len(jobs)} workers={args.workers}", flush=True)

    per_exp_rows: dict[str, list[dict]] = {"align_to_vicon": [], "scale_to_vicon": [], "one_baseline": []}
    done = 0
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(solve_one_job, job) for job in jobs]
        for fut in as_completed(futures):
            result = fut.result()
            per_exp_rows[result["experiment"]].extend(result["rows"])
            done += 1
            if done % max(1, len(jobs) // 20) == 0 or done == len(jobs):
                print(f"[static-ablation] completed {done}/{len(jobs)}", flush=True)

    for exp, rows in per_exp_rows.items():
        if not rows:
            continue
        out_dir = exp_dirs[exp]
        tables = out_dir / "tables"
        reports = out_dir / "reports"
        write_csv(tables / "static_abs_errors_per_session.csv", rows)
        if exp == "align_to_vicon":
            group_cols = ["layout_solver", "layout_variant", "delay_mode", "tag_method"]
            title = "FULL AutoPos Align To Vicon: Static Known-Anchor Baseline"
        elif exp == "scale_to_vicon":
            group_cols = ["layout_solver", "layout_variant", "delay_mode", "tag_method"]
            title = "FULL AutoPos Scale To Vicon: Static Scale Ablation"
        else:
            group_cols = ["layout_solver", "baseline_pair", "scale_factor", "delay_mode", "tag_method"]
            title = "FULL AutoPos One-Baseline Scale Correction: Static Ablation"
        summary_rows = summarize(rows, group_cols)
        write_csv(tables / "static_accuracy_summary.csv", summary_rows)
        write_markdown_summary(reports / "STATIC_ABLATION_SUMMARY.md", title, summary_rows, group_cols)
        if exp == "align_to_vicon":
            write_csv(tables / "vicon_delaycal_diagnostics.csv", delay_rows)

    # Four-way static comparison report from the most directly comparable rows.
    comparison_rows: list[dict] = []
    for exp, rows in per_exp_rows.items():
        if not rows:
            continue
        df = pd.DataFrame(summarize(rows, ["layout_solver", "layout_variant", "delay_mode", "tag_method", "scale_source"]))
        if df.empty:
            continue
        for _, row in df.iterrows():
            comparison_rows.append({"experiment": exp, **row.to_dict()})
    comp_tables = COMPARISON_ROOT / "tables"
    comp_reports = COMPARISON_ROOT / "reports"
    write_csv(comp_tables / "static_4way_accuracy_summary.csv", comparison_rows)
    write_markdown_summary(
        comp_reports / "STATIC_4WAY_COMPARISON.md",
        "FULL 4-Way Static Comparison",
        comparison_rows,
        ["experiment", "layout_solver", "layout_variant", "delay_mode", "tag_method", "scale_source"],
    )
    meta = {
        "script": str(THIS),
        "generated_utc": datetime.now(UTC).isoformat(),
        "elapsed_s": time.time() - t0,
        "workers": args.workers,
        "jobs": len(jobs),
        "only": args.only,
    }
    (COMPARISON_ROOT / "run_static_ablation_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[static-ablation] done in {meta['elapsed_s']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
