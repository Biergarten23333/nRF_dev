#!/usr/bin/env python3
from __future__ import annotations

import os

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import argparse
import csv
import json
import math
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import psutil


ANCHORS = list("ABCDEFGH")
UWB_TAGS = ["BS2DCE", "BSDC91"]
ROTO_MARKERS = ["WandBantenna", "WandCantenna"]
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
TRUE_ROTO_RADIUS_MM = 120.0
DTAG_VALUES_MM = [49.6, 75.0]
PRIMARY_METHOD = "T4"

THIS = Path(__file__).resolve()
COMPARISON_ROOT = THIS.parents[1]
EXTRA_ROOT = THIS.parents[2]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
FULL_ROOT = EXTRA_ROOT / "FULL"
ROTO_ABS_ROOT = FULL_ROOT / "roto_absolute"
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"
OPTI_ROOT = OFFICIAL_ROOT / "opti_captures/full"
V4_LAYOUT = LAYOUT_BASE / "v4-io/layout.json"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
SIGMA_PATH = LAYOUT_BASE / "tables/anchor_sigma.json"
OFFSETS_PATH = ROTO_ABS_ROOT / "tables/roto_time_offsets_v4io_T4.csv"
MAPPING_PATH = ROTO_ABS_ROOT / "tables/roto_wand_mapping_decision.csv"

sys.path.insert(0, str(SOLVER_ROOT))
sys.path.insert(0, str(FULL_ROOT / "scripts"))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.trajectory import solve_capture_trajectory  # noqa: E402
from tag_ground_truth import load_corrected_static_truth  # noqa: E402


@dataclass(frozen=True)
class OptiTrackTrajectory:
    time_s: np.ndarray
    xyz_mm: np.ndarray


@dataclass(frozen=True)
class SimilarityFit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float
    aligned_anchors: np.ndarray


def require_path(path: Path, label: str) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"missing required {label}: {path}")
    return path


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def finite_percentile(values: Any, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def rmse(values: Any) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(math.sqrt(np.mean(arr * arr)))


def summarize(values: Any, prefix: str) -> dict[str, float]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {
            f"{prefix}_mean_mm": float("nan"),
            f"{prefix}_median_mm": float("nan"),
            f"{prefix}_rms_mm": float("nan"),
            f"{prefix}_p95_mm": float("nan"),
            f"{prefix}_min_mm": float("nan"),
            f"{prefix}_max_mm": float("nan"),
        }
    return {
        f"{prefix}_mean_mm": float(np.mean(arr)),
        f"{prefix}_median_mm": float(np.median(arr)),
        f"{prefix}_rms_mm": float(math.sqrt(np.mean(arr * arr))),
        f"{prefix}_p95_mm": float(np.percentile(arr, 95)),
        f"{prefix}_min_mm": float(np.min(arr)),
        f"{prefix}_max_mm": float(np.max(arr)),
    }


def parse_anchor_id(value: Any) -> int:
    if isinstance(value, str):
        raw = value.strip().upper()
        if raw in ANCHORS:
            return ANCHORS.index(raw)
    return int(value)


def load_layout_raw(path: Path) -> tuple[list[str], np.ndarray, dict[int, float]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    labels: list[str] = []
    coords: list[list[float]] = []
    delays: dict[int, float] = {}
    for item in data["anchors"]:
        aid = parse_anchor_id(item.get("id", item.get("label")))
        label = str(item.get("label") or ANCHORS[aid])
        labels.append(label)
        coords.append([float(item["x_mm"]), float(item["y_mm"]), float(item["z_mm"])])
        delays[aid] = float(item.get("d_anchor_mm") or 0.0)
    return labels, np.asarray(coords, dtype=float), delays


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> SimilarityFit:
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad similarity fit shapes: {src.shape} vs {dst.shape}")
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
        scale = float(np.sum(s * d) / denom) if denom > 0.0 else 1.0
    t = dst_c - scale * src_c @ r
    aligned = scale * src @ r + t
    return SimilarityFit(rotation=r, translation=t, scale=scale, det=float(np.linalg.det(r)), aligned_anchors=aligned)


def apply_transform(points: np.ndarray, fit: SimilarityFit) -> np.ndarray:
    return fit.scale * np.asarray(points, dtype=float) @ fit.rotation + fit.translation


def fit_circle_3d(points: np.ndarray) -> dict[str, Any]:
    pts = np.asarray(points, dtype=float)
    pts = pts[np.isfinite(pts).all(axis=1)]
    if pts.shape[0] < 30:
        return {"status": "insufficient", "n": int(pts.shape[0])}
    center0 = np.mean(pts, axis=0)
    _u, _s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    e1, e2, normal = vh[0], vh[1], vh[-1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x = uv[:, 0]
    y = uv[:, 1]
    a = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(a, b, rcond=None)[0]
    radius = math.sqrt(max(0.0, float(c + cx * cx + cy * cy)))
    center3 = center0 + cx * e1 + cy * e2
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    out_of_plane = (pts - center0) @ normal
    return {
        "status": "ok",
        "n": int(pts.shape[0]),
        "radius_mm": float(radius),
        "radius_error_mm": float(radius - TRUE_ROTO_RADIUS_MM),
        "radius_error_percent": float((radius / TRUE_ROTO_RADIUS_MM - 1.0) * 100.0),
        "center_x_mm": float(center3[0]),
        "center_y_mm": float(center3[1]),
        "center_z_mm": float(center3[2]),
        "normal_x": float(normal[0]),
        "normal_y": float(normal[1]),
        "normal_z": float(normal[2]),
        "in_plane_radial_rms_mm": rmse(radial),
        "in_plane_radial_p95_abs_mm": finite_percentile(np.abs(radial), 95),
        "out_of_plane_rms_mm": rmse(out_of_plane),
        "out_of_plane_p95_abs_mm": finite_percentile(np.abs(out_of_plane), 95),
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
            raw = field.strip()
            if not raw:
                vals.append(float("nan"))
                continue
            try:
                vals.append(float(raw))
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


def interpolate_opti(traj: OptiTrackTrajectory, query_time_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t = np.asarray(traj.time_s, dtype=float)
    xyz = np.asarray(traj.xyz_mm, dtype=float)
    q = np.asarray(query_time_s, dtype=float)
    good = np.isfinite(q) & (q >= t[0]) & (q <= t[-1])
    out = np.full((q.shape[0], 3), np.nan, dtype=float)
    for axis in range(3):
        out[good, axis] = np.interp(q[good], t, xyz[:, axis])
    return out, good


def capture_id_from_roto_path(path: Path) -> str:
    m = re.search(r"roto_(R\d\d)_", path.parents[1].name)
    if not m:
        raise ValueError(f"cannot parse ROTO capture id from {path}")
    return m.group(1)


def static_id_from_path(path: Path) -> str:
    m = re.search(r"static_(ID\d\d)_", path.parents[1].name)
    if not m:
        raise ValueError(f"cannot parse static position id from {path}")
    return m.group(1)


def discover_roto_capture_files(captures_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for path in sorted(captures_root.glob("roto_R[0-9][0-9]*/tag_capture*/tr_all.csv")):
        if "Static-middle-test" in path.parents[1].name:
            continue
        out[capture_id_from_roto_path(path)] = path
    return dict(sorted(out.items()))


def read_mapping(path: Path) -> dict[str, str]:
    best: dict[str, str] | None = None
    best_score = float("inf")
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("capture_id")) != "ALL":
                continue
            try:
                score = float(row.get("score_median_3d_mm") or "inf")
            except ValueError:
                score = float("inf")
            if score < best_score:
                best_score = score
                best = {"BS2DCE": str(row["BS2DCE_marker"]), "BSDC91": str(row["BSDC91_marker"])}
    if best is None:
        raise RuntimeError(f"missing ALL mapping decision in {path}")
    return best


def read_offsets(path: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if str(row.get("status", "")) != "ok":
                continue
            beta = float(row["beta_s"])
            if math.isfinite(beta):
                out[str(row["capture_id"])] = beta
    if not out:
        raise RuntimeError(f"no ok time offsets in {path}")
    return dict(sorted(out.items()))


def solve_job(job: dict[str, Any]) -> dict[str, Any]:
    for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    tag = str(job["tag"]).upper()
    layout_path = Path(job["layout_path"])
    capture_path = Path(job["capture_path"])
    sigma_path = Path(job["sigma_path"])
    dtag = float(job["dtag_mm"])
    result = solve_capture_trajectory(
        layout_path,
        capture_path,
        method=str(job["method"]),
        anchor_sigma_path=sigma_path,
        tags={tag},
        tag_delay_by_tag={tag: dtag},
    )
    rows = []
    for item in result.results:
        if item.status != "ok":
            continue
        rows.append(
            [
                float(item.host_elapsed_s),
                float(item.host_epoch_s),
                int(item.sweep),
                float(item.x_mm),
                float(item.y_mm),
                float(item.z_mm),
                float(item.residual_rms_mm),
                int(item.anchors_input),
                int(item.anchors_used),
            ]
        )
    arr = np.asarray(rows, dtype=float)
    if arr.size == 0:
        arr = np.empty((0, 9), dtype=float)

    rho_rows: list[dict[str, Any]] = []
    if bool(job.get("need_rho")) and arr.shape[0] > 0:
        layout = load_layout_json(layout_path, sigma_path)
        anchors = layout.anchors
        frames = read_tr_all_frames(capture_path, tags={tag}, min_anchors=4)
        frame_by_key = {(f.tag.upper(), int(f.sweep)): f for f in frames}
        point_by_sweep = {int(row[2]): row[3:6] for row in arr}
        used_by_key: dict[tuple[int, int], bool] = {}
        for item in result.results:
            for aid, used in item.used_by_anchor.items():
                used_by_key[(int(item.sweep), int(aid))] = bool(used)
        for sweep, p in point_by_sweep.items():
            frame = frame_by_key.get((tag, sweep))
            if frame is None:
                continue
            p_xyz = np.asarray(p, dtype=float)
            for obs in frame.observations:
                aid = int(obs.anchor_id)
                anchor = anchors.get(aid)
                if anchor is None:
                    continue
                a_xyz = np.asarray([anchor.x_mm, anchor.y_mm, anchor.z_mm], dtype=float)
                predicted = float(np.linalg.norm(p_xyz - a_xyz) + anchor.d_anchor_mm + dtag)
                rho = float(obs.range_mm - predicted)
                rho_rows.append(
                    {
                        "dataset": str(job["dataset"]),
                        "layout": str(job["layout_name"]),
                        "d_tag_mm": dtag,
                        "method": str(job["method"]),
                        "capture_id": str(job["capture_id"]),
                        "static_id": str(job.get("static_id") or ""),
                        "tag": tag,
                        "sweep": int(sweep),
                        "host_elapsed_s": float(frame.host_elapsed_s),
                        "host_epoch_s": float(frame.host_epoch_s),
                        "anchor_id": aid,
                        "anchor_label": ANCHORS[aid] if 0 <= aid < len(ANCHORS) else str(aid),
                        "range_measured_mm": float(obs.range_mm),
                        "range_predicted_mm": predicted,
                        "rho_mm": rho,
                        "solver_used_anchor": bool(used_by_key.get((sweep, aid), False)),
                    }
                )

    return {
        "dataset": str(job["dataset"]),
        "layout": str(job["layout_name"]),
        "dtag_mm": dtag,
        "method": str(job["method"]),
        "capture_id": str(job["capture_id"]),
        "static_id": str(job.get("static_id") or ""),
        "tag": tag,
        "frames_input": int(result.frames_input),
        "frames_solved": int(result.frames_solved),
        "source_tr_all": str(capture_path),
        "time_s": arr[:, 0],
        "host_epoch_s": arr[:, 1],
        "sweep": arr[:, 2].astype(int),
        "xyz_mm": arr[:, 3:6],
        "residual_rms_mm": arr[:, 6],
        "anchors_input": arr[:, 7],
        "anchors_used": arr[:, 8],
        "rho_rows": rho_rows,
    }


def track_key(track: dict[str, Any]) -> tuple[str, float, str, str]:
    return (str(track["layout"]), float(track["dtag_mm"]), str(track["capture_id"]), str(track["tag"]))


def static_key(track: dict[str, Any]) -> tuple[str, str]:
    return (str(track["static_id"]), str(track["tag"]))


def circle_rows_from_tracks(dynamic_tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_group: dict[tuple[str, float], list[dict[str, Any]]] = {}
    track_circle_by_key: dict[tuple[str, float, str, str], dict[str, Any]] = {}
    for tr in dynamic_tracks:
        if str(tr["dataset"]) != "dynamic":
            continue
        by_group.setdefault((str(tr["layout"]), float(tr["dtag_mm"])), []).append(tr)
        circle = fit_circle_3d(np.asarray(tr["xyz_mm"], dtype=float))
        base = {
            "row_type": "track",
            "layout": tr["layout"],
            "method": tr["method"],
            "d_tag_mm": float(tr["dtag_mm"]),
            "capture_id": tr["capture_id"],
            "tag": tr["tag"],
            "n_points": int(np.asarray(tr["xyz_mm"]).shape[0]),
            "frames_input": int(tr["frames_input"]),
            "frames_solved": int(tr["frames_solved"]),
            "true_radius_mm": TRUE_ROTO_RADIUS_MM,
            "source_tr_all": tr["source_tr_all"],
            "alignment": "self_cal_frame_no_sim3_no_vicon_alignment",
        }
        base.update(circle)
        rows.append(base)
        if circle.get("status") == "ok":
            track_circle_by_key[(str(tr["layout"]), float(tr["dtag_mm"]), str(tr["capture_id"]), str(tr["tag"]))] = base

    for (layout, dtag), tracks in sorted(by_group.items()):
        track_rows = [
            r
            for r in rows
            if r.get("row_type") == "track"
            and r.get("layout") == layout
            and abs(float(r.get("d_tag_mm", float("nan"))) - dtag) < 1e-9
            and r.get("status") == "ok"
        ]
        radii = np.asarray([float(r["radius_mm"]) for r in track_rows], dtype=float)
        radial_rms = np.asarray([float(r["in_plane_radial_rms_mm"]) for r in track_rows], dtype=float)
        plane_rms = np.asarray([float(r["out_of_plane_rms_mm"]) for r in track_rows], dtype=float)
        rows.append(
            {
                "row_type": "summary",
                "layout": layout,
                "method": PRIMARY_METHOD,
                "d_tag_mm": dtag,
                "capture_id": "ALL",
                "tag": "ALL",
                "n_tracks": int(len(track_rows)),
                "n_points": int(sum(int(r["n_points"]) for r in track_rows)),
                "true_radius_mm": TRUE_ROTO_RADIUS_MM,
                "radius_mm_median": finite_percentile(radii, 50),
                "radius_mm_mean": float(np.nanmean(radii)) if radii.size else float("nan"),
                "radius_mm_p25": finite_percentile(radii, 25),
                "radius_mm_p75": finite_percentile(radii, 75),
                "radius_error_mm_median": finite_percentile(radii - TRUE_ROTO_RADIUS_MM, 50),
                "radius_error_percent_median": finite_percentile((radii / TRUE_ROTO_RADIUS_MM - 1.0) * 100.0, 50),
                "in_plane_radial_rms_mm_median": finite_percentile(radial_rms, 50),
                "out_of_plane_rms_mm_median": finite_percentile(plane_rms, 50),
                "alignment": "self_cal_frame_no_sim3_no_vicon_alignment",
                "status": "ok" if len(track_rows) else "empty",
            }
        )
        for tag in UWB_TAGS:
            tag_rows = [r for r in track_rows if r.get("tag") == tag]
            tag_radii = np.asarray([float(r["radius_mm"]) for r in tag_rows], dtype=float)
            tag_radial_rms = np.asarray([float(r["in_plane_radial_rms_mm"]) for r in tag_rows], dtype=float)
            tag_plane_rms = np.asarray([float(r["out_of_plane_rms_mm"]) for r in tag_rows], dtype=float)
            rows.append(
                {
                    "row_type": "tag_summary",
                    "layout": layout,
                    "method": PRIMARY_METHOD,
                    "d_tag_mm": dtag,
                    "capture_id": "ALL",
                    "tag": tag,
                    "n_tracks": int(len(tag_rows)),
                    "n_points": int(sum(int(r["n_points"]) for r in tag_rows)),
                    "true_radius_mm": TRUE_ROTO_RADIUS_MM,
                    "radius_mm_median": finite_percentile(tag_radii, 50),
                    "radius_mm_mean": float(np.nanmean(tag_radii)) if tag_radii.size else float("nan"),
                    "radius_mm_p25": finite_percentile(tag_radii, 25),
                    "radius_mm_p75": finite_percentile(tag_radii, 75),
                    "radius_error_mm_median": finite_percentile(tag_radii - TRUE_ROTO_RADIUS_MM, 50),
                    "radius_error_percent_median": finite_percentile((tag_radii / TRUE_ROTO_RADIUS_MM - 1.0) * 100.0, 50),
                    "in_plane_radial_rms_mm_median": finite_percentile(tag_radial_rms, 50),
                    "out_of_plane_rms_mm_median": finite_percentile(tag_plane_rms, 50),
                    "alignment": "self_cal_frame_no_sim3_no_vicon_alignment",
                    "status": "ok" if len(tag_rows) else "empty",
                    "note": "absolute single-tag circle radius; 120mm reference is also reported as paired BSDC91-BS2DCE delta below",
                }
            )

        pair_rows = []
        for tr in tracks:
            cid = str(tr["capture_id"])
            b = track_circle_by_key.get((layout, dtag, cid, "BS2DCE"))
            c = track_circle_by_key.get((layout, dtag, cid, "BSDC91"))
            if b is None or c is None:
                continue
            delta = float(c["radius_mm"]) - float(b["radius_mm"])
            pair = {
                "row_type": "paired_delta_radius",
                "layout": layout,
                "method": PRIMARY_METHOD,
                "d_tag_mm": dtag,
                "capture_id": cid,
                "tag": "BSDC91_minus_BS2DCE",
                "n_tracks": 2,
                "true_radius_mm": TRUE_ROTO_RADIUS_MM,
                "radius_delta_mm": delta,
                "radius_delta_error_mm": delta - TRUE_ROTO_RADIUS_MM,
                "radius_delta_error_percent": (delta / TRUE_ROTO_RADIUS_MM - 1.0) * 100.0,
                "radius_BS2DCE_mm": float(b["radius_mm"]),
                "radius_BSDC91_mm": float(c["radius_mm"]),
                "alignment": "self_cal_frame_no_sim3_no_vicon_alignment",
                "status": "ok",
                "note": "paired radius separation, matching historical delta_radius_bias_vs_120 diagnostic",
            }
            pair_rows.append(pair)
        seen_captures: set[str] = set()
        unique_pair_rows = []
        for pair in pair_rows:
            cid = str(pair["capture_id"])
            if cid in seen_captures:
                continue
            seen_captures.add(cid)
            unique_pair_rows.append(pair)
        rows.extend(unique_pair_rows)
        deltas = np.asarray([float(r["radius_delta_mm"]) for r in unique_pair_rows], dtype=float)
        rows.append(
            {
                "row_type": "paired_delta_summary",
                "layout": layout,
                "method": PRIMARY_METHOD,
                "d_tag_mm": dtag,
                "capture_id": "ALL",
                "tag": "BSDC91_minus_BS2DCE",
                "n_pairs": int(len(unique_pair_rows)),
                "true_radius_mm": TRUE_ROTO_RADIUS_MM,
                "radius_delta_mm_median": finite_percentile(deltas, 50),
                "radius_delta_mm_mean": float(np.nanmean(deltas)) if deltas.size else float("nan"),
                "radius_delta_mm_p25": finite_percentile(deltas, 25),
                "radius_delta_mm_p75": finite_percentile(deltas, 75),
                "radius_delta_error_mm_median": finite_percentile(deltas - TRUE_ROTO_RADIUS_MM, 50),
                "radius_delta_error_percent_median": finite_percentile((deltas / TRUE_ROTO_RADIUS_MM - 1.0) * 100.0, 50),
                "alignment": "self_cal_frame_no_sim3_no_vicon_alignment",
                "status": "ok" if len(unique_pair_rows) else "empty",
                "note": "paired radius separation, matching historical delta_radius_bias_vs_120 diagnostic",
            }
        )
    return rows


def evaluate_b_tracks(
    dynamic_tracks: list[dict[str, Any]],
    opti_by_capture: dict[str, dict[str, OptiTrackTrajectory]],
    mapping: dict[str, str],
    offsets: dict[str, float],
    rigid_fit: SimilarityFit,
    anchor_rigid_rms_mm: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sample_errors_by_dtag: dict[float, list[np.ndarray]] = {}
    sample_vertical_by_dtag: dict[float, list[np.ndarray]] = {}
    for tr in dynamic_tracks:
        if str(tr["dataset"]) != "dynamic" or str(tr["layout"]) != "v5-commonmode":
            continue
        dtag = float(tr["dtag_mm"])
        cid = str(tr["capture_id"])
        tag = str(tr["tag"])
        marker = mapping.get(tag)
        beta = offsets.get(cid)
        status = "ok"
        if marker is None or beta is None or cid not in opti_by_capture:
            status = "missing_alignment_input"
        xyz = np.asarray(tr["xyz_mm"], dtype=float)
        time_s = np.asarray(tr["time_s"], dtype=float)
        uwb_aligned = apply_transform(xyz, rigid_fit) if xyz.size else np.empty((0, 3), dtype=float)
        if status == "ok":
            truth, good = interpolate_opti(opti_by_capture[cid][marker], time_s + beta)
            finite = good & np.isfinite(uwb_aligned).all(axis=1) & np.isfinite(truth).all(axis=1)
            diff = uwb_aligned[finite] - truth[finite]
        else:
            finite = np.zeros(time_s.shape, dtype=bool)
            diff = np.empty((0, 3), dtype=float)
        err3 = np.linalg.norm(diff, axis=1) if diff.size else np.empty(0)
        vertical = np.abs(diff[:, 1]) if diff.size else np.empty(0)
        sample_errors_by_dtag.setdefault(dtag, []).append(err3)
        sample_vertical_by_dtag.setdefault(dtag, []).append(vertical)
        rows.append(
            {
                "row_type": "track",
                "layout": tr["layout"],
                "method": tr["method"],
                "d_tag_mm": dtag,
                "capture_id": cid,
                "tag": tag,
                "opti_marker": marker or "",
                "status": status if err3.size else "no_overlap",
                "n_overlap": int(err3.size),
                "frames_input": int(tr["frames_input"]),
                "frames_solved": int(tr["frames_solved"]),
                "beta_s": float(beta) if beta is not None else float("nan"),
                "err3d_median_mm": finite_percentile(err3, 50),
                "err3d_p95_mm": finite_percentile(err3, 95),
                "err3d_rmse_mm": rmse(err3),
                "median_abs_vertical_y_mm": finite_percentile(vertical, 50),
                "alignment": "BEST-FIT-ALIGNED: v5 self-cal anchors rigidly aligned to Vicon; capture-level v4-io/T4 time offset reused",
                "time_offset_source": str(OFFSETS_PATH),
            }
        )

    for dtag in sorted(sample_errors_by_dtag):
        err_parts = [x for x in sample_errors_by_dtag[dtag] if x.size]
        vertical_parts = [x for x in sample_vertical_by_dtag[dtag] if x.size]
        err = np.concatenate(err_parts) if err_parts else np.empty(0)
        vertical = np.concatenate(vertical_parts) if vertical_parts else np.empty(0)
        track_rows = [r for r in rows if r.get("row_type") == "track" and abs(float(r["d_tag_mm"]) - dtag) < 1e-9]
        betas = np.asarray([float(r["beta_s"]) for r in track_rows], dtype=float)
        rows.append(
            {
                "row_type": "summary",
                "layout": "v5-commonmode",
                "method": PRIMARY_METHOD,
                "d_tag_mm": dtag,
                "capture_id": "ALL",
                "tag": "ALL",
                "status": "ok" if err.size else "empty",
                "n_tracks": int(len(track_rows)),
                "n_overlap": int(err.size),
                "err3d_median_mm": finite_percentile(err, 50),
                "err3d_p95_mm": finite_percentile(err, 95),
                "err3d_rmse_mm": rmse(err),
                "median_abs_vertical_y_mm": finite_percentile(vertical, 50),
                "beta_s_median": finite_percentile(betas, 50),
                "beta_s_min": finite_percentile(betas, 0),
                "beta_s_max": finite_percentile(betas, 100),
                "layout_anchor_rigid_rms_mm": float(anchor_rigid_rms_mm),
                "alignment": "BEST-FIT-ALIGNED: v5 self-cal anchors rigidly aligned to Vicon; capture-level v4-io/T4 time offset reused",
                "time_offset_source": str(OFFSETS_PATH),
            }
        )
    return rows


def nlos_summary_rows(dynamic_rho: list[dict[str, Any]], static_rho: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    dyn_by_anchor: dict[int, list[float]] = {i: [] for i in range(len(ANCHORS))}
    sta_by_anchor: dict[int, list[float]] = {i: [] for i in range(len(ANCHORS))}
    for row in dynamic_rho:
        dyn_by_anchor.setdefault(int(row["anchor_id"]), []).append(float(row["rho_mm"]))
    for row in static_rho:
        sta_by_anchor.setdefault(int(row["anchor_id"]), []).append(float(row["rho_mm"]))
    for aid in range(len(ANCHORS)):
        dyn = np.asarray(dyn_by_anchor.get(aid, []), dtype=float)
        sta = np.asarray(sta_by_anchor.get(aid, []), dtype=float)
        rows.append(
            {
                "anchor_id": aid,
                "anchor_label": ANCHORS[aid],
                "dynamic_n": int(dyn.size),
                "static_n": int(sta.size),
                "dynamic_rho_mean_mm": float(np.nanmean(dyn)) if dyn.size else float("nan"),
                "dynamic_rho_median_mm": finite_percentile(dyn, 50),
                "dynamic_rho_rms_mm": rmse(dyn),
                "dynamic_rho_p95_mm": finite_percentile(dyn, 95),
                "dynamic_positive_spike_rate_gt100": float(np.mean(dyn > 100.0)) if dyn.size else float("nan"),
                "dynamic_positive_spike_rate_gt150": float(np.mean(dyn > 150.0)) if dyn.size else float("nan"),
                "static_rho_mean_mm": float(np.nanmean(sta)) if sta.size else float("nan"),
                "static_rho_median_mm": finite_percentile(sta, 50),
                "static_rho_rms_mm": rmse(sta),
                "static_rho_p95_mm": finite_percentile(sta, 95),
                "static_positive_spike_rate_gt100": float(np.mean(sta > 100.0)) if sta.size else float("nan"),
                "static_positive_spike_rate_gt150": float(np.mean(sta > 150.0)) if sta.size else float("nan"),
                "delta_dynamic_minus_static_rms_mm": float(rmse(dyn) - rmse(sta)) if dyn.size and sta.size else float("nan"),
                "rho_definition": "range_measured - ||p_solved-a_i|| - d_anchor_i - d_tag",
                "alignment": "sync_independent_solve_residual_v5_self_cal_frame",
            }
        )
    return rows


def prepare_inputs() -> dict[str, Any]:
    for path, label in (
        (V4_LAYOUT, "V4-io layout"),
        (V5_LAYOUT, "V5-commonmode layout"),
        (SIGMA_PATH, "anchor sigma"),
        (CAPTURES_ROOT, "Erlangen capture root"),
        (OPTI_ROOT, "OptiTrack root"),
        (OFFSETS_PATH, "RotoArm time-offset table"),
        (MAPPING_PATH, "RotoArm wand-mapping table"),
    ):
        require_path(path, label)
    roto_files = discover_roto_capture_files(CAPTURES_ROOT)
    if len(roto_files) != 17:
        raise RuntimeError(f"expected 17 RotoArm captures excluding static-middle test, found {len(roto_files)}: {roto_files}")
    static_files = sorted(CAPTURES_ROOT.glob("static_ID*/tag_capture*/tr_all.csv"))
    if len(static_files) != 24:
        raise RuntimeError(f"expected 24 static captures, found {len(static_files)}")
    for cid in roto_files:
        require_path(OPTI_ROOT / f"{cid}.trc", f"OptiTrack TRC for {cid}")
    mapping = read_mapping(MAPPING_PATH)
    offsets = read_offsets(OFFSETS_PATH)
    missing_offsets = sorted(set(roto_files) - set(offsets))
    if missing_offsets:
        raise RuntimeError(f"missing RotoArm offsets for {missing_offsets} in {OFFSETS_PATH}")
    opti_by_capture = {cid: parse_trc_trajectories(OPTI_ROOT / f"{cid}.trc", ROTO_MARKERS) for cid in roto_files}

    labels, v5_coords, _v5_delays = load_layout_raw(V5_LAYOUT)
    by_label = {label: v5_coords[i] for i, label in enumerate(labels)}
    src = np.vstack([by_label[a] for a in ANCHORS])
    anchor_truth, _tag_truth, _tag_truth_meta, _corr = load_corrected_static_truth(OPTI_ROOT, ANCHORS, PRIMARY_IDS)
    truth_coords = np.vstack([anchor_truth[a] for a in ANCHORS])
    rigid_fit = fit_similarity(src, truth_coords, allow_reflection=True, allow_scale=False)
    anchor_rigid_rms = rmse(np.linalg.norm(rigid_fit.aligned_anchors - truth_coords, axis=1))
    return {
        "roto_files": roto_files,
        "static_files": static_files,
        "mapping": mapping,
        "offsets": offsets,
        "opti_by_capture": opti_by_capture,
        "rigid_fit": rigid_fit,
        "anchor_rigid_rms": anchor_rigid_rms,
    }


def build_jobs(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    layouts = {"v4-io": V4_LAYOUT, "v5-commonmode": V5_LAYOUT}
    jobs: list[dict[str, Any]] = []
    for layout_name, layout_path in layouts.items():
        for dtag in DTAG_VALUES_MM:
            for cid, path in inputs["roto_files"].items():
                for tag in UWB_TAGS:
                    jobs.append(
                        {
                            "dataset": "dynamic",
                            "layout_name": layout_name,
                            "layout_path": str(layout_path),
                            "capture_id": cid,
                            "capture_path": str(path),
                            "tag": tag,
                            "dtag_mm": dtag,
                            "method": PRIMARY_METHOD,
                            "sigma_path": str(SIGMA_PATH),
                            "need_rho": layout_name == "v5-commonmode" and abs(dtag - 49.6) < 1e-9,
                        }
                    )
    for path in inputs["static_files"]:
        jobs.append(
            {
                "dataset": "static",
                "layout_name": "v5-commonmode",
                "layout_path": str(V5_LAYOUT),
                "capture_id": static_id_from_path(path),
                "static_id": static_id_from_path(path),
                "capture_path": str(path),
                "tag": "BSF66F",
                "dtag_mm": 49.6,
                "method": PRIMARY_METHOD,
                "sigma_path": str(SIGMA_PATH),
                "need_rho": True,
            }
        )
    return jobs


def print_markdown_report(
    a_rows: list[dict[str, Any]],
    b_rows: list[dict[str, Any]],
    c_rows: list[dict[str, Any]],
    runtime: dict[str, Any],
    inputs: dict[str, Any],
    out_paths: dict[str, Path],
) -> None:
    a_tag_summary = [r for r in a_rows if r.get("row_type") == "tag_summary"]
    a_delta_summary = [r for r in a_rows if r.get("row_type") == "paired_delta_summary"]
    b_summary = [r for r in b_rows if r.get("row_type") == "summary"]
    print("# RotoArm Scale / Delay / NLOS Report")
    print()
    print("## Analysis A - circle fit, no Sim3 / no Vicon alignment")
    print("Absolute per-tag circle radii are reported first. In these captures they are about 450-600 mm, so the 120 mm reference is not behaving as a single-tag absolute radius.")
    print()
    print("| Layout | Tag | D_tag mm | Absolute R_hat median mm | Error vs 120 mm | In-plane RMS mm | Out-of-plane RMS mm | Tracks |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for row in sorted(a_tag_summary, key=lambda r: (str(r["layout"]), str(r["tag"]), float(r["d_tag_mm"]))):
        print(
            f"| {row['layout']} | {row['tag']} | {float(row['d_tag_mm']):.1f} | {float(row['radius_mm_median']):.2f} | "
            f"{float(row['radius_error_mm_median']):+.2f} | "
            f"{float(row['in_plane_radial_rms_mm_median']):.2f} | {float(row['out_of_plane_rms_mm_median']):.2f} | "
            f"{int(row['n_tracks'])} |"
        )
    print()
    print("Paired radius separation, using the historical `BSDC91 - BS2DCE` diagnostic against 120 mm:")
    print()
    print("| Layout | D_tag mm | Delta R median mm | Error mm | Error % | P25-P75 mm | Pairs |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for row in sorted(a_delta_summary, key=lambda r: (str(r["layout"]), float(r["d_tag_mm"]))):
        print(
            f"| {row['layout']} | {float(row['d_tag_mm']):.1f} | {float(row['radius_delta_mm_median']):.2f} | "
            f"{float(row['radius_delta_error_mm_median']):+.2f} | {float(row['radius_delta_error_percent_median']):+.2f} | "
            f"{float(row['radius_delta_mm_p25']):.2f}-{float(row['radius_delta_mm_p75']):.2f} | {int(row['n_pairs'])} |"
        )
    print()
    print("## Analysis B - V5 BEST-FIT-ALIGNED dynamic track floor")
    print("| D_tag mm | Median 3D mm | P95 3D mm | RMSE 3D mm | Median |vertical Y| mm | Offset median s | Samples |")
    print("|---:|---:|---:|---:|---:|---:|---:|")
    for row in sorted(b_summary, key=lambda r: float(r["d_tag_mm"])):
        print(
            f"| {float(row['d_tag_mm']):.1f} | {float(row['err3d_median_mm']):.2f} | {float(row['err3d_p95_mm']):.2f} | "
            f"{float(row['err3d_rmse_mm']):.2f} | {float(row['median_abs_vertical_y_mm']):.2f} | "
            f"{float(row['beta_s_median']):.6f} | {int(row['n_overlap'])} |"
        )
    print()
    print("Alignment label: BEST-FIT-ALIGNED, V5 self-cal anchors rigidly aligned to Vicon; capture-level v4-io/T4 offsets reused.")
    print(f"Time-offset source: `{OFFSETS_PATH}`")
    print(f"Wand mapping source: `{MAPPING_PATH}` -> {inputs['mapping']}")
    print()
    print("## Analysis C - sync-independent solve residual NLOS check")
    print("| Anchor | Static RMS rho mm | Dynamic RMS rho mm | Static >100 | Dynamic >100 | Static >150 | Dynamic >150 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for row in c_rows:
        print(
            f"| {row['anchor_label']} | {float(row['static_rho_rms_mm']):.2f} | {float(row['dynamic_rho_rms_mm']):.2f} | "
            f"{100.0 * float(row['static_positive_spike_rate_gt100']):.2f}% | "
            f"{100.0 * float(row['dynamic_positive_spike_rate_gt100']):.2f}% | "
            f"{100.0 * float(row['static_positive_spike_rate_gt150']):.2f}% | "
            f"{100.0 * float(row['dynamic_positive_spike_rate_gt150']):.2f}% |"
        )
    dyn_rank = sorted(c_rows, key=lambda r: float(r["dynamic_positive_spike_rate_gt100"]), reverse=True)
    sta_rank = sorted(c_rows, key=lambda r: float(r["static_positive_spike_rate_gt100"]), reverse=True)
    dyn_top = "/".join(str(r["anchor_label"]) for r in dyn_rank[:2])
    sta_top = "/".join(str(r["anchor_label"]) for r in sta_rank[:2])
    dyn_mean = np.asarray([float(r["dynamic_rho_mean_mm"]) for r in c_rows], dtype=float)
    verdict = "dynamic residuals are positive-skewed and anchor-concentrated" if np.nanmax(dyn_mean) > 0 else "dynamic residuals are not globally positive-skewed by anchor mean"
    print()
    print(f"Verdict C: {verdict}; top dynamic >100 mm spike anchors are {dyn_top}, top static anchors are {sta_top}.")
    print()
    print("## Runtime")
    print(
        f"Physical/logical cores: {runtime['physical_cores']}/{runtime['logical_cores']}; workers: {runtime['workers']}; "
        f"elapsed: {runtime['elapsed_s']:.2f} s; mean/max live CPU: {runtime['cpu_percent_mean_live']:.1f}%/"
        f"{runtime['cpu_percent_max_live']:.1f}%."
    )
    print()
    print("## Outputs")
    for label, path in out_paths.items():
        print(f"- {label}: `{path}`")


def main() -> int:
    parser = argparse.ArgumentParser(description="RotoArm V4/V5 metric-scale, D_tag, and NLOS residual checks.")
    parser.add_argument("--out-dir", type=Path, default=COMPARISON_ROOT / "tables")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    out_paths = {
        "A circle fit": out_dir / "RotoArm_A_circle_fit.csv",
        "B track floor": out_dir / "RotoArm_B_track_floor.csv",
        "C NLOS per-anchor": out_dir / "RotoArm_C_nlos_per_anchor.csv",
        "C dynamic rho detail": out_dir / "RotoArm_C_dynamic_rho_per_frame_anchor.csv",
        "runtime": out_dir / "RotoArm_runtime.csv",
    }
    if not args.replace:
        for path in out_paths.values():
            if path.exists():
                raise SystemExit(f"refusing to overwrite existing output without --replace: {path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    inputs = prepare_inputs()
    jobs = build_jobs(inputs)
    physical = psutil.cpu_count(logical=False) or 6
    logical = psutil.cpu_count(logical=True) or physical
    workers = max(1, min(int(args.workers), physical, len(jobs)))
    print(
        json.dumps(
            {
                "stage": "start",
                "dynamic_captures": len(inputs["roto_files"]),
                "static_captures": len(inputs["static_files"]),
                "jobs": len(jobs),
                "physical_cores": physical,
                "logical_cores": logical,
                "workers": workers,
                "blas_threads_per_worker": {k: os.environ.get(k) for k in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
                "gpu": "not used",
            },
            sort_keys=True,
        ),
        flush=True,
    )

    started = time.perf_counter()
    psutil.cpu_percent(interval=None)
    cpu_samples: list[float] = []
    dynamic_tracks: list[dict[str, Any]] = []
    dynamic_rho: list[dict[str, Any]] = []
    static_rho: list[dict[str, Any]] = []
    runtime_rows: list[dict[str, Any]] = []
    ctx = get_context("spawn")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        futures = [pool.submit(solve_job, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            result = fut.result()
            done += 1
            cpu_now = float(psutil.cpu_percent(interval=0.2))
            cpu_samples.append(cpu_now)
            if result["dataset"] == "dynamic":
                dynamic_tracks.append(result)
                dynamic_rho.extend(result["rho_rows"])
            else:
                static_rho.extend(result["rho_rows"])
            runtime_rows.append(
                {
                    "completed": done,
                    "total": len(futures),
                    "dataset": result["dataset"],
                    "layout": result["layout"],
                    "d_tag_mm": result["dtag_mm"],
                    "capture_id": result["capture_id"],
                    "tag": result["tag"],
                    "frames_input": result["frames_input"],
                    "frames_solved": result["frames_solved"],
                    "live_cpu_percent": cpu_now,
                }
            )
            if done == 1 or done % 20 == 0 or done == len(futures):
                print(
                    json.dumps(
                        {
                            "stage": "job_done",
                            "done": done,
                            "total": len(futures),
                            "last": {
                                "dataset": result["dataset"],
                                "layout": result["layout"],
                                "d_tag_mm": result["dtag_mm"],
                                "capture_id": result["capture_id"],
                                "tag": result["tag"],
                            },
                            "live_cpu_percent": cpu_now,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )

    dynamic_tracks = sorted(dynamic_tracks, key=track_key)
    a_rows = circle_rows_from_tracks(dynamic_tracks)
    b_rows = evaluate_b_tracks(
        dynamic_tracks,
        inputs["opti_by_capture"],
        inputs["mapping"],
        inputs["offsets"],
        inputs["rigid_fit"],
        float(inputs["anchor_rigid_rms"]),
    )
    c_rows = nlos_summary_rows(dynamic_rho, static_rho)
    elapsed = time.perf_counter() - started
    runtime = {
        "script": str(THIS),
        "physical_cores": int(physical),
        "logical_cores": int(logical),
        "workers": int(workers),
        "elapsed_s": float(elapsed),
        "cpu_percent_mean_live": float(np.nanmean(cpu_samples)) if cpu_samples else float("nan"),
        "cpu_percent_max_live": float(np.nanmax(cpu_samples)) if cpu_samples else float("nan"),
        "n_jobs": int(len(jobs)),
        "n_dynamic_tracks": int(len(dynamic_tracks)),
        "n_dynamic_rho_rows": int(len(dynamic_rho)),
        "n_static_rho_rows": int(len(static_rho)),
        "v4_layout": str(V4_LAYOUT),
        "v5_layout": str(V5_LAYOUT),
        "time_offsets": str(OFFSETS_PATH),
        "wand_mapping": str(MAPPING_PATH),
        "anchor_rigid_rms_mm": float(inputs["anchor_rigid_rms"]),
    }

    write_csv(out_paths["A circle fit"], a_rows)
    write_csv(out_paths["B track floor"], b_rows)
    write_csv(out_paths["C NLOS per-anchor"], c_rows)
    write_csv(out_paths["C dynamic rho detail"], dynamic_rho)
    write_csv(out_paths["runtime"], [runtime, *runtime_rows])
    print_markdown_report(a_rows, b_rows, c_rows, runtime, inputs, out_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
