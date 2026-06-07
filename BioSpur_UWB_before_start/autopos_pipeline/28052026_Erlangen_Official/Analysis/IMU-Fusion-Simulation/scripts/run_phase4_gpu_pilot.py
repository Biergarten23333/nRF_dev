#!/usr/bin/env python3
"""Phase 4 torch/CUDA pilot for raw anchor-tag measurement fusion.

This is a pilot, not the full Phase 4 launcher. It compares the current CPU
range EKF against a batched torch implementation on a deliberately small subset
so the GPU path can become trustworthy before any broad sweep.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
STAGE1_SCRIPT = SIM_ROOT / "scripts" / "run_phase2_stage1_screening.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"
CACHE_ROOT = SIM_ROOT / "cache" / "phase4_gpu_pilot"

DEFAULT_ROWS = [
    "R2:L0:I0:T6",
    "R4:L8:I1+I2+I3+I8:T8",
]


def safe_id(value: object) -> str:
    return re.sub(r"[^A-Za-z0-9_.+-]+", "_", str(value))


def prior_cache_path(cache_root: Path, prior_run_id: str, l_id: str, i_id: str) -> Path:
    return cache_root / "priors" / f"prior_{safe_id(prior_run_id)}_{safe_id(l_id)}_{safe_id(i_id)}.pkl"


def tensor_cache_path(cache_root: Path, prior_run_id: str, l_id: str, i_id: str, max_tracks: int, max_frames: int) -> Path:
    return cache_root / "tensors" / f"tensors_{safe_id(prior_run_id)}_{safe_id(l_id)}_{safe_id(i_id)}_tracks{int(max_tracks)}_frames{int(max_frames)}.npz"


def load_tensor_cache(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    data = np.load(path, allow_pickle=False)
    captures = data["key_capture"].astype(str).tolist()
    tags = data["key_tag"].astype(str).tolist()
    return {
        "keys": list(zip(captures, tags)),
        "raw_by_track": {},
        "ranges": data["ranges"],
        "range_mask": data["range_mask"].astype(bool),
        "frame_mask": data["frame_mask"].astype(bool),
        "raw_time": data["raw_time"],
        "opti_time": data["opti_time"],
        "opti_xyz": data["opti_xyz"],
        "uwb_xyz": data["uwb_xyz"],
        "prior_xyz": data["prior_xyz"],
    }


def save_tensor_cache(path: Path, tensors: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(tensors["keys"])
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp.npz", dir=path.parent)
    os.close(fd)
    try:
        np.savez(
            tmp_name,
            key_capture=np.asarray([k[0] for k in keys], dtype=str),
            key_tag=np.asarray([k[1] for k in keys], dtype=str),
            ranges=np.asarray(tensors["ranges"]),
            range_mask=np.asarray(tensors["range_mask"], dtype=bool),
            frame_mask=np.asarray(tensors["frame_mask"], dtype=bool),
            raw_time=np.asarray(tensors["raw_time"]),
            opti_time=np.asarray(tensors["opti_time"]),
            opti_xyz=np.asarray(tensors["opti_xyz"]),
            uwb_xyz=np.asarray(tensors["uwb_xyz"]),
            prior_xyz=np.asarray(tensors["prior_xyz"]),
        )
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_pickle_cache(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        pd.to_pickle(obj, tmp_name)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


S1 = load_module(STAGE1_SCRIPT, "phase2_stage1_for_phase4_gpu_pilot")


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


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def fmt(value: object, digits: int = 3) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def summarize_times(times: list[float]) -> dict[str, float | int]:
    arr = np.asarray(times, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return {"repeat_count": 0, "wall_time_s": float("nan"), "wall_time_min_s": float("nan"), "wall_time_max_s": float("nan")}
    return {
        "repeat_count": int(arr.size),
        "wall_time_s": float(np.median(arr)),
        "wall_time_min_s": float(np.min(arr)),
        "wall_time_max_s": float(np.max(arr)),
    }


def find_latest_phase2_run() -> str:
    root = SIM_ROOT / "runs" / "phase2_screening"
    candidates: list[str] = []
    if not root.exists():
        raise FileNotFoundError(root)
    for p in root.iterdir():
        g3 = p / "stage0_gate_fulfillment" / "tables" / "g3_range_bias_policy_R2.csv"
        if g3.exists():
            candidates.append(p.name)
    if not candidates:
        raise RuntimeError("no phase2 run with g3 range-bias policy table found")
    return sorted(candidates)[-1]


def load_sensor_props() -> dict[str, dict]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    props: dict[str, dict] = {}
    for lid, row in raw.items():
        if not isinstance(row, dict):
            continue
        needed = [
            "residual_accel_bias_mg",
            "accel_noise_mg",
            "accel_bias_random_walk_mg_sqrt_s",
            "vibration_sensitivity_mg",
            "extrinsic_mg",
        ]
        if not all(k in row for k in needed):
            continue
        props[lid] = {
            "bias_mg": float(row["residual_accel_bias_mg"]),
            "noise_mg": float(row["accel_noise_mg"]),
            "rw_mg": float(row["accel_bias_random_walk_mg_sqrt_s"]),
            "vib_mg": float(row["vibration_sensitivity_mg"]),
            "extrinsic_mg": float(row["extrinsic_mg"]),
        }
    return props


def parse_row_spec(spec: str) -> tuple[str, str, str, str]:
    parts = spec.split(":")
    if len(parts) != 4:
        raise ValueError(f"row spec must be R:L:I:T, got {spec!r}")
    r_id, l_id, i_id, t_id = parts
    if not r_id.startswith("R") or not l_id.startswith("L") or not i_id.startswith("I") or not t_id.startswith("T"):
        raise ValueError(f"invalid row spec {spec!r}")
    return r_id, l_id, i_id, t_id


def limit_raw_frames(raw: pd.DataFrame, max_frames: int) -> pd.DataFrame:
    if max_frames <= 0:
        return raw.copy()
    frames = raw[["time_s", "sweep"]].drop_duplicates().head(max_frames)
    limited = raw.merge(frames, on=["time_s", "sweep"], how="inner")
    return limited.copy()


def load_raw_frames_limited(b0: pd.DataFrame, max_tracks: int) -> dict[tuple[str, str], pd.DataFrame]:
    if max_tracks <= 0:
        return S1.load_raw_frames(b0)
    pairing = S1.P1.build_pairing_manifest()
    raw_ready = [
        r
        for r in pairing
        if int(r.get("uwb_capture_count", 0)) == 1 and str(r.get("alignment_status")) == "ok"
    ]
    beta_by_capture = {str(r["capture_id"]): float(r["beta_s"]) for r in raw_ready}
    b0_by_track = {k: g.sort_values("opti_time_s") for k, g in b0.groupby(["capture_id", "tag"], sort=True)}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for pair in raw_ready:
        cap_id = str(pair["capture_id"])
        cap_dir = S1.OFFICIAL_ROOT / str(pair["uwb_capture_path"])
        tr_files = sorted(cap_dir.glob("tag_capture*/tr_all.csv"))
        if not tr_files:
            continue
        raw = pd.read_csv(
            tr_files[0],
            usecols=["host_elapsed_s", "sweep", "peer_name", "anchor_id", "range_mm", "quality_percent", "valid"],
        )
        raw = raw[(raw["valid"].astype(float) > 0) & (raw["range_mm"].astype(float) > 0)].copy()
        raw["anchor_id"] = raw["anchor_id"].astype(int)
        raw = raw[raw["anchor_id"].between(0, 7)].copy()
        for tag in S1.TAGS:
            key = (cap_id, tag)
            if key not in b0_by_track:
                continue
            g = raw[raw["peer_name"].astype(str) == tag].copy()
            if g.empty:
                continue
            opti_time = g["host_elapsed_s"].to_numpy(float) + beta_by_capture[cap_id]
            samples = b0_by_track[key]
            opti_xyz = S1.interpolate_xyz(
                samples["opti_time_s"].to_numpy(float),
                samples[["opti_x_mm", "opti_y_mm", "opti_z_mm"]].to_numpy(float),
                opti_time,
            )
            uwb_xyz = S1.interpolate_xyz(
                samples["opti_time_s"].to_numpy(float),
                samples[["uwb_x_mm", "uwb_y_mm", "uwb_z_mm"]].to_numpy(float),
                opti_time,
            )
            gg = g.copy()
            gg["capture_id"] = cap_id
            gg["tag"] = tag
            gg["time_s"] = gg["host_elapsed_s"].astype(float)
            gg["opti_time_s"] = opti_time
            gg["opti_x_mm"] = opti_xyz[:, 0]
            gg["opti_y_mm"] = opti_xyz[:, 1]
            gg["opti_z_mm"] = opti_xyz[:, 2]
            gg["uwb_x_mm"] = uwb_xyz[:, 0]
            gg["uwb_y_mm"] = uwb_xyz[:, 1]
            gg["uwb_z_mm"] = uwb_xyz[:, 2]
            out[key] = gg
            if len(out) >= max_tracks:
                return out
    return out


def finite_xyz(values: np.ndarray) -> np.ndarray:
    return np.isfinite(values).all(axis=-1)


def build_track_tensors(
    raw_by_track: dict[tuple[str, str], pd.DataFrame],
    prior: pd.DataFrame,
    max_tracks: int,
    max_frames: int,
    anchor_count: int = 8,
) -> dict[str, object]:
    prior_by_track = {k: g.sort_values("opti_time_s") for k, g in prior.groupby(["capture_id", "tag"], sort=True)}
    keys = sorted(k for k in raw_by_track if k in prior_by_track)
    if max_tracks > 0:
        keys = keys[:max_tracks]
    if not keys:
        raise RuntimeError("no raw tracks matched the IMU prior tracks")

    track_frames: list[list[tuple[float, int, pd.DataFrame]]] = []
    for key in keys:
        raw = limit_raw_frames(raw_by_track[key], max_frames)
        frames = []
        for (time_s, sweep), g in raw.groupby(["time_s", "sweep"], sort=True):
            frames.append((float(time_s), int(sweep), g))
        if not frames:
            raise RuntimeError(f"empty pilot frames for {key}")
        track_frames.append(frames)

    bsz = len(keys)
    tmax = max(len(frames) for frames in track_frames)
    ranges = np.full((bsz, tmax, anchor_count), np.nan, dtype=np.float32)
    mask = np.zeros((bsz, tmax, anchor_count), dtype=bool)
    frame_mask = np.zeros((bsz, tmax), dtype=bool)
    opti_time = np.full((bsz, tmax), np.nan, dtype=np.float64)
    raw_time = np.full((bsz, tmax), np.nan, dtype=np.float64)
    opti_xyz = np.full((bsz, tmax, 3), np.nan, dtype=np.float32)
    uwb_xyz = np.full((bsz, tmax, 3), np.nan, dtype=np.float32)
    prior_xyz = np.full((bsz, tmax, 3), np.nan, dtype=np.float32)

    limited_raw: dict[tuple[str, str], pd.DataFrame] = {}
    for bidx, key in enumerate(keys):
        frames = track_frames[bidx]
        raw_limited = pd.concat([f[2] for f in frames], ignore_index=True)
        limited_raw[key] = raw_limited
        prior_track = prior_by_track[key]
        frame_opti_times = np.asarray([float(f[2]["opti_time_s"].iloc[0]) for f in frames], dtype=float)
        interp_prior = S1.interpolate_xyz(
            prior_track["opti_time_s"].to_numpy(float),
            prior_track[["x_mm", "y_mm", "z_mm"]].to_numpy(float),
            frame_opti_times,
        )
        for tidx, (time_s, _sweep, g) in enumerate(frames):
            aid = g["anchor_id"].to_numpy(int)
            good = (aid >= 0) & (aid < anchor_count)
            aid = aid[good]
            z = g["range_mm"].to_numpy(float)[good]
            ranges[bidx, tidx, aid] = z.astype(np.float32)
            mask[bidx, tidx, aid] = True
            frame_mask[bidx, tidx] = True
            raw_time[bidx, tidx] = time_s
            opti_time[bidx, tidx] = frame_opti_times[tidx]
            opti_xyz[bidx, tidx, :] = [
                float(g["opti_x_mm"].iloc[0]),
                float(g["opti_y_mm"].iloc[0]),
                float(g["opti_z_mm"].iloc[0]),
            ]
            uwb_xyz[bidx, tidx, :] = [
                float(g["uwb_x_mm"].iloc[0]),
                float(g["uwb_y_mm"].iloc[0]),
                float(g["uwb_z_mm"].iloc[0]),
            ]
            prior_xyz[bidx, tidx, :] = interp_prior[tidx].astype(np.float32)

    return {
        "keys": keys,
        "raw_by_track": limited_raw,
        "ranges": ranges,
        "range_mask": mask,
        "frame_mask": frame_mask,
        "raw_time": raw_time,
        "opti_time": opti_time,
        "opti_xyz": opti_xyz,
        "uwb_xyz": uwb_xyz,
        "prior_xyz": prior_xyz,
    }


def sync_if_cuda(device: str) -> None:
    import torch

    if device.startswith("cuda") and torch.cuda.is_available():
        torch.cuda.synchronize(device)


def torch_range_ekf(
    tensors: dict[str, object],
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    prior_sigma: float,
    range_sigma_scale: float,
    robust: bool,
    device: str,
    dtype_name: str,
) -> dict[str, np.ndarray | float]:
    import torch

    dtype = torch.float64 if dtype_name == "float64" else torch.float32
    dev = torch.device(device)

    ranges = torch.as_tensor(tensors["ranges"], dtype=dtype, device=dev)
    obs_mask = torch.as_tensor(tensors["range_mask"], dtype=torch.bool, device=dev)
    frame_mask = torch.as_tensor(tensors["frame_mask"], dtype=torch.bool, device=dev)
    prior_xyz = torch.as_tensor(tensors["prior_xyz"], dtype=dtype, device=dev)
    uwb_xyz = torch.as_tensor(tensors["uwb_xyz"], dtype=dtype, device=dev)

    anchor = torch.as_tensor(anchor_xyz, dtype=dtype, device=dev)
    delay = torch.as_tensor(anchor_delay, dtype=dtype, device=dev)
    bias = torch.as_tensor(range_bias, dtype=dtype, device=dev)
    sigma_base = torch.as_tensor(range_sigma * range_sigma_scale, dtype=dtype, device=dev)
    ident3 = torch.eye(3, dtype=dtype, device=dev)

    bsz, tmax, anchor_count = ranges.shape
    out = torch.full((bsz, tmax, 3), float("nan"), dtype=dtype, device=dev)
    nis_vals = torch.full((bsz, tmax), float("nan"), dtype=dtype, device=dev)
    accept = torch.zeros((bsz, tmax), dtype=dtype, device=dev)

    uwb0_ok = torch.isfinite(uwb_xyz[:, 0, :]).all(dim=1)
    prior0_ok = torch.isfinite(prior_xyz[:, 0, :]).all(dim=1)
    fallback = torch.nan_to_num(prior_xyz[:, 0, :], nan=0.0)
    x = torch.where(uwb0_ok[:, None], uwb_xyz[:, 0, :], fallback)
    x = torch.where((~uwb0_ok & prior0_ok)[:, None], prior_xyz[:, 0, :], x)
    p = ident3.expand(bsz, 3, 3).clone() * (180.0**2)
    prev_prior = prior_xyz[:, 0, :].clone()
    prev_ok = prior0_ok
    eye_b = ident3.expand(bsz, 3, 3)

    for tidx in range(tmax):
        prior_ok = torch.isfinite(prior_xyz[:, tidx, :]).all(dim=1) & frame_mask[:, tidx]
        if tidx > 0:
            use_delta = prior_ok & prev_ok
        else:
            use_delta = torch.zeros(bsz, dtype=torch.bool, device=dev)
        delta = torch.where(use_delta[:, None], prior_xyz[:, tidx, :] - prev_prior, torch.zeros_like(x))
        q_scale = torch.where(use_delta, torch.ones(bsz, dtype=dtype, device=dev), torch.full((bsz,), 12.0, dtype=dtype, device=dev))
        x_pred = x + delta
        p_pred = p + eye_b * ((float(prior_sigma) ** 2) * q_scale)[:, None, None]

        valid_anchor_count = obs_mask[:, tidx, :].sum(dim=1)
        do_update = (valid_anchor_count >= 4) & frame_mask[:, tidx]
        diff = x_pred[:, None, :] - anchor[None, :, :]
        dist = torch.linalg.vector_norm(diff, dim=2).clamp_min(1.0e-6)
        h = diff / dist[:, :, None]
        corrected = ranges[:, tidx, :] - bias[None, :] - delay[None, :] - float(tag_delay)
        residual = corrected - dist

        valid = obs_mask[:, tidx, :] & do_update[:, None]
        h = torch.where(valid[:, :, None], h, torch.zeros_like(h))
        residual = torch.where(valid, residual, torch.zeros_like(residual))
        sigma = sigma_base[None, :].expand(bsz, anchor_count).clone()
        if robust:
            gate = valid & (torch.abs(residual) > 3.0 * sigma)
            sigma = torch.where(gate, sigma * 8.0, sigma)
        sigma = torch.clamp(sigma, min=40.0)
        # Information-form measurement update avoids padded 8x8 matrices with
        # huge invalid-anchor variances. It is equivalent to the linearized
        # Kalman update at x_pred, but much friendlier to batched GPU math.
        weight = torch.where(valid, 1.0 / (sigma * sigma), torch.zeros_like(sigma))
        p_inv = torch.linalg.pinv(p_pred)
        info = p_inv + torch.einsum("bai,baj,ba->bij", h, h, weight)
        rhs = torch.einsum("bai,ba,ba->bi", h, residual, weight)
        p_upd = torch.linalg.pinv(info)
        x_upd = x_pred + (p_upd @ rhs[:, :, None]).squeeze(2)
        nis = torch.sum(residual * residual * weight, dim=1)

        x = torch.where(do_update[:, None], x_upd, x_pred)
        p = torch.where(do_update[:, None, None], p_upd, p_pred)
        out[:, tidx, :] = torch.where(frame_mask[:, tidx, None], x, out[:, tidx, :])
        nis_vals[:, tidx] = torch.where(do_update, nis, nis_vals[:, tidx])
        accept[:, tidx] = do_update.to(dtype)
        prev_prior = torch.where(prior_ok[:, None], prior_xyz[:, tidx, :], prev_prior)
        prev_ok = prior_ok | prev_ok

    sync_if_cuda(device)
    return {
        "xyz": out.detach().cpu().numpy(),
        "nis": nis_vals.detach().cpu().numpy(),
        "accept": accept.detach().cpu().numpy(),
    }


def run_cpu_row(
    tensors: dict[str, object],
    prior: pd.DataFrame,
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    prior_sigma: float,
    robust: bool,
) -> dict[tuple[str, str], pd.DataFrame]:
    prior_by_track = {k: g.sort_values("opti_time_s") for k, g in prior.groupby(["capture_id", "tag"], sort=True)}
    out: dict[tuple[str, str], pd.DataFrame] = {}
    for key, raw in tensors["raw_by_track"].items():
        out[key] = S1.range_ekf_track(
            raw,
            prior_by_track[key],
            anchor_xyz,
            anchor_delay,
            tag_delay,
            range_bias,
            range_sigma,
            prior_sigma,
            robust,
        )
    return out


def compare_cpu_gpu(cpu: dict[tuple[str, str], pd.DataFrame], gpu: dict[str, object], tensors: dict[str, object]) -> list[dict]:
    rows: list[dict] = []
    xyz = np.asarray(gpu["xyz"], dtype=float)
    accept = np.asarray(gpu["accept"], dtype=float)
    for bidx, key in enumerate(tensors["keys"]):
        cpu_df = cpu[key].reset_index(drop=True)
        n = min(len(cpu_df), xyz.shape[1])
        cpu_xyz = cpu_df[["x_mm", "y_mm", "z_mm"]].to_numpy(float)[:n]
        gpu_xyz = xyz[bidx, :n, :]
        frame_ok = np.asarray(tensors["frame_mask"], dtype=bool)[bidx, :n]
        ok = frame_ok & finite_xyz(cpu_xyz) & finite_xyz(gpu_xyz)
        diff = gpu_xyz[ok] - cpu_xyz[ok]
        dist = np.linalg.norm(diff, axis=1) if diff.size else np.asarray([], dtype=float)
        rows.append(
            {
                "capture_id": key[0],
                "tag": key[1],
                "frames": int(n),
                "finite_frames": int(ok.sum()),
                "xyz_diff_rmse_mm": float(math.sqrt(float(np.mean(dist * dist)))) if dist.size else float("nan"),
                "xyz_diff_p95_mm": float(np.percentile(dist, 95)) if dist.size else float("nan"),
                "xyz_diff_max_mm": float(np.max(dist)) if dist.size else float("nan"),
                "cpu_accept_rate": float(cpu_df["uwb_update_accept_rate"].iloc[0]) if "uwb_update_accept_rate" in cpu_df else float("nan"),
                "gpu_accept_rate": float(np.nanmean(accept[bidx, :n])),
            }
        )
    return rows


def run(args: argparse.Namespace) -> dict:
    import torch

    torch.set_num_threads(max(1, int(args.torch_threads)))
    try:
        torch.set_num_interop_threads(max(1, int(args.torch_threads)))
    except RuntimeError:
        # PyTorch only allows this before parallel work starts; ignore if an
        # import side effect already initialized it.
        pass

    phase2_run = args.phase2_run or find_latest_phase2_run()
    phase2_dir = SIM_ROOT / "runs" / "phase2_screening" / phase2_run
    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prior_run_id = str(args.prior_run_id or run_id)
    cache_root = Path(args.cache_root).resolve()
    run_dir = SIM_ROOT / "runs" / "phase4_gpu_pilot" / run_id
    for d in [run_dir / "tables", run_dir / "reports"]:
        d.mkdir(parents=True, exist_ok=True)

    l_props = load_sensor_props()
    if l_props:
        S1.L_PROPS = l_props

    seed_id = str(getattr(args, "seed_id", "") or "")
    manifest = {
        "run_id": run_id,
        "prior_run_id": prior_run_id,
        "seed_id": seed_id,
        "phase2_run": phase2_run,
        "generated_utc": datetime.now(UTC).isoformat(),
        "device": args.device,
        "dtype": args.dtype,
        "torch_threads": int(args.torch_threads),
        "agreement_mode": args.agreement_mode,
        "agreement_sample_rows": int(args.agreement_sample_rows),
        "cache_mode": args.cache_mode,
        "cache_root": str(cache_root),
        "max_tracks": args.max_tracks,
        "max_frames": args.max_frames,
        "rows": args.rows,
        "torch_version": getattr(torch, "__version__", ""),
        "cuda_available": bool(torch.cuda.is_available()),
        "note": "Pilot only. Do not use as final Phase 4 ranking.",
    }
    write_json(run_dir / "manifest.json", manifest)

    b0 = S1.load_b0_samples()
    raw_by_track = load_raw_frames_limited(b0, args.max_tracks)
    anchor_xyz, anchor_delay, tag_delay = S1.load_a0_layout()
    range_bias, range_sigma_base = S1.load_range_policy(phase2_dir)

    result_rows: list[dict] = []
    timing_rows: list[dict] = []
    prior_cache: dict[tuple[str, str], pd.DataFrame] = {}
    tensor_cache: dict[tuple[str, str], dict[str, object]] = {}
    for row_idx, row_spec in enumerate(args.rows):
        r_id, l_id, i_id, t_id = parse_row_spec(row_spec)
        if t_id not in S1.T_PARAMS:
            raise ValueError(f"{t_id} is not implemented by current CPU golden")
        if t_id not in {"T6", "T8"}:
            raise ValueError("phase4 GPU pilot currently supports CPU-golden T6/T8 only")
        params = S1.T_PARAMS[t_id]
        process = S1.li_process_factor(l_id, i_id)
        prior_sigma = float(params["prior_sigma_base"]) * process
        if l_id == "L0":
            prior_sigma = min(prior_sigma, 45.0)
        range_sigma_scale = 1.0 if r_id == "R2" else 1.35
        robust = t_id == "T8" or r_id == "R4"
        range_sigma = range_sigma_base * range_sigma_scale
        should_compare = args.agreement_mode == "full" or (args.agreement_mode == "sample" and row_idx < max(1, int(args.agreement_sample_rows)))

        cache_key = (l_id, i_id)
        if cache_key not in prior_cache:
            p_cache = prior_cache_path(cache_root, prior_run_id, l_id, i_id)
            if args.cache_mode != "off" and p_cache.exists():
                t0 = time.perf_counter()
                prior_cache[cache_key] = pd.read_pickle(p_cache)
                timing_rows.append({"row_spec": row_spec, "seed_id": seed_id, "stage": "load_imu_prior_cache", "cache_key": f"{l_id}:{i_id}", "wall_time_s": time.perf_counter() - t0})
            else:
                t0 = time.perf_counter()
                prior_cache[cache_key] = S1.simulate_imu_for_li(b0, prior_run_id, l_id, i_id)
                timing_rows.append({"row_spec": row_spec, "seed_id": seed_id, "stage": "simulate_imu_prior", "cache_key": f"{l_id}:{i_id}", "wall_time_s": time.perf_counter() - t0})
                if args.cache_mode != "off":
                    save_pickle_cache(p_cache, prior_cache[cache_key])

        prior = prior_cache[cache_key]
        if cache_key not in tensor_cache:
            t_cache = tensor_cache_path(cache_root, prior_run_id, l_id, i_id, args.max_tracks, args.max_frames)
            cached_tensors = load_tensor_cache(t_cache) if args.cache_mode != "off" and not should_compare else None
            if cached_tensors is not None:
                t0 = time.perf_counter()
                tensor_cache[cache_key] = cached_tensors
                timing_rows.append({"row_spec": row_spec, "seed_id": seed_id, "stage": "load_track_tensors_cache", "cache_key": f"{l_id}:{i_id}", "wall_time_s": time.perf_counter() - t0})
            else:
                t0 = time.perf_counter()
                tensor_cache[cache_key] = build_track_tensors(raw_by_track, prior, args.max_tracks, args.max_frames)
                timing_rows.append({"row_spec": row_spec, "seed_id": seed_id, "stage": "build_track_tensors", "cache_key": f"{l_id}:{i_id}", "wall_time_s": time.perf_counter() - t0})
                if args.cache_mode != "off" and not should_compare:
                    save_tensor_cache(t_cache, tensor_cache[cache_key])
        tensors = tensor_cache[cache_key]

        cpu = None
        if should_compare:
            t0 = time.perf_counter()
            cpu = run_cpu_row(tensors, prior, anchor_xyz, anchor_delay, tag_delay, range_bias, range_sigma, prior_sigma, robust)
            timing_rows.append({"row_spec": row_spec, "seed_id": seed_id, "stage": "cpu_golden", "wall_time_s": time.perf_counter() - t0})

        gpu = None
        for _ in range(max(0, int(args.gpu_warmup))):
            gpu = torch_range_ekf(
                tensors,
                anchor_xyz,
                anchor_delay,
                tag_delay,
                range_bias,
                range_sigma_base,
                prior_sigma,
                range_sigma_scale,
                robust,
                args.device,
                args.dtype,
            )
        gpu_times: list[float] = []
        for _ in range(max(1, int(args.gpu_repeat))):
            sync_if_cuda(args.device)
            t0 = time.perf_counter()
            gpu = torch_range_ekf(
                tensors,
                anchor_xyz,
                anchor_delay,
                tag_delay,
                range_bias,
                range_sigma_base,
                prior_sigma,
                range_sigma_scale,
                robust,
                args.device,
                args.dtype,
            )
            sync_if_cuda(args.device)
            gpu_times.append(time.perf_counter() - t0)
        gpu_timing = {"row_spec": row_spec, "seed_id": seed_id, "stage": "torch_gpu", "gpu_warmup": int(args.gpu_warmup), **summarize_times(gpu_times)}
        timing_rows.append(gpu_timing)
        if gpu is None:
            raise RuntimeError("GPU pilot did not produce an output")

        if cpu is not None:
            for row in compare_cpu_gpu(cpu, gpu, tensors):
                row.update(
                    {
                        "row_spec": row_spec,
                        "seed_id": seed_id,
                        "R": r_id,
                        "L": l_id,
                        "I": i_id,
                        "T": t_id,
                        "prior_sigma_mm": prior_sigma,
                        "range_sigma_scale": range_sigma_scale,
                        "robust": robust,
                    }
                )
                result_rows.append(row)

    write_csv(run_dir / "tables" / "phase4_gpu_pilot_agreement.csv", result_rows)
    write_csv(run_dir / "tables" / "phase4_gpu_pilot_timing.csv", timing_rows)
    report = [
        "# Phase 4 GPU Pilot",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "This is a CPU/GPU agreement and timing pilot, not a final ranking run.",
        "",
        "## Outputs",
        "",
        "- `tables/phase4_gpu_pilot_agreement.csv`",
        "- `tables/phase4_gpu_pilot_timing.csv`",
    ]
    (run_dir / "reports" / "PHASE4_GPU_PILOT.md").write_text("\n".join(report), encoding="utf-8")
    return {"run_id": run_id, "run_dir": str(run_dir), "agreement_rows": len(result_rows), "timing_rows": len(timing_rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase2-run", default="", help="Phase 2 run containing G3 range-bias policy. Defaults to latest.")
    parser.add_argument("--run-id", default="", help="Output run id. Defaults to current UTC timestamp.")
    parser.add_argument("--prior-run-id", default="", help="Stable id used for IMU prior random seeds. Defaults to --run-id.")
    parser.add_argument("--seed-id", default="", help="Seed label written to manifest/tables, e.g. S00.")
    parser.add_argument("--rows", nargs="+", default=DEFAULT_ROWS, help="Pilot row specs formatted as R:L:I:T.")
    parser.add_argument("--max-tracks", type=int, default=2, help="Maximum ROTO/tag tracks for the tiny pilot.")
    parser.add_argument("--max-frames", type=int, default=200, help="Maximum raw frames per track for the tiny pilot.")
    parser.add_argument("--device", default="cuda:0", help="Torch device, e.g. cuda:0, cuda:1, or cpu.")
    parser.add_argument("--dtype", choices=["float32", "float64"], default="float32", help="Torch dtype for GPU pilot.")
    parser.add_argument("--torch-threads", type=int, default=1, help="CPU threads used by torch feeder/reference helpers.")
    parser.add_argument("--agreement-mode", choices=["full", "sample", "none"], default="full", help="CPU/GPU comparison policy. Use none for throughput chunks after agreement smoke passes.")
    parser.add_argument("--agreement-sample-rows", type=int, default=1, help="Rows compared when --agreement-mode sample is used.")
    parser.add_argument("--cache-mode", choices=["off", "readwrite"], default="readwrite", help="Reuse IMU prior and GPU tensor caches to turn RAM/page cache into throughput.")
    parser.add_argument("--cache-root", default=str(CACHE_ROOT), help="Cache directory for prior pickle files and tensor npz files.")
    parser.add_argument("--gpu-warmup", type=int, default=0, help="Untimed GPU warmup repeats.")
    parser.add_argument("--gpu-repeat", type=int, default=1, help="Timed GPU repeats; median is reported.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
