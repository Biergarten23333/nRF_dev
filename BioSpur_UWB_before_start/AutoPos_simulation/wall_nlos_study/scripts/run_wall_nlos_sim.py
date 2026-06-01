#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


WALL_SETS = {
    0: [],
    1: ["x_min"],
    2: ["x_min", "x_max"],
    3: ["x_min", "x_max", "y_min"],
    4: ["x_min", "x_max", "y_min", "y_max"],
}
DISTANCES_CM = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 100]
MATERIALS = {
    "light_drywall": {"bias_scale_m": 0.04, "sigma_scale_m": 0.025, "grazing_gain": 0.60},
    "gypsum_block": {"bias_scale_m": 0.07, "sigma_scale_m": 0.035, "grazing_gain": 0.80},
    "aerated_concrete": {"bias_scale_m": 0.09, "sigma_scale_m": 0.045, "grazing_gain": 0.90},
    "sand_lime_brick": {"bias_scale_m": 0.13, "sigma_scale_m": 0.060, "grazing_gain": 1.00},
    "reinforced_concrete_C25_30": {"bias_scale_m": 0.18, "sigma_scale_m": 0.075, "grazing_gain": 1.15},
    "reinforced_concrete_C30_37": {"bias_scale_m": 0.21, "sigma_scale_m": 0.085, "grazing_gain": 1.25},
    "reinforced_concrete_C40_50": {"bias_scale_m": 0.24, "sigma_scale_m": 0.100, "grazing_gain": 1.35},
}
PHASE1_MATERIAL = {"bias_scale_m": 0.18, "sigma_scale_m": 0.07, "grazing_gain": 1.0}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def layout_3x3x1p4() -> np.ndarray:
    return np.asarray(
        [
            [0.0, 0.0, 1.8],
            [3.0, 0.0, 1.8],
            [3.0, 3.0, 1.8],
            [0.0, 3.0, 1.8],
            [0.0, 0.0, 0.4],
            [3.0, 0.0, 0.4],
            [3.0, 3.0, 0.4],
            [0.0, 3.0, 0.4],
        ],
        dtype=np.float32,
    )


def tag_grid(spacing_m: float) -> np.ndarray:
    xs = np.arange(0.25, 2.75 + 0.5 * spacing_m, spacing_m, dtype=np.float32)
    ys = np.arange(0.25, 2.75 + 0.5 * spacing_m, spacing_m, dtype=np.float32)
    zs = np.arange(0.35, 1.85 + 0.5 * spacing_m, spacing_m, dtype=np.float32)
    xx, yy, zz = np.meshgrid(xs, ys, zs, indexing="xy")
    return np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype(np.float32)


def wall_positions(distance_m: float) -> dict[str, float]:
    return {
        "x_min": -distance_m,
        "x_max": 3.0 + distance_m,
        "y_min": -distance_m,
        "y_max": 3.0 + distance_m,
    }


def metal_boxes(seed: int, count: int, distance_m: float) -> list[dict[str, Any]]:
    rng = np.random.default_rng(seed)
    boxes = []
    for idx in range(count):
        side = rng.choice(["x_min", "x_max", "y_min", "y_max"])
        length = float(rng.uniform(0.35, 0.9))
        depth = float(rng.uniform(0.12, 0.35))
        height = float(rng.uniform(0.4, 1.2))
        z0 = float(rng.uniform(0.0, max(0.05, 2.2 - height)))
        if side in {"x_min", "x_max"}:
            y0 = float(rng.uniform(0.0, 3.0 - length))
            if side == "x_min":
                x0 = -distance_m - depth - float(rng.uniform(0.02, 0.25))
            else:
                x0 = 3.0 + distance_m + float(rng.uniform(0.02, 0.25))
            size = [depth, length, height]
        else:
            x0 = float(rng.uniform(0.0, 3.0 - length))
            if side == "y_min":
                y0 = -distance_m - depth - float(rng.uniform(0.02, 0.25))
            else:
                y0 = 3.0 + distance_m + float(rng.uniform(0.02, 0.25))
            size = [length, depth, height]
        boxes.append({"id": idx, "side": side, "origin_m": [x0, y0, z0], "size_m": size})
    return boxes


def simulate(
    anchors_np: np.ndarray,
    tags_np: np.ndarray,
    wall_names: list[str],
    distance_m: float,
    *,
    device_name: str,
    trials: int,
    seed: int,
    metal_box_count: int = 0,
    material: dict[str, float] | None = None,
) -> dict[str, Any]:
    import torch

    torch.manual_seed(seed)
    device = torch.device(device_name)
    anchors = torch.as_tensor(anchors_np, dtype=torch.float32, device=device)
    tags = torch.as_tensor(tags_np, dtype=torch.float32, device=device)
    diff = tags[:, None, :] - anchors[None, :, :]
    ranges = torch.linalg.norm(diff, dim=2).clamp_min(1e-6)
    g = diff / ranges[:, :, None]
    fim = torch.matmul(g.transpose(1, 2), g)
    q = torch.linalg.inv(fim + torch.eye(3, device=device)[None, :, :] * 1e-6)
    gt = torch.matmul(q, g.transpose(1, 2))

    base_sigma = 0.03
    sigma = torch.full(ranges.shape, base_sigma, dtype=torch.float32, device=device)
    bias = torch.zeros(ranges.shape, dtype=torch.float32, device=device)
    walls = wall_positions(distance_m)
    material = material or PHASE1_MATERIAL
    bias_scale = float(material["bias_scale_m"])
    sigma_scale = float(material["sigma_scale_m"])
    grazing_gain = float(material["grazing_gain"])

    for wall_name in wall_names:
        pos = walls[wall_name]
        if wall_name == "x_min":
            da = torch.abs(anchors[:, 0] - pos)[None, :]
            dt = torch.abs(tags[:, 0] - pos)[:, None]
            normal_comp = torch.abs(g[:, :, 0])
        elif wall_name == "x_max":
            da = torch.abs(anchors[:, 0] - pos)[None, :]
            dt = torch.abs(tags[:, 0] - pos)[:, None]
            normal_comp = torch.abs(g[:, :, 0])
        elif wall_name == "y_min":
            da = torch.abs(anchors[:, 1] - pos)[None, :]
            dt = torch.abs(tags[:, 1] - pos)[:, None]
            normal_comp = torch.abs(g[:, :, 1])
        else:
            da = torch.abs(anchors[:, 1] - pos)[None, :]
            dt = torch.abs(tags[:, 1] - pos)[:, None]
            normal_comp = torch.abs(g[:, :, 1])

        near = torch.exp(-torch.minimum(da, dt) / 0.35)
        grazing = torch.clamp(1.0 - normal_comp, 0.0, 1.0) ** 1.4
        risk = near * (0.35 + 0.65 * grazing_gain * grazing)
        bias += bias_scale * risk
        sigma += sigma_scale * risk

    if metal_box_count:
        # Coarse extra reflector model from photo-like equipment near the layout boundary.
        rng = np.random.default_rng(seed)
        box_sides = rng.choice(["x_min", "x_max", "y_min", "y_max"], size=metal_box_count)
        for side in box_sides:
            if side in {"x_min", "x_max"}:
                coord = tags[:, 0][:, None]
                boundary = -distance_m if side == "x_min" else 3.0 + distance_m
            else:
                coord = tags[:, 1][:, None]
                boundary = -distance_m if side == "y_min" else 3.0 + distance_m
            proximity = torch.exp(-torch.abs(coord - boundary) / 0.55)
            bias += 0.06 * proximity
            sigma += 0.04 * proximity

    errors = []
    for _ in range(trials):
        noise = torch.randn_like(ranges) * sigma
        positive = torch.distributions.Exponential(torch.ones_like(ranges) / 0.12).sample() * (bias > 0).float()
        range_error = noise + bias + positive * torch.clamp(bias / 0.18, max=1.0)
        dx = torch.matmul(gt, range_error[:, :, None]).squeeze(2)
        errors.append(dx)
    err = torch.stack(errors, dim=0)
    hor = torch.linalg.norm(err[:, :, :2], dim=2).reshape(-1)
    ver = torch.abs(err[:, :, 2]).reshape(-1)
    pos = torch.linalg.norm(err, dim=2).reshape(-1)
    bias_mean = torch.mean(bias).item()
    sigma_mean = torch.mean(sigma).item()

    def pct(v: Any, p: float) -> float:
        return float(torch.quantile(v, p / 100.0).detach().cpu())

    return {
        "tag_points": int(tags.shape[0]),
        "trials": trials,
        "samples": int(pos.numel()),
        "range_bias_mean_m": bias_mean,
        "range_sigma_mean_m": sigma_mean,
        "hor_err_p50_m": pct(hor, 50),
        "hor_err_p90_m": pct(hor, 90),
        "hor_err_p95_m": pct(hor, 95),
        "z_err_p50_m": pct(ver, 50),
        "z_err_p90_m": pct(ver, 90),
        "z_err_p95_m": pct(ver, 95),
        "pos_err_p50_m": pct(pos, 50),
        "pos_err_p90_m": pct(pos, 90),
        "pos_err_p95_m": pct(pos, 95),
        "failure_gt_0p5m_ratio": float(torch.mean((pos > 0.5).float()).detach().cpu()),
        "failure_gt_1p0m_ratio": float(torch.mean((pos > 1.0).float()).detach().cpu()),
    }


def scenario_dir(out_root: Path, wall_count: int, dist_cm: int, seed: int | None) -> Path:
    base = out_root / f"wall_{wall_count}" / f"dist_{dist_cm:03d}cm"
    return base if seed is None else base / f"seed_{seed:04d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["phase1", "phase2", "phase3"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--grid-spacing-m", type=float, default=0.25)
    ap.add_argument("--trials", type=int, default=96)
    ap.add_argument("--phase2-seeds", type=int, default=12)
    ap.add_argument("--metal-box-count", type=int, default=6)
    args = ap.parse_args()

    out_root = Path(args.out)
    anchors = layout_3x3x1p4()
    tags = tag_grid(args.grid_spacing_m)
    rows: list[dict[str, Any]] = []
    scenario_id = 0
    materials = ["phase1_default_wall"] if args.phase in {"phase1", "phase2"} else list(MATERIALS)
    total = len(WALL_SETS) * len(DISTANCES_CM) * len(materials)
    if args.phase == "phase2":
        total *= args.phase2_seeds
    for material_name in materials:
        material = PHASE1_MATERIAL if material_name == "phase1_default_wall" else MATERIALS[material_name]
        for wall_count, walls in WALL_SETS.items():
            for dist_cm in DISTANCES_CM:
                seeds = [None] if args.phase in {"phase1", "phase3"} else list(range(args.phase2_seeds))
                for seed_item in seeds:
                    scenario_id += 1
                    distance_m = dist_cm / 100.0
                    seed = 10000 + wall_count * 1000 + dist_cm * 10 + (seed_item or 0) + len(material_name) * 100000
                    metal_count = 0 if args.phase != "phase2" else args.metal_box_count
                    print(f"[wall-nlos] {scenario_id}/{total} {args.phase} material={material_name} wall={wall_count} dist={dist_cm}cm seed={seed_item}", flush=True)
                    result = simulate(anchors, tags, walls, distance_m, device_name=args.device, trials=args.trials, seed=seed, metal_box_count=metal_count, material=material)
                    row = {
                        "phase": args.phase,
                        "material": material_name,
                        "wall_count": wall_count,
                        "walls": "+".join(walls) if walls else "none",
                        "distance_cm": dist_cm,
                        "seed": "" if seed_item is None else seed_item,
                        "metal_box_count": metal_count,
                        "device": args.device,
                    }
                    row.update(result)
                    rows.append(row)
                    sdir = scenario_dir(out_root / f"material_{material_name}", wall_count, dist_cm, seed_item)
                    sdir.mkdir(parents=True, exist_ok=True)
                    payload = {"scenario": row, "anchors_m": anchors.tolist(), "walls": walls, "distance_m": distance_m, "material": material}
                    if args.phase == "phase2":
                        payload["metal_boxes"] = metal_boxes(seed, metal_count, distance_m)
                    (sdir / "scenario.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                    write_csv(sdir / "summary.csv", [row])

    write_csv(out_root / "summary.csv", rows)
    meta = {
        "phase": args.phase,
        "layout": "3x3x1.4m paired anchors",
        "ceiling_height_m": 2.5,
        "wall_counts": sorted(WALL_SETS),
        "distances_cm": DISTANCES_CM,
        "grid_spacing_m": args.grid_spacing_m,
        "trials": args.trials,
        "device": args.device,
        "materials": materials,
    }
    (out_root / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"[wall-nlos] wrote {out_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
