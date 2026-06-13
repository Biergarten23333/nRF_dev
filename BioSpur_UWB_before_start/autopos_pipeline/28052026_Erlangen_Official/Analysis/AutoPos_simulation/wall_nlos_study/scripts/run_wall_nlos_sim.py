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
C_M_PER_S = 299_792_458.0
GROUND_GAMMA_MAGNITUDES = [0.5, 0.7, 0.9]
GROUND_RESOLUTION_CM = [15.0, 30.0, 60.0]
GROUND_Z_FLOOR_MM = [0.0, -100.0, -200.0, -300.0]
LOWEST_TRACKED_MARKER_Y_MM = 105.58
MEASURED_TIER_VERTICAL_ABS_MM = {
    "low": 112.647,
    "mid": 33.251,
    "high": 62.638,
}


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


def parse_float_list(text: str, default: list[float]) -> list[float]:
    if not text:
        return list(default)
    return [float(x.strip()) for x in text.split(",") if x.strip()]


def campaign_root_from_script() -> Path:
    return Path(__file__).resolve().parents[4]


def load_real_static_geometry(campaign_root: Path) -> tuple[list[str], np.ndarray, list[dict[str, Any]], np.ndarray]:
    tables = campaign_root / "Analysis" / "official_extra_analysis" / "FULL" / "tables"
    layout_path = tables / "layout_abs_errors_all8.csv"
    static_path = tables / "revision2_dop_at_static_vicon_positions.csv"

    anchor_rows: list[dict[str, str]] = []
    with layout_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["version"] == "v4-io" and row["eval_set"] == "all8":
                anchor_rows.append(row)
    anchor_rows.sort(key=lambda r: r["anchor"])
    anchor_labels = [r["anchor"] for r in anchor_rows]
    if anchor_labels != list("ABCDEFGH"):
        raise ValueError(f"expected anchors A-H in {layout_path}, got {anchor_labels}")

    # Convert Vicon truth coordinates into the simulator convention:
    # columns 0/1 are horizontal, column 2 is vertical.
    anchors_m = np.asarray(
        [
            [
                float(r["truth_x_mm"]) / 1000.0,
                float(r["truth_z_mm"]) / 1000.0,
                float(r["truth_y_vertical_mm"]) / 1000.0,
            ]
            for r in anchor_rows
        ],
        dtype=np.float64,
    )

    tag_rows: list[dict[str, Any]] = []
    tag_coords = []
    with static_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tag_rows.append(row)
            tag_coords.append(
                [
                    float(row["truth_x_mm"]) / 1000.0,
                    float(row["truth_z_mm"]) / 1000.0,
                    float(row["truth_y_vertical_mm"]) / 1000.0,
                ]
            )
    if len(tag_rows) != 24:
        raise ValueError(f"expected 24 static tag rows in {static_path}, got {len(tag_rows)}")
    return anchor_labels, anchors_m, tag_rows, np.asarray(tag_coords, dtype=np.float64)


def load_measured_signed_vertical(campaign_root: Path) -> tuple[dict[str, float], dict[str, int]]:
    path = campaign_root / "Analysis" / "official_extra_analysis" / "FULL" / "tables" / "tag_abs_errors_per_session.csv"
    by_height: dict[str, list[float]] = {"low": [], "mid": [], "high": []}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["version"] == "v4-io" and row["eval_set"] == "all8" and row["method"] == "C_anchor_locked_OFFICIAL":
                by_height[row["height"]].append(float(row["err_y_vertical_mm"]))
    med = {h: float(np.median(v)) for h, v in by_height.items()}
    n = {h: len(v) for h, v in by_height.items()}
    if any(count != 8 for count in n.values()):
        raise ValueError(f"expected 8 signed vertical rows per height, got {n}")
    return med, n


def geometry_projection(anchors_m: np.ndarray, tags_m: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    diff = tags_m[:, None, :] - anchors_m[None, :, :]
    ranges = np.linalg.norm(diff, axis=2)
    ranges = np.maximum(ranges, 1e-9)
    g = diff / ranges[:, :, None]
    fim = np.matmul(np.swapaxes(g, 1, 2), g)
    q = np.linalg.inv(fim + np.eye(3, dtype=np.float64)[None, :, :] * 1e-9)
    gt = np.matmul(q, np.swapaxes(g, 1, 2))
    return ranges, g, gt


def ground_reflection_range_bias(
    anchors_m: np.ndarray,
    tags_m: np.ndarray,
    *,
    z_floor_m: float,
    gamma_abs: float,
    resolution_m: float,
) -> tuple[np.ndarray, dict[str, float]]:
    mirrored = anchors_m.copy()
    mirrored[:, 2] = 2.0 * z_floor_m - mirrored[:, 2]
    direct = np.linalg.norm(tags_m[:, None, :] - anchors_m[None, :, :], axis=2)
    reflected = np.linalg.norm(tags_m[:, None, :] - mirrored[None, :, :], axis=2)
    excess_m = np.maximum(reflected - direct, 0.0)
    tau_s = excess_m / C_M_PER_S
    tau_res_s = resolution_m / C_M_PER_S

    # First-order unresolved two-ray detector model:
    # WB003 is treated as vertically polarised, so the grazing-incidence
    # ground-reflection coefficient is sign-inverted: Gamma = -|Gamma|.
    # The sign-inverted delayed lobe is assumed to delay threshold crossing,
    # giving a positive range bias. A smooth unresolved-path weight
    # exp(-(DeltaL/resolution)^2) makes the bias largest as DeltaL -> 0 and
    # suppresses it as the excess path exceeds the effective range resolution.
    gamma_signed = -abs(gamma_abs)
    unresolved_weight = np.exp(-((excess_m / max(resolution_m, 1e-9)) ** 2))
    bias_m = abs(gamma_signed) * resolution_m * unresolved_weight

    return bias_m, {
        "gamma_signed": float(gamma_signed),
        "range_bias_mean_mm": float(np.mean(bias_m) * 1000.0),
        "range_bias_median_mm": float(np.median(bias_m) * 1000.0),
        "range_bias_p95_mm": float(np.percentile(bias_m, 95) * 1000.0),
        "excess_path_median_mm": float(np.median(excess_m) * 1000.0),
        "excess_path_p95_mm": float(np.percentile(excess_m, 95) * 1000.0),
        "tau_median_ns": float(np.median(tau_s) * 1e9),
        "tau_res_ns": float(tau_res_s * 1e9),
    }


def sign_label(value: float, eps: float = 1e-9) -> str:
    if value > eps:
        return "positive"
    if value < -eps:
        return "negative"
    return "zero"


def run_ground_reflection_real_static(
    *,
    campaign_root: Path,
    out_root: Path,
    gamma_magnitudes: list[float],
    resolution_cm: list[float],
    z_floor_mm: list[float],
) -> int:
    out_root.mkdir(parents=True, exist_ok=True)
    if any(z > LOWEST_TRACKED_MARKER_Y_MM for z in z_floor_mm):
        raise ValueError(
            f"all z_floor values must be <= {LOWEST_TRACKED_MARKER_Y_MM:.2f} mm, "
            "the lowest tracked-marker vertical coordinate"
        )

    anchor_labels, anchors_m, tag_rows, tags_m = load_real_static_geometry(campaign_root)
    measured_signed_median, measured_counts = load_measured_signed_vertical(campaign_root)
    _, _, gt = geometry_projection(anchors_m, tags_m)

    summary_rows: list[dict[str, Any]] = []
    per_position_rows: list[dict[str, Any]] = []
    measured_rows = [
        {
            "height": height,
            "n": measured_counts[height],
            "measured_signed_vertical_median_mm": measured_signed_median[height],
            "measured_signed_vertical_sign": sign_label(measured_signed_median[height]),
            "measured_abs_vertical_target_mm": MEASURED_TIER_VERTICAL_ABS_MM[height],
        }
        for height in ("low", "mid", "high")
    ]

    for gamma_abs in gamma_magnitudes:
        for res_cm in resolution_cm:
            resolution_m = res_cm / 100.0
            for floor_mm in z_floor_mm:
                bias_m, bias_stats = ground_reflection_range_bias(
                    anchors_m,
                    tags_m,
                    z_floor_m=floor_mm / 1000.0,
                    gamma_abs=gamma_abs,
                    resolution_m=resolution_m,
                )
                dx = np.matmul(gt, bias_m[:, :, None]).squeeze(2)
                hor_mm = np.linalg.norm(dx[:, :2], axis=1) * 1000.0
                ver_signed_mm = dx[:, 2] * 1000.0
                ver_abs_mm = np.abs(ver_signed_mm)
                pos_mm = np.linalg.norm(dx, axis=1) * 1000.0

                tier_stats: dict[str, dict[str, float]] = {}
                for height in ("low", "mid", "high"):
                    idx = [i for i, r in enumerate(tag_rows) if r["height"] == height]
                    tier_stats[height] = {
                        "pred_vertical_signed_median_mm": float(np.median(ver_signed_mm[idx])),
                        "pred_vertical_abs_median_mm": float(np.median(ver_abs_mm[idx])),
                        "pred_horizontal_median_mm": float(np.median(hor_mm[idx])),
                        "pred_3d_median_mm": float(np.median(pos_mm[idx])),
                    }

                low_abs = tier_stats["low"]["pred_vertical_abs_median_mm"]
                mid_abs = tier_stats["mid"]["pred_vertical_abs_median_mm"]
                high_abs = tier_stats["high"]["pred_vertical_abs_median_mm"]
                low_worst = low_abs > mid_abs and low_abs > high_abs
                measured_order_match = low_abs > high_abs > mid_abs
                vertical_dominance = float(np.median(ver_abs_mm)) > float(np.median(hor_mm))
                sign_match_all = all(
                    sign_label(tier_stats[h]["pred_vertical_signed_median_mm"])
                    == sign_label(measured_signed_median[h])
                    for h in ("low", "mid", "high")
                )
                sign_match_low = (
                    sign_label(tier_stats["low"]["pred_vertical_signed_median_mm"])
                    == sign_label(measured_signed_median["low"])
                )

                row = {
                    "mode": "ground_reflection_real_static",
                    "gamma_abs": gamma_abs,
                    "gamma_signed": bias_stats["gamma_signed"],
                    "resolution_cm": res_cm,
                    "z_floor_mm": floor_mm,
                    "n_positions": len(tag_rows),
                    "horizontal_median_mm": float(np.median(hor_mm)),
                    "vertical_abs_median_mm": float(np.median(ver_abs_mm)),
                    "vertical_signed_median_mm": float(np.median(ver_signed_mm)),
                    "pos_3d_median_mm": float(np.median(pos_mm)),
                    "horizontal_p95_mm": float(np.percentile(hor_mm, 95)),
                    "vertical_abs_p95_mm": float(np.percentile(ver_abs_mm, 95)),
                    "pos_3d_p95_mm": float(np.percentile(pos_mm, 95)),
                    "vertical_dominance": vertical_dominance,
                    "low_worst": low_worst,
                    "measured_order_low_high_mid": measured_order_match,
                    "sign_match_low": sign_match_low,
                    "sign_match_all_tiers": sign_match_all,
                    **bias_stats,
                }
                for height in ("low", "mid", "high"):
                    for key, value in tier_stats[height].items():
                        row[f"{height}_{key}"] = value
                    row[f"{height}_measured_abs_vertical_target_mm"] = MEASURED_TIER_VERTICAL_ABS_MM[height]
                    row[f"{height}_measured_signed_vertical_median_mm"] = measured_signed_median[height]
                    row[f"{height}_pred_signed_sign"] = sign_label(tier_stats[height]["pred_vertical_signed_median_mm"])
                    row[f"{height}_measured_signed_sign"] = sign_label(measured_signed_median[height])
                summary_rows.append(row)

                for i, tag_row in enumerate(tag_rows):
                    per_position_rows.append(
                        {
                            "gamma_abs": gamma_abs,
                            "gamma_signed": bias_stats["gamma_signed"],
                            "resolution_cm": res_cm,
                            "z_floor_mm": floor_mm,
                            "ID": tag_row["ID"],
                            "height": tag_row["height"],
                            "location": tag_row["location"],
                            "facing": tag_row["facing"],
                            "truth_x_mm": tag_row["truth_x_mm"],
                            "truth_y_vertical_mm": tag_row["truth_y_vertical_mm"],
                            "truth_z_mm": tag_row["truth_z_mm"],
                            "pred_dx_horizontal1_mm": float(dx[i, 0] * 1000.0),
                            "pred_dx_horizontal2_mm": float(dx[i, 1] * 1000.0),
                            "pred_dy_vertical_signed_mm": float(ver_signed_mm[i]),
                            "pred_horizontal_mm": float(hor_mm[i]),
                            "pred_vertical_abs_mm": float(ver_abs_mm[i]),
                            "pred_3d_mm": float(pos_mm[i]),
                            "link_bias_mean_mm": float(np.mean(bias_m[i]) * 1000.0),
                            "link_bias_min_mm": float(np.min(bias_m[i]) * 1000.0),
                            "link_bias_max_mm": float(np.max(bias_m[i]) * 1000.0),
                        }
                    )

    write_csv(out_root / "summary.csv", summary_rows)
    write_csv(out_root / "per_position.csv", per_position_rows)
    write_csv(out_root / "measured_signed_vertical_by_height.csv", measured_rows)

    robust = {
        "vertical_dominance_all": all(bool(r["vertical_dominance"]) for r in summary_rows),
        "low_worst_all": all(bool(r["low_worst"]) for r in summary_rows),
        "measured_order_low_high_mid_all": all(bool(r["measured_order_low_high_mid"]) for r in summary_rows),
        "sign_match_low_all": all(bool(r["sign_match_low"]) for r in summary_rows),
        "sign_match_all_tiers_all": all(bool(r["sign_match_all_tiers"]) for r in summary_rows),
    }
    robust_rows = [{"criterion": key, "holds_for_entire_sweep": value} for key, value in robust.items()]
    z_floor_rows: list[dict[str, Any]] = []
    for floor_mm in sorted(set(float(r["z_floor_mm"]) for r in summary_rows), reverse=True):
        sub = [r for r in summary_rows if float(r["z_floor_mm"]) == floor_mm]
        row: dict[str, Any] = {
            "z_floor_mm": floor_mm,
            "n_sweep_rows": len(sub),
            "vertical_dominance_count": sum(bool(r["vertical_dominance"]) for r in sub),
            "low_worst_count": sum(bool(r["low_worst"]) for r in sub),
            "measured_order_low_high_mid_count": sum(bool(r["measured_order_low_high_mid"]) for r in sub),
            "sign_match_low_count": sum(bool(r["sign_match_low"]) for r in sub),
            "sign_match_all_tiers_count": sum(bool(r["sign_match_all_tiers"]) for r in sub),
        }
        for height in ("low", "mid", "high"):
            vals = np.asarray([float(r[f"{height}_pred_vertical_abs_median_mm"]) for r in sub], dtype=float)
            row[f"{height}_pred_vertical_abs_min_mm"] = float(np.min(vals))
            row[f"{height}_pred_vertical_abs_median_over_sweep_mm"] = float(np.median(vals))
            row[f"{height}_pred_vertical_abs_max_mm"] = float(np.max(vals))
            row[f"{height}_measured_abs_vertical_target_mm"] = MEASURED_TIER_VERTICAL_ABS_MM[height]
        z_floor_rows.append(row)
    write_csv(out_root / "robust_verdict.csv", robust_rows)
    write_csv(out_root / "z_floor_magnitude_summary.csv", z_floor_rows)

    meta = {
        "mode": "ground_reflection_real_static",
        "description": "First-order consistency check only; not validation and not parameter tuned.",
        "campaign_root": str(campaign_root),
        "layout_source": "Analysis/official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv truth_x/truth_y/truth_z rows",
        "static_tag_source": "Analysis/official_extra_analysis/FULL/tables/revision2_dop_at_static_vicon_positions.csv",
        "signed_vertical_source": "Analysis/official_extra_analysis/FULL/tables/tag_abs_errors_per_session.csv v4-io/all8/C_anchor_locked_OFFICIAL",
        "coordinate_convention": "sim columns are horizontal X, horizontal Z, vertical raw Vicon Y",
        "z_floor_mm": z_floor_mm,
        "z_floor_certainty": "swept; z_floor=0 follows operator procedure but is not file-proven",
        "lowest_tracked_marker_y_mm": LOWEST_TRACKED_MARKER_Y_MM,
        "gamma_magnitudes": gamma_magnitudes,
        "gamma_sign_convention": "Gamma = -|Gamma| for vertically polarised grazing-incidence ground reflection",
        "resolution_cm": resolution_cm,
        "range_bias_model": "bias_m = |Gamma| * resolution_m * exp(-(excess_path_m / resolution_m)^2), positive by threshold-delay assumption",
        "measured_tier_vertical_abs_targets_mm": MEASURED_TIER_VERTICAL_ABS_MM,
        "measured_signed_vertical_median_mm": measured_signed_median,
        "robust_verdict_flags": robust,
        "anchor_labels": anchor_labels,
    }
    (out_root / "run_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Ground-Reflection Real-Static Consistency Check",
        "",
        "This is a first-order consistency check, not validation. Parameters are swept and not tuned to the measured vertical-error magnitudes.",
        "",
        f"Robust flags over all {len(summary_rows)} swept combinations:",
        "",
    ]
    for key, value in robust.items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "Measured signed vertical medians (UWB minus Vicon), mm:", ""])
    for row in measured_rows:
        lines.append(
            f"- {row['height']}: {row['measured_signed_vertical_median_mm']:.1f} "
            f"({row['measured_signed_vertical_sign']})"
        )
    (out_root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[ground-reflection] wrote {out_root} ({len(summary_rows)} scenarios)", flush=True)
    return 0


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
    ap.add_argument("--phase", choices=["phase1", "phase2", "phase3"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--grid-spacing-m", type=float, default=0.25)
    ap.add_argument("--trials", type=int, default=96)
    ap.add_argument("--phase2-seeds", type=int, default=12)
    ap.add_argument("--metal-box-count", type=int, default=6)
    ap.add_argument("--wall-counts", default="", help="Comma-separated wall counts. Defaults to 0,1,2,3,4.")
    ap.add_argument("--distances-cm", default="", help="Comma-separated distances in cm. Defaults to the standard Phase 1 sweep.")
    ap.add_argument(
        "--ground-reflection-real-static",
        action="store_true",
        help="Run the flagged real-geometry floor two-ray consistency check instead of the wall/metal grid simulation.",
    )
    ap.add_argument("--campaign-root", default="", help="Campaign root for the real-geometry ground-reflection check.")
    ap.add_argument("--ground-gamma-magnitudes", default="", help="Comma-separated |Gamma| sweep. Defaults to 0.5,0.7,0.9.")
    ap.add_argument("--ground-resolution-cm", default="", help="Comma-separated effective range-resolution sweep in cm. Defaults to 15,30,60.")
    ap.add_argument("--ground-z-floor-mm", default="", help="Comma-separated floor-plane vertical sweep in mm. Defaults to 0,-100,-200,-300.")
    args = ap.parse_args()

    out_root = Path(args.out)
    if args.ground_reflection_real_static:
        campaign_root = Path(args.campaign_root).resolve() if args.campaign_root else campaign_root_from_script()
        gamma_magnitudes = parse_float_list(args.ground_gamma_magnitudes, GROUND_GAMMA_MAGNITUDES)
        resolution_cm = parse_float_list(args.ground_resolution_cm, GROUND_RESOLUTION_CM)
        z_floor_mm = parse_float_list(args.ground_z_floor_mm, GROUND_Z_FLOOR_MM)
        return run_ground_reflection_real_static(
            campaign_root=campaign_root,
            out_root=out_root,
            gamma_magnitudes=gamma_magnitudes,
            resolution_cm=resolution_cm,
            z_floor_mm=z_floor_mm,
        )

    if args.phase is None:
        ap.error("--phase is required unless --ground-reflection-real-static is set")

    anchors = layout_3x3x1p4()
    tags = tag_grid(args.grid_spacing_m)
    rows: list[dict[str, Any]] = []
    scenario_id = 0
    wall_counts = [int(x) for x in args.wall_counts.split(",") if x.strip()] if args.wall_counts else sorted(WALL_SETS)
    distances_cm = [int(x) for x in args.distances_cm.split(",") if x.strip()] if args.distances_cm else DISTANCES_CM
    materials = ["phase1_default_wall"] if args.phase in {"phase1", "phase2"} else list(MATERIALS)
    total = len(wall_counts) * len(distances_cm) * len(materials)
    if args.phase == "phase2":
        total *= args.phase2_seeds
    for material_name in materials:
        material = PHASE1_MATERIAL if material_name == "phase1_default_wall" else MATERIALS[material_name]
        for wall_count in wall_counts:
            walls = WALL_SETS[wall_count]
            for dist_cm in distances_cm:
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
        "wall_counts": wall_counts,
        "distances_cm": distances_cm,
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
