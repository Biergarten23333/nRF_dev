#!/usr/bin/env python3
"""ROTO layout/delay ablations for the corrected FULL OptiTrack export.

This script mirrors the static four-way ablation, but for the 17 ROTO captures.
It intentionally reuses the capture-level time offsets solved in the original
FULL ROTO analysis.  That keeps the time alignment fixed while the spatial
layout/delay/tag-solver variants change.

Outputs are written into clean `roto_absolute/` subfolders under:

* FULL_AutoPos_align_to_Vicon
* FULL_AutoPos_scale_to_vicon
* FULL_AutoPos_one_baseline_scale_correction

and a merged comparison report under FULL_4way_comparison.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import itertools
import json
import math
import os
import shutil
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
UWB_TAGS = ["BS2DCE", "BSDC91"]
OPTITRACK_MARKERS = ["WandBantenna", "WandCantenna"]
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
REPRESENTATIVE_ONE_BASELINE = "E-H"

THIS = Path(__file__).resolve()
COMPARISON_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT_DEFAULT = THIS.parents[4]
FULL_ROOT = EXTRA_ROOT / "FULL"

sys.path.insert(0, str(THIS.parent))
import run_static_layout_ablation as static_ablation  # noqa: E402
from tag_ground_truth import parse_trc_medians  # noqa: E402


def import_roto_module():
    path = FULL_ROOT / "roto_absolute/scripts/run_roto_absolute_analysis.py"
    spec = importlib.util.spec_from_file_location("full_roto_absolute_analysis", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


roto = import_roto_module()


@dataclass(frozen=True)
class VariantSpec:
    experiment: str
    output_dir: Path
    layout_key: str
    labels: list[str]
    coords_opti_frame: np.ndarray
    delays: dict[int, float]
    tag_delay_mm: float
    layout_det: float
    layout_anchor_rms_mm: float
    metadata: dict


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


def percentile(values, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def rms_anchor_error(coords: np.ndarray, truth_coords: np.ndarray) -> float:
    diff = np.asarray(coords, dtype=float) - np.asarray(truth_coords, dtype=float)
    return float(math.sqrt(np.nanmean(np.sum(diff * diff, axis=1))))


def read_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return dict(roto.DEFAULT_MAPPING)
    df = pd.read_csv(path)
    all_rows = df[df["capture_id"].astype(str) == "ALL"]
    if all_rows.empty:
        return dict(roto.DEFAULT_MAPPING)
    row = all_rows.sort_values("score_median_3d_mm").iloc[0]
    return {"BS2DCE": str(row["BS2DCE_marker"]), "BSDC91": str(row["BSDC91_marker"])}


def read_offsets(path: Path) -> dict[str, float]:
    df = pd.read_csv(path)
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        if str(row.get("status", "")) != "ok":
            continue
        beta = float(row["beta_s"])
        if math.isfinite(beta):
            out[str(row["capture_id"])] = beta
    return out


def load_anchor_truth_only(opti_dir: Path) -> dict[str, np.ndarray]:
    marker_names = [f"{a}antenna" for a in ANCHORS]
    medians_by_id: dict[str, dict[str, np.ndarray]] = {}
    for sid in PRIMARY_IDS:
        path = opti_dir / f"{sid}.trc"
        if not path.exists():
            raise FileNotFoundError(path)
        medians_by_id[sid] = parse_trc_medians(path, marker_names)
    return {
        a: np.nanmedian(
            np.vstack([medians_by_id[sid][f"{a}antenna"] for sid in PRIMARY_IDS]),
            axis=0,
        )
        for a in ANCHORS
    }


def capture_id_from_path(path: Path) -> str:
    return roto.capture_id_from_roto_dir(path.parents[1])


def solve_variant_capture_job(job: dict) -> dict:
    layout = static_ablation.build_layout(
        name=job["layout_key"],
        labels=job["labels"],
        coords_opti_frame=np.asarray(job["coords_opti_frame"], dtype=float),
        delays={int(k): float(v) for k, v in job["delays"].items()},
        tag_delay_mm=float(job["tag_delay_mm"]),
        sigma_by_id={int(k): float(v) for k, v in job["sigma_by_id"].items()},
        metadata=job["metadata"],
    )
    frames = static_ablation.read_tr_all_frames(Path(job["tr_all_path"]), tags=set(UWB_TAGS), min_anchors=4)
    frames_by_tag = {
        tag: sorted([f for f in frames if f.tag == tag], key=lambda f: (f.host_elapsed_s, f.sweep))
        for tag in UWB_TAGS
    }
    tracks: list[dict] = []
    for method in job["tag_methods"]:
        for tag in UWB_TAGS:
            solver = static_ablation.TagPositionSolver(layout, static_ablation.SolverConfig(method=method))
            rows = []
            for frame in frames_by_tag[tag]:
                result = solver.solve_frame(frame)
                if result is None or result.status != "ok":
                    continue
                rows.append(
                    (
                        float(result.host_elapsed_s),
                        float(result.x_mm),
                        float(result.y_mm),
                        float(result.z_mm),
                        float(result.residual_rms_mm),
                        float(result.anchors_input),
                        float(result.anchors_used),
                    )
                )
            arr = np.asarray(rows, dtype=float)
            if arr.size == 0:
                arr = np.empty((0, 7), dtype=float)
            tracks.append(
                {
                    "layout_key": job["layout_key"],
                    "tag_method": method,
                    "capture_id": job["capture_id"],
                    "tag": tag,
                    "time_s": arr[:, 0],
                    "xyz_opti_frame_mm": arr[:, 1:4],
                    "residual_rms_mm": arr[:, 4],
                    "anchors_input": arr[:, 5],
                    "anchors_used": arr[:, 6],
                    "source_tr_all": job["tr_all_path"],
                }
            )
    return {"variant_index": job["variant_index"], "capture_id": job["capture_id"], "tracks": tracks}


def make_variants(args: argparse.Namespace) -> tuple[list[VariantSpec], dict[str, Path], list[dict]]:
    official_root = Path(args.official_root).resolve()
    layout_base = Path(args.layout_dir).resolve() if args.layout_dir else official_root / "solver/outputs/v1_to_v4_io_field_check"
    opti_dir = official_root / "opti_captures/full"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"

    print("[roto-ablation] loading FULL OptiTrack anchor truth medians", flush=True)
    anchor_truth = load_anchor_truth_only(opti_dir)
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    delaycal_delays, delaycal_tag_delay, delay_rows = static_ablation.estimate_delaycal(anchor_truth, pair_quality)

    suffix = str(args.output_suffix or "")
    experiment_dirs = {
        "align_to_vicon": EXTRA_ROOT / f"FULL_AutoPos_align_to_Vicon{suffix}/roto_absolute",
        "scale_to_vicon": EXTRA_ROOT / f"FULL_AutoPos_scale_to_vicon{suffix}/roto_absolute",
        "one_baseline": EXTRA_ROOT / f"FULL_AutoPos_one_baseline_scale_correction{suffix}/roto_absolute",
    }

    layout_versions = LAYOUT_VERSIONS if args.layouts == "all" else [x.strip() for x in args.layouts.split(",") if x.strip()]
    selected_experiments = (
        {"align_to_vicon", "scale_to_vicon", "one_baseline"}
        if args.only == "all"
        else {args.only}
    )
    variants: list[VariantSpec] = []

    for layout_name in layout_versions:
        print(f"[roto-ablation] building layout variants for {layout_name}", flush=True)
        labels, coords, solver_delays, solver_tag_delay = static_ablation.load_layout_json_raw(layout_base / layout_name / "layout.json")
        by_label = {label: coords[i] for i, label in enumerate(labels)}
        src = np.vstack([by_label[a] for a in ANCHORS])
        rigid = static_ablation.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
        similarity = static_ablation.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=True)
        original_coords = static_ablation.apply_fit(src, rigid)
        scaled_coords = static_ablation.apply_fit(src, similarity)
        scaled_delaycal_delays, scaled_delaycal_tag_delay, scaled_delay_rows = static_ablation.estimate_delaycal_from_points(
            {a: scaled_coords[i] for i, a in enumerate(ANCHORS)},
            pair_quality,
            f"{layout_name} full-similarity scaled layout",
        )
        delay_rows.extend(scaled_delay_rows)

        if "align_to_vicon" in selected_experiments:
            for delay_mode, delays, tag_delay in [
                ("zero_delay", {i: 0.0 for i in range(8)}, 0.0),
                ("solver_delay", solver_delays, solver_tag_delay),
                ("vicon_inter_anchor_delaycal", delaycal_delays, delaycal_tag_delay),
            ]:
                meta = {
                    "experiment": "align_to_vicon",
                    "layout_solver": layout_name,
                    "layout_variant": "vicon_truth",
                    "delay_mode": delay_mode,
                    "scale_mode": "none_truth_anchor",
                    "scale_factor": 1.0,
                    "scale_source": "OptiTrack truth anchors",
                    "alignment_frame": "OptiTrack",
                }
                variants.append(
                    VariantSpec(
                        experiment="align_to_vicon",
                        output_dir=experiment_dirs["align_to_vicon"],
                        layout_key=f"align_to_vicon/{layout_name}/vicon_truth/{delay_mode}",
                        labels=ANCHORS[:],
                        coords_opti_frame=truth_coords,
                        delays=delays,
                        tag_delay_mm=tag_delay,
                        layout_det=1.0,
                        layout_anchor_rms_mm=0.0,
                        metadata=meta,
                    )
                )

        if "scale_to_vicon" in selected_experiments:
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
                meta = {
                    "experiment": "scale_to_vicon",
                    "layout_solver": layout_name,
                    "layout_variant": variant,
                    "delay_mode": delay_mode,
                    "scale_mode": scale_mode,
                    "scale_factor": float(scale_factor),
                    "scale_source": "all_anchor_similarity" if scale_mode == "full_similarity" else scale_mode,
                    "alignment_frame": "OptiTrack",
                }
                variants.append(
                    VariantSpec(
                        experiment="scale_to_vicon",
                        output_dir=experiment_dirs["scale_to_vicon"],
                        layout_key=f"scale_to_vicon/{layout_name}/{variant}/{delay_mode}",
                        labels=ANCHORS[:],
                        coords_opti_frame=variant_coords,
                        delays=delays,
                        tag_delay_mm=tag_delay,
                        layout_det=rigid.det,
                        layout_anchor_rms_mm=rms_anchor_error(variant_coords, truth_coords),
                        metadata=meta,
                    )
                )

        if "one_baseline" in selected_experiments:
            truth_pair_lengths = [
                np.linalg.norm(truth_coords[i] - truth_coords[j]) for i, j in itertools.combinations(range(8), 2)
            ]
            median_pair_len = float(np.nanmedian(truth_pair_lengths))
            for a, b in itertools.combinations(ANCHORS, 2):
                ia, ib = ANCHORS.index(a), ANCHORS.index(b)
                d_auto = float(np.linalg.norm(src[ia] - src[ib]))
                d_true = float(np.linalg.norm(truth_coords[ia] - truth_coords[ib]))
                pair_scale = d_true / d_auto if d_auto > 0 else float("nan")
                pair_fit = static_ablation.fit_with_fixed_scale(src, truth_coords, rigid.rotation, pair_scale)
                pair_coords = static_ablation.apply_fit(src, pair_fit)
                pair_delaycal_delays, pair_delaycal_tag_delay, pair_delay_rows = static_ablation.estimate_delaycal_from_points(
                    {label: pair_coords[i] for i, label in enumerate(ANCHORS)},
                    pair_quality,
                    f"{layout_name} one-baseline {a}-{b} scaled layout",
                )
                delay_rows.extend(pair_delay_rows)
                for delays, tag_delay, delay_mode in [
                    (solver_delays, solver_tag_delay, "solver_delay"),
                    (pair_delaycal_delays, pair_delaycal_tag_delay, "one_baseline_layout_inter_anchor_delaycal"),
                ]:
                    meta = {
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
                        "baseline_length_class": "long" if d_true >= median_pair_len else "short",
                        "alignment_frame": "OptiTrack",
                    }
                    variants.append(
                        VariantSpec(
                            experiment="one_baseline",
                            output_dir=experiment_dirs["one_baseline"],
                            layout_key=f"one_baseline/{layout_name}/{a}{b}/{delay_mode}",
                            labels=ANCHORS[:],
                            coords_opti_frame=pair_coords,
                            delays=delays,
                            tag_delay_mm=tag_delay,
                            layout_det=rigid.det,
                            layout_anchor_rms_mm=rms_anchor_error(pair_coords, truth_coords),
                            metadata=meta,
                        )
                    )
    return variants, experiment_dirs, delay_rows


def should_include_samples(meta: dict, tag_method: str) -> bool:
    if tag_method != "T4":
        return False
    if meta["experiment"] == "align_to_vicon":
        return (
            meta["layout_solver"] == "v4-io"
            and meta["layout_variant"] == "vicon_truth"
            and meta["delay_mode"] == "vicon_inter_anchor_delaycal"
        )
    if meta["experiment"] == "scale_to_vicon":
        return (
            meta["layout_solver"] == "v4-io"
            and meta["layout_variant"] == "solver_similarity_scale_to_vicon"
            and meta["delay_mode"] == "scaled_layout_inter_anchor_delaycal"
        )
    if meta["experiment"] == "one_baseline":
        return (
            meta["layout_solver"] == "v4-io"
            and meta.get("baseline_pair") == REPRESENTATIVE_ONE_BASELINE
            and meta["delay_mode"] == "one_baseline_layout_inter_anchor_delaycal"
        )
    return False


def aggregate_track_rows(rows: list[dict], group_cols: list[str], sample_rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df = df[df["status"] == "ok"].copy()
    if df.empty:
        return []
    sample_df = pd.DataFrame(sample_rows)
    out: list[dict] = []
    for key, g in df.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        row = {col: key[i] for i, col in enumerate(group_cols)}
        row.update(
            {
                "tracks_ok": int(len(g)),
                "captures_ok": int(g["capture_id"].nunique()),
                "n_overlap_total": int(g["n_overlap"].sum()),
                "err3d_p50_track_median_mm": percentile(g["err3d_p50_mm"], 50),
                "err3d_p95_track_median_mm": percentile(g["err3d_p95_mm"], 50),
                "err3d_rmse_track_median_mm": percentile(g["err3d_rmse_mm"], 50),
                "err_horizontal_xz_p50_track_median_mm": percentile(g["err_horizontal_xz_p50_mm"], 50),
                "err_horizontal_xz_p95_track_median_mm": percentile(g["err_horizontal_xz_p95_mm"], 50),
                "err_vertical_y_p50_track_median_mm": percentile(g["err_vertical_y_p50_mm"], 50),
                "err_vertical_y_p95_track_median_mm": percentile(g["err_vertical_y_p95_mm"], 50),
                "turn_center_abs_error_3d_track_median_mm": percentile(g["turn_center_abs_error_3d_mm"], 50),
                "turn_center_abs_error_3d_track_p95_mm": percentile(g["turn_center_abs_error_3d_mm"], 95),
                "radius_error_abs_track_median_mm": percentile(np.abs(g["radius_error_mm"].astype(float)), 50),
                "radius_error_abs_track_p95_mm": percentile(np.abs(g["radius_error_mm"].astype(float)), 95),
                "axis_angle_abs_track_median_deg": percentile(g["axis_angle_abs_deg"], 50),
                "residual_rms_track_median_mm": percentile(g["residual_rms_median_mm"], 50),
            }
        )
        if not sample_df.empty:
            mask = np.ones(len(sample_df), dtype=bool)
            for col, val in row.items():
                if col in sample_df.columns and col in group_cols:
                    mask &= sample_df[col].astype(str).to_numpy() == str(val)
            sub = sample_df[mask]
            if not sub.empty:
                e3 = sub["err3d_mm"].to_numpy(dtype=float)
                hxz = sub["err_horizontal_xz_mm"].to_numpy(dtype=float)
                vy = sub["err_vertical_y_mm"].to_numpy(dtype=float)
                row.update(
                    {
                        "sample_weighted_err3d_p50_mm": percentile(e3, 50),
                        "sample_weighted_err3d_p95_mm": percentile(e3, 95),
                        "sample_weighted_err3d_rmse_mm": float(math.sqrt(np.nanmean(e3 * e3))),
                        "sample_weighted_horizontal_xz_p50_mm": percentile(hxz, 50),
                        "sample_weighted_horizontal_xz_p95_mm": percentile(hxz, 95),
                        "sample_weighted_vertical_y_p50_mm": percentile(vy, 50),
                        "sample_weighted_vertical_y_p95_mm": percentile(vy, 95),
                    }
                )
        out.append(row)
    return out


def write_summary_report(path: Path, title: str, rows: list[dict], group_cols: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}\n\n", f"Generated {datetime.now(UTC).isoformat()}.\n\n"]
    if not rows:
        lines.append("No rows.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    df = pd.DataFrame(rows)
    sort_cols = ["err3d_p50_track_median_mm", "err3d_p95_track_median_mm"]
    cols = [
        *group_cols,
        "tracks_ok",
        "captures_ok",
        "err3d_p50_track_median_mm",
        "err3d_p95_track_median_mm",
        "err_horizontal_xz_p95_track_median_mm",
        "err_vertical_y_p95_track_median_mm",
        "turn_center_abs_error_3d_track_median_mm",
        "radius_error_abs_track_median_mm",
    ]
    lines.append("## Best Rows By Track-Median 3D P50\n\n")
    lines.extend(markdown_table(df.sort_values(sort_cols).head(30), cols))
    lines.append("\n## Full Summary\n\n")
    lines.extend(markdown_table(df.sort_values(group_cols), cols))
    path.write_text("".join(lines), encoding="utf-8")


def markdown_table(df: pd.DataFrame, cols: list[str]) -> list[str]:
    lines = ["| " + " | ".join(cols) + " |\n", "| " + " | ".join(["---"] * len(cols)) + " |\n"]
    for _, row in df.iterrows():
        vals = []
        for col in cols:
            val = row.get(col, "")
            if pd.isna(val):
                vals.append("")
                continue
            if isinstance(val, (float, np.floating)):
                if col == "scale_factor":
                    vals.append(f"{float(val):.4f}")
                else:
                    vals.append(f"{float(val):.1f}")
            else:
                vals.append(str(val))
        lines.append("| " + " | ".join(vals) + " |\n")
    return lines


def make_comparison_report(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# FULL 4-Way ROTO Comparison\n\n", f"Generated {datetime.now(UTC).isoformat()}.\n\n"]
    lines.append(
        "All rows use the corrected FULL OptiTrack export and fixed capture-level offsets from the original FULL v4-io/T4 ROTO alignment. "
        "This compares spatial layout/delay/tag-solver variants, not a newly fitted time offset per variant.\n\n"
    )
    if not rows:
        lines.append("No rows.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    df = pd.DataFrame(rows)
    cols = [
        "experiment",
        "layout_solver",
        "layout_variant",
        "delay_mode",
        "tag_method",
        "scale_source",
        "baseline_pair",
        "tracks_ok",
        "err3d_p50_track_median_mm",
        "err3d_p95_track_median_mm",
        "err_horizontal_xz_p95_track_median_mm",
        "err_vertical_y_p95_track_median_mm",
        "turn_center_abs_error_3d_track_median_mm",
    ]
    for col in cols:
        if col not in df.columns:
            df[col] = ""
    lines.append("## Best Overall Rows\n\n")
    lines.extend(markdown_table(df.sort_values(["err3d_p50_track_median_mm", "err3d_p95_track_median_mm"]).head(40), cols))
    lines.append("\n## Best Row Per Experiment\n\n")
    best_exp = df.sort_values(["err3d_p50_track_median_mm", "err3d_p95_track_median_mm"]).groupby("experiment", as_index=False).head(1)
    lines.extend(markdown_table(best_exp, cols))
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run ROTO layout/delay ablations with fixed FULL time offsets.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT_DEFAULT))
    parser.add_argument("--layout-dir", default=None)
    parser.add_argument("--full-root", default=str(FULL_ROOT))
    parser.add_argument("--comparison-root", default=str(COMPARISON_ROOT))
    parser.add_argument("--output-suffix", default="")
    parser.add_argument("--layouts", default="all")
    parser.add_argument("--tag-methods", default="all")
    parser.add_argument("--workers", type=int, default=max(1, min(10, os.cpu_count() or 1)))
    parser.add_argument("--only", choices=["all", "align_to_vicon", "scale_to_vicon", "one_baseline"], default="all")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    full_root = Path(args.full_root).resolve()
    comparison_root = Path(args.comparison_root).resolve()

    official_root = Path(args.official_root).resolve()
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    original_roto = full_root / "roto_absolute"

    t0 = time.time()
    print("[roto-ablation] initializing", flush=True)
    variants, experiment_dirs, delay_rows = make_variants(args)
    if args.only != "all":
        variants = [v for v in variants if v.experiment == args.only]
    tag_methods = TAG_METHODS if args.tag_methods == "all" else [x.strip().upper() for x in args.tag_methods.split(",") if x.strip()]

    if args.clean:
        for out in experiment_dirs.values():
            if args.only == "all" or out.name == "roto_absolute":
                if out.exists() and (args.only == "all" or out.parent.name.endswith(args.only)):
                    shutil.rmtree(out)
        # Simpler and safer: only remove dirs for selected variants.
        selected_exps = {v.experiment for v in variants}
        for exp in selected_exps:
            out = experiment_dirs[exp]
            if out.exists():
                shutil.rmtree(out)

    for out in experiment_dirs.values():
        for sub in ["tables", "reports", "figs", "logs", "scripts"]:
            (out / sub).mkdir(parents=True, exist_ok=True)
    (comparison_root / "tables").mkdir(parents=True, exist_ok=True)
    (comparison_root / "reports").mkdir(parents=True, exist_ok=True)

    mapping = read_mapping(original_roto / "tables/roto_wand_mapping_decision.csv")
    beta_by_capture = read_offsets(original_roto / "tables/roto_time_offsets_v4io_T4.csv")
    if not beta_by_capture:
        raise RuntimeError("No fixed ROTO offsets found; run FULL/roto_absolute first.")

    tr_all_by_capture = roto.discover_roto_capture_files(captures_root)
    capture_ids = sorted(cid for cid in tr_all_by_capture if cid in beta_by_capture)
    opti_by_capture = {
        cid: roto.parse_trc_trajectories(opti_dir / f"{cid}.trc", OPTITRACK_MARKERS)
        for cid in capture_ids
    }
    sigma_by_id = static_ablation.load_anchor_sigma(sigma_path)

    jobs: list[dict] = []
    for idx, variant in enumerate(variants):
        for capture_id in capture_ids:
            jobs.append(
                {
                    "variant_index": idx,
                    "layout_key": variant.layout_key,
                    "labels": variant.labels,
                    "coords_opti_frame": variant.coords_opti_frame.tolist(),
                    "delays": variant.delays,
                    "tag_delay_mm": variant.tag_delay_mm,
                    "sigma_by_id": sigma_by_id,
                    "metadata": variant.metadata,
                    "tag_methods": tag_methods,
                    "capture_id": capture_id,
                    "tr_all_path": str(tr_all_by_capture[capture_id]),
                }
            )

    print(
        f"[roto-ablation] variants={len(variants)} captures={len(capture_ids)} jobs={len(jobs)} "
        f"workers={args.workers} methods={','.join(tag_methods)}",
        flush=True,
    )
    per_exp_track_rows: dict[str, list[dict]] = {exp: [] for exp in experiment_dirs}
    per_exp_sample_rows: dict[str, list[dict]] = {exp: [] for exp in experiment_dirs}

    done = 0
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(solve_variant_capture_job, job) for job in jobs]
        for fut in as_completed(futures):
            raw = fut.result()
            variant = variants[int(raw["variant_index"])]
            beta = beta_by_capture[str(raw["capture_id"])]
            for track_raw in raw["tracks"]:
                track = roto.SolvedTrack(
                    layout=variant.layout_key,
                    tag_method=track_raw["tag_method"],
                    capture_id=track_raw["capture_id"],
                    tag=track_raw["tag"],
                    time_s=np.asarray(track_raw["time_s"], dtype=float),
                    xyz_autopos_mm=np.asarray(track_raw["xyz_opti_frame_mm"], dtype=float),
                    xyz_opti_frame_mm=np.asarray(track_raw["xyz_opti_frame_mm"], dtype=float),
                    residual_rms_mm=np.asarray(track_raw["residual_rms_mm"], dtype=float),
                    anchors_input=np.asarray(track_raw["anchors_input"], dtype=float),
                    anchors_used=np.asarray(track_raw["anchors_used"], dtype=float),
                    source_tr_all=track_raw["source_tr_all"],
                )
                marker = mapping[track.tag]
                include_samples = should_include_samples(variant.metadata, track.tag_method)
                row, samples = roto.evaluate_track(
                    track,
                    opti_by_capture[track.capture_id][marker],
                    beta_s=beta,
                    layout_det=variant.layout_det,
                    layout_anchor_rms_mm=variant.layout_anchor_rms_mm,
                    include_samples=include_samples,
                )
                row.update(variant.metadata)
                row["layout_key"] = variant.layout_key
                row["opti_marker"] = marker
                row["time_offset_source"] = "fixed FULL v4-io/T4 capture-level offset"
                per_exp_track_rows[variant.experiment].append(row)
                if include_samples:
                    for sample in samples:
                        sample.update(variant.metadata)
                        sample["layout_key"] = variant.layout_key
                        sample["opti_marker"] = marker
                    per_exp_sample_rows[variant.experiment].extend(samples)
            done += 1
            if done == 1 or done % max(1, len(jobs) // 50) == 0 or done == len(jobs):
                print(f"[roto-ablation] completed {done}/{len(jobs)}", flush=True)

    comparison_rows: list[dict] = []
    for exp, rows in per_exp_track_rows.items():
        if not rows:
            continue
        out = experiment_dirs[exp]
        samples = per_exp_sample_rows.get(exp, [])
        write_csv(out / "tables/roto_abs_per_track.csv", rows)
        write_csv(out / "tables/roto_abs_samples_v4io_T4.csv", samples)
        if exp == "align_to_vicon":
            group_cols = ["experiment", "layout_solver", "layout_variant", "delay_mode", "tag_method"]
            title = "FULL AutoPos Align To Vicon: ROTO Known-Anchor Baseline"
            write_csv(out / "tables/roto_delaycal_diagnostics.csv", delay_rows)
        elif exp == "scale_to_vicon":
            group_cols = ["experiment", "layout_solver", "layout_variant", "delay_mode", "tag_method", "scale_source"]
            title = "FULL AutoPos Scale To Vicon: ROTO Scale Ablation"
        else:
            group_cols = [
                "experiment",
                "layout_solver",
                "layout_variant",
                "baseline_pair",
                "scale_factor",
                "delay_mode",
                "tag_method",
                "scale_source",
            ]
            title = "FULL AutoPos One-Baseline Scale Correction: ROTO Ablation"
        summary = aggregate_track_rows(rows, group_cols, samples)
        write_csv(out / "tables/roto_abs_summary.csv", summary)
        write_summary_report(out / "reports/ROTO_ABLATION_SUMMARY.md", title, summary, group_cols)
        comparison_rows.extend(summary)
        (out / "run_roto_ablation_meta.json").write_text(
            json.dumps(
                {
                    "script": str(THIS),
                    "generated_utc": datetime.now(UTC).isoformat(),
                    "elapsed_s": time.time() - t0,
                    "workers": args.workers,
                    "experiment": exp,
                    "fixed_offset_source": str(original_roto / "tables/roto_time_offsets_v4io_T4.csv"),
                    "mapping": mapping,
                    "captures": capture_ids,
                    "representative_samples_only": True,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    write_csv(comparison_root / "tables/roto_4way_accuracy_summary.csv", comparison_rows)
    make_comparison_report(comparison_root / "reports/ROTO_4WAY_COMPARISON.md", comparison_rows)
    (comparison_root / "run_roto_ablation_meta.json").write_text(
        json.dumps(
            {
                "script": str(THIS),
                "generated_utc": datetime.now(UTC).isoformat(),
                "elapsed_s": time.time() - t0,
                "workers": args.workers,
                "jobs": len(jobs),
                "variants": len(variants),
                "tag_methods": tag_methods,
                "only": args.only,
                "fixed_offset_source": str(original_roto / "tables/roto_time_offsets_v4io_T4.csv"),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[roto-ablation] done in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
