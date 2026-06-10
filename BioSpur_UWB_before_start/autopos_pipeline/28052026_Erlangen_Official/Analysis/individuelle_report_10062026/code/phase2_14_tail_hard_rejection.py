#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from phase2_6_diagnostics_closure import find_static_capture_dirs, load_offline_solver
from phase2_7_final_closure import fit_similarity, fit_tag_model_generic, summarize_errors, tag_error_rows, write_csv_rows
from phase2_8_runtime_correction import make_vb_zero_delay_layout
from phase2_9_estimated_distance_runtime import corrected_frames_additive, load_layout_coords
from phase2_solver_ablation import load_primary_vicon_anchor_truth, load_sweep_deltas
from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import load_phase1_data, tag_coord_map


ALL_ANCHORS = tuple(range(8))
THRESH_VALUES = (1.3, 1.5, 2.0)
GUARD_VALUES = (1.5, 2.0, 3.0)
TAU_VALUES = (2.5, 3.0, 4.0)
CIR_WATCHLIST_ANCHORS = {"C", "E", "F", "G", "H"}
DOMINANT_DROP_FRACTION = 0.05


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 2.14 P95 tail decomposition and offline hard-link rejection.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def subset_key(subset: tuple[int, ...]) -> str:
    return "".join(ANCHOR_LABELS[i] for i in subset)


def parse_subset_key(key: str) -> tuple[int, ...]:
    return tuple(ANCHOR_LABELS.index(ch) for ch in key)


def all_subsets() -> list[tuple[int, ...]]:
    out = [ALL_ANCHORS]
    out.extend(tuple(c) for c in itertools.combinations(ALL_ANCHORS, 7))
    out.extend(tuple(c) for c in itertools.combinations(ALL_ANCHORS, 6))
    return out


def filter_frame(frame: Any, subset: tuple[int, ...], Frame: Any, Observation: Any) -> Any:
    keep = set(subset)
    obs = [
        Observation(item.anchor_id, float(item.range_mm), item.quality_percent, item.status)
        for item in frame.observations
        if item.anchor_id in keep
    ]
    return Frame(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=tuple(obs),
        imu=frame.imu,
    )


def median_frame(frames: list[Any], subset: tuple[int, ...], Frame: Any, Observation: Any) -> Any:
    rows: list[Any] = []
    for aid in subset:
        vals = [
            float(item.range_mm)
            for frame in frames
            for item in frame.observations
            if item.anchor_id == aid and math.isfinite(float(item.range_mm)) and float(item.range_mm) > 0.0
        ]
        if vals:
            rows.append(Observation(aid, float(np.nanmedian(vals)), 100.0, "O"))
    return Frame(tag="BSF66F", sweep=0, host_elapsed_s=0.0, host_epoch_s=0.0, observations=tuple(rows), imu=None)


def layout_xyz(layout: Any) -> np.ndarray:
    return np.asarray([[layout.anchors[i].x_mm, layout.anchors[i].y_mm, layout.anchors[i].z_mm] for i in range(8)], dtype=float)


def layout_sigmas(layout: Any) -> np.ndarray:
    return np.asarray([max(5.0, float(layout.anchors[i].sigma_mm)) for i in range(8)], dtype=float)


def layout_delays(layout: Any) -> np.ndarray:
    return np.asarray([float(layout.anchors[i].d_anchor_mm) for i in range(8)], dtype=float)


def vertical_gdop(point: np.ndarray, subset: tuple[int, ...], anchors_xyz: np.ndarray, sigmas: np.ndarray) -> tuple[float, float]:
    rows = []
    for aid in subset:
        diff = point - anchors_xyz[aid]
        dist = float(np.linalg.norm(diff))
        if dist <= 1e-9:
            continue
        rows.append(diff / dist / max(5.0, float(sigmas[aid])))
    if len(rows) < 3:
        return float("inf"), float("inf")
    j = np.asarray(rows, dtype=float)
    normal = j.T @ j
    try:
        inv = np.linalg.pinv(normal)
        vg = float(math.sqrt(max(inv[1, 1], 0.0)))
        cond = float(np.linalg.cond(normal))
    except np.linalg.LinAlgError:
        return float("inf"), float("inf")
    return vg, cond


def score_result(result: Any, subset: tuple[int, ...], anchors_xyz: np.ndarray, sigmas: np.ndarray) -> dict:
    point = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
    norm_by_anchor = {}
    sum_norm2 = 0.0
    for aid, residual in result.residuals_by_anchor.items():
        sigma = max(5.0, float(sigmas[int(aid)]))
        val = float(residual) / sigma
        norm_by_anchor[int(aid)] = val
        sum_norm2 += val * val
    n = int(len(norm_by_anchor))
    dof = max(n - 3, 1)
    score = float(math.sqrt(sum_norm2 / dof))
    if norm_by_anchor:
        max_aid = max(norm_by_anchor, key=lambda a: abs(norm_by_anchor[a]))
        max_abs = float(abs(norm_by_anchor[max_aid]))
    else:
        max_aid = -1
        max_abs = float("nan")
    vg, cond = vertical_gdop(point, tuple(sorted(norm_by_anchor)), anchors_xyz, sigmas)
    return {
        "point": point,
        "score": score,
        "sum_norm2": float(sum_norm2),
        "dof": int(dof),
        "n_obs": n,
        "vgdop_y_mm": vg,
        "normal_cond": cond,
        "max_norm_anchor": int(max_aid),
        "max_norm_abs": max_abs,
        "norm_by_anchor": norm_by_anchor,
        "residuals_by_anchor": {int(k): float(v) for k, v in result.residuals_by_anchor.items()},
    }


def solve_subset_sequence(
    frames: list[Any],
    subset: tuple[int, ...],
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    anchors_xyz: np.ndarray,
    sigmas: np.ndarray,
) -> list[dict | None]:
    solver = Solver(layout, SolverConfig(method="T4"))
    out: list[dict | None] = []
    for idx, frame in enumerate(frames):
        filtered = filter_frame(frame, subset, Frame, Observation)
        result = solver.solve_frame(filtered)
        if result is None:
            out.append(None)
            continue
        row = score_result(result, subset, anchors_xyz, sigmas)
        row["frame_index"] = idx
        row["subset_key"] = subset_key(subset)
        row["anchors_input"] = int(result.anchors_input)
        row["anchors_used"] = int(result.anchors_used)
        row["residual_rms_mm"] = float(result.residual_rms_mm)
        out.append(row)
    return out


def sequence_point(rows: list[dict | None]) -> np.ndarray:
    pts = [r["point"] for r in rows if r is not None and np.all(np.isfinite(r["point"]))]
    if not pts:
        return np.full(3, np.nan)
    return np.nanmean(np.asarray(pts, dtype=float), axis=0)


def sequence_score(rows: list[dict | None]) -> float:
    sum_norm2 = 0.0
    dof = 0
    for row in rows:
        if row is None:
            continue
        sum_norm2 += float(row["sum_norm2"])
        dof += int(row["dof"])
    if dof <= 0:
        return float("inf")
    return float(math.sqrt(sum_norm2 / dof))


def sequence_frames_solved(rows: list[dict | None]) -> int:
    return int(sum(row is not None for row in rows))


def mean_geometry(rows: list[dict | None], subset: tuple[int, ...], anchors_xyz: np.ndarray, sigmas: np.ndarray) -> tuple[float, float]:
    point = sequence_point(rows)
    if not np.all(np.isfinite(point)):
        return float("inf"), float("inf")
    return vertical_gdop(point, subset, anchors_xyz, sigmas)


def point_error(point: np.ndarray, transform: Any, truth: np.ndarray) -> dict:
    aligned = transform(point[None, :])[0]
    diff = aligned - truth
    return {
        "aligned_x_mm": float(aligned[0]),
        "aligned_y_vertical_mm": float(aligned[1]),
        "aligned_z_mm": float(aligned[2]),
        "err_x_mm": float(diff[0]),
        "err_y_vertical_mm": float(diff[1]),
        "err_z_mm": float(diff[2]),
        "err_horizontal_mm": float(math.hypot(diff[0], diff[2])),
        "err_vertical_mm": float(abs(diff[1])),
        "err_3d_mm": float(np.linalg.norm(diff)),
    }


def selector_mode_b1(thresh: float, guard: float) -> str:
    return f"B1_session_t{thresh:g}_g{guard:g}"


def selector_mode_b2(tau: float, thresh: float, guard: float) -> str:
    return f"B2_frame_greedy_tau{tau:g}_t{thresh:g}_g{guard:g}"


def selector_mode_b3(thresh: float, guard: float) -> str:
    return f"B3_frame_exhaustive_t{thresh:g}_g{guard:g}"


def session_selector(
    cache: dict[str, list[dict | None]],
    thresh: float,
    guard: float,
    anchors_xyz: np.ndarray,
    sigmas: np.ndarray,
) -> tuple[tuple[int, ...], dict]:
    full_key = subset_key(ALL_ANCHORS)
    full_rows = cache[full_key]
    full_vg, _full_cond = mean_geometry(full_rows, ALL_ANCHORS, anchors_xyz, sigmas)
    current = ALL_ANCHORS
    current_score = sequence_score(cache[full_key])
    decisions = []
    for step in range(2):
        candidates = []
        for aid in current:
            subset = tuple(a for a in current if a != aid)
            if len(subset) < 6:
                continue
            key = subset_key(subset)
            rows = cache.get(key)
            if rows is None:
                continue
            score = sequence_score(rows)
            vg, cond = mean_geometry(rows, subset, anchors_xyz, sigmas)
            ok = bool(score < current_score / thresh and vg <= full_vg * guard)
            candidates.append((ok, score, vg, cond, aid, subset))
        valid = [c for c in candidates if c[0]]
        if not valid:
            break
        _ok, score, vg, cond, aid, subset = min(valid, key=lambda c: c[1])
        decisions.append(
            {
                "step": step + 1,
                "dropped_anchor": ANCHOR_LABELS[aid],
                "score_before": current_score,
                "score_after": score,
                "vgdop_y_after_mm": vg,
                "normal_cond_after": cond,
            }
        )
        current = subset
        current_score = score
    final_rows = cache[subset_key(current)]
    return current, {
        "final_subset": subset_key(current),
        "dropped_links": ",".join(ANCHOR_LABELS[a] for a in ALL_ANCHORS if a not in current),
        "score": float(sequence_score(final_rows)),
        "frames_solved": sequence_frames_solved(final_rows),
        "decisions": decisions,
    }


def choose_b2_frame(
    frame_cache: dict[str, dict],
    tau: float,
    thresh: float,
    guard: float,
) -> tuple[str, dict | None]:
    full_key = subset_key(ALL_ANCHORS)
    current_key = full_key
    current = frame_cache.get(current_key)
    full = current
    if current is None:
        return current_key, current
    full_vg = float(full["vgdop_y_mm"])
    for _step in range(2):
        if current is None or float(current["max_norm_abs"]) <= tau:
            break
        aid = int(current["max_norm_anchor"])
        subset = tuple(a for a in parse_subset_key(current_key) if a != aid)
        if len(subset) < 6:
            break
        cand_key = subset_key(subset)
        cand = frame_cache.get(cand_key)
        if cand is None:
            break
        if float(cand["score"]) < float(current["score"]) / thresh and float(cand["vgdop_y_mm"]) <= full_vg * guard:
            current_key = cand_key
            current = cand
        else:
            break
    return current_key, current


def choose_b3_frame(frame_cache: dict[str, dict], thresh: float, guard: float) -> tuple[str, dict | None]:
    full_key = subset_key(ALL_ANCHORS)
    full = frame_cache.get(full_key)
    if full is None:
        return full_key, full
    full_score = float(full["score"])
    full_vg = float(full["vgdop_y_mm"])
    best_key = full_key
    best = full
    for key, row in frame_cache.items():
        if key == full_key or row is None:
            continue
        if float(row["score"]) < full_score / thresh and float(row["vgdop_y_mm"]) <= full_vg * guard:
            if float(row["score"]) < float(best["score"]):
                best_key = key
                best = row
    return best_key, best


def frame_cache_by_index(cache: dict[str, list[dict | None]]) -> list[dict[str, dict]]:
    n = max(len(rows) for rows in cache.values())
    out: list[dict[str, dict]] = []
    for idx in range(n):
        row = {}
        for key, rows in cache.items():
            if idx < len(rows) and rows[idx] is not None:
                row[key] = rows[idx]
        out.append(row)
    return out


def point_from_selected_frames(selected: list[dict | None]) -> np.ndarray:
    pts = [row["point"] for row in selected if row is not None]
    if not pts:
        return np.full(3, np.nan)
    return np.nanmean(np.asarray(pts, dtype=float), axis=0)


def drop_counts_from_keys(keys: list[str], frames: list[Any]) -> dict[int, int]:
    counts = {aid: 0 for aid in ALL_ANCHORS}
    for key, frame in zip(keys, frames):
        chosen = set(parse_subset_key(key))
        observed = {int(item.anchor_id) for item in frame.observations}
        for aid in observed - chosen:
            counts[aid] += 1
    return counts


def dominant_drop_set(counts: dict[int, int], frames_total: int) -> set[int]:
    if frames_total <= 0:
        return set()
    return {aid for aid, count in counts.items() if count / frames_total >= DOMINANT_DROP_FRACTION}


def solve_oracle_median_subsets(
    frames: list[Any],
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    transform: Any,
    truth: np.ndarray,
) -> tuple[list[dict], dict]:
    rows = []
    for subset in all_subsets():
        frame = median_frame(frames, subset, Frame, Observation)
        solver = Solver(layout, SolverConfig(method="T4"))
        result = solver.solve_frame(frame)
        if result is None:
            continue
        point = np.asarray([result.x_mm, result.y_mm, result.z_mm], dtype=float)
        err = point_error(point, transform, truth)
        row = {
            "subset": subset_key(subset),
            "kept_n": len(subset),
            "dropped_links": ",".join(ANCHOR_LABELS[a] for a in ALL_ANCHORS if a not in subset),
            "frames_solved": 1,
            **err,
        }
        rows.append(row)
    best = min(rows, key=lambda r: float(r["err_3d_mm"])) if rows else {}
    return rows, best


def per_link_tail_rows(
    position: str,
    frames: list[Any],
    solved_point: np.ndarray,
    anchors_xyz: np.ndarray,
    anchor_truth: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    top12_keys: set[tuple[str, str]],
) -> list[dict]:
    rows = []
    for aid, anchor in enumerate(ANCHOR_LABELS):
        vals = [
            float(item.range_mm)
            for frame in frames
            for item in frame.observations
            if item.anchor_id == aid and math.isfinite(float(item.range_mm)) and float(item.range_mm) > 0.0
        ]
        if not vals:
            continue
        arr = np.asarray(vals, dtype=float)
        med = float(np.nanmedian(arr))
        std = float(np.nanstd(arr, ddof=1)) if len(arr) > 1 else float("nan")
        vicon_d = float(np.linalg.norm(tag_truth[position] - anchor_truth[anchor]))
        solved_d = float(np.linalg.norm(solved_point - anchors_xyz[aid]))
        rows.append(
            {
                "position": position,
                "anchor": anchor,
                "n": int(len(arr)),
                "corrected_median_range_mm": med,
                "vicon_distance_mm": vicon_d,
                "range_minus_vicon_mm": med - vicon_d,
                "distance_to_solved_position_mm": solved_d,
                "range_minus_solved_distance_mm": med - solved_d,
                "link_noise_std_mm": std,
                "in_top12_abs_bias": bool((position, anchor) in top12_keys),
            }
        )
    return rows


def summarize_position_rows(rows: list[dict], mode_col: str = "method") -> list[dict]:
    summary = summarize_errors(rows, group_cols=[mode_col])
    out = []
    for row in summary:
        row["mode"] = row.pop(mode_col)
        out.append(row)
    return out


def choose_best_mode(summary_rows: list[dict], family_prefix: str, headline_median: float) -> dict:
    candidates = [r for r in summary_rows if str(r["mode"]).startswith(family_prefix)]
    if not candidates:
        raise ValueError(f"No candidates for {family_prefix}")
    accepted = [r for r in candidates if float(r["median_3d_mm"]) <= headline_median + 5.0]
    pool = accepted if accepted else candidates
    return min(pool, key=lambda r: (float(r["p95_3d_mm"]), float(r["rmse_3d_mm"]), float(r["median_3d_mm"])))


def parse_selector_params(mode: str) -> dict:
    # Mode strings are deterministic and compact; parsing with splits keeps the report reproducible.
    if mode.startswith("B1_session_"):
        tail = mode.replace("B1_session_t", "")
        t, g = tail.split("_g")
        return {"family": "B1", "thresh": float(t), "guard": float(g)}
    if mode.startswith("B2_frame_greedy_"):
        tail = mode.replace("B2_frame_greedy_tau", "")
        tau, rest = tail.split("_t")
        t, g = rest.split("_g")
        return {"family": "B2", "tau": float(tau), "thresh": float(t), "guard": float(g)}
    if mode.startswith("B3_frame_exhaustive_"):
        tail = mode.replace("B3_frame_exhaustive_t", "")
        t, g = tail.split("_g")
        return {"family": "B3", "thresh": float(t), "guard": float(g)}
    raise ValueError(mode)


def apply_best_selector_from_cache(
    cache: dict[str, list[dict | None]],
    frames: list[Any],
    params: dict,
    anchors_xyz: np.ndarray,
    sigmas: np.ndarray,
) -> tuple[np.ndarray, dict, dict[int, int]]:
    family = params["family"]
    if family == "B1":
        final_subset, info = session_selector(cache, float(params["thresh"]), float(params["guard"]), anchors_xyz, sigmas)
        rows = cache[subset_key(final_subset)]
        counts = {aid: (len(frames) if aid not in final_subset else 0) for aid in ALL_ANCHORS}
        return sequence_point(rows), info, counts

    by_frame = frame_cache_by_index(cache)
    selected = []
    selected_keys = []
    if family == "B2":
        for fc in by_frame:
            key, row = choose_b2_frame(fc, float(params["tau"]), float(params["thresh"]), float(params["guard"]))
            selected_keys.append(key)
            selected.append(row)
    elif family == "B3":
        for fc in by_frame:
            key, row = choose_b3_frame(fc, float(params["thresh"]), float(params["guard"]))
            selected_keys.append(key)
            selected.append(row)
    else:
        raise ValueError(family)
    counts = drop_counts_from_keys(selected_keys, frames)
    info = {
        "final_subset": "frame_variable",
        "dropped_links": ",".join(
            f"{ANCHOR_LABELS[aid]}:{count}" for aid, count in counts.items() if count > 0
        ),
        "frames_solved": int(sum(row is not None for row in selected)),
        "score": float("nan"),
    }
    return point_from_selected_frames(selected), info, counts


def run_position_headline(
    *,
    position: str,
    frames: list[Any],
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    link_df: pd.DataFrame,
    sweep_delta: dict[str, float],
    transform: Any,
    tag_truth: dict[str, np.ndarray],
    anchor_truth: dict[str, np.ndarray],
    top12_keys: set[tuple[str, str]],
    worst6: set[str],
) -> dict:
    train = link_df[link_df["position"].astype(str) != position].reset_index(drop=True)
    fit = fit_tag_model_generic(f"phase2_14_additive_loo_without_{position}", train, sweep_delta, [])
    additive = 0.5 * fit.anchor_deltas_mm + 0.5 * float(fit.delta_tag_mm)
    corrected = corrected_frames_additive(frames, additive, Frame, Observation)
    anchors_xyz = layout_xyz(layout)
    sigmas = layout_sigmas(layout)
    cache = {
        subset_key(subset): solve_subset_sequence(
            corrected,
            subset,
            layout,
            Solver,
            SolverConfig,
            Frame,
            Observation,
            anchors_xyz,
            sigmas,
        )
        for subset in all_subsets()
    }

    full_key = subset_key(ALL_ANCHORS)
    headline_point = sequence_point(cache[full_key])
    position_rows = [
        {
            "method": "headline_additive_only",
            "registration": "anchor_only_3d_rigid",
            "position": position,
            **point_error(headline_point, transform, tag_truth[position]),
        }
    ]
    selector_info_rows = []
    drop_count_rows = []

    for thresh in THRESH_VALUES:
        for guard in GUARD_VALUES:
            mode = selector_mode_b1(thresh, guard)
            subset, info = session_selector(cache, thresh, guard, anchors_xyz, sigmas)
            point = sequence_point(cache[subset_key(subset)])
            position_rows.append({"method": mode, "registration": "anchor_only_3d_rigid", "position": position, **point_error(point, transform, tag_truth[position])})
            selector_info_rows.append({"mode": mode, "position": position, **info})
            for aid in ALL_ANCHORS:
                if aid not in subset:
                    drop_count_rows.append({"mode": mode, "position": position, "anchor": ANCHOR_LABELS[aid], "dropped_frames": len(frames), "frames": len(frames)})

    by_frame = frame_cache_by_index(cache)
    for tau in TAU_VALUES:
        for thresh in THRESH_VALUES:
            for guard in GUARD_VALUES:
                mode = selector_mode_b2(tau, thresh, guard)
                selected = []
                selected_keys = []
                for fc in by_frame:
                    key, row = choose_b2_frame(fc, tau, thresh, guard)
                    selected_keys.append(key)
                    selected.append(row)
                point = point_from_selected_frames(selected)
                position_rows.append({"method": mode, "registration": "anchor_only_3d_rigid", "position": position, **point_error(point, transform, tag_truth[position])})
                counts = drop_counts_from_keys(selected_keys, corrected)
                selector_info_rows.append(
                    {
                        "mode": mode,
                        "position": position,
                        "final_subset": "frame_variable",
                        "dropped_links": ",".join(f"{ANCHOR_LABELS[a]}:{c}" for a, c in counts.items() if c > 0),
                        "frames_solved": int(sum(row is not None for row in selected)),
                        "score": float("nan"),
                    }
                )
                for aid, count in counts.items():
                    if count > 0:
                        drop_count_rows.append({"mode": mode, "position": position, "anchor": ANCHOR_LABELS[aid], "dropped_frames": count, "frames": len(frames)})

    for thresh in THRESH_VALUES:
        for guard in GUARD_VALUES:
            mode = selector_mode_b3(thresh, guard)
            selected = []
            selected_keys = []
            for fc in by_frame:
                key, row = choose_b3_frame(fc, thresh, guard)
                selected_keys.append(key)
                selected.append(row)
            point = point_from_selected_frames(selected)
            position_rows.append({"method": mode, "registration": "anchor_only_3d_rigid", "position": position, **point_error(point, transform, tag_truth[position])})
            counts = drop_counts_from_keys(selected_keys, corrected)
            selector_info_rows.append(
                {
                    "mode": mode,
                    "position": position,
                    "final_subset": "frame_variable",
                    "dropped_links": ",".join(f"{ANCHOR_LABELS[a]}:{c}" for a, c in counts.items() if c > 0),
                    "frames_solved": int(sum(row is not None for row in selected)),
                    "score": float("nan"),
                }
            )
            for aid, count in counts.items():
                if count > 0:
                    drop_count_rows.append({"mode": mode, "position": position, "anchor": ANCHOR_LABELS[aid], "dropped_frames": count, "frames": len(frames)})

    oracle_rows, oracle_best = solve_oracle_median_subsets(corrected, layout, Solver, SolverConfig, Frame, Observation, transform, tag_truth[position])
    oracle_best_row = {
        "method": "oracle_best_subset_median_range",
        "registration": "anchor_only_3d_rigid",
        "position": position,
        **{k: oracle_best[k] for k in oracle_best if k.startswith("err_") or k.startswith("aligned_")},
    }
    position_rows.append(oracle_best_row)
    oracle_rows = [{"position": position, **row} for row in oracle_rows]
    oracle_best = {"position": position, **oracle_best}

    per_link_rows = []
    if position in worst6:
        per_link_rows = per_link_tail_rows(
            position,
            corrected,
            headline_point,
            anchors_xyz,
            anchor_truth,
            tag_truth,
            top12_keys,
        )

    fit_row = {
        "position": position,
        "delta_tag_mm": float(fit.delta_tag_mm),
        "train_rms_mm": float(fit.rms_mm),
        "train_links": int(fit.n_links),
        "additive_min_mm": float(np.min(additive)),
        "additive_max_mm": float(np.max(additive)),
    }
    return {
        "position_rows": position_rows,
        "selector_info_rows": selector_info_rows,
        "drop_count_rows": drop_count_rows,
        "oracle_rows": oracle_rows,
        "oracle_best": oracle_best,
        "per_link_rows": per_link_rows,
        "fit_row": fit_row,
    }


def run_position_production_best(
    *,
    position: str,
    frames: list[Any],
    layout: Any,
    Solver: Any,
    SolverConfig: Any,
    Frame: Any,
    Observation: Any,
    transform: Any,
    tag_truth: dict[str, np.ndarray],
    best_params: dict,
) -> tuple[dict, list[dict]]:
    anchors_xyz = layout_xyz(layout)
    sigmas = layout_sigmas(layout)
    cache = {
        subset_key(subset): solve_subset_sequence(
            frames,
            subset,
            layout,
            Solver,
            SolverConfig,
            Frame,
            Observation,
            anchors_xyz,
            sigmas,
        )
        for subset in all_subsets()
    }
    point, info, counts = apply_best_selector_from_cache(cache, frames, best_params, anchors_xyz, sigmas)
    row = {
        "method": "production_baseline_with_best_selector",
        "registration": "anchor_only_3d_rigid",
        "position": position,
        **point_error(point, transform, tag_truth[position]),
    }
    drops = [
        {
            "mode": "production_baseline_with_best_selector",
            "position": position,
            "anchor": ANCHOR_LABELS[aid],
            "dropped_frames": count,
            "frames": len(frames),
        }
        for aid, count in counts.items()
        if count > 0
    ]
    return row, drops


def run_position_headline_process(payload: tuple[str, str, str, tuple[str, ...]]) -> dict:
    data_dir_s, out_dir_s, position, worst6_tuple = payload
    data_dir = Path(data_dir_s)
    out_dir = Path(out_dir_s)
    tables_dir = out_dir / "tables"

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    top12_df = pd.read_csv(tables_dir / "04_tag_side_top12_abs_bias_links.csv")
    top12_keys = {(str(r.position), str(r.anchor)) for r in top12_df.itertuples(index=False)}

    read_tr_all_frames, Solver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    frames = read_tr_all_frames(capture_dirs[position], tags={"BSF66F"}, min_anchors=4)
    layout_path = make_vb_zero_delay_layout(out_dir)
    sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    layout = load_layout_json(layout_path, sigma_path)
    coords = load_layout_coords(layout_path)
    truth_xyz = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    rigid = fit_similarity(coords, truth_xyz, allow_reflection=True, allow_scale=False)

    return run_position_headline(
        position=position,
        frames=frames,
        layout=layout,
        Solver=Solver,
        SolverConfig=SolverConfig,
        Frame=Frame,
        Observation=Observation,
        link_df=link_df,
        sweep_delta=sweep_delta,
        transform=rigid.apply,
        tag_truth=tag_truth,
        anchor_truth=primary_truth,
        top12_keys=top12_keys,
        worst6=set(worst6_tuple),
    )


def run_position_production_best_process(payload: tuple[str, str, str, dict]) -> tuple[dict, list[dict]]:
    data_dir_s, out_dir_s, position, best_params = payload
    data_dir = Path(data_dir_s)
    out_dir = Path(out_dir_s)
    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)

    read_tr_all_frames, Solver, load_layout_json, SolverConfig, Frame, Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    frames = read_tr_all_frames(capture_dirs[position], tags={"BSF66F"}, min_anchors=4)

    production_layout_path = data_dir / "solver" / "work" / "field_dataset_staged" / "FULL-COMPARE-1000-production-T4-real" / "v4-io" / "layout.json"
    production_sigma_path = data_dir / "solver" / "work" / "field_dataset_staged" / "FULL-COMPARE-1000-production-T4-real" / "tables" / "anchor_sigma.json"
    if not production_layout_path.exists():
        production_layout_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "v4-io" / "layout.json"
        production_sigma_path = data_dir / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"
    production_layout = load_layout_json(production_layout_path, production_sigma_path)
    prod_coords = load_layout_coords(production_layout_path)
    truth_xyz = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)
    prod_rigid = fit_similarity(prod_coords, truth_xyz, allow_reflection=True, allow_scale=False)

    return run_position_production_best(
        position=position,
        frames=frames,
        layout=production_layout,
        Solver=Solver,
        SolverConfig=SolverConfig,
        Frame=Frame,
        Observation=Observation,
        transform=prod_rigid.apply,
        tag_truth=tag_truth,
        best_params=best_params,
    )


def improvement_rows(position_rows: list[dict], modes: list[str], headline_mode: str = "headline_additive_only") -> tuple[list[dict], list[dict]]:
    df = pd.DataFrame(position_rows)
    base = df[df["method"] == headline_mode].set_index("position")["err_3d_mm"].astype(float).to_dict()
    summary = []
    worse_rows = []
    for mode in modes:
        sub = df[df["method"] == mode].copy()
        deltas = []
        worse = 0
        for row in sub.itertuples(index=False):
            delta = float(row.err_3d_mm) - float(base[str(row.position)])
            deltas.append(delta)
            if delta > 0:
                worse += 1
                worse_rows.append(
                    {
                        "mode": mode,
                        "position": str(row.position),
                        "headline_err_3d_mm": float(base[str(row.position)]),
                        "selector_err_3d_mm": float(row.err_3d_mm),
                        "delta_vs_headline_mm": delta,
                    }
                )
        arr = np.asarray(deltas, dtype=float)
        summary.append(
            {
                "mode": mode,
                "positions": int(len(arr)),
                "improved_vs_headline": int(np.sum(arr < 0)),
                "worse_or_equal_vs_headline": int(np.sum(arr >= 0)),
                "median_delta_vs_headline_mm": float(np.nanmedian(arr)),
                "max_worse_delta_mm": float(np.nanmax(arr)),
                "max_better_delta_mm": float(np.nanmin(arr)),
            }
        )
    worse_rows.sort(key=lambda r: (-float(r["delta_vs_headline_mm"]), r["mode"], r["position"]))
    return summary, worse_rows


def oracle_audit_rows(
    oracle_best_rows: list[dict],
    drop_count_rows: list[dict],
    modes: list[str],
) -> tuple[list[dict], list[dict]]:
    oracle = {
        str(row["position"]): {ANCHOR_LABELS.index(ch) for ch in str(row.get("dropped_links", "")).replace(",", "") if ch in ANCHOR_LABELS}
        for row in oracle_best_rows
    }
    drop_df = pd.DataFrame(drop_count_rows)
    rows = []
    summary = []
    for mode in modes:
        false_total = 0
        miss_total = 0
        dropped_total = 0
        oracle_total = 0
        for pos, oracle_drop in oracle.items():
            sub = drop_df[(drop_df["mode"].astype(str) == mode) & (drop_df["position"].astype(str) == pos)] if not drop_df.empty else pd.DataFrame()
            if sub.empty:
                selector_drop = set()
            else:
                frames = float(sub["frames"].max())
                selector_drop = {
                    ANCHOR_LABELS.index(str(r.anchor))
                    for r in sub.itertuples(index=False)
                    if frames > 0 and float(r.dropped_frames) / frames >= DOMINANT_DROP_FRACTION
                }
            false = selector_drop - oracle_drop
            miss = oracle_drop - selector_drop
            rows.append(
                {
                    "mode": mode,
                    "position": pos,
                    "oracle_dropped": ",".join(ANCHOR_LABELS[a] for a in sorted(oracle_drop)),
                    "selector_dominant_dropped": ",".join(ANCHOR_LABELS[a] for a in sorted(selector_drop)),
                    "false_dropped": ",".join(ANCHOR_LABELS[a] for a in sorted(false)),
                    "missed_oracle_drop": ",".join(ANCHOR_LABELS[a] for a in sorted(miss)),
                    "false_drop_count": int(len(false)),
                    "miss_count": int(len(miss)),
                }
            )
            false_total += len(false)
            miss_total += len(miss)
            dropped_total += len(selector_drop)
            oracle_total += len(oracle_drop)
        summary.append(
            {
                "mode": mode,
                "positions": int(len(oracle)),
                "selector_dropped_total": int(dropped_total),
                "oracle_dropped_total": int(oracle_total),
                "false_drop_total": int(false_total),
                "miss_total": int(miss_total),
                "false_drop_rate": float(false_total / dropped_total) if dropped_total else float("nan"),
                "miss_rate": float(miss_total / oracle_total) if oracle_total else float("nan"),
            }
        )
    return rows, summary


def crossref_drop_rows(
    drop_count_rows: list[dict],
    modes: list[str],
    oracle_best_rows: list[dict],
    top12_keys: set[tuple[str, str]],
) -> list[dict]:
    oracle = {
        str(row["position"]): {ch for ch in str(row.get("dropped_links", "")).replace(",", "") if ch in ANCHOR_LABELS}
        for row in oracle_best_rows
    }
    rows = []
    for row in drop_count_rows:
        mode = str(row["mode"])
        if mode not in modes:
            continue
        frames = float(row.get("frames", 0.0))
        pct = float(row["dropped_frames"]) / frames * 100.0 if frames > 0 else float("nan")
        if pct < DOMINANT_DROP_FRACTION * 100.0:
            continue
        pos = str(row["position"])
        anchor = str(row["anchor"])
        rows.append(
            {
                "mode": mode,
                "position": pos,
                "anchor": anchor,
                "dropped_frames": int(row["dropped_frames"]),
                "frames": int(row["frames"]),
                "dropped_frame_percent": pct,
                "oracle_dropped_this_link": bool(anchor in oracle.get(pos, set())),
                "in_top12_abs_bias": bool((pos, anchor) in top12_keys),
                "high_noise_FG_anchor": bool(anchor in {"F", "G"}),
                "cir_watchlist_anchor_member": bool(anchor in CIR_WATCHLIST_ANCHORS),
            }
        )
    rows.sort(key=lambda r: (r["mode"], r["position"], -float(r["dropped_frame_percent"])))
    return rows


def assert_per_link_residual_scale(per_link_rows: list[dict], fit_rows: list[dict]) -> None:
    if not per_link_rows or not fit_rows:
        return
    residual = np.asarray([float(row["range_minus_vicon_mm"]) for row in per_link_rows], dtype=float)
    train_rms = np.asarray([float(row["train_rms_mm"]) for row in fit_rows], dtype=float)
    residual_rms = float(np.sqrt(np.nanmean(residual * residual)))
    train_median = float(np.nanmedian(train_rms))
    ratio = residual_rms / train_median if train_median > 0.0 else float("inf")
    if not (0.8 <= ratio <= 1.2):
        raise RuntimeError(
            "Per-link tail residual scale check failed: "
            f"worst-6 residual RMS {residual_rms:.3f} mm vs LOO train RMS median {train_median:.3f} mm "
            f"(ratio {ratio:.3f}). This usually indicates an inconsistent correction convention."
        )


def worst6_verdict_rows(headline_rows: list[dict], oracle_best_rows: list[dict]) -> list[dict]:
    headline = {str(r["position"]): float(r["err_3d_mm"]) for r in headline_rows if r["method"] == "headline_additive_only"}
    oracle = {str(r["position"]): r for r in oracle_best_rows}
    worst = sorted(headline, key=lambda p: headline[p], reverse=True)[:6]
    rows = []
    for pos in worst:
        best = oracle[pos]
        dropped = [ch for ch in str(best.get("dropped_links", "")).replace(",", "") if ch in ANCHOR_LABELS]
        improvement = headline[pos] - float(best["err_3d_mm"])
        if dropped and improvement > 20.0:
            verdict = f"oracle improves after dropping {len(dropped)} link(s)"
        else:
            verdict = "broad degradation / no clear removable-link tail"
        rows.append(
            {
                "position": pos,
                "headline_err_3d_mm": headline[pos],
                "oracle_best_err_3d_mm": float(best["err_3d_mm"]),
                "oracle_improvement_mm": improvement,
                "oracle_dropped_links": ",".join(dropped),
                "verdict": verdict,
            }
        )
    return rows


def build_report(
    out_dir: Path,
    main_rows: list[dict],
    selector_best_rows: list[dict],
    verdict_rows: list[dict],
    per_link_rows: list[dict],
    improvement_summary: list[dict],
    worse_rows: list[dict],
    audit_summary: list[dict],
    production_summary: list[dict],
    crossref_rows: list[dict],
    best_selector_mode: str,
) -> None:
    lines = ["# Phase 2.14 P95 Tail Decomposition + Hard Link Rejection\n"]
    lines.append(f"- Generated: `{pd.Timestamp.now().isoformat(timespec='seconds')}`")
    lines.append("- Ground-truth terminology: `Vicon`")
    lines.append("- Scope: offline diagnostic replay only; no production files were modified.")
    lines.append("")
    lines.append("## Part A -- Tail Decomposition")
    lines.append(
        "The oracle analysis is physically separated from the implementable selectors. "
        "It uses Vicon truth only to choose the best subset and is therefore a non-deployable upper bound."
    )
    lines.append("")
    if per_link_rows:
        lines.append("Worst-6 per-link decomposition from the corrected headline row:")
        lines.append("")
        lines.append(markdown_table(per_link_rows, ["position", "anchor", "range_minus_vicon_mm", "range_minus_solved_distance_mm", "link_noise_std_mm", "in_top12_abs_bias"]))
        lines.append("")
    lines.append(markdown_table(verdict_rows, ["position", "headline_err_3d_mm", "oracle_best_err_3d_mm", "oracle_improvement_mm", "oracle_dropped_links", "verdict"]))
    lines.append("")
    lines.append("## Part B/C -- Deployable Selector Evaluation")
    lines.append(
        "B1 is session-level static rejection. B2 is the deployment-shaped frame-level greedy selector. "
        "B3 is frame-level exhaustive subset selection. All three use only corrected ranges, solver residuals, and subset geometry; Vicon is not used by the selectors."
    )
    lines.append("")
    lines.append(markdown_table(main_rows, ["mode", "oracle", "deployable", "positions", "median_3d_mm", "rmse_3d_mm", "p95_3d_mm", "median_delta_vs_headline_mm", "p95_delta_vs_headline_mm"]))
    lines.append("")
    lines.append("Best deployable selector selected for production-baseline comparison: `" + best_selector_mode + "`.")
    lines.append("")
    lines.append("Selector family best rows:")
    lines.append(markdown_table(selector_best_rows, ["family", "mode", "positions", "median_3d_mm", "rmse_3d_mm", "p95_3d_mm", "median_regression_vs_headline_mm"]))
    lines.append("")
    lines.append("## Per-Position Deltas")
    lines.append(markdown_table(improvement_summary, ["mode", "positions", "improved_vs_headline", "worse_or_equal_vs_headline", "median_delta_vs_headline_mm", "max_worse_delta_mm", "max_better_delta_mm"]))
    if worse_rows:
        lines.append("")
        lines.append("Worst selector regressions relative to the headline row:")
        lines.append(markdown_table(worse_rows[:18], ["mode", "position", "headline_err_3d_mm", "selector_err_3d_mm", "delta_vs_headline_mm"]))
    lines.append("")
    lines.append("## Selector Audit vs Oracle")
    lines.append(markdown_table(audit_summary, ["mode", "selector_dropped_total", "oracle_dropped_total", "false_drop_total", "miss_total", "false_drop_rate", "miss_rate"]))
    lines.append("")
    lines.append("## Production Baseline Selector Check")
    lines.append(markdown_table(production_summary, ["mode", "positions", "median_3d_mm", "rmse_3d_mm", "p95_3d_mm", "median_horizontal_mm", "median_vertical_mm"]))
    lines.append("")
    lines.append("## Drop Cross-References")
    if crossref_rows:
        lines.append(markdown_table(crossref_rows[:40], ["mode", "position", "anchor", "dropped_frame_percent", "oracle_dropped_this_link", "in_top12_abs_bias", "high_noise_FG_anchor", "cir_watchlist_anchor_member"]))
    else:
        lines.append("No selector produced dominant dropped links at the reporting threshold.")
    lines.append("")
    lines.append(
        "STOP: Phase 2.14 complete. Oracle rows are upper bounds only; B1/B2/B3 remain offline diagnostic selectors and were not integrated into production."
    )
    (out_dir / "02_14_tail_hard_rejection.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = args.data_dir.resolve()
    out_dir = args.out_dir.resolve()
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    phase1 = load_phase1_data(data_dir, out_dir)
    primary_truth = load_primary_vicon_anchor_truth(data_dir, phase1.anchor_truth)
    tag_truth = tag_coord_map(phase1.tag_truth)
    sweep_delta = load_sweep_deltas(out_dir)
    link_df = pd.read_csv(tables_dir / "03_tag_link_bias_links.csv")
    link_df["position"] = link_df["position"].astype(str)
    link_df["anchor"] = link_df["anchor"].astype(str)
    if float(link_df["vicon_distance_mm"].median()) < 1000.0:
        raise RuntimeError("Expected millimetre-scale Vicon distances; unit check failed.")

    top12_df = pd.read_csv(tables_dir / "04_tag_side_top12_abs_bias_links.csv")
    top12_keys = {(str(r.position), str(r.anchor)) for r in top12_df.itertuples(index=False)}

    _read_tr_all_frames, _Solver, _load_layout_json, _SolverConfig, _Frame, _Observation = load_offline_solver(data_dir)
    capture_dirs = find_static_capture_dirs(data_dir)
    positions = sorted(set(link_df["position"].astype(str)))
    positions = [pos for pos in positions if pos in capture_dirs]

    layout_path = make_vb_zero_delay_layout(out_dir)
    layout_coords = load_layout_coords(layout_path)
    truth_xyz = np.asarray([primary_truth[a] for a in ANCHOR_LABELS], dtype=float)

    headline_existing = pd.read_csv(tables_dir / "11_additive_coherence_positions.csv")
    head_sub = headline_existing[headline_existing["method"].astype(str) == "tagfit_additive_only_coherent"].copy()
    worst6 = set(head_sub.sort_values("err_3d_mm", ascending=False)["position"].astype(str).head(6).tolist())

    all_position_rows: list[dict] = []
    selector_info_rows: list[dict] = []
    drop_count_rows: list[dict] = []
    oracle_rows: list[dict] = []
    oracle_best_rows: list[dict] = []
    per_link_rows: list[dict] = []
    fit_rows: list[dict] = []

    max_workers = max(1, min(args.workers, len(positions)))
    payloads = [(str(data_dir), str(out_dir), pos, tuple(sorted(worst6))) for pos in positions]
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_position_headline_process, payload): payload[2] for payload in payloads}
        for fut in as_completed(futs):
            result = fut.result()
            all_position_rows.extend(result["position_rows"])
            selector_info_rows.extend(result["selector_info_rows"])
            drop_count_rows.extend(result["drop_count_rows"])
            oracle_rows.extend(result["oracle_rows"])
            oracle_best_rows.append(result["oracle_best"])
            per_link_rows.extend(result["per_link_rows"])
            fit_rows.append(result["fit_row"])

    all_position_rows.sort(key=lambda r: (r["method"], r["position"]))
    selector_info_rows.sort(key=lambda r: (r["mode"], r["position"]))
    drop_count_rows.sort(key=lambda r: (r["mode"], r["position"], r["anchor"]))
    oracle_rows.sort(key=lambda r: (r["position"], r["kept_n"], r["subset"]))
    oracle_best_rows.sort(key=lambda r: r["position"])
    per_link_rows.sort(key=lambda r: (r["position"], -abs(float(r["range_minus_vicon_mm"]))))
    assert_per_link_residual_scale(per_link_rows, fit_rows)
    fit_rows.sort(key=lambda r: r["position"])

    summary_rows = summarize_position_rows(all_position_rows, mode_col="method")
    headline_summary = next(r for r in summary_rows if r["mode"] == "headline_additive_only")
    headline_median = float(headline_summary["median_3d_mm"])
    headline_p95 = float(headline_summary["p95_3d_mm"])
    b1_best = choose_best_mode(summary_rows, "B1_session_", headline_median)
    b2_best = choose_best_mode(summary_rows, "B2_frame_greedy_", headline_median)
    b3_best = choose_best_mode(summary_rows, "B3_frame_exhaustive_", headline_median)
    deployable_best = min([b1_best, b2_best, b3_best], key=lambda r: (float(r["p95_3d_mm"]), float(r["rmse_3d_mm"]), float(r["median_3d_mm"])))
    selected_modes = [b1_best["mode"], b2_best["mode"], b3_best["mode"]]

    oracle_summary = next(r for r in summary_rows if r["mode"] == "oracle_best_subset_median_range")
    main_modes = ["headline_additive_only", b1_best["mode"], b2_best["mode"], b3_best["mode"], "oracle_best_subset_median_range"]
    main_rows = []
    for row in summary_rows:
        if row["mode"] not in main_modes:
            continue
        mode = str(row["mode"])
        main_rows.append(
            {
                **row,
                "oracle": bool(mode == "oracle_best_subset_median_range"),
                "deployable": bool(mode != "oracle_best_subset_median_range"),
                "median_delta_vs_headline_mm": float(row["median_3d_mm"]) - headline_median,
                "p95_delta_vs_headline_mm": float(row["p95_3d_mm"]) - headline_p95,
            }
        )
    order = {mode: idx for idx, mode in enumerate(main_modes)}
    main_rows.sort(key=lambda r: order[str(r["mode"])])

    selector_best_rows = []
    for family, row in [("B1_session", b1_best), ("B2_frame_greedy", b2_best), ("B3_frame_exhaustive", b3_best)]:
        selector_best_rows.append(
            {
                "family": family,
                **row,
                "median_regression_vs_headline_mm": float(row["median_3d_mm"]) - headline_median,
            }
        )

    improvement_summary_rows, worse_rows = improvement_rows(all_position_rows, selected_modes)
    audit_rows, audit_summary = oracle_audit_rows(oracle_best_rows, drop_count_rows, selected_modes)
    crossref_rows = crossref_drop_rows(drop_count_rows, selected_modes, oracle_best_rows, top12_keys)
    verdict_rows = worst6_verdict_rows(all_position_rows, oracle_best_rows)

    best_params = parse_selector_params(str(deployable_best["mode"]))
    production_rows: list[dict] = []
    production_drops: list[dict] = []
    prod_payloads = [(str(data_dir), str(out_dir), pos, best_params) for pos in positions]
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(run_position_production_best_process, payload): payload[2] for payload in prod_payloads}
        for fut in as_completed(futs):
            row, drops = fut.result()
            production_rows.append(row)
            production_drops.extend(drops)
    production_rows.sort(key=lambda r: r["position"])
    production_summary = summarize_position_rows(production_rows, mode_col="method")

    write_csv_rows(tables_dir / "13_tail_headline_selector_positions.csv", all_position_rows)
    write_csv_rows(tables_dir / "13_tail_selector_summary_all.csv", summary_rows)
    write_csv_rows(tables_dir / "13_tail_selector_main_table.csv", main_rows)
    write_csv_rows(tables_dir / "13_tail_selector_family_best.csv", selector_best_rows)
    write_csv_rows(tables_dir / "13_tail_selector_info.csv", selector_info_rows)
    write_csv_rows(tables_dir / "13_tail_selector_drop_counts.csv", drop_count_rows)
    write_csv_rows(tables_dir / "13_tail_worst6_per_link.csv", per_link_rows)
    write_csv_rows(tables_dir / "13_tail_oracle_subset_rows.csv", oracle_rows)
    write_csv_rows(tables_dir / "13_tail_oracle_best.csv", oracle_best_rows)
    write_csv_rows(tables_dir / "13_tail_worst6_verdict.csv", verdict_rows)
    write_csv_rows(tables_dir / "13_tail_selector_improvement_summary.csv", improvement_summary_rows)
    write_csv_rows(tables_dir / "13_tail_selector_worse_positions.csv", worse_rows)
    write_csv_rows(tables_dir / "13_tail_selector_oracle_audit.csv", audit_rows)
    write_csv_rows(tables_dir / "13_tail_selector_oracle_audit_summary.csv", audit_summary)
    write_csv_rows(tables_dir / "13_tail_selector_drop_crossref.csv", crossref_rows)
    write_csv_rows(tables_dir / "13_tail_production_best_selector_positions.csv", production_rows)
    write_csv_rows(tables_dir / "13_tail_production_best_selector_summary.csv", production_summary)
    write_csv_rows(tables_dir / "13_tail_production_best_selector_drops.csv", production_drops)
    write_csv_rows(tables_dir / "13_tail_additive_fit_rows.csv", fit_rows)

    build_report(
        out_dir,
        main_rows,
        selector_best_rows,
        verdict_rows,
        per_link_rows,
        improvement_summary_rows,
        worse_rows,
        audit_summary,
        production_summary,
        crossref_rows,
        str(deployable_best["mode"]),
    )
    print(f"Phase 2.14 report written: {out_dir / '02_14_tail_hard_rejection.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
