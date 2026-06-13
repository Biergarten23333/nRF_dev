#!/usr/bin/env python3
"""Revision-2 Phase A verification checks.

This script intentionally writes only analysis artefacts.  The report text is
edited only after the numerical gates are checked.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
OFFICIAL_ROOT = THIS.parents[4]
CUDA_REPLAY_PATH = OFFICIAL_ROOT / "Analysis" / "scripts" / "cuda_t4_keepk_replay.py"
LAYOUT_COMPARE_PATH = FULL_ROOT / "scripts" / "layout_optitrack_compare.py"
STATIC_ABS_PATH = FULL_ROOT / "scripts" / "static_tag_absolute_accuracy.py"
STRATIFIED_PATH = FULL_ROOT / "scripts" / "stratified_keepk_replay.py"

ANCHOR_LABELS = list("ABCDEFGH")
LOWER = set("ABCD")
UPPER = set("EFGH")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def finite(arr) -> np.ndarray:
    a = np.asarray(arr, dtype=float)
    return a[np.isfinite(a)]


def pct(arr, q: float) -> float:
    a = finite(arr)
    return float(np.percentile(a, q)) if a.size else float("nan")


def median(arr) -> float:
    return pct(arr, 50)


def iqr(arr) -> tuple[float, float]:
    return pct(arr, 25), pct(arr, 75)


def sim3_layout_check(tables_dir: Path) -> tuple[list[dict], float]:
    layout_mod = load_module("rev2_layout_compare", LAYOUT_COMPARE_PATH)
    static_mod = load_module("rev2_static_abs", STATIC_ABS_PATH)
    anchor_truth, _tag_truth, _meta, _corr = static_mod.load_corrected_static_truth(
        OFFICIAL_ROOT / "opti_captures" / "full",
        ANCHOR_LABELS,
        ["ID01", "ID02", "ID03", "ID04", "ID05"],
    )
    layout_dir = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
    rows: list[dict] = []
    for version in ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]:
        labels, coords = layout_mod.load_layout(layout_dir / version / "layout.json")
        idx = [labels.index(a) for a in ANCHOR_LABELS]
        src = coords[idx]
        dst = np.array([anchor_truth[a] for a in ANCHOR_LABELS], dtype=float)
        fit = layout_mod.fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
        err = np.linalg.norm(fit.aligned - dst, axis=1)
        rows.append(
            {
                "solver": version,
                "sim3_median_mm": median(err),
                "sim3_p95_mm": pct(err, 95),
                "sim3_rmse_mm": float(np.sqrt(np.mean(err * err))),
                "sim3_scale": float(fit.scale),
                "sim3_det": float(fit.det),
            }
        )
    write_csv(tables_dir / "revision2_sim3_layout_residuals.csv", rows)
    v4_rmse = [r["sim3_rmse_mm"] for r in rows if r["solver"] == "v4-io"][0]
    return rows, v4_rmse


def geometry_check(tables_dir: Path) -> dict:
    static_mod = load_module("rev2_static_abs_geom", STATIC_ABS_PATH)
    anchor_truth, tag_truth, _meta, _corr = static_mod.load_corrected_static_truth(
        OFFICIAL_ROOT / "opti_captures" / "full",
        ANCHOR_LABELS,
        ["ID01", "ID02", "ID03", "ID04", "ID05"],
    )
    lower_y = np.array([anchor_truth[a][1] for a in "ABCD"], dtype=float)
    upper_y = np.array([anchor_truth[a][1] for a in "EFGH"], dtype=float)
    tag_y = np.array([v[1] for _k, v in sorted(tag_truth.items())], dtype=float)
    out = {
        "lower_layer_vertical_mean_mm": float(np.mean(lower_y)),
        "upper_layer_vertical_mean_mm": float(np.mean(upper_y)),
        "layer_gap_mm": float(abs(np.mean(upper_y) - np.mean(lower_y))),
        "layer_gap_m": float(abs(np.mean(upper_y) - np.mean(lower_y)) / 1000.0),
        "static_tag_vertical_min_mm": float(np.min(tag_y)),
        "static_tag_vertical_max_mm": float(np.max(tag_y)),
        "static_tag_vertical_coverage_mm": float(np.max(tag_y) - np.min(tag_y)),
        "static_tag_vertical_coverage_m": float((np.max(tag_y) - np.min(tag_y)) / 1000.0),
    }
    write_csv(tables_dir / "revision2_geometry_values.csv", [out])
    return out


def wall_material_check(tables_dir: Path, representative_cm: int) -> list[dict]:
    src = OFFICIAL_ROOT / "Analysis" / "AutoPos_simulation" / "wall_nlos_study" / "analysis" / "phase3_material_safe_distance.csv"
    if not src.exists():
        return []
    df = pd.read_csv(src)
    col = f"p95_at_{representative_cm}cm_m"
    if col not in df.columns:
        raise KeyError(f"{col} not in {src}")
    rows = []
    for _, r in df[df["wall_count"] == 4].iterrows():
        rows.append(
            {
                "material": r["material"],
                "wall_count": int(r["wall_count"]),
                "representative_wall_distance_cm": representative_cm,
                "p95_m": float(r[col]),
                "p95_mm": float(r[col] * 1000.0),
                "safe_distance_cm_p95lt0p15": int(r["safe_distance_cm_p95lt0p15"]),
                "safe_distance_cm_p95lt0p25": int(r["safe_distance_cm_p95lt0p25"]),
            }
        )
    write_csv(tables_dir / f"revision2_phase3_material_p95_at_{representative_cm}cm.csv", rows)
    return rows


def dop_metric_definition(tables_dir: Path) -> dict:
    static_script = STATIC_ABS_PATH.read_text(encoding="utf-8")
    raw_script = (FULL_ROOT / "scripts" / "static_tag_raw_replay_matrix.py").read_text(encoding="utf-8")
    source = "static_all_captures.csv copied by vdop_map.py"
    definition = {
        "source": source,
        "radial_definition": (
            "For each static session, radial_p95 is computed from per-frame 3D distances "
            "between each solved UWB point and that session's own point estimate."
        ),
        "p95_level": "per-frame within one 120 s static session; not position-level over n=2",
        "outer_median": (
            "dop_facing_height_summary groups the 24 static sessions by facing x height "
            "and reports the median of those per-session radial_p95 values; each cell has n=2 sessions."
        ),
        "flag": "No n=2 P95 issue for radial_p95 itself; the outer aggregation is a median over two per-session P95 values.",
        "static_script_contains_radial": "radial_p95" in static_script,
        "raw_replay_script_contains_radial": "radial_p95_mm" in raw_script,
    }
    write_csv(tables_dir / "revision2_dop_metric_definition.csv", [definition])
    return definition


def make_replay() -> tuple[object, list, np.ndarray, np.ndarray, np.ndarray]:
    cuda_mod = load_module("rev2_cuda_keepk", CUDA_REPLAY_PATH)
    layout = cuda_mod.load_layout_json(cuda_mod.available_layouts()["v4-io"], cuda_mod.layout_paths()[1])
    anchor_xyz = np.asarray(
        [[layout.anchors[aid].x_mm, layout.anchors[aid].y_mm, layout.anchors[aid].z_mm] for aid in range(8)],
        dtype=np.float32,
    )
    delays = np.asarray([layout.anchors[aid].d_anchor_mm + layout.tag_delay_mm for aid in range(8)], dtype=np.float32)
    sigmas = np.asarray([layout.anchors[aid].sigma_mm for aid in range(8)], dtype=np.float32)
    tracks = cuda_mod.load_static_tracks(0)
    ranges, quality, available, _lengths = cuda_mod.pack_tracks(tracks)
    replay = cuda_mod.CudaT4Replay(anchor_xyz, delays, sigmas, device="cuda")
    return cuda_mod, tracks, ranges, quality, available, replay


def anchor_locked_transform():
    layout_mod = load_module("rev2_layout_compare_for_transform", LAYOUT_COMPARE_PATH)
    static_mod = load_module("rev2_static_abs_for_transform", STATIC_ABS_PATH)
    anchor_truth, tag_truth, _meta, _corr = static_mod.load_corrected_static_truth(
        OFFICIAL_ROOT / "opti_captures" / "full",
        ANCHOR_LABELS,
        ["ID01", "ID02", "ID03", "ID04", "ID05"],
    )
    layout_dir = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
    labels, coords = layout_mod.load_layout(layout_dir / "v4-io" / "layout.json")
    idx = [labels.index(a) for a in ANCHOR_LABELS]
    src = coords[idx]
    dst = np.array([anchor_truth[a] for a in ANCHOR_LABELS])
    fit = layout_mod.fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    return fit.rotation, fit.translation, fit.scale, fit.det, tag_truth


def summarise_abs(pos: np.ndarray, valid: np.ndarray, tracks: list, r: np.ndarray, t: np.ndarray, scale: float, tag_truth: dict) -> dict:
    repeat_err = []
    repeat_h = []
    repeat_v = []
    for ri in range(pos.shape[1]):
        e3 = []
        eh = []
        ev = []
        for ti, track in enumerate(tracks):
            sid = track.capture_id
            if sid not in tag_truth:
                continue
            pts = pos[ti, ri]
            m = valid[ti, ri] & np.isfinite(pts).all(axis=1)
            if np.count_nonzero(m) < 10:
                continue
            p = np.nanmean(pts[m], axis=0)
            aligned = scale * p @ r + t
            diff = aligned - tag_truth[sid]
            e3.append(float(np.linalg.norm(diff)))
            eh.append(float(np.sqrt(diff[0] * diff[0] + diff[2] * diff[2])))
            ev.append(float(abs(diff[1])))
        if len(e3) >= 20:
            repeat_err.append(median(e3))
            repeat_h.append(median(eh))
            repeat_v.append(median(ev))
    q1, q3 = iqr(repeat_err)
    return {
        "repeat_count": len(repeat_err),
        "_repeat_accuracy": repeat_err,
        "_repeat_horizontal": repeat_h,
        "_repeat_vertical": repeat_v,
        "accuracy_median_over_repeats_mm": median(repeat_err),
        "accuracy_iqr_low_mm": q1,
        "accuracy_iqr_high_mm": q3,
        "horizontal_median_over_repeats_mm": median(repeat_h),
        "vertical_median_over_repeats_mm": median(repeat_v),
    }


def keepk_abs_accuracy(tables_dir: Path, repeats: int, repeat_batch: int, seed: int, skip_mc: bool = False, skip_stratified: bool = False) -> tuple[list[dict], list[dict]]:
    cuda_mod, tracks, ranges, quality, available, replay = make_replay()
    strat_mod = load_module("rev2_stratified_keepk", STRATIFIED_PATH)
    r, t, scale, _det, tag_truth = anchor_locked_transform()
    mc_path = tables_dir / "revision2_keepk_static_abs_accuracy_mc.csv"
    rows: list[dict] = []
    if skip_mc and mc_path.exists():
        rows = pd.read_csv(mc_path).to_dict("records")
    elif not skip_mc:
        for keep_k in [8, 7, 6, 5, 4]:
            repeats_total = 1 if keep_k == 8 else repeats
            chunks = []
            start = 0
            t0 = time.perf_counter()
            while start < repeats_total:
                n = min(repeat_batch, repeats_total - start)
                run_seed = seed + keep_k + start * 7919
                print(f"[rev2-A1] MC keep_k={keep_k} repeats={start + n}/{repeats_total}", flush=True)
                mask = cuda_mod.make_keep_mask(available, keep_k, n, run_seed)
                pos, _rms, valid = replay.replay(ranges, quality, mask, keep_k, "T4")
                chunks.append(summarise_abs(pos, valid, tracks, r, t, scale, tag_truth))
                start += n
            vals = []
            hs = []
            vs = []
            for c in chunks:
                vals.extend(c["_repeat_accuracy"])
                hs.extend(c["_repeat_horizontal"])
                vs.extend(c["_repeat_vertical"])
            q1, q3 = iqr(vals)
            rows.append(
                {
                    "layout": "v4-io",
                    "tag_method": "T4",
                    "kind": "static",
                    "keep_k": keep_k,
                    "repeats": repeats_total,
                    "seed_base": seed,
                    "frame_gating": "same as cuda_t4_keepk_replay.make_keep_mask; k=8 requires all 8 anchors in frame",
                    "mc_mask_note": "per-frame random keep-k masks; no persisted subset list exists in the previous MC replay",
                    "accuracy_median_mm": median(vals),
                    "accuracy_iqr_low_mm": q1,
                    "accuracy_iqr_high_mm": q3,
                    "horizontal_median_mm": median(hs),
                    "vertical_median_mm": median(vs),
                    "elapsed_s": time.perf_counter() - t0,
                }
            )
        write_csv(mc_path, rows)

    strat_rows: list[dict] = []
    if not skip_stratified:
        for keep_k in [7, 6, 5, 4]:
            keep_sets = strat_mod.generate_keep_sets(keep_k)
            for si, keep_set in enumerate(keep_sets, start=1):
                print(f"[rev2-A1] strat keep_k={keep_k} subset={si}/{len(keep_sets)}", flush=True)
                mask = strat_mod.make_fixed_masks(available, [keep_set])
                pos, _rms, valid = replay.replay(ranges, quality, mask, keep_k, "T4")
                s = summarise_abs(pos, valid, tracks, r, t, scale, tag_truth)
                strat_rows.append(
                    {
                        "layout": "v4-io",
                        "tag_method": "T4",
                        "kind": "static",
                        "keep_k": keep_k,
                        **strat_mod.describe_drop_set(keep_set),
                        "accuracy_median_mm": s["accuracy_median_over_repeats_mm"],
                        "horizontal_median_mm": s["horizontal_median_over_repeats_mm"],
                        "vertical_median_mm": s["vertical_median_over_repeats_mm"],
                    }
                )
        write_csv(tables_dir / "revision2_keepk_static_abs_accuracy_stratified_by_drop_set.csv", strat_rows)
    elif (tables_dir / "revision2_keepk_static_abs_accuracy_stratified_by_drop_set.csv").exists():
        strat_rows = pd.read_csv(tables_dir / "revision2_keepk_static_abs_accuracy_stratified_by_drop_set.csv").to_dict("records")
    cat_rows: list[dict] = []
    df = pd.DataFrame(strat_rows)
    for (keep_k, category), g in df.groupby(["keep_k", "drop_category"]):
        cat_rows.append(
            {
                "keep_k": int(keep_k),
                "drop_category": category,
                "drop_sets": int(len(g)),
                "accuracy_median_mm": median(g["accuracy_median_mm"]),
                "accuracy_iqr_low_mm": pct(g["accuracy_median_mm"], 25),
                "accuracy_iqr_high_mm": pct(g["accuracy_median_mm"], 75),
                "horizontal_median_mm": median(g["horizontal_median_mm"]),
                "vertical_median_mm": median(g["vertical_median_mm"]),
            }
        )
    cat_rows = sorted(cat_rows, key=lambda x: (-x["keep_k"], x["drop_category"]))
    write_csv(tables_dir / "revision2_keepk_static_abs_accuracy_stratified_summary.csv", cat_rows)
    return rows, cat_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=5000)
    parser.add_argument("--repeat-batch", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260528)
    parser.add_argument("--wall-distance-cm", type=int, default=100)
    parser.add_argument("--skip-keepk", action="store_true")
    parser.add_argument("--skip-mc", action="store_true")
    parser.add_argument("--skip-stratified", action="store_true")
    args = parser.parse_args()

    tables_dir = FULL_ROOT / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    sim_rows, v4_sim_rmse = sim3_layout_check(tables_dir)
    geom = geometry_check(tables_dir)
    dop_def = dop_metric_definition(tables_dir)
    wall_rows = wall_material_check(tables_dir, args.wall_distance_cm)
    if args.skip_keepk:
        keep_rows, strat_rows = [], []
    else:
        keep_rows, strat_rows = keepk_abs_accuracy(
            tables_dir,
            args.repeats,
            args.repeat_batch,
            args.seed,
            skip_mc=args.skip_mc,
            skip_stratified=args.skip_stratified,
        )

    report = {
        "sim3_v4_rmse_mm": v4_sim_rmse,
        "sim3_gate_matches_67p1": abs(v4_sim_rmse - 67.1) < 0.2,
        "geometry": geom,
        "dop_metric_definition": dop_def,
        "wall_material_rows": wall_rows,
        "keepk_mc_rows": keep_rows,
        "keepk_stratified_rows": strat_rows,
        "keepk_k8_gate_pass": None,
    }
    if keep_rows:
        k8 = next((r for r in keep_rows if int(r["keep_k"]) == 8), None)
        if k8:
            report["keepk_k8_deviation_from_headline_72p7_mm"] = float(k8["accuracy_median_mm"] - 72.7)
            report["keepk_k8_gate_pass"] = abs(float(k8["accuracy_median_mm"] - 72.7)) <= 15.0
    out_json = tables_dir / "revision2_phaseA_report.json"
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
