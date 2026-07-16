#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))

from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.models import Frame, Layout, MethodName, Observation, SolverConfig

ANCHOR_LABELS = "ABCDEFGH"
METHODS: tuple[MethodName, ...] = ("T1", "T2", "T3", "T4")

G_LAYOUT: Layout | None = None
G_STATIC: dict[str, list[Frame]] = {}
G_ROTO: dict[str, list[Frame]] = {}


@dataclass(frozen=True)
class FailureCondition:
    name: str
    family: str
    kind: str
    params: dict[str, Any]


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


def percentile(vals: list[float], q: float) -> float:
    clean = sorted(v for v in vals if math.isfinite(v))
    if not clean:
        return float("nan")
    if len(clean) == 1:
        return clean[0]
    pos = (q / 100.0) * (len(clean) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    frac = pos - lo
    return clean[lo] * (1.0 - frac) + clean[hi] * frac


def median(vals: list[float]) -> float:
    return percentile(vals, 50.0)


def safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_static_captures(data: Path, max_frames_per_capture: int = 0) -> dict[str, list[Frame]]:
    captures: dict[str, list[Frame]] = {}
    for cap in sorted((data / "Static_Test").glob("ID*/tr_all.csv")):
        capture_id = cap.parent.name.split("_", 1)[0]
        frames = read_tr_all_frames(cap, min_anchors=4)
        if max_frames_per_capture > 0:
            frames = frames[:max_frames_per_capture]
        captures[capture_id] = sorted(frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))
    return captures


def load_roto_captures(data: Path, max_frames_per_capture: int = 0) -> dict[str, list[Frame]]:
    captures: dict[str, list[Frame]] = {}
    for cap in sorted((data / "Roto_Test").glob("ID*/tr_all.csv")):
        capture_id = cap.parent.name.split("_", 1)[0]
        frames = read_tr_all_frames(cap, min_anchors=4)
        if max_frames_per_capture > 0:
            frames = frames[:max_frames_per_capture]
        captures[capture_id] = sorted(frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))
    return captures


def condition_set() -> list[FailureCondition]:
    out: list[FailureCondition] = []
    for keep_k in (8, 7, 6, 5, 4):
        out.append(FailureCondition(f"MC1_keep_{keep_k}", "MC1_random_keep_k", "keep_k", {"keep_k": keep_k}))
    out.extend(
        [
            FailureCondition(
                "MC2_H_weak_p35",
                "MC2_anchor_specific_dropout",
                "anchor_dropout",
                {"default_p": 0.03, "anchor_p": {7: 0.35}},
            ),
            FailureCondition(
                "MC2_EBH_weak",
                "MC2_anchor_specific_dropout",
                "anchor_dropout",
                {"default_p": 0.03, "anchor_p": {1: 0.10, 4: 0.15, 7: 0.35}},
            ),
            FailureCondition(
                "MC2_upper_tail_weak",
                "MC2_anchor_specific_dropout",
                "anchor_dropout",
                {"default_p": 0.03, "anchor_p": {4: 0.12, 5: 0.12, 6: 0.12, 7: 0.30}},
            ),
        ]
    )
    out.extend(
        [
            FailureCondition(
                "MC3_burst_H_0p5s",
                "MC3_burst_dropout",
                "burst_dropout",
                {"duration_s": 0.5, "target_anchors": [7]},
            ),
            FailureCondition(
                "MC3_burst_H_1p0s",
                "MC3_burst_dropout",
                "burst_dropout",
                {"duration_s": 1.0, "target_anchors": [7]},
            ),
            FailureCondition(
                "MC3_burst_random_EBH_1p0s",
                "MC3_burst_dropout",
                "burst_dropout",
                {"duration_s": 1.0, "target_anchors": [1, 4, 7]},
            ),
        ]
    )
    for bias_mm in (100.0, 200.0, 300.0):
        out.append(
            FailureCondition(
                f"MC4_nlos_random_anchor_persistent_p{int(bias_mm)}",
                "MC4_nlos_positive_bias",
                "nlos_bias",
                {"bias_mm": bias_mm, "target_anchors": list(range(8)), "frame_probability": 1.0},
            )
        )
    return out


def init_worker(layout: Layout, static: dict[str, list[Frame]], roto: dict[str, list[Frame]]) -> None:
    global G_LAYOUT, G_STATIC, G_ROTO
    G_LAYOUT = layout
    G_STATIC = static
    G_ROTO = roto


def with_observations(frame: Frame, observations: list[Observation]) -> Frame | None:
    if len(observations) < 4:
        return None
    return Frame(
        tag=frame.tag,
        sweep=frame.sweep,
        host_elapsed_s=frame.host_elapsed_s,
        host_epoch_s=frame.host_epoch_s,
        observations=tuple(sorted(observations, key=lambda o: o.anchor_id)),
    )


def apply_condition_to_capture(
    frames: list[Frame],
    condition: FailureCondition,
    rng: random.Random,
) -> tuple[list[Frame], dict[str, Any]]:
    kept: list[Frame] = []
    meta: dict[str, Any] = {}
    if condition.kind == "keep_k":
        keep_k = int(condition.params["keep_k"])
        for frame in frames:
            obs = list(frame.observations)
            if len(obs) < keep_k:
                continue
            chosen = obs if len(obs) == keep_k else rng.sample(obs, keep_k)
            new_frame = with_observations(frame, list(chosen))
            if new_frame is not None:
                kept.append(new_frame)
        meta["target_anchor"] = ""
        meta["bias_mm"] = ""
        meta["burst_duration_s"] = ""
        return kept, meta

    if condition.kind == "anchor_dropout":
        default_p = float(condition.params.get("default_p", 0.0))
        anchor_p = {int(k): float(v) for k, v in condition.params.get("anchor_p", {}).items()}
        dropped = defaultdict(int)
        for frame in frames:
            obs_out: list[Observation] = []
            for obs in frame.observations:
                p = anchor_p.get(obs.anchor_id, default_p)
                if rng.random() < p:
                    dropped[obs.anchor_id] += 1
                    continue
                obs_out.append(obs)
            new_frame = with_observations(frame, obs_out)
            if new_frame is not None:
                kept.append(new_frame)
        meta["target_anchor"] = ",".join(ANCHOR_LABELS[i] for i in sorted(anchor_p))
        meta["dropped_observations"] = sum(dropped.values())
        meta["bias_mm"] = ""
        meta["burst_duration_s"] = ""
        return kept, meta

    if condition.kind == "burst_dropout":
        if not frames:
            return [], {"target_anchor": "", "burst_duration_s": condition.params.get("duration_s", "")}
        duration_s = float(condition.params.get("duration_s", 1.0))
        targets = [int(v) for v in condition.params.get("target_anchors", list(range(8)))]
        target_anchor = rng.choice(targets)
        times = [f.host_elapsed_s for f in frames if math.isfinite(f.host_elapsed_s)]
        t0 = min(times) if times else 0.0
        t1 = max(times) if times else float(len(frames))
        if t1 - t0 <= duration_s:
            start = t0
        else:
            start = rng.uniform(t0, t1 - duration_s)
        end = start + duration_s
        dropped = 0
        for frame in frames:
            obs_out = list(frame.observations)
            if start <= frame.host_elapsed_s <= end:
                before = len(obs_out)
                obs_out = [obs for obs in obs_out if obs.anchor_id != target_anchor]
                dropped += before - len(obs_out)
            new_frame = with_observations(frame, obs_out)
            if new_frame is not None:
                kept.append(new_frame)
        meta["target_anchor"] = ANCHOR_LABELS[target_anchor]
        meta["burst_start_s"] = f"{start:.3f}"
        meta["burst_end_s"] = f"{end:.3f}"
        meta["burst_duration_s"] = duration_s
        meta["dropped_observations"] = dropped
        meta["bias_mm"] = ""
        return kept, meta

    if condition.kind == "nlos_bias":
        targets = [int(v) for v in condition.params.get("target_anchors", list(range(8)))]
        target_anchor = rng.choice(targets)
        bias_mm = float(condition.params.get("bias_mm", 0.0))
        frame_probability = float(condition.params.get("frame_probability", 1.0))
        biased_obs = 0
        for frame in frames:
            apply_bias = rng.random() <= frame_probability
            obs_out: list[Observation] = []
            for obs in frame.observations:
                if apply_bias and obs.anchor_id == target_anchor:
                    obs_out.append(
                        Observation(
                            anchor_id=obs.anchor_id,
                            range_mm=obs.range_mm + bias_mm,
                            quality_percent=obs.quality_percent,
                            status=obs.status,
                        )
                    )
                    biased_obs += 1
                else:
                    obs_out.append(obs)
            new_frame = with_observations(frame, obs_out)
            if new_frame is not None:
                kept.append(new_frame)
        meta["target_anchor"] = ANCHOR_LABELS[target_anchor]
        meta["bias_mm"] = bias_mm
        meta["biased_observations"] = biased_obs
        meta["burst_duration_s"] = ""
        return kept, meta

    raise ValueError(f"unknown condition kind: {condition.kind}")


def solve_positions(layout: Layout, frames: list[Frame], method: MethodName) -> list[tuple[float, float, float, float, int, int | None]]:
    solver = TagPositionSolver(layout, SolverConfig(method=method))
    rows: list[tuple[float, float, float, float, int, int | None]] = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is None:
            continue
        rows.append(
            (
                result.x_mm,
                result.y_mm,
                result.z_mm,
                result.residual_rms_mm,
                result.anchors_used,
                result.rejected_anchor_id,
            )
        )
    return rows


def summarize_positions(rows: list[tuple[float, float, float, float, int, int | None]], input_frames: int) -> dict:
    solved = len(rows)
    solved_rate = 100.0 * solved / max(1, input_frames)
    if solved < 10:
        return {
            "input_frames": input_frames,
            "solved": solved,
            "solved_rate": solved_rate,
            "x_std_mm": float("nan"),
            "y_std_mm": float("nan"),
            "z_std_mm": float("nan"),
            "d3_std_mm": float("nan"),
            "residual_rms_median_mm": float("nan"),
            "residual_rms_p95_mm": float("nan"),
            "rejected_frames": 0,
        }
    arr = np.asarray([[r[0], r[1], r[2]] for r in rows], dtype=float)
    centered = arr - np.mean(arr, axis=0)[None, :]
    std = np.sqrt(np.mean(centered * centered, axis=0))
    d3 = np.sqrt(np.sum(centered * centered, axis=1))
    residuals = [r[3] for r in rows]
    return {
        "input_frames": input_frames,
        "solved": solved,
        "solved_rate": solved_rate,
        "x_std_mm": float(std[0]),
        "y_std_mm": float(std[1]),
        "z_std_mm": float(std[2]),
        "d3_std_mm": float(np.sqrt(np.mean(d3 * d3))),
        "residual_rms_median_mm": median(residuals),
        "residual_rms_p95_mm": percentile(residuals, 95.0),
        "rejected_frames": sum(1 for r in rows if r[5] is not None),
    }


def summarize_static_set(capture_rows: list[dict]) -> dict:
    ok = [r for r in capture_rows if int(r.get("solved") or 0) >= 10 and math.isfinite(safe_float(r.get("d3_std_mm")))]
    return {
        "tracks_ok": len(ok),
        "input_frames_total": sum(int(r.get("input_frames") or 0) for r in capture_rows),
        "solved_total": sum(int(r.get("solved") or 0) for r in capture_rows),
        "solved_rate_median": median([safe_float(r.get("solved_rate")) for r in ok]),
        "x_std_mm_median": median([safe_float(r.get("x_std_mm")) for r in ok]),
        "y_std_mm_median": median([safe_float(r.get("y_std_mm")) for r in ok]),
        "z_std_mm_median": median([safe_float(r.get("z_std_mm")) for r in ok]),
        "d3_std_mm_median": median([safe_float(r.get("d3_std_mm")) for r in ok]),
        "d3_std_mm_p95": percentile([safe_float(r.get("d3_std_mm")) for r in ok], 95.0),
        "residual_rms_median_mm": median([safe_float(r.get("residual_rms_median_mm")) for r in ok]),
        "residual_rms_p95_mm": median([safe_float(r.get("residual_rms_p95_mm")) for r in ok]),
        "rejected_frames_total": sum(int(r.get("rejected_frames") or 0) for r in ok),
    }


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return {"status": "insufficient", "N_frames": int(pts.shape[0])}
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
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
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
        "radius_mm": radius,
        "circle_thickness_rms_mm": float(np.sqrt(np.mean(total * total))),
        "circle_thickness_p95_mm": float(np.percentile(total, 95.0)),
        "center_x": float(center3[0]),
        "center_y": float(center3[1]),
        "center_z": float(center3[2]),
        "_theta": theta,
    }


def per_turn_center_stats(points: np.ndarray, theta: np.ndarray) -> dict:
    if points.shape[0] < 80 or theta.size != points.shape[0]:
        return {"turn_count": 0}
    th = theta - theta[0]
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers: list[list[float]] = []
    radii: list[float] = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_circle_3d(points[idx])
        if fit.get("status") == "ok":
            centers.append([fit["center_x"], fit["center_y"], fit["center_z"]])
            radii.append(float(fit["radius_mm"]))
    if len(centers) < 2:
        return {"turn_count": len(centers)}
    c = np.asarray(centers, dtype=float)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean[None, :], axis=1)
    return {
        "turn_count": int(len(centers)),
        "turn_center_rms_3d_mm": float(np.sqrt(np.mean(dist * dist))),
        "turn_center_p95_3d_mm": float(np.percentile(dist, 95.0)),
        "turn_radius_std_mm": float(np.std(radii, ddof=1)) if len(radii) > 1 else float("nan"),
    }


def summarize_roto_points(rows: list[tuple[float, float, float, float, int, int | None]], input_frames: int) -> dict:
    if len(rows) < 80:
        return {
            "status": "insufficient",
            "input_frames": input_frames,
            "solved": len(rows),
            "solved_rate": 100.0 * len(rows) / max(1, input_frames),
            "turn_count": 0,
        }
    points = np.asarray([[r[0], r[1], r[2]] for r in rows], dtype=float)
    fit = fit_circle_3d(points)
    if fit.get("status") != "ok":
        return {
            "status": "fit_failed",
            "input_frames": input_frames,
            "solved": len(rows),
            "solved_rate": 100.0 * len(rows) / max(1, input_frames),
            "turn_count": 0,
        }
    theta = fit.pop("_theta")
    turns = per_turn_center_stats(points, theta)
    return {
        "status": "ok",
        "input_frames": input_frames,
        "solved": len(rows),
        "solved_rate": 100.0 * len(rows) / max(1, input_frames),
        "radius_mm": fit.get("radius_mm", float("nan")),
        "circle_thickness_rms_mm": fit.get("circle_thickness_rms_mm", float("nan")),
        "circle_thickness_p95_mm": fit.get("circle_thickness_p95_mm", float("nan")),
        **turns,
        "rejected_frames": sum(1 for r in rows if r[5] is not None),
    }


def summarize_roto_set(track_rows: list[dict]) -> dict:
    ok = [
        r for r in track_rows
        if r.get("status") == "ok"
        and int(r.get("turn_count") or 0) >= 2
        and math.isfinite(safe_float(r.get("turn_center_rms_3d_mm")))
    ]
    return {
        "tracks_ok": len(ok),
        "input_frames_total": sum(int(r.get("input_frames") or 0) for r in track_rows),
        "solved_total": sum(int(r.get("solved") or 0) for r in track_rows),
        "solved_rate_median": median([safe_float(r.get("solved_rate")) for r in ok]),
        "turn_center_rms_3d_mm_median": median([safe_float(r.get("turn_center_rms_3d_mm")) for r in ok]),
        "turn_center_p95_3d_mm_median": median([safe_float(r.get("turn_center_p95_3d_mm")) for r in ok]),
        "circle_thickness_rms_mm_median": median([safe_float(r.get("circle_thickness_rms_mm")) for r in ok]),
        "circle_thickness_p95_mm_median": median([safe_float(r.get("circle_thickness_p95_mm")) for r in ok]),
        "radius_mm_median": median([safe_float(r.get("radius_mm")) for r in ok]),
        "turn_count_median": median([safe_float(r.get("turn_count")) for r in ok]),
        "rejected_frames_total": sum(int(r.get("rejected_frames") or 0) for r in ok),
    }


def task_worker(args: tuple[FailureCondition, MethodName, int, int]) -> tuple[list[dict], list[dict], list[dict]]:
    condition, method, repeat, seed = args
    assert G_LAYOUT is not None
    rng = random.Random(seed)
    aggregate_rows: list[dict] = []
    static_rows: list[dict] = []
    roto_rows: list[dict] = []

    for capture_id, frames in G_STATIC.items():
        local_rng = random.Random(rng.randrange(1 << 62))
        conditioned, meta = apply_condition_to_capture(frames, condition, local_rng)
        rows = solve_positions(G_LAYOUT, conditioned, method)
        summary = summarize_positions(rows, len(conditioned))
        static_rows.append(
            {
                "dataset": "static",
                "condition": condition.name,
                "family": condition.family,
                "method": method,
                "repeat": repeat,
                "capture": capture_id,
                **meta,
                **summary,
            }
        )
    if G_STATIC:
        aggregate_rows.append(
            {
                "dataset": "static",
                "condition": condition.name,
                "family": condition.family,
                "method": method,
                "repeat": repeat,
                **summarize_static_set(static_rows),
            }
        )

    for capture_id, frames in G_ROTO.items():
        local_rng = random.Random(rng.randrange(1 << 62))
        conditioned, meta = apply_condition_to_capture(frames, condition, local_rng)
        by_tag: dict[str, list[Frame]] = defaultdict(list)
        for frame in conditioned:
            by_tag[frame.tag].append(frame)
        for tag, tag_frames in sorted(by_tag.items()):
            rows = solve_positions(G_LAYOUT, tag_frames, method)
            summary = summarize_roto_points(rows, len(tag_frames))
            roto_rows.append(
                {
                    "dataset": "roto",
                    "condition": condition.name,
                    "family": condition.family,
                    "method": method,
                    "repeat": repeat,
                    "capture": capture_id,
                    "tag": tag,
                    **meta,
                    **summary,
                }
            )
    if G_ROTO:
        aggregate_rows.append(
            {
                "dataset": "roto",
                "condition": condition.name,
                "family": condition.family,
                "method": method,
                "repeat": repeat,
                **summarize_roto_set(roto_rows),
            }
        )

    return aggregate_rows, static_rows, roto_rows


def aggregate_by_condition(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["dataset"], row["condition"], row["method"])].append(row)
    out: list[dict] = []
    metric_keys = [
        "tracks_ok",
        "input_frames_total",
        "solved_total",
        "solved_rate_median",
        "x_std_mm_median",
        "y_std_mm_median",
        "z_std_mm_median",
        "d3_std_mm_median",
        "d3_std_mm_p95",
        "residual_rms_median_mm",
        "residual_rms_p95_mm",
        "turn_center_rms_3d_mm_median",
        "turn_center_p95_3d_mm_median",
        "circle_thickness_rms_mm_median",
        "circle_thickness_p95_mm_median",
        "radius_mm_median",
        "turn_count_median",
        "rejected_frames_total",
    ]
    for (dataset, condition, method), items in sorted(groups.items()):
        base = {
            "dataset": dataset,
            "condition": condition,
            "family": items[0].get("family", ""),
            "method": method,
            "repeats": len(items),
        }
        for key in metric_keys:
            vals = [safe_float(item.get(key)) for item in items]
            base[f"{key}_median"] = median(vals)
            base[f"{key}_p05"] = percentile(vals, 5.0)
            base[f"{key}_p95"] = percentile(vals, 95.0)
        out.append(base)
    return out


def plot_metric(summary_rows: list[dict], methods: list[str], dataset: str, metric: str, title: str, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = [r for r in summary_rows if r["dataset"] == dataset]
    if not rows:
        return
    conditions = []
    for row in rows:
        if row["condition"] not in conditions:
            conditions.append(row["condition"])
    x = np.arange(len(conditions))
    width = min(0.22, 0.78 / max(1, len(methods)))
    fig, ax = plt.subplots(figsize=(max(10.5, 0.45 * len(conditions) + 4), 4.8), constrained_layout=True)
    offset0 = -0.5 * width * (len(methods) - 1)
    for idx, method in enumerate(methods):
        vals = []
        for condition in conditions:
            match = next((r for r in rows if r["condition"] == condition and r["method"] == method), None)
            vals.append(safe_float(match.get(metric)) if match else float("nan"))
        ax.bar(x + offset0 + idx * width, vals, width=width, label=method)
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, rotation=45, ha="right")
    ax.set_ylabel("mm")
    ax.set_title(title)
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def make_readme(
    out_dir: Path,
    data: Path,
    repeats: int,
    conditions: list[FailureCondition],
    summary_rows: list[dict],
    static_count: int,
    roto_count: int,
    methods: list[str],
) -> None:
    def row(dataset: str, condition: str, method: str) -> dict | None:
        return next((r for r in summary_rows if r["dataset"] == dataset and r["condition"] == condition and r["method"] == method), None)

    lines = [
        "# Outdoor 2026-05-13 T-Series Monte Carlo Failure Modes",
        "",
        f"This run evaluates {', '.join(methods)} against four runtime failure modes using the outdoor 2026-05-13 V4-io layout.",
        "",
        "## Inputs",
        "",
        f"- Dataset: `{data}`",
        f"- Static captures: `{static_count}`",
        f"- Roto captures: `{roto_count}`",
        f"- Repeats per condition/method: `{repeats}`",
        "- Layout: `FULL-COMPARE-1000/v4-io/layout.json`",
        "",
        "## Failure Modes",
        "",
    ]
    for cond in conditions:
        lines.append(f"- `{cond.name}`: `{cond.family}`, params `{json.dumps(cond.params, sort_keys=True)}`")
    lines.extend(
        [
            "",
            "## Quick Comparison",
            "",
            "| Dataset | Condition | " + " | ".join(methods) + " | Metric |",
            "| --- | --- | " + " | ".join(["---:"] * len(methods)) + " | --- |",
        ]
    )
    key_conditions = [
        "MC1_keep_8",
        "MC1_keep_6",
        "MC1_keep_4",
        "MC2_EBH_weak",
        "MC3_burst_random_EBH_1p0s",
        "MC4_nlos_random_anchor_persistent_p200",
    ]
    for cond in key_conditions:
        vals = [row("static", cond, method) for method in methods]
        if all(v is not None for v in vals):
            lines.append(
                "| static | "
                f"{cond} | "
                + " | ".join(f"{safe_float(v.get('d3_std_mm_median_median')):.1f}" for v in vals)
                + " | "
                "3D repeatability median (mm) |"
            )
        vals = [row("roto", cond, method) for method in methods]
        if all(v is not None for v in vals):
            lines.append(
                "| roto | "
                f"{cond} | "
                + " | ".join(f"{safe_float(v.get('turn_center_rms_3d_mm_median_median')):.1f}" for v in vals)
                + " | "
                "turn-center RMS median (mm) |"
            )
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `mc_condition_repeat_summary.csv`: one row per dataset/condition/method/repeat.",
            "- `mc_summary_by_condition.csv`: repeat-aggregated condition summary.",
            "- `mc_static_capture_detail.csv`: per-static-capture detail rows.",
            "- `mc_roto_track_detail.csv`: per-roto-capture/tag detail rows.",
            "- `figures/mc_static_d3_by_condition.png`: static 3D repeatability overview.",
            "- `figures/mc_static_z_by_condition.png`: static Z repeatability overview.",
            "- `figures/mc_roto_center_by_condition.png`: Roto turn-center robustness overview.",
            "- `figures/mc_roto_thickness_by_condition.png`: Roto circle-thickness robustness overview.",
        ]
    )
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run T-series Monte Carlo failure-mode tests on the outdoor dataset.")
    parser.add_argument("--data", default=str(REPO / "autopos_pipeline/outdoor_20260513"))
    parser.add_argument("--out", default=str(ROOT / "validation_outputs/outdoor_20260513_failure_modes"))
    parser.add_argument("--repeats", type=int, default=80)
    parser.add_argument("--seed", type=int, default=20260524)
    parser.add_argument("--max-frames-per-capture", type=int, default=0)
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--skip-static", action="store_true")
    parser.add_argument("--skip-roto", action="store_true")
    parser.add_argument("--methods", default=",".join(METHODS), help="Comma-separated methods, e.g. T4 or T1,T2,T3,T4.")
    args = parser.parse_args()

    data = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout_path = data / "FULL-COMPARE-1000/v4-io/layout.json"
    sigma_path = ROOT / "validation_outputs/outdoor_20260513/official_anchor_sigma.json"
    if not sigma_path.exists():
        sigma_path = data / "FULL-COMPARE-1000/tables/anchor_sigma.json"
    layout = load_layout_json(layout_path, sigma_path)
    static = {} if args.skip_static else load_static_captures(data, args.max_frames_per_capture)
    roto = {} if args.skip_roto else load_roto_captures(data, args.max_frames_per_capture)
    conditions = condition_set()
    methods = [m.strip().upper() for m in args.methods.split(",") if m.strip()]
    valid_methods = set(METHODS)
    bad_methods = [m for m in methods if m not in valid_methods]
    if bad_methods:
        raise ValueError(f"unknown methods: {bad_methods}; valid={sorted(valid_methods)}")
    workers = args.workers if args.workers > 0 else min(12, max(1, (os_cpu_count() or 1) - 1))

    tasks: list[tuple[FailureCondition, MethodName, int, int]] = []
    seed_rng = random.Random(args.seed)
    for condition in conditions:
        for method in methods:
            for repeat in range(args.repeats):
                tasks.append((condition, method, repeat, seed_rng.randrange(1 << 62)))  # type: ignore[arg-type]

    aggregate_rows: list[dict] = []
    static_detail_rows: list[dict] = []
    roto_detail_rows: list[dict] = []
    completed = 0
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=init_worker,
        initargs=(layout, static, roto),
    ) as executor:
        futures = [executor.submit(task_worker, task) for task in tasks]
        for future in as_completed(futures):
            agg, static_rows, roto_rows = future.result()
            aggregate_rows.extend(agg)
            static_detail_rows.extend(static_rows)
            roto_detail_rows.extend(roto_rows)
            completed += 1
            if completed == 1 or completed % max(1, len(tasks) // 20) == 0 or completed == len(tasks):
                print(f"[mc] completed {completed}/{len(tasks)} tasks", flush=True)

    aggregate_rows.sort(key=lambda r: (r["dataset"], r["condition"], r["method"], int(r["repeat"])))
    static_detail_rows.sort(key=lambda r: (r["condition"], r["method"], int(r["repeat"]), r["capture"]))
    roto_detail_rows.sort(key=lambda r: (r["condition"], r["method"], int(r["repeat"]), r["capture"], r.get("tag", "")))
    summary_rows = aggregate_by_condition(aggregate_rows)

    write_csv(out_dir / "mc_condition_repeat_summary.csv", aggregate_rows)
    write_csv(out_dir / "mc_summary_by_condition.csv", summary_rows)
    write_csv(out_dir / "mc_static_capture_detail.csv", static_detail_rows)
    write_csv(out_dir / "mc_roto_track_detail.csv", roto_detail_rows)
    plot_metric(
        summary_rows,
        methods,
        "static",
        "d3_std_mm_median_median",
        "Static 3D Repeatability Under Failure Modes",
        out_dir / "figures/mc_static_d3_by_condition.png",
    )
    plot_metric(
        summary_rows,
        methods,
        "static",
        "z_std_mm_median_median",
        "Static Z Repeatability Under Failure Modes",
        out_dir / "figures/mc_static_z_by_condition.png",
    )
    plot_metric(
        summary_rows,
        methods,
        "roto",
        "turn_center_rms_3d_mm_median_median",
        "Roto Turn-Center Repeatability Under Failure Modes",
        out_dir / "figures/mc_roto_center_by_condition.png",
    )
    plot_metric(
        summary_rows,
        methods,
        "roto",
        "circle_thickness_rms_mm_median_median",
        "Roto Circle Thickness Under Failure Modes",
        out_dir / "figures/mc_roto_thickness_by_condition.png",
    )
    make_readme(out_dir, data, args.repeats, conditions, summary_rows, len(static), len(roto), methods)

    print(
        json.dumps(
            {
                "out": str(out_dir),
                "conditions": len(conditions),
                "methods": methods,
                "repeats": args.repeats,
                "tasks": len(tasks),
                "workers": workers,
                "static_captures": len(static),
                "roto_captures": len(roto),
                "summary_csv": str(out_dir / "mc_summary_by_condition.csv"),
                "repeat_csv": str(out_dir / "mc_condition_repeat_summary.csv"),
            },
            indent=2,
        )
    )
    return 0


def os_cpu_count() -> int | None:
    try:
        import os

        return os.cpu_count()
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
