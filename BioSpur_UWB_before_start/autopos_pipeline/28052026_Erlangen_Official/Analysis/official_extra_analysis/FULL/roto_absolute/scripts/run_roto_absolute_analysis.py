#!/usr/bin/env python3
"""FULL OptiTrack absolute validation for Erlangen ROTO captures.

The ROTO captures do not have a trusted shared UTC timestamp between UWB and
OptiTrack.  This script therefore keeps spatial validation anchor-locked, then
estimates one relative time offset per ROTO capture from the primary
v4-io/T4 trajectory.  The same offset is reused for the full layout/tag-solver
matrix so solver comparisons do not get their own timing fit.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ANCHORS = list("ABCDEFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]
PRIMARY_LAYOUT = "v4-io"
PRIMARY_TAG_METHOD = "T4"
UWB_TAGS = ["BS2DCE", "BSDC91"]
OPTITRACK_MARKERS = ["WandBantenna", "WandCantenna"]
DEFAULT_MAPPING = {"BS2DCE": "WandBantenna", "BSDC91": "WandCantenna"}
SWAPPED_MAPPING = {"BS2DCE": "WandCantenna", "BSDC91": "WandBantenna"}
PRIMARY_ANCHOR_TRUTH_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
OPTITRACK_VERTICAL_AXIS = "Y"

THIS = Path(__file__).resolve()
ROTO_ROOT = THIS.parents[1]
FULL_ROOT = THIS.parents[2]
OFFICIAL_ROOT = THIS.parents[5]
REPO_ROOT = THIS.parents[7]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"

sys.path.insert(0, str(FULL_ROOT / "scripts"))
sys.path.insert(0, str(SOLVER_ROOT))

from tag_ground_truth import load_corrected_static_truth  # noqa: E402
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import SolverConfig  # noqa: E402


@dataclass(frozen=True)
class SimilarityFit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float
    aligned_anchors: np.ndarray


@dataclass(frozen=True)
class OptiTrackTrajectory:
    time_s: np.ndarray
    xyz_mm: np.ndarray


@dataclass
class SolvedTrack:
    layout: str
    tag_method: str
    capture_id: str
    tag: str
    time_s: np.ndarray
    xyz_autopos_mm: np.ndarray
    xyz_opti_frame_mm: np.ndarray
    residual_rms_mm: np.ndarray
    anchors_input: np.ndarray
    anchors_used: np.ndarray
    source_tr_all: str


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    out_dir.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


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


def format_float(value: float, ndigits: int = 1) -> str:
    if not math.isfinite(float(value)):
        return "nan"
    return f"{float(value):.{ndigits}f}"


def percentile(values: np.ndarray | list[float], pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def summary_stats(values: np.ndarray | list[float], prefix: str) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}_mean_mm": float("nan"),
            f"{prefix}_rmse_mm": float("nan"),
            f"{prefix}_p50_mm": float("nan"),
            f"{prefix}_p90_mm": float("nan"),
            f"{prefix}_p95_mm": float("nan"),
            f"{prefix}_max_mm": float("nan"),
        }
    return {
        f"{prefix}_mean_mm": float(np.mean(arr)),
        f"{prefix}_rmse_mm": float(math.sqrt(np.mean(arr * arr))),
        f"{prefix}_p50_mm": float(np.percentile(arr, 50)),
        f"{prefix}_p90_mm": float(np.percentile(arr, 90)),
        f"{prefix}_p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}_max_mm": float(np.max(arr)),
    }


def parse_trc_trajectories(path: Path, markers: list[str]) -> dict[str, OptiTrackTrajectory]:
    with path.open("r", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if len(rows) < 6:
        raise ValueError(f"TRC too short: {path}")
    marker_row = [x.strip() for x in rows[3][2:] if x.strip()]
    marker_to_index = {name: i for i, name in enumerate(marker_row)}
    missing = [m for m in markers if m not in marker_to_index]
    if missing:
        raise KeyError(f"{missing} missing in {path}")

    data: list[list[float]] = []
    for row in rows[5:]:
        if not row or not row[0].strip():
            continue
        vals = []
        for field in row:
            field = field.strip()
            if not field:
                vals.append(float("nan"))
            else:
                try:
                    vals.append(float(field))
                except ValueError:
                    vals.append(float("nan"))
        data.append(vals)
    if not data:
        raise ValueError(f"no data rows in {path}")
    arr = np.full((len(data), max(len(r) for r in data)), np.nan, dtype=float)
    for i, row in enumerate(data):
        arr[i, : len(row)] = row
    time_s = arr[:, 1]

    out: dict[str, OptiTrackTrajectory] = {}
    for marker in markers:
        start = 2 + marker_to_index[marker] * 3
        xyz = arr[:, start : start + 3]
        good = np.isfinite(time_s) & np.isfinite(xyz).all(axis=1)
        out[marker] = OptiTrackTrajectory(time_s=time_s[good], xyz_mm=xyz[good])
    return out


def parse_trc_medians(path: Path, markers: list[str]) -> dict[str, np.ndarray]:
    traj = parse_trc_trajectories(path, markers)
    return {name: np.nanmedian(item.xyz_mm, axis=0) for name, item in traj.items()}


def load_autopos_layout_coords(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data["anchors"]
    labels = [a.get("label", chr(ord("A") + int(a["id"]))) for a in anchors]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)
    return labels, coords


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> SimilarityFit:
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad fit shape {src.shape} vs {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(s * d) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    aligned = scale * src @ r + t
    return SimilarityFit(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)), aligned_anchors=aligned)


def apply_transform(points: np.ndarray, fit: SimilarityFit) -> np.ndarray:
    return fit.scale * points @ fit.rotation + fit.translation


def load_layout_transforms(layout_base: Path, anchor_truth: dict[str, np.ndarray]) -> dict[str, SimilarityFit]:
    transforms: dict[str, SimilarityFit] = {}
    dst = np.vstack([anchor_truth[a] for a in ANCHORS])
    for layout_name in LAYOUT_VERSIONS:
        layout_path = layout_base / layout_name / "layout.json"
        labels, coords = load_autopos_layout_coords(layout_path)
        by_label = {label: coords[i] for i, label in enumerate(labels)}
        src = np.vstack([by_label[a] for a in ANCHORS])
        transforms[layout_name] = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    return transforms


def capture_id_from_roto_dir(path: Path) -> str:
    m = re.search(r"roto_(R\d\d)_", path.name)
    if not m:
        raise ValueError(f"cannot parse ROTO capture id from {path}")
    return m.group(1)


def discover_roto_capture_files(captures_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(captures_root.glob("roto_R[0-9][0-9]*/tag_capture*/tr_all.csv")):
        if "Static-middle-test" in path.parents[1].name:
            continue
        cid = capture_id_from_roto_dir(path.parents[1])
        out[cid] = path
    return out


def solve_track_worker(job: dict) -> dict:
    layout_path = Path(job["layout_path"])
    sigma_path = Path(job["sigma_path"])
    tr_all_path = Path(job["tr_all_path"])
    tag = str(job["tag"])
    method = str(job["tag_method"])
    capture_id = str(job["capture_id"])
    layout_name = str(job["layout"])

    layout = load_layout_json(layout_path, sigma_path)
    solver = TagPositionSolver(layout, SolverConfig(method=method))
    frames = read_tr_all_frames(tr_all_path, tags={tag}, min_anchors=4)
    rows = []
    for frame in sorted(frames, key=lambda f: (f.host_elapsed_s, f.sweep)):
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
    return {
        "layout": layout_name,
        "tag_method": method,
        "capture_id": capture_id,
        "tag": tag,
        "source_tr_all": str(tr_all_path),
        "time_s": arr[:, 0],
        "xyz_autopos_mm": arr[:, 1:4],
        "residual_rms_mm": arr[:, 4],
        "anchors_input": arr[:, 5],
        "anchors_used": arr[:, 6],
    }


def interpolate_opti(traj: OptiTrackTrajectory, query_time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(traj.time_s, dtype=float)
    xyz = np.asarray(traj.xyz_mm, dtype=float)
    q = np.asarray(query_time_s, dtype=float)
    good = np.isfinite(q) & (q >= t[0]) & (q <= t[-1])
    out = np.full((q.shape[0], 3), np.nan, dtype=float)
    for axis in range(3):
        out[good, axis] = np.interp(q[good], t, xyz[:, axis])
    return out, good


def overlap_errors_for_capture(
    tracks: dict[str, SolvedTrack],
    opti: dict[str, OptiTrackTrajectory],
    mapping: dict[str, str],
    beta_s: float,
    min_points: int = 80,
) -> tuple[np.ndarray, np.ndarray, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    errors = []
    weights = []
    detail: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for tag, track in tracks.items():
        if tag not in mapping or mapping[tag] not in opti or track.time_s.size == 0:
            continue
        shifted = track.time_s + beta_s
        opti_xyz, good = interpolate_opti(opti[mapping[tag]], shifted)
        finite = good & np.isfinite(track.xyz_opti_frame_mm).all(axis=1) & np.isfinite(opti_xyz).all(axis=1)
        if int(np.sum(finite)) < min_points:
            continue
        err_vec = track.xyz_opti_frame_mm[finite] - opti_xyz[finite]
        err3 = np.linalg.norm(err_vec, axis=1)
        errors.append(err3)
        weights.append(np.ones_like(err3))
        detail[tag] = (track.xyz_opti_frame_mm[finite], opti_xyz[finite], track.time_s[finite])
    if not errors:
        return np.empty(0), np.empty(0), detail
    return np.concatenate(errors), np.concatenate(weights), detail


def score_beta(
    tracks: dict[str, SolvedTrack],
    opti: dict[str, OptiTrackTrajectory],
    mapping: dict[str, str],
    beta_s: float,
    min_points: int,
) -> tuple[float, int]:
    errors, _weights, _detail = overlap_errors_for_capture(tracks, opti, mapping, beta_s, min_points=min_points)
    if errors.size < min_points:
        return float("inf"), int(errors.size)
    return float(np.median(errors)), int(errors.size)


def beta_search_bounds(tracks: dict[str, SolvedTrack], opti: dict[str, OptiTrackTrajectory], mapping: dict[str, str]) -> tuple[float, float]:
    u_min = float("inf")
    u_max = float("-inf")
    o_min = float("inf")
    o_max = float("-inf")
    for tag, track in tracks.items():
        if tag not in mapping or mapping[tag] not in opti or track.time_s.size == 0:
            continue
        u_min = min(u_min, float(np.nanmin(track.time_s)))
        u_max = max(u_max, float(np.nanmax(track.time_s)))
        ot = opti[mapping[tag]].time_s
        o_min = min(o_min, float(ot[0]))
        o_max = max(o_max, float(ot[-1]))
    if not math.isfinite(u_min) or not math.isfinite(o_min):
        return 0.0, 0.0
    beta_min = o_min - u_min
    beta_max = o_max - u_max
    if beta_max <= beta_min:
        beta_min = o_min - u_max + 60.0
        beta_max = o_max - u_min - 60.0
    return beta_min, beta_max


def find_top_separated_candidates(betas: np.ndarray, scores: np.ndarray, min_sep_s: float = 0.5, n: int = 5) -> list[tuple[float, float]]:
    finite_idx = np.where(np.isfinite(scores))[0]
    order = finite_idx[np.argsort(scores[finite_idx])]
    picked: list[tuple[float, float]] = []
    for idx in order:
        beta = float(betas[idx])
        score = float(scores[idx])
        if all(abs(beta - b) >= min_sep_s for b, _ in picked):
            picked.append((beta, score))
        if len(picked) >= n:
            break
    return picked


def estimate_capture_offset(
    capture_id: str,
    tracks: dict[str, SolvedTrack],
    opti: dict[str, OptiTrackTrajectory],
    mapping: dict[str, str],
    coarse_step_s: float,
    refine_step_s: float,
    min_points: int,
) -> tuple[dict, list[dict]]:
    beta_min, beta_max = beta_search_bounds(tracks, opti, mapping)
    if beta_max <= beta_min:
        return (
            {
                "capture_id": capture_id,
                "status": "no_overlap",
                "beta_s": float("nan"),
                "score_median_3d_mm": float("nan"),
                "n_overlap": 0,
            },
            [],
        )
    coarse = np.arange(beta_min, beta_max + 0.5 * coarse_step_s, coarse_step_s)
    scores = np.full_like(coarse, np.inf, dtype=float)
    counts = np.zeros_like(coarse, dtype=int)
    for i, beta in enumerate(coarse):
        scores[i], counts[i] = score_beta(tracks, opti, mapping, float(beta), min_points=min_points)
    candidates = find_top_separated_candidates(coarse, scores, min_sep_s=0.5, n=5)
    if not candidates:
        return (
            {
                "capture_id": capture_id,
                "status": "no_valid_score",
                "beta_s": float("nan"),
                "score_median_3d_mm": float("nan"),
                "n_overlap": 0,
            },
            [],
        )

    best_beta = candidates[0][0]
    lo = max(beta_min, best_beta - max(0.25, coarse_step_s * 3.0))
    hi = min(beta_max, best_beta + max(0.25, coarse_step_s * 3.0))
    fine = np.arange(lo, hi + 0.5 * refine_step_s, refine_step_s)
    fine_scores = np.full_like(fine, np.inf, dtype=float)
    fine_counts = np.zeros_like(fine, dtype=int)
    for i, beta in enumerate(fine):
        fine_scores[i], fine_counts[i] = score_beta(tracks, opti, mapping, float(beta), min_points=min_points)
    best_i = int(np.nanargmin(fine_scores))
    best_beta = float(fine[best_i])
    best_score = float(fine_scores[best_i])
    best_count = int(fine_counts[best_i])
    second_score = float(candidates[1][1]) if len(candidates) > 1 else float("nan")
    ambiguity_ratio = second_score / best_score if math.isfinite(second_score) and best_score > 0 else float("nan")

    candidate_rows = []
    for rank, (beta, score) in enumerate(candidates, start=1):
        _s, count = score_beta(tracks, opti, mapping, beta, min_points=min_points)
        candidate_rows.append(
            {
                "capture_id": capture_id,
                "rank": rank,
                "coarse_beta_s": beta,
                "coarse_score_median_3d_mm": score,
                "n_overlap": count,
            }
        )
    return (
        {
            "capture_id": capture_id,
            "status": "ok",
            "beta_s": best_beta,
            "score_median_3d_mm": best_score,
            "n_overlap": best_count,
            "coarse_beta_min_s": float(beta_min),
            "coarse_beta_max_s": float(beta_max),
            "coarse_step_s": float(coarse_step_s),
            "refine_step_s": float(refine_step_s),
            "second_candidate_score_median_3d_mm": second_score,
            "second_to_best_score_ratio": ambiguity_ratio,
        },
        candidate_rows,
    )


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] < 30:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2, normal = vh[0], vh[1], vh[-1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    center3 = center0 + cx * e1 + cy * e2
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    thickness = np.sqrt(radial * radial + zplane * zplane)
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "center": center3,
        "radius_mm": float(radius),
        "normal": normal / np.linalg.norm(normal),
        "circle_thickness_rms_mm": float(math.sqrt(np.mean(thickness * thickness))),
        "circle_thickness_p95_mm": float(np.percentile(thickness, 95)),
    }


def axis_angle_deg(a: np.ndarray, b: np.ndarray) -> float:
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        return float("nan")
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na <= 0 or nb <= 0:
        return float("nan")
    dot = abs(float(np.dot(a / na, b / nb)))
    dot = max(-1.0, min(1.0, dot))
    return float(math.degrees(math.acos(dot)))


def evaluate_track(
    track: SolvedTrack,
    opti_traj: OptiTrackTrajectory,
    beta_s: float,
    layout_det: float,
    layout_anchor_rms_mm: float,
    include_samples: bool,
) -> tuple[dict, list[dict]]:
    opti_xyz, good = interpolate_opti(opti_traj, track.time_s + beta_s)
    finite = good & np.isfinite(track.xyz_opti_frame_mm).all(axis=1) & np.isfinite(opti_xyz).all(axis=1)
    uwb = track.xyz_opti_frame_mm[finite]
    truth = opti_xyz[finite]
    t = track.time_s[finite]
    residual_rms = track.residual_rms_mm[finite]
    anchors_input = track.anchors_input[finite]
    anchors_used = track.anchors_used[finite]
    if uwb.shape[0] == 0:
        base = {
            "layout": track.layout,
            "tag_method": track.tag_method,
            "capture_id": track.capture_id,
            "tag": track.tag,
            "status": "no_overlap",
            "n_overlap": 0,
            "beta_s": beta_s,
        }
        return base, []

    diff = uwb - truth
    err3 = np.linalg.norm(diff, axis=1)
    horiz = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
    vertical = np.abs(diff[:, 1])
    row = {
        "layout": track.layout,
        "tag_method": track.tag_method,
        "capture_id": track.capture_id,
        "tag": track.tag,
        "status": "ok",
        "n_overlap": int(uwb.shape[0]),
        "uwb_duration_s": float(track.time_s[-1] - track.time_s[0]) if track.time_s.size else float("nan"),
        "overlap_duration_s": float(t[-1] - t[0]) if t.size > 1 else 0.0,
        "beta_s": float(beta_s),
        "layout_anchor_fit_det": float(layout_det),
        "layout_anchor_rms_3d_mm": float(layout_anchor_rms_mm),
        "residual_rms_median_mm": percentile(residual_rms, 50),
        "residual_rms_p95_mm": percentile(residual_rms, 95),
        "anchors_input_median": percentile(anchors_input, 50),
        "anchors_used_median": percentile(anchors_used, 50),
        "pct_frames_ge7_anchors_input": float(np.mean(anchors_input >= 7.0) * 100.0),
        "pct_frames_ge8_anchors_input": float(np.mean(anchors_input >= 8.0) * 100.0),
        **summary_stats(err3, "err3d"),
        **summary_stats(horiz, "err_horizontal_xz"),
        **summary_stats(vertical, "err_vertical_y"),
        "signed_bias_x_mm": float(np.mean(diff[:, 0])),
        "signed_bias_y_vertical_mm": float(np.mean(diff[:, 1])),
        "signed_bias_z_mm": float(np.mean(diff[:, 2])),
        "signed_std_x_mm": float(np.std(diff[:, 0], ddof=1)) if diff.shape[0] > 1 else 0.0,
        "signed_std_y_vertical_mm": float(np.std(diff[:, 1], ddof=1)) if diff.shape[0] > 1 else 0.0,
        "signed_std_z_mm": float(np.std(diff[:, 2], ddof=1)) if diff.shape[0] > 1 else 0.0,
    }

    uwb_circle = fit_circle_3d(uwb)
    truth_circle = fit_circle_3d(truth)
    if uwb_circle.get("status") == "ok" and truth_circle.get("status") == "ok":
        center_err = float(np.linalg.norm(uwb_circle["center"] - truth_circle["center"]))
        row.update(
            {
                "turn_center_abs_error_3d_mm": center_err,
                "turn_center_abs_error_horizontal_xz_mm": float(
                    np.linalg.norm((uwb_circle["center"] - truth_circle["center"])[[0, 2]])
                ),
                "turn_center_abs_error_vertical_y_mm": float(abs((uwb_circle["center"] - truth_circle["center"])[1])),
                "radius_uwb_mm": float(uwb_circle["radius_mm"]),
                "radius_opti_mm": float(truth_circle["radius_mm"]),
                "radius_error_mm": float(uwb_circle["radius_mm"] - truth_circle["radius_mm"]),
                "axis_angle_abs_deg": axis_angle_deg(uwb_circle["normal"], truth_circle["normal"]),
                "uwb_circle_thickness_rms_mm": float(uwb_circle["circle_thickness_rms_mm"]),
                "opti_circle_thickness_rms_mm": float(truth_circle["circle_thickness_rms_mm"]),
            }
        )
    else:
        row.update(
            {
                "turn_center_abs_error_3d_mm": float("nan"),
                "turn_center_abs_error_horizontal_xz_mm": float("nan"),
                "turn_center_abs_error_vertical_y_mm": float("nan"),
                "radius_uwb_mm": float("nan"),
                "radius_opti_mm": float("nan"),
                "radius_error_mm": float("nan"),
                "axis_angle_abs_deg": float("nan"),
                "uwb_circle_thickness_rms_mm": float("nan"),
                "opti_circle_thickness_rms_mm": float("nan"),
            }
        )

    sample_rows: list[dict] = []
    if include_samples:
        for i in range(uwb.shape[0]):
            sample_rows.append(
                {
                    "layout": track.layout,
                    "tag_method": track.tag_method,
                    "capture_id": track.capture_id,
                    "tag": track.tag,
                    "uwb_time_s": float(t[i]),
                    "opti_time_s": float(t[i] + beta_s),
                    "err3d_mm": float(err3[i]),
                    "err_horizontal_xz_mm": float(horiz[i]),
                    "err_vertical_y_mm": float(vertical[i]),
                    "err_x_mm": float(diff[i, 0]),
                    "err_y_vertical_mm": float(diff[i, 1]),
                    "err_z_mm": float(diff[i, 2]),
                    "uwb_x_mm": float(uwb[i, 0]),
                    "uwb_y_vertical_mm": float(uwb[i, 1]),
                    "uwb_z_mm": float(uwb[i, 2]),
                    "opti_x_mm": float(truth[i, 0]),
                    "opti_y_vertical_mm": float(truth[i, 1]),
                    "opti_z_mm": float(truth[i, 2]),
                }
            )
    return row, sample_rows


def aggregate_summary(track_rows: list[dict], sample_rows_primary: list[dict]) -> list[dict]:
    rows: list[dict] = []
    combos = sorted({(r["layout"], r["tag_method"]) for r in track_rows if r.get("status") == "ok"})
    for layout, method in combos:
        sub = [r for r in track_rows if r.get("layout") == layout and r.get("tag_method") == method and r.get("status") == "ok"]
        if not sub:
            continue
        # Aggregate by track summaries first. This gives every ROTO tag track equal weight.
        row = {
            "layout": layout,
            "tag_method": method,
            "tracks_ok": len(sub),
            "captures_ok": len({r["capture_id"] for r in sub}),
            "n_overlap_total": int(sum(int(r["n_overlap"]) for r in sub)),
            "err3d_p50_track_median_mm": percentile([r["err3d_p50_mm"] for r in sub], 50),
            "err3d_p95_track_median_mm": percentile([r["err3d_p95_mm"] for r in sub], 50),
            "err3d_rmse_track_median_mm": percentile([r["err3d_rmse_mm"] for r in sub], 50),
            "err_horizontal_xz_p50_track_median_mm": percentile([r["err_horizontal_xz_p50_mm"] for r in sub], 50),
            "err_horizontal_xz_p95_track_median_mm": percentile([r["err_horizontal_xz_p95_mm"] for r in sub], 50),
            "err_vertical_y_p50_track_median_mm": percentile([r["err_vertical_y_p50_mm"] for r in sub], 50),
            "err_vertical_y_p95_track_median_mm": percentile([r["err_vertical_y_p95_mm"] for r in sub], 50),
            "turn_center_abs_error_3d_track_median_mm": percentile([r["turn_center_abs_error_3d_mm"] for r in sub], 50),
            "turn_center_abs_error_3d_track_p95_mm": percentile([r["turn_center_abs_error_3d_mm"] for r in sub], 95),
            "radius_error_abs_track_median_mm": percentile([abs(float(r["radius_error_mm"])) for r in sub], 50),
            "axis_angle_abs_track_median_deg": percentile([r["axis_angle_abs_deg"] for r in sub], 50),
        }
        if layout == PRIMARY_LAYOUT and method == PRIMARY_TAG_METHOD and sample_rows_primary:
            e3 = np.array([r["err3d_mm"] for r in sample_rows_primary], dtype=float)
            hxz = np.array([r["err_horizontal_xz_mm"] for r in sample_rows_primary], dtype=float)
            vy = np.array([r["err_vertical_y_mm"] for r in sample_rows_primary], dtype=float)
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
        rows.append(row)
    return rows


def build_mapping_decision(
    primary_tracks: dict[str, dict[str, SolvedTrack]],
    opti_by_capture: dict[str, dict[str, OptiTrackTrajectory]],
    coarse_step_s: float,
    min_points: int,
) -> tuple[dict[str, str], list[dict]]:
    mapping_options = [("default", DEFAULT_MAPPING), ("swapped", SWAPPED_MAPPING)]
    rows: list[dict] = []
    for name, mapping in mapping_options:
        scores = []
        radii_rows = []
        for capture_id, tracks in sorted(primary_tracks.items()):
            if capture_id not in opti_by_capture:
                continue
            offset, _cands = estimate_capture_offset(
                capture_id,
                tracks,
                opti_by_capture[capture_id],
                mapping,
                coarse_step_s=coarse_step_s,
                refine_step_s=max(0.02, coarse_step_s),
                min_points=min_points,
            )
            score = float(offset.get("score_median_3d_mm", float("nan")))
            if math.isfinite(score):
                scores.append(score)
            radii_rows.append(
                {
                    "mapping_name": name,
                    "capture_id": capture_id,
                    "score_median_3d_mm": score,
                    "beta_s": offset.get("beta_s", float("nan")),
                    "n_overlap": offset.get("n_overlap", 0),
                    "BS2DCE_marker": mapping["BS2DCE"],
                    "BSDC91_marker": mapping["BSDC91"],
                }
            )
        rows.extend(radii_rows)
        rows.append(
            {
                "mapping_name": name,
                "capture_id": "ALL",
                "score_median_3d_mm": percentile(scores, 50),
                "score_p95_3d_mm": percentile(scores, 95),
                "n_captures_scored": len(scores),
                "BS2DCE_marker": mapping["BS2DCE"],
                "BSDC91_marker": mapping["BSDC91"],
            }
        )
    default_score = next((r["score_median_3d_mm"] for r in rows if r["mapping_name"] == "default" and r["capture_id"] == "ALL"), float("inf"))
    swapped_score = next((r["score_median_3d_mm"] for r in rows if r["mapping_name"] == "swapped" and r["capture_id"] == "ALL"), float("inf"))
    return (DEFAULT_MAPPING if default_score <= swapped_score else SWAPPED_MAPPING), rows


def plot_primary_cdf(sample_rows: list[dict], out_png: Path) -> None:
    if not sample_rows:
        return
    e3 = np.sort(np.array([r["err3d_mm"] for r in sample_rows], dtype=float))
    hxz = np.sort(np.array([r["err_horizontal_xz_mm"] for r in sample_rows], dtype=float))
    vy = np.sort(np.array([r["err_vertical_y_mm"] for r in sample_rows], dtype=float))
    fig, ax = plt.subplots(figsize=(7.2, 4.5), constrained_layout=True)
    for vals, label, color in [
        (e3, "3D", "#4C78A8"),
        (hxz, "horizontal XZ", "#54A24B"),
        (vy, "vertical Y", "#E45756"),
    ]:
        p = np.linspace(0, 100, vals.size, endpoint=True)
        ax.plot(vals, p, label=label, color=color)
    ax.set_xlabel("absolute error (mm)")
    ax.set_ylabel("CDF (%)")
    ax.set_title("ROTO absolute error CDF, v4-io/T4")
    ax.grid(True, alpha=0.3)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def plot_primary_per_capture(track_rows: list[dict], out_png: Path) -> None:
    rows = [r for r in track_rows if r.get("layout") == PRIMARY_LAYOUT and r.get("tag_method") == PRIMARY_TAG_METHOD and r.get("status") == "ok"]
    if not rows:
        return
    captures = sorted({r["capture_id"] for r in rows})
    x = np.arange(len(captures))
    med = [percentile([r["err3d_p50_mm"] for r in rows if r["capture_id"] == c], 50) for c in captures]
    p95 = [percentile([r["err3d_p95_mm"] for r in rows if r["capture_id"] == c], 50) for c in captures]
    fig, ax = plt.subplots(figsize=(10.5, 4.6), constrained_layout=True)
    ax.plot(x, med, marker="o", label="track median 3D", color="#4C78A8")
    ax.plot(x, p95, marker="o", label="track P95 3D", color="#E45756")
    ax.set_xticks(x)
    ax.set_xticklabels(captures, rotation=45, ha="right")
    ax.set_ylabel("error (mm)")
    ax.set_title("ROTO absolute error by capture, v4-io/T4")
    ax.grid(True, alpha=0.3)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def plot_primary_axis_split(track_rows: list[dict], out_png: Path) -> None:
    rows = [r for r in track_rows if r.get("layout") == PRIMARY_LAYOUT and r.get("tag_method") == PRIMARY_TAG_METHOD and r.get("status") == "ok"]
    if not rows:
        return
    captures = sorted({r["capture_id"] for r in rows})
    x = np.arange(len(captures))
    width = 0.36
    hxz = [percentile([r["err_horizontal_xz_p95_mm"] for r in rows if r["capture_id"] == c], 50) for c in captures]
    vy = [percentile([r["err_vertical_y_p95_mm"] for r in rows if r["capture_id"] == c], 50) for c in captures]
    fig, ax = plt.subplots(figsize=(10.5, 4.6), constrained_layout=True)
    ax.bar(x - width / 2, hxz, width=width, label="horizontal XZ P95", color="#54A24B")
    ax.bar(x + width / 2, vy, width=width, label="vertical Y P95", color="#E45756")
    ax.set_xticks(x)
    ax.set_xticklabels(captures, rotation=45, ha="right")
    ax.set_ylabel("error (mm)")
    ax.set_title("ROTO horizontal/vertical split, v4-io/T4")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def plot_solver_matrix(summary_rows: list[dict], out_png: Path) -> None:
    if not summary_rows:
        return
    layouts = LAYOUT_VERSIONS
    methods = TAG_METHODS
    mat = np.full((len(layouts), len(methods)), np.nan)
    for r in summary_rows:
        if r["layout"] in layouts and r["tag_method"] in methods:
            mat[layouts.index(r["layout"]), methods.index(r["tag_method"])] = float(r["err3d_p50_track_median_mm"])
    fig, ax = plt.subplots(figsize=(7.2, 4.8), constrained_layout=True)
    im = ax.imshow(mat, cmap="viridis")
    ax.set_xticks(np.arange(len(methods)))
    ax.set_xticklabels(methods)
    ax.set_yticks(np.arange(len(layouts)))
    ax.set_yticklabels(layouts)
    ax.set_title("ROTO absolute median 3D by solver matrix")
    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            if math.isfinite(mat[i, j]):
                ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", color="white" if mat[i, j] > np.nanmedian(mat) else "black")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("track-median 3D P50 (mm)")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def generate_report(
    report_path: Path,
    summary_rows: list[dict],
    offset_rows: list[dict],
    mapping: dict[str, str],
    mapping_rows: list[dict],
) -> None:
    primary = next((r for r in summary_rows if r["layout"] == PRIMARY_LAYOUT and r["tag_method"] == PRIMARY_TAG_METHOD), None)
    offsets_ok = [r for r in offset_rows if r.get("status") == "ok"]
    mapping_name = "default" if mapping == DEFAULT_MAPPING else "swapped"
    lines: list[str] = []
    lines.append("# FULL ROTO Absolute OptiTrack Analysis\n\n")
    lines.append("This analysis uses the corrected FULL OptiTrack export. All anchors A--H are retained; no anchor-removal evaluation is generated in this path.\n\n")
    lines.append("Spatial alignment is anchor-locked: AutoPos layout anchors are aligned to OptiTrack antenna medians using reflection-allowed rigid Kabsch with no scale. Tag/Wand trajectories are never used to fit the spatial transform.\n\n")
    lines.append("Because there is no trusted shared UTC timestamp, one relative time offset is estimated per ROTO capture from the primary `v4-io/T4` trajectory. The same offset is then reused for all layout/tag-solver combinations.\n\n")
    lines.append(f"Selected Wand mapping: `{mapping_name}`; `BS2DCE -> {mapping['BS2DCE']}`, `BSDC91 -> {mapping['BSDC91']}`.\n\n")
    if primary:
        lines.append("## Primary v4-io/T4 Headline\n\n")
        lines.append(
            f"- Track-median 3D P50: **{format_float(primary['err3d_p50_track_median_mm'])} mm**; "
            f"track-median 3D P95: **{format_float(primary['err3d_p95_track_median_mm'])} mm**.\n"
        )
        lines.append(
            f"- Sample-weighted 3D P50/P95: **{format_float(primary.get('sample_weighted_err3d_p50_mm', float('nan')))} / "
            f"{format_float(primary.get('sample_weighted_err3d_p95_mm', float('nan')))} mm**.\n"
        )
        lines.append(
            f"- Horizontal XZ sample P50/P95: **{format_float(primary.get('sample_weighted_horizontal_xz_p50_mm', float('nan')))} / "
            f"{format_float(primary.get('sample_weighted_horizontal_xz_p95_mm', float('nan')))} mm**.\n"
        )
        lines.append(
            f"- Vertical Y sample P50/P95: **{format_float(primary.get('sample_weighted_vertical_y_p50_mm', float('nan')))} / "
            f"{format_float(primary.get('sample_weighted_vertical_y_p95_mm', float('nan')))} mm**.\n"
        )
        lines.append(
            f"- Track-median turn-center absolute 3D error: **{format_float(primary['turn_center_abs_error_3d_track_median_mm'])} mm**.\n\n"
        )
    if offsets_ok:
        betas = np.array([r["beta_s"] for r in offsets_ok], dtype=float)
        scores = np.array([r["score_median_3d_mm"] for r in offsets_ok], dtype=float)
        lines.append("## Time Alignment\n\n")
        lines.append(
            f"Offsets solved for {len(offsets_ok)} captures. Median offset is {format_float(float(np.median(betas)), 3)} s "
            f"(range {format_float(float(np.min(betas)), 3)} to {format_float(float(np.max(betas)), 3)} s). "
            f"Median primary alignment score is {format_float(float(np.median(scores)))} mm.\n\n"
        )
        lines.append("The offset is an analysis variable, not a latency measurement. Periodic circular motion can create secondary local minima; see `roto_time_alignment_candidates_v4io_T4.csv`.\n\n")
    lines.append("## Solver Matrix\n\n")
    cols = [
        "layout",
        "tag_method",
        "tracks_ok",
        "err3d_p50_track_median_mm",
        "err3d_p95_track_median_mm",
        "err_horizontal_xz_p95_track_median_mm",
        "err_vertical_y_p95_track_median_mm",
        "turn_center_abs_error_3d_track_median_mm",
    ]
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for r in summary_rows:
        vals = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                vals.append(format_float(v))
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |\n")
    lines.append("\n## Output Files\n\n")
    lines.append("- `tables/roto_abs_summary_by_solver.csv`\n")
    lines.append("- `tables/roto_abs_per_track.csv`\n")
    lines.append("- `tables/roto_abs_samples_v4io_T4.csv`\n")
    lines.append("- `tables/roto_time_offsets_v4io_T4.csv`\n")
    lines.append("- `tables/roto_time_alignment_candidates_v4io_T4.csv`\n")
    lines.append("- `tables/roto_wand_mapping_decision.csv`\n")
    lines.append("- `figs/roto_abs_cdf_v4io_T4.png`\n")
    lines.append("- `figs/roto_abs_per_capture_v4io_T4.png`\n")
    lines.append("- `figs/roto_xy_vertical_split_v4io_T4.png`\n")
    lines.append("- `figs/roto_solver_matrix_median3d.png`\n\n")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="FULL OptiTrack absolute ROTO analysis with capture-level time-offset estimation.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT))
    parser.add_argument("--out", default=str(ROTO_ROOT))
    parser.add_argument("--layouts", default="all", help="comma list from v1-old,v2,v3-lite,v3-full,v4-io or all")
    parser.add_argument("--tag-methods", default="all", help="comma list from T1,T2,T3,T4 or all")
    parser.add_argument("--workers", type=int, default=max(1, min(10, os.cpu_count() or 1)))
    parser.add_argument("--coarse-step-s", type=float, default=0.05)
    parser.add_argument("--refine-step-s", type=float, default=0.005)
    parser.add_argument("--min-points", type=int, default=500)
    parser.add_argument("--skip-solve", action="store_true", help="reserved for future cache use")
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_root = Path(args.out).resolve()
    tables_dir = out_root / "tables"
    figs_dir = out_root / "figs"
    reports_dir = out_root / "reports"
    scripts_dir = out_root / "scripts"
    for p in [tables_dir, figs_dir, reports_dir, scripts_dir]:
        p.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    if not captures_root.exists():
        raise FileNotFoundError(captures_root)
    if not opti_dir.exists():
        raise FileNotFoundError(opti_dir)

    layouts = LAYOUT_VERSIONS if args.layouts == "all" else [x.strip() for x in args.layouts.split(",") if x.strip()]
    tag_methods = TAG_METHODS if args.tag_methods == "all" else [x.strip().upper() for x in args.tag_methods.split(",") if x.strip()]
    for layout in layouts:
        if layout not in LAYOUT_VERSIONS:
            raise ValueError(f"unknown layout {layout}")
    for method in tag_methods:
        if method not in TAG_METHODS:
            raise ValueError(f"unknown tag method {method}")
    if PRIMARY_LAYOUT not in layouts or PRIMARY_TAG_METHOD not in tag_methods:
        raise ValueError("primary v4-io/T4 must be included so capture time offsets can be estimated")

    anchor_truth, _static_truth, _static_meta, _corr_rows = load_corrected_static_truth(opti_dir, ANCHORS, PRIMARY_ANCHOR_TRUTH_IDS)
    transforms = load_layout_transforms(layout_base, anchor_truth)
    layout_anchor_rms: dict[str, float] = {}
    for layout_name, fit in transforms.items():
        dst = np.vstack([anchor_truth[a] for a in ANCHORS])
        diff = fit.aligned_anchors - dst
        layout_anchor_rms[layout_name] = float(math.sqrt(np.mean(np.sum(diff * diff, axis=1))))

    tr_all_by_capture = discover_roto_capture_files(captures_root)
    if not tr_all_by_capture:
        raise FileNotFoundError(f"no ROTO tr_all files under {captures_root}")
    capture_ids = sorted(tr_all_by_capture)
    opti_by_capture: dict[str, dict[str, OptiTrackTrajectory]] = {}
    for capture_id in capture_ids:
        trc_path = opti_dir / f"{capture_id}.trc"
        if not trc_path.exists():
            raise FileNotFoundError(trc_path)
        opti_by_capture[capture_id] = parse_trc_trajectories(trc_path, OPTITRACK_MARKERS)

    jobs = []
    for layout_name in layouts:
        layout_path = layout_base / layout_name / "layout.json"
        for method in tag_methods:
            for capture_id, tr_path in tr_all_by_capture.items():
                for tag in UWB_TAGS:
                    jobs.append(
                        {
                            "layout": layout_name,
                            "layout_path": str(layout_path),
                            "sigma_path": str(sigma_path),
                            "tag_method": method,
                            "capture_id": capture_id,
                            "tag": tag,
                            "tr_all_path": str(tr_path),
                        }
                    )

    print(f"[roto-absolute] solving {len(jobs)} tracks with {args.workers} workers", flush=True)
    solved: dict[tuple[str, str, str, str], SolvedTrack] = {}
    completed = 0
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(solve_track_worker, job) for job in jobs]
        for fut in as_completed(futures):
            raw = fut.result()
            layout_name = raw["layout"]
            fit = transforms[layout_name]
            xyz_autopos = np.asarray(raw["xyz_autopos_mm"], dtype=float)
            xyz_opti = apply_transform(xyz_autopos, fit) if xyz_autopos.size else np.empty((0, 3), dtype=float)
            key = (layout_name, raw["tag_method"], raw["capture_id"], raw["tag"])
            solved[key] = SolvedTrack(
                layout=layout_name,
                tag_method=raw["tag_method"],
                capture_id=raw["capture_id"],
                tag=raw["tag"],
                time_s=np.asarray(raw["time_s"], dtype=float),
                xyz_autopos_mm=xyz_autopos,
                xyz_opti_frame_mm=xyz_opti,
                residual_rms_mm=np.asarray(raw["residual_rms_mm"], dtype=float),
                anchors_input=np.asarray(raw["anchors_input"], dtype=float),
                anchors_used=np.asarray(raw["anchors_used"], dtype=float),
                source_tr_all=raw["source_tr_all"],
            )
            completed += 1
            if completed == 1 or completed % 25 == 0 or completed == len(jobs):
                print(f"[roto-absolute] solved {completed}/{len(jobs)} tracks", flush=True)

    primary_tracks_by_capture: dict[str, dict[str, SolvedTrack]] = {}
    for capture_id in capture_ids:
        tracks = {}
        for tag in UWB_TAGS:
            key = (PRIMARY_LAYOUT, PRIMARY_TAG_METHOD, capture_id, tag)
            if key in solved:
                tracks[tag] = solved[key]
        primary_tracks_by_capture[capture_id] = tracks

    print("[roto-absolute] deciding WandB/WandC mapping", flush=True)
    mapping, mapping_rows = build_mapping_decision(
        primary_tracks_by_capture,
        opti_by_capture,
        coarse_step_s=max(0.10, float(args.coarse_step_s) * 2.0),
        min_points=int(args.min_points),
    )
    write_csv(tables_dir / "roto_wand_mapping_decision.csv", mapping_rows)

    offset_rows: list[dict] = []
    candidate_rows: list[dict] = []
    print("[roto-absolute] estimating capture-level time offsets from v4-io/T4", flush=True)
    for capture_id in capture_ids:
        offset, candidates = estimate_capture_offset(
            capture_id,
            primary_tracks_by_capture[capture_id],
            opti_by_capture[capture_id],
            mapping,
            coarse_step_s=float(args.coarse_step_s),
            refine_step_s=float(args.refine_step_s),
            min_points=int(args.min_points),
        )
        offset_rows.append(offset)
        candidate_rows.extend(candidates)
        print(
            f"[roto-absolute] {capture_id} beta={offset.get('beta_s', float('nan')):.3f}s "
            f"score={offset.get('score_median_3d_mm', float('nan')):.1f}mm",
            flush=True,
        )
    write_csv(tables_dir / "roto_time_offsets_v4io_T4.csv", offset_rows)
    write_csv(tables_dir / "roto_time_alignment_candidates_v4io_T4.csv", candidate_rows)
    beta_by_capture = {r["capture_id"]: float(r["beta_s"]) for r in offset_rows if r.get("status") == "ok" and math.isfinite(float(r["beta_s"]))}

    print("[roto-absolute] evaluating all solver tracks with fixed capture offsets", flush=True)
    track_rows: list[dict] = []
    sample_rows_primary: list[dict] = []
    for layout_name in layouts:
        for method in tag_methods:
            for capture_id in capture_ids:
                if capture_id not in beta_by_capture:
                    continue
                beta = beta_by_capture[capture_id]
                for tag in UWB_TAGS:
                    key = (layout_name, method, capture_id, tag)
                    if key not in solved:
                        continue
                    marker = mapping[tag]
                    row, samples = evaluate_track(
                        solved[key],
                        opti_by_capture[capture_id][marker],
                        beta_s=beta,
                        layout_det=transforms[layout_name].det,
                        layout_anchor_rms_mm=layout_anchor_rms[layout_name],
                        include_samples=(layout_name == PRIMARY_LAYOUT and method == PRIMARY_TAG_METHOD),
                    )
                    row["opti_marker"] = marker
                    row["time_offset_source"] = "capture-level v4-io/T4 trajectory fit"
                    track_rows.append(row)
                    sample_rows_primary.extend(samples)
    write_csv(tables_dir / "roto_abs_per_track.csv", track_rows)
    write_csv(tables_dir / "roto_abs_samples_v4io_T4.csv", sample_rows_primary)

    sensitivity_rows: list[dict] = []
    for capture_id in capture_ids:
        if capture_id not in beta_by_capture:
            continue
        for delta in [-0.5, -0.2, -0.1, -0.05, 0.0, 0.05, 0.1, 0.2, 0.5]:
            score, count = score_beta(
                primary_tracks_by_capture[capture_id],
                opti_by_capture[capture_id],
                mapping,
                beta_by_capture[capture_id] + delta,
                min_points=int(args.min_points),
            )
            sensitivity_rows.append(
                {
                    "capture_id": capture_id,
                    "delta_from_best_s": delta,
                    "beta_s": beta_by_capture[capture_id] + delta,
                    "score_median_3d_mm": score,
                    "n_overlap": count,
                }
            )
    write_csv(tables_dir / "roto_time_sensitivity_v4io_T4.csv", sensitivity_rows)

    summary_rows = aggregate_summary(track_rows, sample_rows_primary)
    write_csv(tables_dir / "roto_abs_summary_by_solver.csv", summary_rows)

    plot_primary_cdf(sample_rows_primary, figs_dir / "roto_abs_cdf_v4io_T4.png")
    plot_primary_per_capture(track_rows, figs_dir / "roto_abs_per_capture_v4io_T4.png")
    plot_primary_axis_split(track_rows, figs_dir / "roto_xy_vertical_split_v4io_T4.png")
    plot_solver_matrix(summary_rows, figs_dir / "roto_solver_matrix_median3d.png")
    generate_report(
        reports_dir / "ROTO_ABSOLUTE_ANALYSIS.md",
        summary_rows=summary_rows,
        offset_rows=offset_rows,
        mapping=mapping,
        mapping_rows=mapping_rows,
    )

    append_run_meta(
        out_root,
        {
            "script": str(THIS),
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "official_root": str(official_root),
            "opti_dir": str(opti_dir),
            "captures_root": str(captures_root),
            "layouts": layouts,
            "tag_methods": tag_methods,
            "primary_offset_solver": f"{PRIMARY_LAYOUT}/{PRIMARY_TAG_METHOD}",
            "mapping": mapping,
            "axis_convention": "OptiTrack frame; Y vertical; horizontal plane X/Z",
            "spatial_alignment": "anchor-locked reflection-allowed rigid Kabsch, no scale",
            "time_alignment": "one beta per ROTO capture from primary trajectory; reused by all solvers",
            "elapsed_s": time.time() - t0,
            "inputs": {
                "layout_base": str(layout_base),
                "anchor_sigma": str(sigma_path),
                "anchor_sigma_sha256": sha256_file(sigma_path),
                "full_opti_raw_is_gitignored": True,
            },
        },
    )

    primary = next((r for r in summary_rows if r["layout"] == PRIMARY_LAYOUT and r["tag_method"] == PRIMARY_TAG_METHOD), None)
    if primary:
        print(
            "[roto-absolute] primary v4-io/T4 "
            f"track-median 3D P50={primary['err3d_p50_track_median_mm']:.1f}mm "
            f"P95={primary['err3d_p95_track_median_mm']:.1f}mm "
            f"sample P50={primary.get('sample_weighted_err3d_p50_mm', float('nan')):.1f}mm "
            f"sample P95={primary.get('sample_weighted_err3d_p95_mm', float('nan')):.1f}mm",
            flush=True,
        )
    print(f"[roto-absolute] done in {time.time() - t0:.1f}s -> {out_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
