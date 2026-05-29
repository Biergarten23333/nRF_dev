#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch


THIS = Path(__file__).resolve()
OFFICIAL_ROOT = THIS.parents[2]
REPO_ROOT = THIS.parents[4]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame  # noqa: E402


ANCHORS = list(range(8))


@dataclass
class Track:
    kind: str
    capture_id: str
    tag: str
    path: str
    frames: list[Frame]
    ranges_mm: np.ndarray
    quality: np.ndarray
    available: np.ndarray


def percentile(vals: list[float] | np.ndarray, pct: float) -> float:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def median(vals: list[float] | np.ndarray) -> float:
    return percentile(vals, 50.0)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def layout_paths() -> tuple[Path, Path]:
    base = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
    return base / "v4-io" / "layout.json", base / "tables" / "anchor_sigma.json"


def available_layouts() -> dict[str, Path]:
    base = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
    names = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
    return {name: base / name / "layout.json" for name in names if (base / name / "layout.json").exists()}


def frames_to_arrays(frames: list[Frame], max_frames: int = 0) -> tuple[list[Frame], np.ndarray, np.ndarray, np.ndarray]:
    frames = sorted(frames, key=lambda f: (f.host_epoch_s, f.tag, f.sweep))
    if max_frames > 0:
        frames = frames[:max_frames]
    ranges = np.zeros((len(frames), 8), dtype=np.float32)
    quality = np.full((len(frames), 8), 100.0, dtype=np.float32)
    available = np.zeros((len(frames), 8), dtype=bool)
    for i, frame in enumerate(frames):
        for obs in frame.observations:
            if 0 <= obs.anchor_id < 8 and obs.range_mm > 0:
                aid = int(obs.anchor_id)
                ranges[i, aid] = float(obs.range_mm)
                quality[i, aid] = float(obs.quality_percent if obs.quality_percent > 0 else 100.0)
                available[i, aid] = True
    return frames, ranges, quality, available


def load_static_tracks(max_frames: int = 0) -> list[Track]:
    root = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
    tracks: list[Track] = []
    for tr in sorted(root.glob("static_ID*/tag_capture*/tr_all.csv")):
        cap_name = tr.parents[1].name
        capture_id = cap_name.split("_", 2)[1]
        frames = read_tr_all_frames(tr, min_anchors=4)
        frames, ranges, quality, available = frames_to_arrays(frames, max_frames)
        tracks.append(Track("static", capture_id, "BSF66F", str(tr), frames, ranges, quality, available))
    return tracks


def load_roto_tracks(max_frames: int = 0) -> list[Track]:
    root = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
    tracks: list[Track] = []
    for tr in sorted(root.glob("roto_R[0-9][0-9]*/tag_capture*/tr_all.csv")):
        cap_name = tr.parents[1].name
        if "Static-middle-test" in cap_name:
            continue
        capture_id = cap_name.split("_", 2)[1]
        frames = read_tr_all_frames(tr, min_anchors=4)
        by_tag: dict[str, list[Frame]] = {}
        for frame in frames:
            by_tag.setdefault(frame.tag, []).append(frame)
        for tag, tag_frames in sorted(by_tag.items()):
            kept_frames, ranges, quality, available = frames_to_arrays(tag_frames, max_frames)
            tracks.append(Track("roto", capture_id, tag, str(tr), kept_frames, ranges, quality, available))
    return tracks


def pack_tracks(tracks: list[Track]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[int]]:
    if not tracks:
        raise ValueError("no tracks")
    lengths = [len(t.frames) for t in tracks]
    fmax = max(lengths)
    ranges = np.zeros((len(tracks), fmax, 8), dtype=np.float32)
    quality = np.full((len(tracks), fmax, 8), 100.0, dtype=np.float32)
    available = np.zeros((len(tracks), fmax, 8), dtype=bool)
    for i, track in enumerate(tracks):
        n = len(track.frames)
        ranges[i, :n, :] = track.ranges_mm
        quality[i, :n, :] = track.quality
        available[i, :n, :] = track.available
    return ranges, quality, available, lengths


def make_keep_mask(available: np.ndarray, keep_k: int, repeats: int, seed: int) -> np.ndarray:
    tracks, frames, anchors = available.shape
    if keep_k >= anchors:
        full_frame = available.sum(axis=2, keepdims=True) >= anchors
        full_available = available & full_frame
        return np.broadcast_to(full_available[:, None, :, :], (tracks, 1, frames, anchors)).copy()
    rng = np.random.default_rng(seed)
    scores = rng.random((tracks, repeats, frames, anchors), dtype=np.float32)
    scores = np.where(available[:, None, :, :], scores, 2.0)
    idx = np.argpartition(scores, keep_k - 1, axis=3)[..., :keep_k]
    mask = np.zeros((tracks, repeats, frames, anchors), dtype=bool)
    np.put_along_axis(mask, idx, True, axis=3)
    mask &= available[:, None, :, :]
    return mask


class CudaT4Replay:
    def __init__(self, anchor_xyz: np.ndarray, delays: np.ndarray, sigmas: np.ndarray, device: str = "cuda"):
        self.device = torch.device(device)
        self.dtype = torch.float32
        self.anchor_xyz = torch.as_tensor(anchor_xyz, device=self.device, dtype=self.dtype)
        self.delays = torch.as_tensor(delays, device=self.device, dtype=self.dtype)
        self.base_sigmas = torch.as_tensor(sigmas, device=self.device, dtype=self.dtype).clamp_min(5.0)
        self.eye = torch.eye(3, device=self.device, dtype=self.dtype)
        self.max_iters = 8
        self.huber_k = 2.0
        self.max_step_mm = 500.0
        self.temporal_prior_sigma_mm = 180.0
        self.q_alpha = 0.3
        self.r_alpha = 0.3

    def _solve_frame_batch(
        self,
        ranges: torch.Tensor,
        quality: torch.Tensor,
        mask: torch.Tensor,
        last_pos: torch.Tensor,
        last_valid: torch.Tensor,
        q_ema: torch.Tensor,
        q_valid: torch.Tensor,
        r_ema: torch.Tensor,
        r_valid: torch.Tensor,
        tag_method: str,
        keep_k: int,
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        tag_method = tag_method.upper()
        t4_full_anchor_path = tag_method == "T4" and keep_k >= 8
        use_quality = tag_method in {"T2", "T3"} or (tag_method == "T4" and keep_k < 8)
        use_residual_memory = tag_method == "T3" or (tag_method == "T4" and keep_k < 8)
        use_temporal_prior = tag_method == "T3" or (tag_method == "T4" and keep_k < 8)
        keep = mask.to(self.dtype)
        count = keep.sum(dim=1).clamp_min(1.0)
        centroid = keep @ self.anchor_xyz / count[:, None]
        # CPU wrapper passes the previous position as the GN initial point for
        # T1/T2/T3 and T4 low-anchor frames. T4 full-anchor frames intentionally
        # use the memory-free T1 path.
        pos = centroid.clone() if t4_full_anchor_path else torch.where(last_valid[:, None], last_pos, centroid).clone()

        q_new = self.q_alpha * quality + (1.0 - self.q_alpha) * torch.where(q_valid, q_ema, quality)
        q_use = torch.where(mask, q_new, q_ema)
        q_valid_out = q_valid | mask

        if use_quality:
            q_mix = 0.5 * quality + 0.5 * q_new
            q_mix = q_mix.clamp(0.0, 100.0)
            bad = torch.clamp((100.0 - q_mix) / 50.0, min=0.0)
            q_penalty = (1.0 + 1.5 * bad * bad).clamp(1.0, 4.0)
        else:
            q_penalty = torch.ones_like(ranges)
        if use_residual_memory:
            r_input = torch.where(r_valid, r_ema, torch.zeros_like(r_ema))
            excess = (r_input - 120.0) / 80.0
            r_penalty = torch.where(r_input > 120.0, 1.0 + 0.50 * excess, torch.ones_like(r_input))
            r_penalty = r_penalty.clamp(1.0, 2.5)
        else:
            r_penalty = torch.ones_like(ranges)
        sigma = self.base_sigmas[None, :] * q_penalty * r_penalty

        eye = self.eye[None, :, :].expand(ranges.shape[0], 3, 3)
        for _ in range(self.max_iters):
            diff = pos[:, None, :] - self.anchor_xyz[None, :, :]
            dist = torch.linalg.norm(diff, dim=2).clamp_min(1e-6)
            residual = dist + self.delays[None, :] - ranges
            rn = residual / sigma
            abs_rn = rn.abs()
            weight = torch.where(abs_rn <= self.huber_k, torch.ones_like(rn), self.huber_k / abs_rn.clamp_min(1e-6))
            weight = weight * keep
            scale = torch.sqrt(weight) / sigma
            jac = diff / dist[:, :, None] * scale[:, :, None]
            rr = rn * torch.sqrt(weight)
            h = torch.matmul(jac.transpose(1, 2), jac) + 1e-6 * eye
            g = torch.matmul(jac.transpose(1, 2), rr[:, :, None]).squeeze(2)
            if use_temporal_prior:
                prior = last_valid
                if prior.any():
                    inv = 1.0 / self.temporal_prior_sigma_mm
                    inv_var = inv * inv
                    h = h + prior.to(self.dtype)[:, None, None] * inv_var * self.eye[None, :, :]
                    g = g + prior.to(self.dtype)[:, None] * (pos - last_pos) * inv_var
            step = torch.linalg.solve(h, -g)
            norm = torch.linalg.norm(step, dim=1).clamp_min(1e-9)
            step_scale = torch.clamp(self.max_step_mm / norm, max=1.0)
            pos = pos + step * step_scale[:, None]

        diff = pos[:, None, :] - self.anchor_xyz[None, :, :]
        dist = torch.linalg.norm(diff, dim=2)
        residual = dist + self.delays[None, :] - ranges
        denom = keep.sum(dim=1).clamp_min(1.0)
        rms = torch.sqrt(torch.sum(residual * residual * keep, dim=1) / denom)
        abs_res = residual.abs()
        r_prev = torch.where(r_valid, r_ema, abs_res)
        r_new = self.r_alpha * abs_res + (1.0 - self.r_alpha) * r_prev
        r_use = torch.where(mask, r_new, r_ema)
        r_valid_out = r_valid | mask
        return pos, rms, residual, q_use, q_valid_out, r_use, r_valid_out

    def replay(
        self,
        ranges_np: np.ndarray,
        quality_np: np.ndarray,
        keep_mask_np: np.ndarray,
        keep_k: int,
        tag_method: str,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        tracks, repeats, frames, anchors = keep_mask_np.shape
        ranges = torch.as_tensor(ranges_np, device=self.device, dtype=self.dtype)
        quality = torch.as_tensor(quality_np, device=self.device, dtype=self.dtype)
        keep_mask = torch.as_tensor(keep_mask_np, device=self.device, dtype=torch.bool)
        n_states = tracks * repeats
        last_pos = torch.zeros((n_states, 3), device=self.device, dtype=self.dtype)
        last_valid = torch.zeros((n_states,), device=self.device, dtype=torch.bool)
        q_ema = torch.zeros((n_states, anchors), device=self.device, dtype=self.dtype)
        q_valid = torch.zeros((n_states, anchors), device=self.device, dtype=torch.bool)
        r_ema = torch.zeros((n_states, anchors), device=self.device, dtype=self.dtype)
        r_valid = torch.zeros((n_states, anchors), device=self.device, dtype=torch.bool)
        pos_out = torch.full((n_states, frames, 3), float("nan"), device=self.device, dtype=self.dtype)
        rms_out = torch.full((n_states, frames), float("nan"), device=self.device, dtype=self.dtype)
        valid_out = torch.zeros((n_states, frames), device=self.device, dtype=torch.bool)

        for f in range(frames):
            mask_f = keep_mask[:, :, f, :].reshape(n_states, anchors)
            active = mask_f.sum(dim=1) >= 4
            if not bool(active.any()):
                continue
            idx = active.nonzero(as_tuple=False).squeeze(1)
            track_idx = torch.div(idx, repeats, rounding_mode="floor")
            ranges_f = ranges[track_idx, f, :]
            quality_f = quality[track_idx, f, :]
            pos, rms, _resid, q_up, q_valid_up, r_up, r_valid_up = self._solve_frame_batch(
                ranges_f,
                quality_f,
                mask_f[idx],
                last_pos[idx],
                last_valid[idx],
                q_ema[idx],
                q_valid[idx],
                r_ema[idx],
                r_valid[idx],
                tag_method=tag_method,
                keep_k=keep_k,
            )
            last_pos[idx] = pos
            last_valid[idx] = True
            q_ema[idx] = q_up
            q_valid[idx] = q_valid_up
            r_ema[idx] = r_up
            r_valid[idx] = r_valid_up
            pos_out[idx, f, :] = pos
            rms_out[idx, f] = rms
            valid_out[idx, f] = True

        pos_np = pos_out.reshape(tracks, repeats, frames, 3).detach().cpu().numpy()
        rms_np = rms_out.reshape(tracks, repeats, frames).detach().cpu().numpy()
        valid_np = valid_out.reshape(tracks, repeats, frames).detach().cpu().numpy()
        return pos_np, rms_np, valid_np


def summarize_positions(points: np.ndarray, rms: np.ndarray, valid: np.ndarray) -> dict:
    pts = points[valid]
    rr = rms[valid]
    if pts.shape[0] < 10:
        return {
            "solved": int(pts.shape[0]),
            "x_std_mm": float("nan"),
            "y_std_mm": float("nan"),
            "z_std_mm": float("nan"),
            "d3_std_mm": float("nan"),
            "residual_rms_median_mm": float("nan"),
            "residual_rms_p95_mm": float("nan"),
        }
    mean = np.mean(pts, axis=0)
    d = pts - mean[None, :]
    d3 = np.linalg.norm(d, axis=1)
    return {
        "solved": int(pts.shape[0]),
        "x_std_mm": float(np.std(d[:, 0])),
        "y_std_mm": float(np.std(d[:, 1])),
        "z_std_mm": float(np.std(d[:, 2])),
        "d3_std_mm": float(np.sqrt(np.mean(d3 * d3))),
        "residual_rms_median_mm": percentile(rr, 50),
        "residual_rms_p95_mm": percentile(rr, 95),
    }


def summarize_capture_set(rows: list[dict]) -> dict:
    ok = [r for r in rows if int(r.get("solved") or 0) >= 10 and math.isfinite(float(r.get("d3_std_mm", float("nan"))))]
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
        "radius_mm": float(radius),
        "circle_thickness_rms_mm": float(np.sqrt(np.mean(total * total))),
        "circle_thickness_p95_mm": float(np.percentile(total, 95)),
        "center_x": float(center3[0]),
        "center_y": float(center3[1]),
        "center_z": float(center3[2]),
        "_center0": center0,
        "_e1": e1,
        "_e2": e2,
        "_normal": normal,
        "_theta": theta,
    }


def fit_circle_in_basis(points: np.ndarray, center0: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> tuple[np.ndarray, float] | None:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return None
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    a = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    return center0 + cx * e1 + cy * e2, radius


def per_turn_center_stats(points: np.ndarray, theta: np.ndarray, basis: tuple[np.ndarray, np.ndarray, np.ndarray]) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 80 or theta.size != pts.shape[0]:
        return {"turn_count": 0}
    center0, e1, e2 = basis
    th = theta - theta[0]
    estimated_turns = float((th[-1] - th[0]) / (2.0 * math.pi))
    bins = np.floor(th / (2.0 * math.pi)).astype(int)
    centers = []
    for b in range(int(np.min(bins)), int(np.max(bins)) + 1):
        idx = np.where(bins == b)[0]
        if idx.size < 30:
            continue
        fit = fit_circle_in_basis(pts[idx], center0, e1, e2)
        if fit is not None:
            center, _radius = fit
            centers.append(center)
    if len(centers) < 2:
        return {"turn_count": len(centers), "estimated_turns": estimated_turns}
    c = np.asarray(centers, dtype=float)
    mean = np.mean(c, axis=0)
    dist = np.linalg.norm(c - mean, axis=1)
    return {
        "turn_count": int(len(centers)),
        "estimated_turns": estimated_turns,
        "turn_center_rms_3d_mm": float(np.sqrt(np.mean(dist * dist))),
        "turn_center_p95_3d_mm": float(np.percentile(dist, 95)),
    }


def summarize_roto_points(points: np.ndarray, valid: np.ndarray) -> dict:
    pts = points[valid]
    if pts.shape[0] < 80:
        return {"status": "insufficient", "solved": int(pts.shape[0]), "turn_count": 0}
    fit = fit_circle_3d(pts)
    if fit.get("status") != "ok":
        return {"status": "fit_failed", "solved": int(pts.shape[0]), "turn_count": 0}
    theta = fit.pop("_theta")
    center0 = fit.pop("_center0")
    e1 = fit.pop("_e1")
    e2 = fit.pop("_e2")
    fit.pop("_normal", None)
    turn = per_turn_center_stats(pts, theta, (center0, e1, e2))
    return {"status": "ok", "solved": int(pts.shape[0]), **fit, **turn}


def summarize_roto_set(rows: list[dict]) -> dict:
    ok = [
        r
        for r in rows
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
        }
    return {
        "roto_tracks_ok": len(ok),
        "solved_total": sum(int(r["solved"]) for r in ok),
        "turn_center_rms_3d_mm_median": median([float(r["turn_center_rms_3d_mm"]) for r in ok]),
        "turn_center_p95_3d_mm_median": median([float(r["turn_center_p95_3d_mm"]) for r in ok]),
        "circle_thickness_rms_mm_median": median([float(r["circle_thickness_rms_mm"]) for r in ok]),
        "radius_mm_median": median([float(r["radius_mm"]) for r in ok]),
        "turn_count_median": median([float(r["turn_count"]) for r in ok]),
    }


def plot_static_summary(summary_csv: Path, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with summary_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    xs = [int(r["keep_k"]) for r in rows]
    d3 = [float(r["d3_std_mm_median"]) for r in rows]
    z = [float(r["z_std_mm_median"]) for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8), constrained_layout=True)
    axes[0].plot(xs, d3, marker="o")
    axes[0].set_title("Static repeatability vs keep-k")
    axes[0].set_ylabel("median 3D std (mm)")
    axes[1].plot(xs, z, marker="o", color="#6aa84f")
    axes[1].set_title("Static Z repeatability vs keep-k")
    axes[1].set_ylabel("median Z std (mm)")
    for ax in axes:
        ax.set_xlabel("Kept anchors")
        ax.set_xticks([4, 5, 6, 7, 8])
        ax.invert_xaxis()
        ax.grid(True, alpha=0.3)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def plot_roto_summary(summary_csv: Path, out_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with summary_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    xs = [int(r["keep_k"]) for r in rows]
    center = [float(r["turn_center_rms_3d_mm_median"]) for r in rows]
    thick = [float(r["circle_thickness_rms_mm_median"]) for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(10.0, 3.8), constrained_layout=True)
    axes[0].plot(xs, center, marker="o")
    axes[0].set_title("Roto turn-center stability vs keep-k")
    axes[0].set_ylabel("median turn-center RMS (mm)")
    axes[1].plot(xs, thick, marker="o", color="#6aa84f")
    axes[1].set_title("Roto circle thickness vs keep-k")
    axes[1].set_ylabel("median thickness RMS (mm)")
    for ax in axes:
        ax.set_xlabel("Kept anchors")
        ax.set_xticks([4, 5, 6, 7, 8])
        ax.invert_xaxis()
        ax.grid(True, alpha=0.3)
    fig.savefig(out_png, dpi=180)
    plt.close(fig)


def expected_summary_path(out_root: Path, layout_name: str, tag_method: str, kind: str) -> Path:
    return out_root / layout_name / tag_method.upper() / kind / f"{kind}_keepk_summary.csv"


def summary_complete(path: Path, expected_keep_values: list[int]) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return False
    if not rows:
        return False
    keeps = {int(float(r.get("keep_k", "-1"))) for r in rows if str(r.get("keep_k", "")).strip()}
    return set(expected_keep_values).issubset(keeps)


def estimate_host_batch_gb(n_tracks: int, n_repeats: int, n_frames: int, n_anchors: int = 8) -> float:
    """Approximate host RAM needed by the current chunked implementation.

    This is intentionally conservative enough to protect the desktop session:
    CPU-side random scores, boolean masks, and CPU trajectory copies all exist
    during a batch. GPU memory may look low while host RAM is already dangerous.
    """
    bytes_per_track_repeat_frame = (
        n_anchors * 4  # random scores float32
        + n_anchors * 1  # keep mask bool
        + 3 * 4  # xyz float32 copied back for summaries
        + 4  # residual rms float32
        + 1  # valid bool
    )
    return n_tracks * n_repeats * n_frames * bytes_per_track_repeat_frame / (1024.0 ** 3)


def run_kind(kind: str, tracks: list[Track], replay: CudaT4Replay, args: argparse.Namespace, layout_name: str, tag_method: str) -> None:
    tag_method = tag_method.upper()
    out_dir = Path(args.out) / layout_name / tag_method / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    ranges, quality, available, _lengths = pack_tracks(tracks)
    ranges_t = ranges
    quality_t = quality
    detail_rows: list[dict] = []
    repeat_summary_rows: list[dict] = []
    summary_rows: list[dict] = []
    print(
        f"[cuda-tx] layout={layout_name} tag_method={tag_method} "
        f"kind={kind} tracks={len(tracks)} frames_max={ranges.shape[1]}",
        flush=True,
    )

    keep_values = [int(x) for x in str(args.keep_list).split(",") if x.strip()]
    for keep_k in keep_values:
        repeats_total = 1 if keep_k == 8 else int(args.repeats)
        repeat_batch = max(1, int(args.repeat_batch))
        repeats_summary = []
        elapsed_total = 0.0
        for start in range(0, repeats_total, repeat_batch):
            repeats = min(repeat_batch, repeats_total - start)
            host_est_gb = estimate_host_batch_gb(len(tracks), repeats, ranges.shape[1])
            if host_est_gb > float(args.max_host_gb) and not args.force_large_batch:
                raise RuntimeError(
                    f"Refusing unsafe batch: layout={layout_name} kind={kind} keep={keep_k} "
                    f"batch={repeats} estimated_host_ram={host_est_gb:.2f}GB > "
                    f"--max-host-gb={float(args.max_host_gb):.2f}. "
                    "Lower --repeat-batch or use --force-large-batch only from a non-desktop session."
                )
            seed = int(args.seed) + (1000 if kind == "roto" else 0) + keep_k + start * 7919
            mask = make_keep_mask(available, keep_k, repeats, seed)
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
            t0 = time.perf_counter()
            pos, rms, valid = replay.replay(ranges_t, quality_t, mask, keep_k, tag_method)
            torch.cuda.synchronize()
            elapsed = time.perf_counter() - t0
            peak_alloc_gb = torch.cuda.max_memory_allocated() / (1024.0 ** 3)
            peak_reserved_gb = torch.cuda.max_memory_reserved() / (1024.0 ** 3)
            elapsed_total += elapsed
            print(
                f"[cuda-tx] layout={layout_name} tag_method={tag_method} kind={kind} keep={keep_k} "
                f"repeats={start + repeats}/{repeats_total} batch={repeats} elapsed={elapsed:.2f}s "
                f"host_est={host_est_gb:.2f}GB peak_alloc={peak_alloc_gb:.2f}GB "
                f"peak_reserved={peak_reserved_gb:.2f}GB",
                flush=True,
            )

            for r_local in range(repeats):
                repeat_index = start + r_local
                rows_for_repeat: list[dict] = []
                for ti, track in enumerate(tracks):
                    if kind == "static":
                        s = summarize_positions(pos[ti, r_local], rms[ti, r_local], valid[ti, r_local])
                    else:
                        s = summarize_roto_points(pos[ti, r_local], valid[ti, r_local])
                    row = {
                        "method": f"{tag_method}_CUDA_REPLAY",
                        "tag_method": tag_method,
                        "layout": layout_name,
                        "keep_k": keep_k,
                        "repeat": repeat_index,
                        "capture": track.capture_id,
                        "tag": track.tag,
                        **s,
                    }
                    rows_for_repeat.append(row)
                    if not args.summary_only:
                        detail_rows.append({**row, "path": track.path})
                if kind == "static":
                    s_rep = summarize_capture_set(rows_for_repeat)
                else:
                    s_rep = summarize_roto_set(rows_for_repeat)
                s_rep_row = {
                    "method": f"{tag_method}_CUDA_REPLAY",
                    "tag_method": tag_method,
                    "layout": layout_name,
                    "keep_k": keep_k,
                    "repeat": repeat_index,
                    **s_rep,
                }
                repeats_summary.append(s_rep)
                repeat_summary_rows.append(s_rep_row)
        if kind == "static":
            metric_keys = [
                "captures_ok",
                "solved_total",
                "x_std_mm_median",
                "y_std_mm_median",
                "z_std_mm_median",
                "d3_std_mm_median",
                "d3_std_mm_p95",
                "residual_rms_median_mm",
                "residual_rms_p95_mm",
            ]
        else:
            metric_keys = [
                "roto_tracks_ok",
                "solved_total",
                "turn_center_rms_3d_mm_median",
                "turn_center_p95_3d_mm_median",
                "circle_thickness_rms_mm_median",
                "radius_mm_median",
                "turn_count_median",
            ]
        row = {
            "method": f"{tag_method}_CUDA_REPLAY",
            "tag_method": tag_method,
            "layout": layout_name,
            "keep_k": keep_k,
            "repeats": repeats_total,
            "elapsed_s": elapsed_total,
            "repeat_batch": repeat_batch,
        }
        for key in metric_keys:
            row[key] = median([float(s.get(key, float("nan"))) for s in repeats_summary])
        summary_rows.append(row)

    detail_csv = out_dir / f"{kind}_keepk_detail.csv"
    repeat_summary_csv = out_dir / f"{kind}_keepk_repeat_summary.csv"
    summary_csv = out_dir / f"{kind}_keepk_summary.csv"
    if not args.summary_only:
        write_csv(detail_csv, detail_rows)
    write_csv(repeat_summary_csv, repeat_summary_rows)
    write_csv(summary_csv, summary_rows)
    if kind == "static":
        plot_static_summary(summary_csv, out_dir / "static_keepk_cuda_t4.png")
    else:
        plot_roto_summary(summary_csv, out_dir / "roto_keepk_cuda_t4.png")


def main() -> int:
    parser = argparse.ArgumentParser(description="GPU replay of T4 random keep-k robustness for Erlangen official captures.")
    parser.add_argument("--out", default=str(OFFICIAL_ROOT / "Analysis" / "keepk_cuda_t4_mc500"))
    parser.add_argument("--repeats", type=int, default=500)
    parser.add_argument("--repeat-batch", type=int, default=500)
    parser.add_argument("--max-host-gb", type=float, default=4.0)
    parser.add_argument("--force-large-batch", action="store_true")
    parser.add_argument("--keep-list", default="8,7,6,5,4")
    parser.add_argument("--seed", type=int, default=20260528)
    parser.add_argument("--max-frames-per-track", type=int, default=0)
    parser.add_argument(
        "--layout-versions",
        default="v4-io",
        help="comma list from v1-old,v2,v3-lite,v3-full,v4-io, or 'all'",
    )
    parser.add_argument(
        "--tag-methods",
        default="T4",
        help="comma list from T1,T2,T3,T4, or 'all'",
    )
    parser.add_argument("--skip-static", action="store_true")
    parser.add_argument("--skip-roto", action="store_true")
    parser.add_argument("--summary-only", action="store_true", help="do not write per-capture per-repeat detail rows")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip a layout/tag/kind block when its summary CSV already contains all requested keep-k rows",
    )
    parser.add_argument("--num-shards", type=int, default=1, help="split layout/tag/kind blocks across this many workers")
    parser.add_argument("--shard-id", type=int, default=0, help="zero-based worker index used with --num-shards")
    parser.add_argument(
        "--block-indices",
        default="",
        help="optional comma list of 1-based layout/tag/kind block indices to run; overrides modulo sharding",
    )
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda is not available")
    if args.num_shards < 1:
        raise ValueError("--num-shards must be >= 1")
    if args.shard_id < 0 or args.shard_id >= args.num_shards:
        raise ValueError("--shard-id must satisfy 0 <= shard-id < num-shards")
    _default_layout_path, sigma_path = layout_paths()
    layouts = available_layouts()
    if args.layout_versions.strip().lower() == "all":
        selected_layouts = layouts
    else:
        selected_layouts = {}
        for name in [x.strip() for x in args.layout_versions.split(",") if x.strip()]:
            if name not in layouts:
                raise ValueError(f"unknown layout version {name!r}; available={sorted(layouts)}")
            selected_layouts[name] = layouts[name]
    if not selected_layouts:
        raise ValueError("no layouts selected")
    all_methods = ["T1", "T2", "T3", "T4"]
    if args.tag_methods.strip().lower() == "all":
        selected_methods = all_methods
    else:
        selected_methods = [x.strip().upper() for x in args.tag_methods.split(",") if x.strip()]
        bad_methods = [m for m in selected_methods if m not in all_methods]
        if bad_methods:
            raise ValueError(f"unknown tag methods {bad_methods}; available={all_methods}")
    if not selected_methods:
        raise ValueError("no tag methods selected")

    static_tracks = [] if args.skip_static else load_static_tracks(args.max_frames_per_track)
    roto_tracks = [] if args.skip_roto else load_roto_tracks(args.max_frames_per_track)
    selected_kinds = []
    if static_tracks:
        selected_kinds.append("static")
    if roto_tracks:
        selected_kinds.append("roto")
    if not selected_kinds:
        raise ValueError("no capture kinds selected")
    keep_values = [int(x) for x in str(args.keep_list).split(",") if x.strip()]
    if not keep_values:
        raise ValueError("empty --keep-list")
    all_blocks = [
        (layout_name, tag_method, kind)
        for layout_name in selected_layouts
        for tag_method in selected_methods
        for kind in selected_kinds
    ]
    requested_block_indices = [int(x) for x in str(args.block_indices).split(",") if x.strip()]
    if requested_block_indices:
        requested = set(requested_block_indices)
        bad = sorted(i for i in requested if i < 1 or i > len(all_blocks))
        if bad:
            raise ValueError(f"--block-indices out of range: {bad}; total_blocks={len(all_blocks)}")
        shard_blocks = [
            (idx, block)
            for idx, block in enumerate(all_blocks)
            if idx + 1 in requested
        ]
    else:
        shard_blocks = [
            (idx, block)
            for idx, block in enumerate(all_blocks)
            if idx % int(args.num_shards) == int(args.shard_id)
        ]
    if not shard_blocks:
        raise ValueError("this shard has no blocks to process")

    metadata = {
        "layouts": {name: str(path) for name, path in selected_layouts.items()},
        "tag_methods": selected_methods,
        "kinds": selected_kinds,
        "total_blocks": len(all_blocks),
        "shard_blocks": [
            {"block_index": idx + 1, "layout": b[0], "tag_method": b[1], "kind": b[2]} for idx, b in shard_blocks
        ],
        "num_shards": int(args.num_shards),
        "shard_id": int(args.shard_id),
        "block_indices_override": requested_block_indices,
        "sigma": str(sigma_path),
        "device": torch.cuda.get_device_name(0),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
        "torch": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "repeats_requested": int(args.repeats),
        "repeat_batch": int(args.repeat_batch),
        "max_host_gb": float(args.max_host_gb),
        "force_large_batch": bool(args.force_large_batch),
        "keep_list": str(args.keep_list),
        "summary_only": bool(args.summary_only),
        "skip_existing": bool(args.skip_existing),
        "keep8_note": "keep8 is deterministic and is computed once",
        "randomization": "per-frame random keep-k mask, matching the previous CPU MC script",
    }
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    if int(args.num_shards) == 1:
        metadata_path = out_root / "metadata.json"
    else:
        metadata_path = out_root / f"metadata_shard{int(args.shard_id):02d}_of_{int(args.num_shards):02d}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    replay_cache = {}
    for block_index, (layout_name, tag_method, kind) in shard_blocks:
        summary_path = expected_summary_path(out_root, layout_name, tag_method, kind)
        if bool(args.skip_existing) and summary_complete(summary_path, keep_values):
            print(
                f"[cuda-tx] block={block_index + 1}/{len(all_blocks)} shard={args.shard_id}/{args.num_shards} "
                f"skip existing layout={layout_name} tag_method={tag_method} kind={kind} "
                f"summary={summary_path}",
                flush=True,
            )
            continue
        print(
            f"[cuda-tx] block={block_index + 1}/{len(all_blocks)} shard={args.shard_id}/{args.num_shards} "
            f"start layout={layout_name} tag_method={tag_method} kind={kind}",
            flush=True,
        )
        if layout_name not in replay_cache:
            layout_path = selected_layouts[layout_name]
            layout = load_layout_json(layout_path, sigma_path)
            anchor_xyz = np.asarray(
                [[layout.anchors[aid].x_mm, layout.anchors[aid].y_mm, layout.anchors[aid].z_mm] for aid in ANCHORS],
                dtype=np.float32,
            )
            delays = np.asarray([layout.anchors[aid].d_anchor_mm + layout.tag_delay_mm for aid in ANCHORS], dtype=np.float32)
            sigmas = np.asarray([layout.anchors[aid].sigma_mm for aid in ANCHORS], dtype=np.float32)
            replay_cache[layout_name] = CudaT4Replay(anchor_xyz, delays, sigmas)
        replay = replay_cache[layout_name]
        if kind == "static":
            run_kind("static", static_tracks, replay, args, layout_name, tag_method)
        elif kind == "roto":
            run_kind("roto", roto_tracks, replay, args, layout_name, tag_method)
        else:
            raise ValueError(f"unknown kind {kind!r}")
        print(
            f"[cuda-tx] block={block_index + 1}/{len(all_blocks)} shard={args.shard_id}/{args.num_shards} "
            f"done layout={layout_name} tag_method={tag_method} kind={kind}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
