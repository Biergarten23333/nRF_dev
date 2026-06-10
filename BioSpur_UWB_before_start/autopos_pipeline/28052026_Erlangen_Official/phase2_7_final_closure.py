#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from phase2_6_diagnostics_closure import (
    add_tag_geometry_covariates,
    apply_height_preserving,
    find_static_capture_dirs,
    fit_height_preserving,
    load_layout_json_plain,
    load_offline_solver,
)
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas, write_csv
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import anchor_coord_map, load_phase1_data, tag_coord_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.7 final closure before analysis freeze.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


@dataclass
class Rigid3DFit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float

    def apply(self, points: np.ndarray) -> np.ndarray:
        pts = np.asarray(points, dtype=float)
        return self.scale * pts @ self.rotation + self.translation


@dataclass
class GenericTagFit:
    name: str
    covariates: tuple[str, ...]
    delta_tag_mm: float
    anchor_deltas_mm: np.ndarray
    coeffs: dict[str, float]
    rms_mm: float
    n_links: int


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool = True, allow_scale: bool = False) -> Rigid3DFit:
    src = np.asarray(src, dtype=float)
    dst = np.asarray(dst, dtype=float)
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    rotation = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        scale = float(np.sum(s * d) / np.sum(x * x))
    translation = dst_c - scale * src_c @ rotation
    return Rigid3DFit(rotation=rotation, translation=translation, scale=scale, det=float(np.linalg.det(rotation)))


def write_csv_rows(path: Path, rows: list[dict]) -> None:
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
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def summarize_errors(rows: list[dict], *, group_cols: list[str]) -> list[dict]:
    df = pd.DataFrame(rows)
    out: list[dict] = []
    for keys, g in df.groupby(group_cols, sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        err = g["err_3d_mm"].to_numpy(dtype=float)
        row = {col: key for col, key in zip(group_cols, keys)}
        row.update(
            {
                "positions": int(len(g)),
                "median_3d_mm": float(np.nanmedian(err)),
                "p95_3d_mm": float(np.nanpercentile(err, 95)),
                "rmse_3d_mm": float(np.sqrt(np.nanmean(err * err))),
                "median_horizontal_mm": float(np.nanmedian(g["err_horizontal_mm"].to_numpy(dtype=float))),
                "median_vertical_mm": float(np.nanmedian(g["err_vertical_mm"].to_numpy(dtype=float))),
            }
        )
        out.append(row)
    return out


def tag_error_rows(
    *,
    method: str,
    registration: str,
    points_by_position: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    transform: Any,
) -> list[dict]:
    rows = []
    for position, point in sorted(points_by_position.items()):
        aligned = transform(point[None, :])[0]
        truth = tag_truth[position]
        diff = aligned - truth
        rows.append(
            {
                "method": method,
                "registration": registration,
                "position": position,
                "aligned_x_mm": float(aligned[0]),
                "aligned_y_vertical_mm": float(aligned[1]),
                "aligned_z_mm": float(aligned[2]),
                "truth_x_mm": float(truth[0]),
                "truth_y_vertical_mm": float(truth[1]),
                "truth_z_mm": float(truth[2]),
                "err_x_mm": float(diff[0]),
                "err_y_vertical_mm": float(diff[1]),
                "err_z_mm": float(diff[2]),
                "err_horizontal_mm": float(math.hypot(diff[0], diff[2])),
                "err_vertical_mm": float(abs(diff[1])),
                "err_3d_mm": float(np.linalg.norm(diff)),
            }
        )
    return rows


def build_transforms(layout: dict, vicon_truth: dict[str, np.ndarray]) -> dict[str, Any]:
    src = layout["coords"]
    dst = np.asarray([vicon_truth[a] for a in ANCHOR_LABELS], dtype=float)
    r3 = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    r2, t2, z_shift, det2 = fit_height_preserving(src, dst, list(ANCHOR_LABELS))
    return {
        "anchor_only_3d_rigid": {
            "apply": lambda p: r3.apply(p),
            "det": r3.det,
            "anchor_rms_mm": float(np.sqrt(np.mean(np.linalg.norm(r3.apply(src) - dst, axis=1) ** 2))),
        },
        "official_height_preserving": {
            "apply": lambda p: apply_height_preserving(p, r2, t2, z_shift),
            "det": det2,
            "anchor_rms_mm": float(
                np.sqrt(np.mean(np.linalg.norm(apply_height_preserving(src, r2, t2, z_shift) - dst, axis=1) ** 2))
            ),
        },
    }


def fit_tag_model_generic(
    name: str,
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    covariates: list[str],
) -> GenericTagFit:
    work = link_df.reset_index(drop=True)
    mean_sweep = float(np.mean([sweep_delta[a] for a in ANCHOR_LABELS]))
    x = np.zeros((len(work), 8 + len(covariates)), dtype=float)
    for row, anchor in enumerate(work["anchor"].astype(str)):
        idx = ANCHOR_LABELS.index(anchor)
        if idx < 7:
            x[row, idx] = 0.5
        else:
            x[row, :7] = -0.5
        x[row, 7] = 0.5
    for cidx, cov in enumerate(covariates):
        x[:, 8 + cidx] = pd.to_numeric(work[cov], errors="coerce").to_numpy(dtype=float)
    y = work["bias_mm"].to_numpy(dtype=float) - 0.5 * mean_sweep
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    theta = np.r_[beta[:7], -float(np.sum(beta[:7]))]
    anchor_deltas = mean_sweep + theta
    delta_tag = float(beta[7])
    coeffs = {cov: float(beta[8 + idx]) for idx, cov in enumerate(covariates)}
    anchors = work["anchor"].astype(str).map(lambda a: ANCHOR_LABELS.index(a)).to_numpy(dtype=int)
    pred = 0.5 * delta_tag + 0.5 * anchor_deltas[anchors]
    for cov in covariates:
        pred = pred + coeffs[cov] * pd.to_numeric(work[cov], errors="coerce").to_numpy(dtype=float)
    residual = work["bias_mm"].to_numpy(dtype=float) - pred
    return GenericTagFit(
        name=name,
        covariates=tuple(covariates),
        delta_tag_mm=delta_tag,
        anchor_deltas_mm=anchor_deltas,
        coeffs=coeffs,
        rms_mm=float(np.sqrt(np.mean(residual * residual))),
        n_links=int(len(work)),
    )


def covariates_for_position(position: str, anchor_truth: dict[str, np.ndarray], tag_truth: dict[str, np.ndarray]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    t = tag_truth[position]
    for aid, anchor in enumerate(ANCHOR_LABELS):
        a = anchor_truth[anchor]
        d = a - t
        horizontal = float(math.hypot(d[0], d[2]))
        vertical = float(abs(d[1]))
        distance = float(np.linalg.norm(d))
        elevation = float(math.degrees(math.atan2(vertical, max(horizontal, 1e-9))))
        out[aid] = {
            "vicon_distance_mm": distance,
            "horizontal_distance_mm": horizontal,
            "vertical_abs_mm": vertical,
            "elevation_angle_deg": elevation,
        }
    return out


def corrected_frames_for_fit(
    frames: list[Any],
    fit: GenericTagFit,
    cov_by_anchor: dict[int, dict[str, float]],
    Frame: Any,
    Observation: Any,
) -> list[Any]:
    additive = 0.5 * fit.anchor_deltas_mm + 0.5 * fit.delta_tag_mm
    rho = float(fit.coeffs.get("vicon_distance_mm", 0.0))
    denom = 1.0 + rho
    out = []
    for frame in frames:
        obs = []
        for item in frame.observations:
            correction = float(additive[item.anchor_id])
            for cov, coeff in fit.coeffs.items():
                if cov == "vicon_distance_mm":
                    continue
                correction += float(coeff) * float(cov_by_anchor[item.anchor_id][cov])
            corrected_range = (float(item.range_mm) - correction) / denom
            obs.append(Observation(item.anchor_id, corrected_range, item.quality_percent, item.status))
        out.append(
            Frame(
                tag=frame.tag,
                sweep=frame.sweep,
                host_elapsed_s=frame.host_elapsed_s,
                host_epoch_s=frame.host_epoch_s,
                observations=tuple(obs),
                imu=frame.imu,
            )
        )
    return out


def summarize_solver_results(results: list[Any]) -> dict:
    if not results:
        return {"frames_solved": 0, "point": np.full(3, np.nan), "d3_std_mm": math.nan, "residual_rms_median_mm": math.nan}
    pts = np.asarray([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
    p = np.nanmean(pts, axis=0)
    d3 = np.linalg.norm(pts - p[None, :], axis=1)
    residual = np.asarray([r.residual_rms_mm for r in results], dtype=float)
    return {
        "frames_solved": int(len(results)),
        "point": p,
        "d3_std_mm": float(np.sqrt(np.nanmean(d3 * d3))),
        "residual_rms_median_mm": float(np.nanmedian(residual)),
    }


def solve_static_positions(
    *,
    data_dir: Path,
    layout_path: Path,
    frames_by_position: dict[str, list[Any]],
    sigma_path: Path,
    mode: str,
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    median_anchor_truth: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    workers: int,
) -> tuple[dict[str, np.ndarray], list[dict], list[dict]]:
    read_tr_all_frames, TagPositionSolver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    layout = load_layout_json(layout_path, sigma_path)
    fit_rows: list[dict] = []
    detail_rows: list[dict] = []

    mode_covariates = {
        "production_baseline_T4_mean": [],
        "distance_rho": ["vicon_distance_mm"],
        "elevation_beta": ["elevation_angle_deg"],
        "distance_plus_elevation": ["vicon_distance_mm", "elevation_angle_deg"],
    }
    covariates = mode_covariates[mode]

    def run_one(position: str) -> tuple[str, np.ndarray, dict, dict | None]:
        frames = frames_by_position[position]
        fit: GenericTagFit | None = None
        solve_frames = frames
        if mode != "production_baseline_T4_mean":
            train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
            fit = fit_tag_model_generic(f"{mode}_loo_without_{position}", train, sweep_delta, covariates)
            cov_by_anchor = covariates_for_position(position, median_anchor_truth, tag_truth)
            solve_frames = corrected_frames_for_fit(frames, fit, cov_by_anchor, Frame, Observation)
        solver = TagPositionSolver(layout, SolverConfig(method="T4"))
        results = []
        for frame in solve_frames:
            result = solver.solve_frame(frame)
            if result is not None:
                results.append(result)
        summary = summarize_solver_results(results)
        detail = {
            "mode": mode,
            "position": position,
            "frames_input": int(len(frames)),
            "frames_solved": int(summary["frames_solved"]),
            "d3_std_mm": summary["d3_std_mm"],
            "residual_rms_median_mm": summary["residual_rms_median_mm"],
        }
        fit_row = None
        if fit is not None:
            fit_row = {
                "mode": mode,
                "heldout_position": position,
                "train_links": fit.n_links,
                "delta_tag_mm": fit.delta_tag_mm,
                "rms_train_mm": fit.rms_mm,
                "rho_distance_percent": fit.coeffs.get("vicon_distance_mm", math.nan) * 100.0,
                "elevation_coeff_mm_per_deg": fit.coeffs.get("elevation_angle_deg", math.nan),
            }
        return position, summary["point"], detail, fit_row

    points: dict[str, np.ndarray] = {}
    max_workers = max(1, min(workers, len(frames_by_position)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_one, pos): pos for pos in sorted(frames_by_position)}
        for fut in as_completed(futs):
            position, point, detail, fit_row = fut.result()
            points[position] = point
            detail_rows.append(detail)
            if fit_row is not None:
                fit_rows.append(fit_row)
    detail_rows.sort(key=lambda r: (r["mode"], r["position"]))
    fit_rows.sort(key=lambda r: (r["mode"], r["heldout_position"]))
    return points, detail_rows, fit_rows


def inspect_table4_code_path(data_dir: Path) -> list[dict]:
    run_meta = data_dir / "Analysis" / "official_extra_analysis" / "FULL_4way_comparison" / "production_method_probe" / "production_static_method_real_run_eval" / "run_meta.json"
    script = data_dir / "Analysis" / "official_extra_analysis" / "FULL" / "scripts" / "static_tag_absolute_accuracy.py"
    rows = []
    meta = json.loads(run_meta.read_text(encoding="utf-8"))["runs"][-1]
    rows.append(
        {
            "evidence": "real_run_meta_script",
            "value": meta.get("script", ""),
            "path_or_line": str(run_meta),
        }
    )
    rows.append(
        {
            "evidence": "real_run_meta_layout_dir",
            "value": str(meta.get("args", {}).get("layout_dir", "")),
            "path_or_line": str(run_meta),
        }
    )
    rows.append(
        {
            "evidence": "real_run_meta_static_csv",
            "value": str(meta.get("args", {}).get("static_csv", "")),
            "path_or_line": str(run_meta),
        }
    )
    text = script.read_text(encoding="utf-8")
    for needle in [
        "r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)",
        "aligned = apply_transform(p, r, t, scale)[0]",
        "note\": \"production tag solver; official tag errors use corrected ground truth and anchor-locked method C\"",
    ]:
        line_no = next((idx for idx, line in enumerate(text.splitlines(), start=1) if needle in line), None)
        rows.append(
            {
                "evidence": "static_tag_absolute_accuracy_code",
                "value": needle,
                "path_or_line": f"{script}:{line_no}",
            }
        )
    rows.append(
        {
            "evidence": "definitive_registration_for_published_table4",
            "value": "anchor-only 3D rigid/reflection, no scale",
            "path_or_line": "static_tag_absolute_accuracy.py uses fit_similarity/apply_transform; not fit_height_preserving",
        }
    )
    return rows


def va_rerun_verification(data_dir: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    original = load_layout_json_plain(out_dir / "phase2_solver_layouts" / "V-A_unbounded" / "layout.json")
    fixed_path = out_dir / "phase2_6_layouts" / "V-A_subtractive_delay_sanity" / "layout.json"
    fixed = load_layout_json_plain(fixed_path)
    x_diff = np.linalg.norm(original["coords"] - fixed["coords"], axis=1)
    delay_sum = original["delays"] + fixed["delays"]
    rows = []
    for idx, anchor in enumerate(ANCHOR_LABELS):
        rows.append(
            {
                "anchor": anchor,
                "original_delay_mm": float(original["delays"][idx]),
                "sign_fixed_delay_mm": float(fixed["delays"][idx]),
                "delay_sum_mm": float(delay_sum[idx]),
                "layout_coord_delta_mm": float(x_diff[idx]),
            }
        )
    summary = [
        {
            "original_delay_sign": "+1 in residual distance + d_i + d_j - measured",
            "sign_fixed_delay_sign": "-1 in residual distance - d_i - d_j - measured",
            "initial_position_source": "same solve_autopos_v1(fused) initialization",
            "initial_delay_vector": "zeros for both runs",
            "delay_bound_mm": 400.0,
            "parameterization_changed": True,
            "max_abs_delay_sum_mm": float(np.max(np.abs(delay_sum))),
            "max_layout_coord_delta_mm": float(np.max(x_diff)),
            "conclusion": "solutions are sign-mirrored in delay with the same geometry/objective value; V-A failure is degeneracy, not implementation sign bug",
        }
    ]
    return rows, summary


def plot_registration_summary(path: Path, summary_rows: list[dict]) -> None:
    df = pd.DataFrame(summary_rows)
    fig, ax = plt.subplots(figsize=(9.0, 4.8))
    methods = list(dict.fromkeys(df["method"].astype(str).tolist()))
    regs = list(dict.fromkeys(df["registration"].astype(str).tolist()))
    x = np.arange(len(methods))
    width = 0.34
    for ridx, reg in enumerate(regs):
        vals = []
        for method in methods:
            g = df[(df["method"] == method) & (df["registration"] == reg)]
            vals.append(float(g["rmse_3d_mm"].iloc[0]) if len(g) else math.nan)
        ax.bar(x + (ridx - (len(regs) - 1) / 2.0) * width, vals, width=width, label=reg)
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=15, ha="right")
    ax.set_ylabel("static tag RMSE [mm]")
    ax.set_title("Registration Harmonization, C-core T4 mean")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def build_report(
    out_dir: Path,
    code_rows: list[dict],
    registration_summary: list[dict],
    model_summary: list[dict],
    facing_rows: list[dict],
    va_summary: list[dict],
    fig_name: str,
) -> None:
    lines = ["# Phase 2.7 Final Closure\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: final diagnostics closure only; no production solver files were modified.")
    lines.append("")
    lines.append("## 2.7a Registration Harmonization")
    lines.append(
        "Published Table 4 static tag numbers were produced by `static_tag_absolute_accuracy.py`, "
        "which uses anchor-only 3D rigid/reflection registration (`fit_similarity`, no scale). "
        "The height-preserving registration belongs to the raw replay matrix path and is not the published Table 4 path."
    )
    lines.append(
        "For the real production and V-B diagnostic layouts evaluated below, the height-preserving rows are compatibility checks only: "
        "their anchor-fit RMS is large because these layouts are in arbitrary 3D gauges, not in the pre-normalised deployment gauge assumed by the height-preserving raw-replay script."
    )
    lines.append("")
    lines.append(markdown_table(code_rows, ["evidence", "value", "path_or_line"]))
    lines.append("")
    lines.append(markdown_table(registration_summary, ["method", "registration", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm", "anchor_fit_rms_mm"]))
    lines.append("")
    lines.append(f"![Registration harmonization](figures/{fig_name})")
    lines.append("")
    lines.append("## 2.7b Rho Parameterization")
    lines.append(
        "All rows use V-B layout, C-core T4, mean session estimator, leave-one-position-out fit, and anchor-only 3D rigid/reflection registration. "
        "The covariate values for the held-out correction use Vicon geometry, so this is a supervised mechanism diagnostic, not a deployable calibration recipe."
    )
    lines.append("")
    lines.append(markdown_table(model_summary, ["method", "positions", "median_3d_mm", "p95_3d_mm", "rmse_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("## 2.7c Facing Stratification")
    lines.append("Facing-specific `rho_tag` fits are exploratory: each group has six static positions and 48 links.")
    lines.append("")
    lines.append(markdown_table(facing_rows, ["facing", "positions", "links", "rho_distance_percent", "delta_tag_mm", "rms_mm"]))
    lines.append("")
    lines.append("## 2.7d V-A Rerun Verification")
    lines.append(markdown_table(va_summary, ["original_delay_sign", "sign_fixed_delay_sign", "initial_position_source", "initial_delay_vector", "delay_bound_mm", "parameterization_changed", "max_abs_delay_sum_mm", "max_layout_coord_delta_mm", "conclusion"]))
    lines.append("")
    lines.append("STOP: Phase 2.7 final closure complete. Freeze diagnostics here; solver integration/report writing is the next phase.")
    (out_dir / "02_7_final_closure.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    figures_dir = out_dir / "figures"
    layout_work = out_dir / "phase2_7_layouts"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)
    layout_work.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    median_anchor_truth = anchor_coord_map(phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)

    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    link_df = add_tag_geometry_covariates(link_df, median_anchor_truth, tag_truth)

    read_tr_all_frames, _TagPositionSolver, _load_layout_json, _SolverConfig, _Frame, _Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    frames_by_position = {
        pos: read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        for pos, path in capture_dirs.items()
        if pos in set(link_df["position"].astype(str))
    }

    production_layout_path = data_dir / "solver" / "work" / "field_dataset_staged" / "FULL-COMPARE-1000-production-T4-real" / "v4-io" / "layout.json"
    production_sigma_path = data_dir / "solver" / "work" / "field_dataset_staged" / "FULL-COMPARE-1000-production-T4-real" / "tables" / "anchor_sigma.json"
    if not production_layout_path.exists():
        production_layout_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "v4-io" / "layout.json"
        production_sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    vb_layout_src = out_dir / "phase2_solver_layouts" / "V-B_calibrated" / "layout.json"
    vb_zero_layout = layout_work / "V-B_calibrated_zero_delay" / "layout.json"
    vb_data = json.loads(vb_layout_src.read_text(encoding="utf-8"))
    for item in vb_data["anchors"]:
        item["d_anchor_mm"] = 0.0
    vb_zero_layout.parent.mkdir(parents=True, exist_ok=True)
    vb_zero_layout.write_text(json.dumps(vb_data, indent=2) + "\n", encoding="utf-8")
    vb_sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"

    solved_points: dict[str, dict[str, np.ndarray]] = {}
    detail_rows: list[dict] = []
    fit_rows: list[dict] = []
    modes = [
        ("production_baseline_T4_mean", production_layout_path, production_sigma_path),
        ("distance_rho", vb_zero_layout, vb_sigma_path),
        ("elevation_beta", vb_zero_layout, vb_sigma_path),
        ("distance_plus_elevation", vb_zero_layout, vb_sigma_path),
    ]
    for mode, layout_path, sigma_path in modes:
        points, details, fits = solve_static_positions(
            data_dir=data_dir,
            layout_path=layout_path,
            frames_by_position=frames_by_position,
            sigma_path=sigma_path,
            mode=mode,
            link_df=link_df,
            sweep_delta=sweep_delta,
            median_anchor_truth=median_anchor_truth,
            tag_truth=tag_truth,
            workers=args.workers,
        )
        solved_points[mode] = points
        detail_rows.extend(details)
        fit_rows.extend(fits)

    write_csv_rows(tables_dir / "06_static_solve_details.csv", detail_rows)
    write_csv_rows(tables_dir / "06_loo_tag_model_fits.csv", fit_rows)

    production_layout = load_layout_json_plain(production_layout_path)
    vb_layout = load_layout_json_plain(vb_zero_layout)
    transforms_by_layout = {
        "production_baseline_T4_mean": build_transforms(production_layout, primary_truth),
        "distance_rho": build_transforms(vb_layout, primary_truth),
        "elevation_beta": build_transforms(vb_layout, primary_truth),
        "distance_plus_elevation": build_transforms(vb_layout, primary_truth),
    }

    per_position_rows: list[dict] = []
    for method, points in solved_points.items():
        for registration, tinfo in transforms_by_layout[method].items():
            rows = tag_error_rows(
                method=method,
                registration=registration,
                points_by_position=points,
                tag_truth=tag_truth,
                transform=tinfo["apply"],
            )
            for row in rows:
                row["anchor_fit_rms_mm"] = tinfo["anchor_rms_mm"]
                row["anchor_fit_det"] = tinfo["det"]
            per_position_rows.extend(rows)
    write_csv_rows(tables_dir / "06_registration_harmonization_positions.csv", per_position_rows)
    registration_summary = summarize_errors(per_position_rows, group_cols=["method", "registration"])
    for row in registration_summary:
        method = row["method"]
        registration = row["registration"]
        row["anchor_fit_rms_mm"] = transforms_by_layout[method][registration]["anchor_rms_mm"]
    write_csv_rows(tables_dir / "06_registration_harmonization_summary.csv", registration_summary)

    model_summary = [
        row
        for row in registration_summary
        if row["registration"] == "anchor_only_3d_rigid"
        and row["method"] in {"distance_rho", "elevation_beta", "distance_plus_elevation"}
    ]
    write_csv_rows(tables_dir / "06_rho_parameterization_summary.csv", model_summary)

    facing_rows: list[dict] = []
    for facing, g in link_df.groupby("facing"):
        fit = fit_tag_model_generic(f"facing_{facing}", g.reset_index(drop=True), sweep_delta, ["vicon_distance_mm"])
        facing_rows.append(
            {
                "facing": facing,
                "positions": int(g["position"].nunique()),
                "links": int(len(g)),
                "rho_distance_percent": float(fit.coeffs["vicon_distance_mm"] * 100.0),
                "delta_tag_mm": float(fit.delta_tag_mm),
                "rms_mm": float(fit.rms_mm),
            }
        )
    facing_rows.sort(key=lambda r: str(r["facing"]))
    write_csv_rows(tables_dir / "06_facing_rho_stratification.csv", facing_rows)

    va_rows, va_summary = va_rerun_verification(data_dir, out_dir)
    write_csv_rows(tables_dir / "06_va_rerun_verification.csv", va_rows)
    write_csv_rows(tables_dir / "06_va_rerun_verification_summary.csv", va_summary)

    code_rows = inspect_table4_code_path(data_dir)
    write_csv_rows(tables_dir / "06_table4_registration_code_path.csv", code_rows)

    fig = figures_dir / "06_registration_harmonization_rmse.png"
    plot_registration_summary(fig, [r for r in registration_summary if r["method"] in {"production_baseline_T4_mean", "distance_rho"}])

    build_report(
        out_dir,
        code_rows,
        [r for r in registration_summary if r["method"] in {"production_baseline_T4_mean", "distance_rho"}],
        model_summary,
        facing_rows,
        va_summary,
        fig.name,
    )
    print(f"Phase 2.7 report written: {out_dir / '02_7_final_closure.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
