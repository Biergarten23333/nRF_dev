#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch


THIS = Path(__file__).resolve()
OFFICIAL_ROOT = THIS.parents[2]
REPO_ROOT = THIS.parents[4]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame, SolverConfig  # noqa: E402


def _find_default_capture() -> Path:
    root = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
    matches = sorted(root.glob("static_ID01*/tag_capture*/tr_all.csv"))
    if not matches:
        raise FileNotFoundError(f"no static_ID01 tr_all.csv found under {root}")
    return matches[-1]


def _layout_path() -> Path:
    return OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check" / "v4-io" / "layout.json"


def _sigma_path() -> Path:
    return OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check" / "tables" / "anchor_sigma.json"


def _frames_to_dense_ranges(frames: list[Frame], anchor_ids: list[int]) -> tuple[list[Frame], np.ndarray]:
    kept: list[Frame] = []
    rows: list[list[float]] = []
    anchor_set = set(anchor_ids)
    for frame in frames:
        obs_by_id = {o.anchor_id: o.range_mm for o in frame.observations if o.anchor_id in anchor_set}
        if all(aid in obs_by_id for aid in anchor_ids):
            kept.append(frame)
            rows.append([float(obs_by_id[aid]) for aid in anchor_ids])
    if not rows:
        raise RuntimeError("no full 8-anchor frames available for CUDA smoke test")
    return kept, np.asarray(rows, dtype=np.float32)


def solve_batched_cuda(
    ranges_mm: np.ndarray,
    anchor_xyz_mm: np.ndarray,
    anchor_delay_mm: np.ndarray,
    sigma_mm: np.ndarray,
    mask: np.ndarray | None = None,
    *,
    max_iters: int = 10,
    huber_k: float = 2.0,
    max_step_mm: float = 500.0,
    dtype: torch.dtype = torch.float32,
) -> tuple[np.ndarray, np.ndarray, float]:
    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda is not available")
    device = torch.device("cuda")
    torch.cuda.empty_cache()

    ranges = torch.as_tensor(ranges_mm, device=device, dtype=dtype)
    anchors = torch.as_tensor(anchor_xyz_mm, device=device, dtype=dtype)
    delays = torch.as_tensor(anchor_delay_mm, device=device, dtype=dtype)
    sigmas = torch.as_tensor(sigma_mm, device=device, dtype=dtype).clamp_min(5.0)
    if mask is None:
        mask_t = torch.ones_like(ranges, dtype=dtype)
    else:
        mask_t = torch.as_tensor(mask, device=device, dtype=dtype)

    n = ranges.shape[0]
    p0 = anchors.mean(dim=0)
    pos = p0.expand(n, 3).clone()
    eye = torch.eye(3, device=device, dtype=dtype).expand(n, 3, 3)

    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    for _ in range(max_iters):
        diff = pos[:, None, :] - anchors[None, :, :]
        dist = torch.linalg.norm(diff, dim=2).clamp_min(1e-6)
        residual = dist + delays[None, :] - ranges
        rn = residual / sigmas[None, :]
        abs_rn = rn.abs()
        weight = torch.where(abs_rn <= huber_k, torch.ones_like(rn), huber_k / abs_rn.clamp_min(1e-6))
        weight = weight * mask_t
        scale = torch.sqrt(weight) / sigmas[None, :]
        jac = diff / dist[:, :, None] * scale[:, :, None]
        rr = rn * torch.sqrt(weight)
        h = torch.matmul(jac.transpose(1, 2), jac) + 1e-6 * eye
        g = torch.matmul(jac.transpose(1, 2), rr[:, :, None]).squeeze(2)
        step = torch.linalg.solve(h, -g)
        step_norm = torch.linalg.norm(step, dim=1).clamp_min(1e-9)
        step_scale = torch.clamp(max_step_mm / step_norm, max=1.0)
        pos = pos + step * step_scale[:, None]
    end.record()
    torch.cuda.synchronize()
    elapsed_ms = float(start.elapsed_time(end))

    diff = pos[:, None, :] - anchors[None, :, :]
    dist = torch.linalg.norm(diff, dim=2)
    residual = dist + delays[None, :] - ranges
    denom = mask_t.sum(dim=1).clamp_min(1.0)
    rms = torch.sqrt(torch.sum(residual * residual * mask_t, dim=1) / denom)
    return pos.detach().cpu().numpy(), rms.detach().cpu().numpy(), elapsed_ms


def solve_c_reference(frames: list[Frame], layout_path: Path, sigma_path: Path, method: str) -> np.ndarray:
    layout = load_layout_json(layout_path, sigma_path)
    solver = TagPositionSolver(layout, SolverConfig(method=method))
    out: list[list[float]] = []
    for frame in frames:
        res = solver.solve_frame(frame)
        if res is None or res.status != "ok":
            out.append([math.nan, math.nan, math.nan])
        else:
            out.append([res.x_mm, res.y_mm, res.z_mm])
    return np.asarray(out, dtype=np.float64)


def percentile(values: np.ndarray, p: float) -> float:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, p))


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="CUDA smoke test for batched 8-anchor tag positioning.")
    parser.add_argument("--capture", type=Path, default=_find_default_capture())
    parser.add_argument("--out-dir", type=Path, default=OFFICIAL_ROOT / "Analysis" / "cuda_test")
    parser.add_argument("--stress-repeats", type=int, default=200)
    parser.add_argument("--stress-keep", default="8,7,6,5,4")
    parser.add_argument("--max-frames", type=int, default=0, help="0 means use all full-anchor frames")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    layout = load_layout_json(_layout_path(), _sigma_path())
    anchor_ids = sorted(layout.anchors)
    if len(anchor_ids) != 8:
        raise RuntimeError(f"expected 8 anchors, got {anchor_ids}")

    frames_all = read_tr_all_frames(args.capture, min_anchors=4)
    frames, ranges = _frames_to_dense_ranges(frames_all, anchor_ids)
    if args.max_frames and len(frames) > args.max_frames:
        frames = frames[: args.max_frames]
        ranges = ranges[: args.max_frames]

    anchor_xyz = np.asarray(
        [[layout.anchors[aid].x_mm, layout.anchors[aid].y_mm, layout.anchors[aid].z_mm] for aid in anchor_ids],
        dtype=np.float32,
    )
    delays = np.asarray([layout.anchors[aid].d_anchor_mm + layout.tag_delay_mm for aid in anchor_ids], dtype=np.float32)
    sigmas = np.asarray([layout.anchors[aid].sigma_mm for aid in anchor_ids], dtype=np.float32)

    t0 = time.perf_counter()
    cuda_pos, cuda_rms, cuda_ms = solve_batched_cuda(ranges, anchor_xyz, delays, sigmas)
    host_elapsed = time.perf_counter() - t0

    c_t0 = time.perf_counter()
    c_pos = solve_c_reference(frames, _layout_path(), _sigma_path(), "T1")
    c_elapsed = time.perf_counter() - c_t0
    diff = np.linalg.norm(cuda_pos.astype(np.float64) - c_pos, axis=1)

    stress_frames = int(ranges.shape[0]) * max(1, int(args.stress_repeats))
    stress_ranges = np.tile(ranges, (max(1, int(args.stress_repeats)), 1))
    stress_rows = []
    stress_pos = None
    for keep_k in [int(x) for x in args.stress_keep.split(",") if x.strip()]:
        rng = np.random.default_rng(1000 + keep_k)
        if keep_k >= len(anchor_ids):
            keep_mask = np.ones_like(stress_ranges, dtype=np.float32)
        else:
            scores = rng.random(stress_ranges.shape)
            keep_idx = np.argpartition(scores, keep_k - 1, axis=1)[:, :keep_k]
            keep_mask = np.zeros_like(stress_ranges, dtype=np.float32)
            row_idx = np.arange(stress_ranges.shape[0])[:, None]
            keep_mask[row_idx, keep_idx] = 1.0
        stress_pos, stress_rms, stress_cuda_ms = solve_batched_cuda(
            stress_ranges,
            anchor_xyz,
            delays,
            sigmas,
            mask=keep_mask,
        )
        stress_rows.append(
            {
                "keep_k": keep_k,
                "stress_frames": stress_frames,
                "stress_cuda_ms": stress_cuda_ms,
                "stress_frames_per_s_cuda_timer": float(stress_frames) / max(stress_cuda_ms / 1000.0, 1e-9),
                "stress_residual_rms_median_mm": percentile(stress_rms, 50),
                "stress_residual_rms_p95_mm": percentile(stress_rms, 95),
            }
        )

    summary = {
        "capture": str(args.capture),
        "device": torch.cuda.get_device_name(0),
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "frames_total_parser": len(frames_all),
        "frames_full_8_anchor": int(ranges.shape[0]),
        "anchor_ids": anchor_ids,
        "single_batch_cuda_ms": cuda_ms,
        "single_batch_host_elapsed_s": host_elapsed,
        "single_batch_frames_per_s_cuda_timer": float(ranges.shape[0]) / max(cuda_ms / 1000.0, 1e-9),
        "c_reference_elapsed_s": c_elapsed,
        "c_reference_frames_per_s": float(ranges.shape[0]) / max(c_elapsed, 1e-9),
        "cuda_residual_rms_median_mm": percentile(cuda_rms, 50),
        "cuda_residual_rms_p95_mm": percentile(cuda_rms, 95),
        "cuda_vs_c_3d_diff_median_mm": percentile(diff, 50),
        "cuda_vs_c_3d_diff_p95_mm": percentile(diff, 95),
        "cuda_vs_c_3d_diff_max_mm": float(np.nanmax(diff)),
        "stress_repeats": int(args.stress_repeats),
        "stress_by_keep": stress_rows,
    }
    (args.out_dir / "cuda_smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_csv(
        args.out_dir / "cuda_smoke_frame_sample.csv",
        [
            {
                "tag": frame.tag,
                "sweep": frame.sweep,
                "host_elapsed_s": frame.host_elapsed_s,
                "cuda_x_mm": float(cuda_pos[i, 0]),
                "cuda_y_mm": float(cuda_pos[i, 1]),
                "cuda_z_mm": float(cuda_pos[i, 2]),
                "cuda_residual_rms_mm": float(cuda_rms[i]),
                "c_x_mm": float(c_pos[i, 0]),
                "c_y_mm": float(c_pos[i, 1]),
                "c_z_mm": float(c_pos[i, 2]),
                "cuda_vs_c_3d_diff_mm": float(diff[i]),
            }
            for i, frame in enumerate(frames[: min(200, len(frames))])
        ],
    )
    write_csv(args.out_dir / "cuda_keepk_stress.csv", stress_rows)
    print(json.dumps(summary, indent=2))
    _ = stress_pos  # keep the stress solve live until after synchronization.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
