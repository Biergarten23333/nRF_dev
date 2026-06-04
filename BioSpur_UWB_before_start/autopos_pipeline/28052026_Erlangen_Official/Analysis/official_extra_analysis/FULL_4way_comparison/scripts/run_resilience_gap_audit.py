#!/usr/bin/env python3
"""Bootstrap and synthetic-dropout audit for the remaining reporting gaps.

This script fills three currently feasible gaps without claiming new repeated
experiments:

* AutoPos bootstrap numerical precision from raw inter-anchor ranges.
* Layout-level residual delay correction bootstrap numerical precision.
* Synthetic packet/dropout stress for the four FULL comparison conditions.

The bootstrap is diagnostic.  It estimates within-campaign median sampling
precision under resampled raw range observations from this capture campaign.
Because each anchor pair has a large number of samples, the resulting
millimeter-class layout/delay spread is mainly the sampling SE of per-pair
medians; it is not a replacement for independent repeated AutoPos deployments.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import math
import sys
import itertools
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
COMP_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = EXTRA_ROOT.parent.parent
FULL_ROOT = EXTRA_ROOT / "FULL"
ALIGN_ROOT = EXTRA_ROOT / "FULL_AutoPos_align_to_Vicon"
SCALE_ROOT = EXTRA_ROOT / "FULL_AutoPos_scale_to_vicon"
ONE_BASELINE_ROOT = EXTRA_ROOT / "FULL_AutoPos_one_baseline_scale_correction"
OUT_ROOT = COMP_ROOT / "resilience_gap_audit"
TABLE_DIR = OUT_ROOT / "tables"
REPORT_DIR = OUT_ROOT / "reports"

ANCHORS = list("ABCDEFGH")
ANCHOR_TO_ID = {a: i for i, a in enumerate(ANCHORS)}
STATIC_TAG = "BSF66F"
ROTO_TAGS = ["BS2DCE", "BSDC91"]


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
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def pct(values, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def rmse(values) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(float(np.mean(arr * arr))))


def position_gdop(anchors_xyz: np.ndarray, point_xyz: np.ndarray, anchor_ids: list[int] | tuple[int, ...]) -> float:
    anchors = np.asarray(anchors_xyz, dtype=float)
    point = np.asarray(point_xyz, dtype=float)
    ids = [int(i) for i in anchor_ids]
    if len(ids) < 4:
        return float("nan")
    vec = anchors[ids] - point[None, :]
    dist = np.linalg.norm(vec, axis=1)
    good = np.isfinite(dist) & (dist > 1e-9) & np.isfinite(vec).all(axis=1)
    if int(np.sum(good)) < 4:
        return float("nan")
    h = vec[good] / dist[good, None]
    normal = h.T @ h
    try:
        q = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return float("nan")
    val = float(np.trace(q))
    return float(math.sqrt(val)) if math.isfinite(val) and val >= 0.0 else float("nan")


def best_gdop_subset(anchors_xyz: np.ndarray, point_xyz: np.ndarray, k: int = 4) -> tuple[tuple[int, ...], float]:
    best_ids: tuple[int, ...] = tuple()
    best = float("inf")
    for ids in itertools.combinations(range(len(ANCHORS)), k):
        gdop = position_gdop(anchors_xyz, point_xyz, ids)
        if math.isfinite(gdop) and gdop < best:
            best = gdop
            best_ids = tuple(int(i) for i in ids)
    return best_ids, best


def stable_seed(base_seed: int, *parts: object) -> int:
    text = "|".join([str(base_seed), *[str(p) for p in parts]])
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "little") & ((1 << 63) - 1)


def fixed_random_subset(base_seed: int, case_id: str, sid: str, k: int) -> tuple[int, ...]:
    rng = np.random.default_rng(stable_seed(base_seed, case_id, sid, "fixedrandom", k))
    return tuple(sorted(int(i) for i in rng.choice(len(ANCHORS), size=k, replace=False)))


def fmt(x: float, ndigits: int = 1) -> str:
    if x is None or not math.isfinite(float(x)):
        return "nan"
    return f"{float(x):.{ndigits}f}"


def import_static_ablation_module():
    path = COMP_ROOT / "scripts" / "run_static_layout_ablation.py"
    spec = importlib.util.spec_from_file_location("full_static_ablation_for_resilience", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class Case:
    case_id: str
    label: str
    coords_mm: np.ndarray
    delays: dict[int, float]
    tag_delay_mm: float
    scale_factor: float
    delay_mode: str
    coord_mode: str
    roto_samples: Path


def pair_key(a: str, b: str) -> tuple[str, str]:
    aa, bb = str(a), str(b)
    return (aa, bb) if ANCHOR_TO_ID[aa] < ANCHOR_TO_ID[bb] else (bb, aa)


def pair_distance_vector(coords: np.ndarray) -> np.ndarray:
    vals = []
    for i in range(len(ANCHORS)):
        for j in range(i + 1, len(ANCHORS)):
            vals.append(float(np.linalg.norm(coords[i] - coords[j])))
    return np.asarray(vals, dtype=float)


def metric_mds_from_distances(dist: np.ndarray) -> np.ndarray:
    """Classical MDS reconstruction from a full distance matrix."""
    d2 = dist * dist
    n = d2.shape[0]
    h = np.eye(n) - np.ones((n, n), dtype=float) / n
    gram = -0.5 * h @ d2 @ h
    vals, vecs = np.linalg.eigh(gram)
    order = np.argsort(vals)[::-1][:3]
    vals = np.maximum(vals[order], 0.0)
    vecs = vecs[:, order]
    return vecs * np.sqrt(vals)[None, :]


def distances_to_coords(pair_medians: dict[tuple[str, str], float]) -> np.ndarray:
    n = len(ANCHORS)
    dist = np.zeros((n, n), dtype=float)
    for (a, b), val in pair_medians.items():
        ia, ib = ANCHOR_TO_ID[a], ANCHOR_TO_ID[b]
        dist[ia, ib] = dist[ib, ia] = float(val)
    return metric_mds_from_distances(dist)


def estimate_anchor_residual_delays(coords: np.ndarray, pair_medians: dict[tuple[str, str], float]) -> dict:
    design = []
    target = []
    residual_rows = []
    for i in range(len(ANCHORS)):
        for j in range(i + 1, len(ANCHORS)):
            a, b = ANCHORS[i], ANCHORS[j]
            measured = float(pair_medians[(a, b)])
            geom = float(np.linalg.norm(coords[i] - coords[j]))
            bias = measured - geom
            row = np.zeros(len(ANCHORS), dtype=float)
            row[i] = 1.0
            row[j] = 1.0
            design.append(row)
            target.append(bias)
            residual_rows.append((a, b, bias))
    a_mat = np.vstack(design)
    y = np.asarray(target, dtype=float)
    delays, *_ = np.linalg.lstsq(a_mat, y, rcond=None)
    pred = a_mat @ delays
    resid = pred - y
    rel_a = delays - delays[0]
    return {
        "delay_mm": delays,
        "delay_rel_A_mm": rel_a,
        "common_mode_mm": float(np.mean(delays)),
        "delay_median_mm": float(np.median(delays)),
        "pair_residual_rms_mm": rmse(resid),
        "pair_residual_p95_abs_mm": pct(np.abs(resid), 95),
    }


def load_pair_observations() -> dict[tuple[str, str], np.ndarray]:
    path = OFFICIAL_ROOT / "solver/work/field_dataset_staged/sweep1000/pairs_all.csv"
    df = pd.read_csv(path)
    if "ok" in df.columns:
        df = df[df["ok"].astype(int) == 1].copy()
    out: dict[tuple[str, str], list[float]] = {}
    for _, row in df.iterrows():
        key = pair_key(str(row["a"]), str(row["b"]))
        out.setdefault(key, []).append(float(row["dist_mm"]))
    return {k: np.asarray(v, dtype=float) for k, v in out.items()}


def pair_sampling_se_rows(pair_obs: dict[tuple[str, str], np.ndarray]) -> list[dict]:
    rows: list[dict] = []
    for a, b in sorted(pair_obs):
        vals = np.asarray(pair_obs[(a, b)], dtype=float)
        vals = vals[np.isfinite(vals)]
        std = float(np.std(vals, ddof=1)) if vals.size > 1 else float("nan")
        se_median = 1.2533 * std / math.sqrt(float(vals.size)) if vals.size else float("nan")
        rows.append(
            {
                "pair": f"{a}-{b}",
                "anchor_a": a,
                "anchor_b": b,
                "n_samples": int(vals.size),
                "range_std_mm": std,
                "analytical_se_median_mm": se_median,
                "se_formula": "1.2533 * std / sqrt(n_samples)",
            }
        )
    return rows


def bootstrap_numerical_precision_rows(layout_rows: list[dict], pair_se_rows: list[dict]) -> list[dict]:
    median_se = pct([r["analytical_se_median_mm"] for r in pair_se_rows], 50)
    p95_se = pct([r["analytical_se_median_mm"] for r in pair_se_rows], 95)
    median_n = pct([r["n_samples"] for r in pair_se_rows], 50)
    out: list[dict] = []
    for row in layout_rows:
        case_id = str(row["case_id"])
        fixed_truth = str(row.get("truth_status", "")) == "fixed_truth_coords_no_layout_bootstrap"
        coord_sd = float(row["coordinate_sd_median_mm"])
        pair_sd = float(row["pairwise_distance_sd_median_mm"])
        coord_ratio = coord_sd / median_se if median_se > 0.0 and not fixed_truth else float("nan")
        pair_ratio = pair_sd / median_se if median_se > 0.0 and not fixed_truth else float("nan")
        coord_ratio_p95 = coord_sd / p95_se if p95_se > 0.0 and not fixed_truth else float("nan")
        pair_ratio_p95 = pair_sd / p95_se if p95_se > 0.0 and not fixed_truth else float("nan")
        out.append(
            {
                "case_id": case_id,
                "case_label": row.get("case_label", ""),
                "median_pair_n_samples": median_n,
                "pair_analytical_se_median_mm": median_se,
                "pair_analytical_se_p95_mm": p95_se,
                "bootstrap_pairwise_distance_sd_median_mm": pair_sd,
                "bootstrap_coordinate_sd_median_mm": coord_sd,
                "pairwise_sd_over_median_se": pair_ratio,
                "coordinate_sd_over_median_se": coord_ratio,
                "pairwise_sd_over_p95_se": pair_ratio_p95,
                "coordinate_sd_over_p95_se": coord_ratio_p95,
                "comparison_applicable": not fixed_truth,
                "sampling_se_confirmed": bool((not fixed_truth) and max(pair_ratio_p95, coord_ratio_p95) <= 1.5),
                "interpretation": (
                    "fixed truth coordinates; no layout bootstrap to interpret"
                    if fixed_truth
                    else "within-campaign median sampling SE, NOT independent deployment repeatability"
                ),
            }
        )
    return out


def bootstrap_pair_medians(pair_obs: dict[tuple[str, str], np.ndarray], rng: np.random.Generator) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for key, vals in pair_obs.items():
        idx = rng.integers(0, vals.size, size=vals.size)
        out[key] = float(np.median(vals[idx]))
    return out


def load_case_layouts(ablation_mod) -> tuple[list[Case], np.ndarray, dict[str, np.ndarray], dict[str, dict], list[Path]]:
    layout_base = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
    opti_dir = OFFICIAL_ROOT / "opti_captures/full"
    captures_root = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality = layout_base / "tables/pair_quality_solve.csv"

    anchor_truth, tag_truth, tag_truth_meta, _corr = ablation_mod.load_corrected_static_truth(
        opti_dir,
        ablation_mod.ANCHORS,
        ablation_mod.PRIMARY_IDS,
    )
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    _sigma_by_id = ablation_mod.load_anchor_sigma(sigma_path)

    labels, raw_coords, solver_delays, solver_tag_delay = ablation_mod.load_layout_json_raw(layout_base / "v4-io" / "layout.json")
    by_label = {label: raw_coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    rigid = ablation_mod.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    sim = ablation_mod.fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=True)
    self_coords = ablation_mod.apply_fit(src, rigid)
    scale_coords = ablation_mod.apply_fit(src, sim)

    truth_by_label = {a: truth_coords[i] for i, a in enumerate(ANCHORS)}
    delaycal_delays, delaycal_tag_delay, _ = ablation_mod.estimate_delaycal(truth_by_label, pair_quality)

    scale_by_label = {a: scale_coords[i] for i, a in enumerate(ANCHORS)}
    scale_delays, scale_tag_delay, _ = ablation_mod.estimate_delaycal_from_points(
        scale_by_label,
        pair_quality,
        "v4-io full-similarity scaled layout",
    )

    ia, ib = ANCHOR_TO_ID["E"], ANCHOR_TO_ID["H"]
    d_auto = float(np.linalg.norm(src[ia] - src[ib]))
    d_true = float(np.linalg.norm(truth_coords[ia] - truth_coords[ib]))
    eh_scale = d_true / d_auto
    eh_fit = ablation_mod.fit_with_fixed_scale(src, truth_coords, rigid.rotation, eh_scale)
    eh_coords = ablation_mod.apply_fit(src, eh_fit)
    eh_by_label = {a: eh_coords[i] for i, a in enumerate(ANCHORS)}
    eh_delays, eh_tag_delay, _ = ablation_mod.estimate_delaycal_from_points(
        eh_by_label,
        pair_quality,
        "v4-io one-baseline E-H scaled layout",
    )

    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    cases = [
        Case(
            case_id="original_selfcal",
            label="FULL original self-cal v4-io/T4",
            coords_mm=self_coords,
            delays=solver_delays,
            tag_delay_mm=solver_tag_delay,
            scale_factor=1.0,
            delay_mode="solver_layout_residual_corrections",
            coord_mode="rigid_no_scale_to_vicon",
            roto_samples=FULL_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
        ),
        Case(
            case_id="vicon_truth_delaycal",
            label="FULL Vicon-truth+delaycal v4-io/T4",
            coords_mm=truth_coords,
            delays=delaycal_delays,
            tag_delay_mm=delaycal_tag_delay,
            scale_factor=1.0,
            delay_mode="vicon_inter_anchor_delaycal",
            coord_mode="truth_anchor_coords",
            roto_samples=ALIGN_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
        ),
        Case(
            case_id="scale_to_vicon_delaycal",
            label="FULL Sim(3)-scale-to-Vicon+delaycal v4-io/T4",
            coords_mm=scale_coords,
            delays=scale_delays,
            tag_delay_mm=scale_tag_delay,
            scale_factor=float(sim.scale),
            delay_mode="scaled_layout_inter_anchor_delaycal",
            coord_mode="sim3_scale_to_vicon",
            roto_samples=SCALE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
        ),
        Case(
            case_id="one_baseline_EH_delaycal",
            label="FULL one-baseline E-H+delaycal v4-io/T4",
            coords_mm=eh_coords,
            delays=eh_delays,
            tag_delay_mm=eh_tag_delay,
            scale_factor=float(eh_scale),
            delay_mode="one_baseline_layout_inter_anchor_delaycal",
            coord_mode="one_baseline_EH_scale",
            roto_samples=ONE_BASELINE_ROOT / "roto_absolute/tables/roto_abs_samples_v4io_T4.csv",
        ),
    ]
    return cases, truth_coords, tag_truth, tag_truth_meta, static_files


def bootstrap_layout_and_delay(
    ablation_mod,
    cases: list[Case],
    truth_coords: np.ndarray,
    *,
    n_bootstrap: int,
    seed: int,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    pair_obs = load_pair_observations()
    full_medians = {key: float(np.median(vals)) for key, vals in pair_obs.items()}
    rng = np.random.default_rng(seed)

    # Reference case coordinates use the same MDS bootstrap machinery on full medians.
    ref_raw = distances_to_coords(full_medians)
    ref_by_case: dict[str, np.ndarray] = {}
    ref_delay_by_case: dict[str, dict] = {}
    for case in cases:
        if case.case_id == "vicon_truth_delaycal":
            coords = truth_coords.copy()
        elif case.case_id == "original_selfcal":
            fit = ablation_mod.fit_similarity(ref_raw, truth_coords, allow_reflection=True, allow_scale=False)
            coords = ablation_mod.apply_fit(ref_raw, fit)
        elif case.case_id == "scale_to_vicon_delaycal":
            fit = ablation_mod.fit_similarity(ref_raw, truth_coords, allow_reflection=True, allow_scale=True)
            coords = ablation_mod.apply_fit(ref_raw, fit)
        elif case.case_id == "one_baseline_EH_delaycal":
            rigid = ablation_mod.fit_similarity(ref_raw, truth_coords, allow_reflection=True, allow_scale=False)
            ia, ib = ANCHOR_TO_ID["E"], ANCHOR_TO_ID["H"]
            scale = float(np.linalg.norm(truth_coords[ia] - truth_coords[ib]) / np.linalg.norm(ref_raw[ia] - ref_raw[ib]))
            fit = ablation_mod.fit_with_fixed_scale(ref_raw, truth_coords, rigid.rotation, scale)
            coords = ablation_mod.apply_fit(ref_raw, fit)
        else:
            raise ValueError(case.case_id)
        ref_by_case[case.case_id] = coords
        ref_delay_by_case[case.case_id] = estimate_anchor_residual_delays(coords, full_medians)

    coord_samples = {case.case_id: [] for case in cases}
    pairdist_samples = {case.case_id: [] for case in cases}
    delay_samples = {case.case_id: [] for case in cases}
    common_mode_samples = {case.case_id: [] for case in cases}
    scale_samples = {case.case_id: [] for case in cases}
    pair_resid_samples = {case.case_id: [] for case in cases}

    for _rep in range(n_bootstrap):
        sampled = bootstrap_pair_medians(pair_obs, rng)
        raw = distances_to_coords(sampled)
        for case in cases:
            if case.case_id == "vicon_truth_delaycal":
                coords = truth_coords.copy()
                scale = 1.0
            elif case.case_id == "original_selfcal":
                fit = ablation_mod.fit_similarity(raw, truth_coords, allow_reflection=True, allow_scale=False)
                coords = ablation_mod.apply_fit(raw, fit)
                scale = 1.0
            elif case.case_id == "scale_to_vicon_delaycal":
                fit = ablation_mod.fit_similarity(raw, truth_coords, allow_reflection=True, allow_scale=True)
                coords = ablation_mod.apply_fit(raw, fit)
                scale = float(fit.scale)
            elif case.case_id == "one_baseline_EH_delaycal":
                rigid = ablation_mod.fit_similarity(raw, truth_coords, allow_reflection=True, allow_scale=False)
                ia, ib = ANCHOR_TO_ID["E"], ANCHOR_TO_ID["H"]
                scale = float(np.linalg.norm(truth_coords[ia] - truth_coords[ib]) / np.linalg.norm(raw[ia] - raw[ib]))
                fit = ablation_mod.fit_with_fixed_scale(raw, truth_coords, rigid.rotation, scale)
                coords = ablation_mod.apply_fit(raw, fit)
            else:
                raise ValueError(case.case_id)
            delay = estimate_anchor_residual_delays(coords, sampled)
            coord_samples[case.case_id].append(coords)
            pairdist_samples[case.case_id].append(pair_distance_vector(coords))
            delay_samples[case.case_id].append(delay["delay_rel_A_mm"])
            common_mode_samples[case.case_id].append(float(delay["common_mode_mm"]))
            scale_samples[case.case_id].append(scale)
            pair_resid_samples[case.case_id].append(delay["pair_residual_rms_mm"])

    layout_rows: list[dict] = []
    delay_rows: list[dict] = []
    anchor_rows: list[dict] = []
    delay_anchor_rows: list[dict] = []
    for case in cases:
        coords_arr = np.stack(coord_samples[case.case_id], axis=0)
        pair_arr = np.stack(pairdist_samples[case.case_id], axis=0)
        scales = np.asarray(scale_samples[case.case_id], dtype=float)
        delays_rel = np.stack(delay_samples[case.case_id], axis=0)
        common_modes = np.asarray(common_mode_samples[case.case_id], dtype=float)
        pair_resids = np.asarray(pair_resid_samples[case.case_id], dtype=float)

        per_anchor_sd = np.sqrt(np.sum(np.nanstd(coords_arr, axis=0, ddof=1) ** 2, axis=1))
        per_anchor_ref_abs = np.linalg.norm(ref_by_case[case.case_id] - case.coords_mm, axis=1)
        pair_sd = np.nanstd(pair_arr, axis=0, ddof=1)
        delay_sd = np.nanstd(delays_rel, axis=0, ddof=1)
        layout_rows.append(
            {
                "case_id": case.case_id,
                "case_label": case.label,
                "n_bootstrap": n_bootstrap,
                "bootstrap_input": "raw inter-anchor ranges, resampled within each unordered anchor pair",
                "bootstrap_layout_method": "metric MDS from pair medians, then case-specific alignment/scale gauge",
                "coordinate_sd_median_mm": pct(per_anchor_sd, 50),
                "coordinate_sd_p95_mm": pct(per_anchor_sd, 95),
                "coordinate_sd_worst_mm": float(np.nanmax(per_anchor_sd)),
                "pairwise_distance_sd_median_mm": pct(pair_sd, 50),
                "pairwise_distance_sd_p95_mm": pct(pair_sd, 95),
                "scale_factor_median": pct(scales, 50),
                "scale_factor_sd": float(np.nanstd(scales, ddof=1)),
                "reference_case_coord_gap_median_mm": pct(per_anchor_ref_abs, 50),
                "reference_case_coord_gap_worst_mm": float(np.nanmax(per_anchor_ref_abs)),
                "truth_status": "fixed_truth_coords_no_layout_bootstrap" if case.case_id == "vicon_truth_delaycal" else "bootstrapped_from_raw_pairs",
                "interpretation": "within-campaign median sampling SE, NOT independent deployment repeatability",
            }
        )
        delay_rows.append(
            {
                "case_id": case.case_id,
                "case_label": case.label,
                "n_bootstrap": n_bootstrap,
                "delay_term_name": "layout-level residual delay correction rel_A",
                "anchor_delay_rel_A_sd_median_mm": pct(delay_sd, 50),
                "anchor_delay_rel_A_sd_p95_mm": pct(delay_sd, 95),
                "anchor_delay_rel_A_sd_worst_mm": float(np.nanmax(delay_sd)),
                "common_mode_sd_mm": float(np.nanstd(common_modes, ddof=1)),
                "pair_residual_rms_median_mm": pct(pair_resids, 50),
                "pair_residual_rms_p95_mm": pct(pair_resids, 95),
                "identifiability_note": "only rel_A differences are quotable from pair/range data; absolute delay is gauge-coupled",
            }
        )
        for aid, label in enumerate(ANCHORS):
            anchor_rows.append(
                {
                    "case_id": case.case_id,
                    "anchor": label,
                    "coord_sd_3d_mm": float(per_anchor_sd[aid]),
                    "reference_case_coord_gap_mm": float(per_anchor_ref_abs[aid]),
                    "x_sd_mm": float(np.nanstd(coords_arr[:, aid, 0], ddof=1)),
                    "y_sd_mm": float(np.nanstd(coords_arr[:, aid, 1], ddof=1)),
                    "z_sd_mm": float(np.nanstd(coords_arr[:, aid, 2], ddof=1)),
                }
            )
            delay_anchor_rows.append(
                {
                    "case_id": case.case_id,
                    "anchor": label,
                    "delay_rel_A_median_mm": pct(delays_rel[:, aid], 50),
                    "delay_rel_A_sd_mm": float(delay_sd[aid]),
                    "delay_rel_A_p05_mm": pct(delays_rel[:, aid], 5),
                    "delay_rel_A_p95_mm": pct(delays_rel[:, aid], 95),
                }
            )
    return layout_rows, delay_rows, anchor_rows, delay_anchor_rows


def make_layout(ablation_mod, case: Case):
    sigma_by_id = ablation_mod.load_anchor_sigma(
        OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check/tables/anchor_sigma.json"
    )
    return ablation_mod.build_layout(
        name=f"resilience_gap/{case.case_id}",
        labels=ANCHORS,
        coords_opti_frame=case.coords_mm,
        delays=case.delays,
        tag_delay_mm=case.tag_delay_mm,
        sigma_by_id=sigma_by_id,
        metadata={"case_id": case.case_id, "delay_mode": case.delay_mode, "coord_mode": case.coord_mode},
    )


def dropped_frame(ablation_mod, frame, anchor_keep: int, rng: np.random.Generator, fixed_anchor_ids: tuple[int, ...] | None = None):
    observations = list(frame.observations)
    if fixed_anchor_ids is not None:
        wanted = set(int(i) for i in fixed_anchor_ids)
        observations = [o for o in observations if int(o.anchor_id) in wanted]
    elif len(observations) > anchor_keep:
        idx = np.sort(rng.choice(len(observations), size=anchor_keep, replace=False))
        observations = [observations[int(i)] for i in idx]
    if len(observations) < 4:
        return None
    return ablation_mod.Frame(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=tuple(observations),
        imu=frame.imu,
    )


def solve_static_dropout(
    ablation_mod,
    cases: list[Case],
    tag_truth: dict[str, np.ndarray],
    tag_truth_meta: dict[str, dict],
    static_files: list[Path],
    *,
    max_frames_per_position: int,
    seed: int,
) -> tuple[list[dict], list[dict]]:
    conditions = [
        ("baseline_all_frames_all8", 1.00, 8, "all_available"),
        ("frame_keep_75_all8", 0.75, 8, "all_available"),
        ("frame_keep_50_all8", 0.50, 8, "all_available"),
        ("frame_keep_25_all8", 0.25, 8, "all_available"),
        ("anchor_keep_7", 1.00, 7, "random_per_frame"),
        ("anchor_keep_6", 1.00, 6, "random_per_frame"),
        ("anchor_keep_5", 1.00, 5, "random_per_frame"),
        ("anchor_keep_4", 1.00, 4, "random_per_frame"),
        ("anchor_keep_4_fixedrandom", 1.00, 4, "fixed_random_per_position"),
        ("anchor_keep_4_bestgdop", 1.00, 4, "best_gdop_per_position"),
        ("frame_keep_50_anchor_keep_6", 0.50, 6, "random_per_frame"),
        ("frame_keep_50_anchor_keep_4", 0.50, 4, "random_per_frame"),
    ]
    per_position: list[dict] = []
    summary_rows: list[dict] = []
    for case_i, case in enumerate(cases):
        layout = make_layout(ablation_mod, case)
        solver = ablation_mod.TagPositionSolver(layout, ablation_mod.SolverConfig(method="T4"))
        for cond_i, (condition, frame_keep, anchor_keep, subset_mode) in enumerate(conditions):
            sample_errors = []
            pos_errors = []
            repeatability = []
            gdop_values = []
            frames_in_total = 0
            frames_solved_total = 0
            for file_i, path in enumerate(static_files):
                sid = ablation_mod.session_id_from_path(path)
                truth = tag_truth.get(sid)
                if truth is None:
                    continue
                frames = ablation_mod.read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
                frames = ablation_mod.filter_frames(frames, set(range(8)))
                if not frames:
                    continue
                local_rng = np.random.default_rng(seed + 100003 * case_i + 9176 * cond_i + file_i)
                if len(frames) > max_frames_per_position:
                    idx = np.sort(local_rng.choice(len(frames), size=max_frames_per_position, replace=False))
                    frames = [frames[int(i)] for i in idx]
                if frame_keep < 1.0:
                    keep = local_rng.random(len(frames)) < frame_keep
                    frames = [frame for frame, use in zip(frames, keep) if bool(use)]
                frames_in_total += len(frames)
                solved_pts = []
                fixed_anchor_ids: tuple[int, ...] | None = None
                fixed_gdop = float("nan")
                if subset_mode == "best_gdop_per_position":
                    fixed_anchor_ids, fixed_gdop = best_gdop_subset(case.coords_mm, truth, anchor_keep)
                    if len(fixed_anchor_ids) < anchor_keep:
                        fixed_anchor_ids = None
                elif subset_mode == "fixed_random_per_position":
                    fixed_anchor_ids = fixed_random_subset(seed, case.case_id, sid, anchor_keep)
                    fixed_gdop = position_gdop(case.coords_mm, truth, fixed_anchor_ids)
                for frame in frames:
                    mod_frame = dropped_frame(ablation_mod, frame, anchor_keep, local_rng, fixed_anchor_ids=fixed_anchor_ids)
                    if mod_frame is None:
                        continue
                    result = solver.solve_frame(mod_frame)
                    if result is None or result.status != "ok":
                        continue
                    point = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
                    if not np.isfinite(point).all():
                        continue
                    solved_pts.append(point)
                    sample_errors.append(float(np.linalg.norm(point - truth)))
                    ids = tuple(sorted(int(o.anchor_id) for o in mod_frame.observations))
                    gdop = fixed_gdop if subset_mode in {"best_gdop_per_position", "fixed_random_per_position"} else position_gdop(case.coords_mm, truth, ids)
                    gdop_values.append(gdop)
                frames_solved_total += len(solved_pts)
                if not solved_pts:
                    continue
                pts = np.vstack(solved_pts)
                median_pt = np.median(pts, axis=0)
                diff = median_pt - truth
                centered = pts - median_pt[None, :]
                err3 = float(np.linalg.norm(diff))
                pos_errors.append(err3)
                repeat = float(math.sqrt(np.mean(np.sum(centered * centered, axis=1))))
                repeatability.append(repeat)
                per_position.append(
                    {
                        "case_id": case.case_id,
                        "case_label": case.label,
                        "condition": condition,
                        "frame_keep_fraction": frame_keep,
                        "anchor_keep": anchor_keep,
                        "anchor_subset_mode": subset_mode,
                        "fixed_anchor_subset": "-".join(ANCHORS[i] for i in fixed_anchor_ids) if fixed_anchor_ids else "",
                        "subset_selection_criterion": (
                            "min range-only GDOP over C(8,4) subsets at the static truth coordinate"
                            if subset_mode == "best_gdop_per_position"
                            else (
                                "one seeded random 4-anchor subset per static truth position, fixed across frames"
                                if subset_mode == "fixed_random_per_position"
                                else ""
                            )
                        ),
                        "gdop_median": pct(gdop_values[-len(solved_pts):], 50),
                        "gdop_p95": pct(gdop_values[-len(solved_pts):], 95),
                        "ID": sid,
                        "frames_input_after_packet_drop": int(len(frames)),
                        "n_frames_attempted": int(len(frames)),
                        "frames_solved": int(len(solved_pts)),
                        "n_frames_failed": int(max(0, len(frames) - len(solved_pts))),
                        "solve_fraction": float(len(solved_pts) / len(frames)) if frames else 0.0,
                        "nonconvergence_rate": float(1.0 - len(solved_pts) / len(frames)) if frames else float("nan"),
                        "position_err_3d_mm": err3,
                        "sample_err_3d_p50_mm": pct([float(np.linalg.norm(p - truth)) for p in solved_pts], 50),
                        "sample_err_3d_p95_mm": pct([float(np.linalg.norm(p - truth)) for p in solved_pts], 95),
                        "repeatability_d3_std_mm": repeat,
                        "tag_truth_source": tag_truth_meta.get(sid, {}).get("tag_truth_source", ""),
                    }
                )
            summary_rows.append(
                {
                    "case_id": case.case_id,
                    "case_label": case.label,
                    "condition": condition,
                    "frame_keep_fraction": frame_keep,
                    "anchor_keep": anchor_keep,
                    "anchor_subset_mode": subset_mode,
                    "n_positions": len(pos_errors),
                    "frames_input_after_packet_drop_total": int(frames_in_total),
                    "n_frames_attempted": int(frames_in_total),
                    "frames_solved_total": int(frames_solved_total),
                    "n_frames_failed": int(max(0, frames_in_total - frames_solved_total)),
                    "solve_fraction": float(frames_solved_total / frames_in_total) if frames_in_total else 0.0,
                    "nonconvergence_rate": float(1.0 - frames_solved_total / frames_in_total) if frames_in_total else float("nan"),
                    "position_err_3d_p50_mm": pct(pos_errors, 50),
                    "position_err_3d_p95_mm": pct(pos_errors, 95),
                    "position_err_3d_rmse_mm": rmse(pos_errors),
                    "sample_err_3d_p50_mm": pct(sample_errors, 50),
                    "sample_err_3d_p95_mm": pct(sample_errors, 95),
                    "sample_err_3d_rmse_mm": rmse(sample_errors),
                    "repeatability_d3_std_median_mm": pct(repeatability, 50),
                    "gdop_median": pct(gdop_values, 50),
                    "gdop_p95": pct(gdop_values, 95),
                    "subset_selection_criterion": (
                        "min range-only GDOP over C(8,4)=70 subsets at each static truth coordinate, fixed across frames"
                        if subset_mode == "best_gdop_per_position"
                        else (
                            "one seeded random 4-anchor subset per static truth position, fixed across frames"
                            if subset_mode == "fixed_random_per_position"
                            else ""
                        )
                    ),
                    "stress_input_note": "static raw frame replay with synthetic frame packet-drop and anchor observation dropout; P50/P95 are conditional on convergence",
                }
            )
    return summary_rows, per_position


def roto_sample_dropout(cases: list[Case], *, seed: int) -> list[dict]:
    conditions = [
        ("solved_sample_keep_100", 1.00),
        ("solved_sample_keep_75", 0.75),
        ("solved_sample_keep_50", 0.50),
        ("solved_sample_keep_25", 0.25),
        ("solved_sample_keep_10", 0.10),
    ]
    rows: list[dict] = []
    for case_i, case in enumerate(cases):
        df = pd.read_csv(case.roto_samples)
        err_col = "err_3d_mm" if "err_3d_mm" in df.columns else "err3d_mm"
        for cond_i, (condition, keep_fraction) in enumerate(conditions):
            rng = np.random.default_rng(seed + 928371 * case_i + 101 * cond_i)
            sample_err = []
            rpe_err = []
            durations = []
            counts = []
            for (_cid, _tag), g0 in df.groupby(["capture_id", "tag"]):
                g = g0.sort_values("uwb_time_s").copy()
                if keep_fraction < 1.0:
                    keep = rng.random(len(g)) < keep_fraction
                    if not np.any(keep) and len(g):
                        keep[int(rng.integers(0, len(g)))] = True
                    g = g[keep]
                if g.empty:
                    continue
                sample_err.extend(g[err_col].to_numpy(float).tolist())
                t = g["uwb_time_s"].to_numpy(float)
                if t.size >= 2:
                    duration = float(np.nanmax(t) - np.nanmin(t))
                    if duration > 0:
                        durations.append(duration)
                        counts.append(int(t.size))
                    u = g[["uwb_x_mm", "uwb_y_vertical_mm", "uwb_z_mm"]].to_numpy(float)
                    o = g[["opti_x_mm", "opti_y_vertical_mm", "opti_z_mm"]].to_numpy(float)
                    du = np.diff(u, axis=0)
                    do = np.diff(o, axis=0)
                    rpe_err.extend(np.linalg.norm(du - do, axis=1).tolist())
            rows.append(
                {
                    "case_id": case.case_id,
                    "case_label": case.label,
                    "condition": condition,
                    "sample_keep_fraction": keep_fraction,
                    "drop_percent": 100.0 * (1.0 - keep_fraction),
                    "samples_kept": int(len(sample_err)),
                    "ate_p50_mm": pct(sample_err, 50),
                    "ate_p95_mm": pct(sample_err, 95),
                    "ate_rmse_mm": rmse(sample_err),
                    "rpe_rmse_mm": rmse(rpe_err),
                    "rpe_p95_mm": pct(rpe_err, 95),
                    "median_effective_update_rate_hz": pct(
                        [c / d for c, d in zip(counts, durations) if d > 0.0],
                        50,
                    ),
                    "stress_input_note": "ROTO solved-sample thinning; not a raw range re-solve",
                }
            )
    return rows


def build_report(
    layout_rows: list[dict],
    delay_rows: list[dict],
    static_rows: list[dict],
    roto_rows: list[dict],
    precision_rows: list[dict] | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# Resilience Gap Audit")
    lines.append("")
    lines.append(
        "This audit adds diagnostic coverage for three previously open gaps: AutoPos bootstrap numerical precision, "
        "layout-level residual delay correction numerical precision, and synthetic packet/dropout stress. It uses the four FULL comparison conditions."
    )
    lines.append("")
    lines.append("## Scope")
    lines.append("")
    lines.append("- Bootstrap input: raw inter-anchor range rows from `pairs_all.csv`, resampled within each unordered anchor pair.")
    lines.append("- Layout bootstrap: metric MDS from bootstrap pair medians, then the same four case-specific alignment/scale gauges.")
    lines.append("- Numerical-precision check: analytical per-pair median SE uses `1.2533 * std / sqrt(n)` and is compared to the existing layout bootstrap spread.")
    lines.append("- Delay bootstrap: additive layout-level residual delay correction differences relative to anchor A, interpreted as within-campaign median sampling SE rather than independent delay repeatability.")
    lines.append("- Static stress: raw static frames replayed through the T4 solver with synthetic frame packet-drop and anchor observation dropout.")
    lines.append("- ROTO stress: solved-sample thinning only; this measures dynamic ATE/RPE/update-rate sensitivity, not raw ROTO range re-solving.")
    lines.append("")
    lines.append("## Bootstrap Layout Numerical Precision")
    lines.append("")
    cols = [
        "case_id",
        "coordinate_sd_median_mm",
        "coordinate_sd_p95_mm",
        "pairwise_distance_sd_median_mm",
        "pairwise_distance_sd_p95_mm",
        "scale_factor_sd",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in layout_rows:
        lines.append(
            "| "
            + " | ".join(
                str(row[c]) if c == "case_id" else fmt(float(row[c]), 3 if "scale" in c else 2)
                for c in cols
            )
            + " |"
        )
    lines.append("")
    if precision_rows:
        lines.append("## Bootstrap Numerical Precision Check")
        lines.append("")
        cols = [
            "case_id",
            "pair_analytical_se_median_mm",
            "pair_analytical_se_p95_mm",
            "bootstrap_pairwise_distance_sd_median_mm",
            "bootstrap_coordinate_sd_median_mm",
            "pairwise_sd_over_median_se",
            "coordinate_sd_over_median_se",
        ]
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
        for row in precision_rows:
            vals = []
            for c in cols:
                vals.append(str(row[c]) if c == "case_id" else fmt(float(row[c]), 3))
            lines.append("| " + " | ".join(vals) + " |")
        lines.append("")
        lines.append(
            "The pair-level analytical median SE is sub-millimeter because each unordered anchor pair has about two thousand samples. "
            "The layout bootstrap SD is the propagated version of that within-campaign median sampling SE, with modest MDS/gauge amplification. "
            "It is therefore a numerical-precision diagnostic, not independent deployment repeatability."
        )
        lines.append("")
    lines.append("## Delay Bootstrap Numerical Precision")
    lines.append("")
    cols = [
        "case_id",
        "anchor_delay_rel_A_sd_median_mm",
        "anchor_delay_rel_A_sd_p95_mm",
        "anchor_delay_rel_A_sd_worst_mm",
        "pair_residual_rms_median_mm",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in delay_rows:
        lines.append(
            "| "
            + " | ".join(str(row[c]) if c == "case_id" else fmt(float(row[c]), 2) for c in cols)
            + " |"
        )
    lines.append("")
    lines.append("## Static Dropout Stress")
    lines.append("")
    baseline = [
        r
        for r in static_rows
        if r["condition"]
        in {
            "baseline_all_frames_all8",
            "anchor_keep_4",
            "anchor_keep_4_fixedrandom",
            "anchor_keep_4_bestgdop",
            "frame_keep_50_anchor_keep_4",
        }
    ]
    cols = [
        "case_id",
        "condition",
        "solve_fraction",
        "n_frames_attempted",
        "n_frames_failed",
        "nonconvergence_rate",
        "position_err_3d_p50_mm",
        "position_err_3d_p95_mm",
        "sample_err_3d_p50_mm",
        "sample_err_3d_p95_mm",
        "repeatability_d3_std_median_mm",
        "gdop_median",
    ]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in baseline:
        vals = []
        for c in cols:
            if c in {"case_id", "condition", "n_frames_attempted", "n_frames_failed"}:
                vals.append(str(row[c]))
            elif c in {"solve_fraction", "nonconvergence_rate"}:
                vals.append(fmt(float(row[c]), 3))
            else:
                vals.append(fmt(float(row[c]), 1))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    best_rows = [r for r in static_rows if r["condition"] == "anchor_keep_4_bestgdop"]
    random_rows = [r for r in static_rows if r["condition"] == "anchor_keep_4"]
    fixed_rows = [r for r in static_rows if r["condition"] == "anchor_keep_4_fixedrandom"]
    if best_rows and random_rows and fixed_rows:
        best_gdop = pct([r["gdop_median"] for r in best_rows], 50)
        random_gdop = pct([r["gdop_median"] for r in random_rows], 50)
        fixed_gdop = pct([r["gdop_median"] for r in fixed_rows], 50)
        best_p50 = pct([r["position_err_3d_p50_mm"] for r in best_rows], 50)
        random_p50 = pct([r["position_err_3d_p50_mm"] for r in random_rows], 50)
        fixed_p50 = pct([r["position_err_3d_p50_mm"] for r in fixed_rows], 50)
        best_fail = pct([r["nonconvergence_rate"] for r in best_rows], 50)
        fixed_fail = pct([r["nonconvergence_rate"] for r in fixed_rows], 50)
        lines.append(
            f"The `anchor_keep_4_bestgdop` control uses the requested criterion: for each static truth position it evaluates all C(8,4)=70 subsets, "
            f"computes range-only GDOP from unit-vector rows, picks the minimum-GDOP subset, and keeps that subset fixed across frames. "
            f"The fair fixed-vs-fixed comparison is `anchor_keep_4_fixedrandom` versus `anchor_keep_4_bestgdop`: median GDOP changes from "
            f"{fmt(fixed_gdop, 2)} to {fmt(best_gdop, 2)}, while position P50 changes from {fmt(fixed_p50)} mm to {fmt(best_p50)} mm "
            f"and median non-convergence changes from {fmt(100.0 * fixed_fail, 1)}% to {fmt(100.0 * best_fail, 1)}%. "
            f"The rotating random-per-frame keep-4 baseline remains useful but is not the clean control; its median position P50 is {fmt(random_p50)} mm "
            f"and median GDOP is {fmt(random_gdop, 2)}. P50/P95 values in this table are conditional on convergence; failed frames are exposed separately."
        )
        lines.append("")
    lines.append("## ROTO Solved-Sample Dropout")
    lines.append("")
    selected = [r for r in roto_rows if r["condition"] in {"solved_sample_keep_100", "solved_sample_keep_50", "solved_sample_keep_10"}]
    cols = ["case_id", "condition", "samples_kept", "ate_p50_mm", "ate_p95_mm", "rpe_rmse_mm", "median_effective_update_rate_hz"]
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in selected:
        vals = []
        for c in cols:
            vals.append(str(row[c]) if c in {"case_id", "condition", "samples_kept"} else fmt(float(row[c]), 1))
        lines.append("| " + " | ".join(vals) + " |")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append(
        "The bootstrap is a numerical-precision diagnostic, not a true repeated-deployment AutoPos split. "
        "After the analytical SE check, the layout-bootstrap and delay-bootstrap rows should be read as within-campaign numerical precision rather than deployment repeatability. "
        "Delay numbers are quoted as differences relative to anchor A because the absolute delay/common-mode gauge is not identifiable from ranges alone."
    )
    lines.append("")
    lines.append(
        "Static dropout is the strongest stress table here because it replays raw range frames through the solver. "
        "The best-GDOP keep-4 result shows that GDOP alone is not a safe runtime subset-selection criterion; a deployable 4-anchor policy needs layer diversity, root/mirror sanity checks, and residual gating. "
        "ROTO dropout is intentionally labelled as solved-sample thinning; a full raw dynamic range re-solve with dropout would be a heavier follow-up."
    )
    lines.append("")
    lines.append("## Output Tables")
    lines.append("")
    for name in [
        "bootstrap_layout_repeatability.csv",
        "bootstrap_pair_sampling_se.csv",
        "bootstrap_numerical_precision.csv",
        "bootstrap_anchor_repeatability.csv",
        "bootstrap_delay_sd.csv",
        "bootstrap_delay_per_anchor.csv",
        "static_dropout_stress_summary.csv",
        "static_dropout_stress_per_position.csv",
        "roto_sample_dropout_stress_summary.csv",
    ]:
        lines.append(f"- `../tables/{name}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run 4xFULL resilience/gap audit.")
    parser.add_argument("--n-bootstrap", type=int, default=300)
    parser.add_argument("--max-frames-per-position", type=int, default=220)
    parser.add_argument("--seed", type=int, default=20260604)
    parser.add_argument("--precision-only", action="store_true", help="update pair sampling-SE tables/report from existing bootstrap outputs")
    args = parser.parse_args()

    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if args.precision_only:
        pair_obs = load_pair_observations()
        pair_se = pair_sampling_se_rows(pair_obs)
        layout_rows = pd.read_csv(TABLE_DIR / "bootstrap_layout_repeatability.csv").to_dict("records")
        delay_rows = pd.read_csv(TABLE_DIR / "bootstrap_delay_sd.csv").to_dict("records")
        static_summary = pd.read_csv(TABLE_DIR / "static_dropout_stress_summary.csv").to_dict("records")
        roto_summary = pd.read_csv(TABLE_DIR / "roto_sample_dropout_stress_summary.csv").to_dict("records")
        precision_rows = bootstrap_numerical_precision_rows(layout_rows, pair_se)
        write_csv(TABLE_DIR / "bootstrap_pair_sampling_se.csv", pair_se)
        write_csv(TABLE_DIR / "bootstrap_numerical_precision.csv", precision_rows)
        report = build_report(layout_rows, delay_rows, static_summary, roto_summary, precision_rows)
        (REPORT_DIR / "RESILIENCE_GAP_AUDIT.md").write_text(report, encoding="utf-8")
        print(
            "Updated bootstrap numerical precision check: "
            f"median pair SE={fmt(precision_rows[0]['pair_analytical_se_median_mm'], 3)} mm, "
            f"p95 pair SE={fmt(precision_rows[0]['pair_analytical_se_p95_mm'], 3)} mm"
        )
        return

    ablation_mod = import_static_ablation_module()
    cases, truth_coords, tag_truth, tag_truth_meta, static_files = load_case_layouts(ablation_mod)
    layout_rows, delay_rows, anchor_rows, delay_anchor_rows = bootstrap_layout_and_delay(
        ablation_mod,
        cases,
        truth_coords,
        n_bootstrap=args.n_bootstrap,
        seed=args.seed,
    )
    pair_se = pair_sampling_se_rows(load_pair_observations())
    precision_rows = bootstrap_numerical_precision_rows(layout_rows, pair_se)
    static_summary, static_per_position = solve_static_dropout(
        ablation_mod,
        cases,
        tag_truth,
        tag_truth_meta,
        static_files,
        max_frames_per_position=args.max_frames_per_position,
        seed=args.seed + 9000,
    )
    roto_summary = roto_sample_dropout(cases, seed=args.seed + 19000)

    write_csv(TABLE_DIR / "bootstrap_layout_repeatability.csv", layout_rows)
    write_csv(TABLE_DIR / "bootstrap_pair_sampling_se.csv", pair_se)
    write_csv(TABLE_DIR / "bootstrap_numerical_precision.csv", precision_rows)
    write_csv(TABLE_DIR / "bootstrap_anchor_repeatability.csv", anchor_rows)
    write_csv(TABLE_DIR / "bootstrap_delay_sd.csv", delay_rows)
    write_csv(TABLE_DIR / "bootstrap_delay_per_anchor.csv", delay_anchor_rows)
    write_csv(TABLE_DIR / "static_dropout_stress_summary.csv", static_summary)
    write_csv(TABLE_DIR / "static_dropout_stress_per_position.csv", static_per_position)
    write_csv(TABLE_DIR / "roto_sample_dropout_stress_summary.csv", roto_summary)
    report = build_report(layout_rows, delay_rows, static_summary, roto_summary, precision_rows)
    (REPORT_DIR / "RESILIENCE_GAP_AUDIT.md").write_text(report, encoding="utf-8")

    best_static = sorted(
        [r for r in static_summary if r["condition"] == "baseline_all_frames_all8"],
        key=lambda r: (float(r["position_err_3d_p50_mm"]), float(r["position_err_3d_p95_mm"])),
    )
    print("Resilience gap audit complete.")
    for row in best_static:
        print(
            f"{row['case_id']}: static baseline position P50/P95="
            f"{fmt(row['position_err_3d_p50_mm'])}/{fmt(row['position_err_3d_p95_mm'])} mm, "
            f"repeat={fmt(row['repeatability_d3_std_median_mm'])} mm"
        )
    print(f"Wrote {REPORT_DIR / 'RESILIENCE_GAP_AUDIT.md'}")


if __name__ == "__main__":
    main()
