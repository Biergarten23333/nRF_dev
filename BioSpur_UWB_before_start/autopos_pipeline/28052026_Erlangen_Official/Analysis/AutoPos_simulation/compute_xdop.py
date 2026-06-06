#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ANCHORS = "ABCDEFGH"
GEOMETRIES = ["true", "v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
DROP_MASKS = ["all8"] + [f"drop{a}" for a in ANCHORS]
BAD_THRESHOLDS = {
    "vdop_gt2": ("vdop", 2.0),
    "vdop_gt3": ("vdop", 3.0),
    "gdop_gt4": ("gdop", 4.0),
    "cond_gt100": ("cond", 100.0),
}


def family_for_layout(layout_id: int) -> str:
    if layout_id < 450:
        return "irregular"
    if layout_id < 900:
        return "concave"
    return "control5x5"


def load_layout_xyz(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    xyz = np.zeros((8, 3), dtype=np.float32)
    for ent in raw["anchors"]:
        idx = int(ent["id"])
        xyz[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    return xyz


def layout_path(root: Path, layout_id: int, geometry: str) -> Path:
    base = root / f"layout_{layout_id:04d}"
    if geometry == "true":
        return base / "true_layout.json"
    return base / geometry / "layout.json"


def available_layout_ids(root: Path) -> list[int]:
    out = []
    for p in sorted(root.glob("layout_*/true_layout.json")):
        out.append(int(p.parent.name.split("_")[-1]))
    return out


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def load_coord_rms(root: Path) -> dict[tuple[int, str], float]:
    rows = read_csv_rows(root / "summary.csv")
    out: dict[tuple[int, str], float] = {}
    for row in rows:
        try:
            out[(int(row["layout_id"]), str(row["solver"]))] = float(row["coord_rms_mm"])
        except Exception:
            pass
    return out


def grid_for_layout(
    xyz: np.ndarray,
    grid_mm: float,
    xy_window_mm: float,
    z_margin_mm: float,
    z_window_mm: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    center_xy = np.mean(xyz[:, :2], axis=0)
    center_z = float(np.mean(xyz[:, 2]))
    half = xy_window_mm / 2.0
    xs = np.arange(center_xy[0] - half, center_xy[0] + half + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    ys = np.arange(center_xy[1] - half, center_xy[1] + half + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    if z_window_mm is None or z_window_mm <= 0.0:
        zlo = float(np.min(xyz[:, 2]) - z_margin_mm)
        zhi = float(np.max(xyz[:, 2]) + z_margin_mm)
    else:
        z_half = z_window_mm / 2.0
        zlo = center_z - z_half
        zhi = center_z + z_half
    zs = np.arange(math.floor(zlo / grid_mm) * grid_mm, math.ceil(zhi / grid_mm) * grid_mm + 0.5 * grid_mm, grid_mm, dtype=np.float32)
    xx, yy, zz = np.meshgrid(xs, ys, zs, indexing="xy")
    grid = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()]).astype(np.float32)

    dx = np.abs(grid[:, 0] - center_xy[0])
    dy = np.abs(grid[:, 1] - center_xy[1])
    center = (dx <= 1500.0) & (dy <= 1500.0)
    full = np.ones(grid.shape[0], dtype=bool)
    zones = {"full": full, "center3m": center, "edge_ring": full & ~center}
    return grid, xs, ys, zs, zones


def mask_indices(mask_name: str) -> list[int]:
    if mask_name == "all8":
        return list(range(8))
    if mask_name.startswith("drop") and len(mask_name) == 5:
        drop = ANCHORS.index(mask_name[-1])
        return [i for i in range(8) if i != drop]
    raise ValueError(mask_name)


def compute_dop_torch(grid: np.ndarray, anchors: np.ndarray, device_name: str, chunk_size: int, weights: np.ndarray | None = None) -> dict[str, np.ndarray]:
    import torch

    device = torch.device(device_name)
    a = torch.as_tensor(anchors, dtype=torch.float32, device=device)
    w = None if weights is None else torch.as_tensor(weights, dtype=torch.float32, device=device)
    out = {k: np.full(grid.shape[0], np.nan, dtype=np.float32) for k in ["gdop", "hdop", "vdop", "cond"]}
    eye = torch.eye(3, dtype=torch.float32, device=device).unsqueeze(0) * 1e-7
    eps = torch.tensor(1e-6, dtype=torch.float32, device=device)
    for start in range(0, grid.shape[0], chunk_size):
        stop = min(start + chunk_size, grid.shape[0])
        p = torch.as_tensor(grid[start:stop], dtype=torch.float32, device=device)
        diff = p[:, None, :] - a[None, :, :]
        dist = torch.linalg.norm(diff, dim=2).clamp_min(eps)
        g = diff / dist[:, :, None]
        if w is None:
            fim = torch.matmul(g.transpose(1, 2), g)
        else:
            fim = torch.matmul(g.transpose(1, 2), g * w[None, :, None])
        try:
            q = torch.linalg.inv(fim + eye)
        except RuntimeError:
            q = torch.linalg.pinv(fim)
        diag = torch.clamp(torch.diagonal(q, dim1=1, dim2=2), min=0.0)
        out["hdop"][start:stop] = torch.sqrt(diag[:, 0] + diag[:, 1]).detach().cpu().numpy()
        out["vdop"][start:stop] = torch.sqrt(diag[:, 2]).detach().cpu().numpy()
        out["gdop"][start:stop] = torch.sqrt(torch.sum(diag, dim=1)).detach().cpu().numpy()
        out["cond"][start:stop] = torch.linalg.cond(fim).detach().cpu().numpy()
    return out


def finite_percentile(values: np.ndarray, pct: float) -> float:
    finite = values[np.isfinite(values)]
    return float(np.percentile(finite, pct)) if finite.size else float("nan")


def summarize_dop(dop: dict[str, np.ndarray], zones: dict[str, np.ndarray]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for zone_name, zone_mask in zones.items():
        row[f"{zone_name}_points"] = int(np.sum(zone_mask))
        for metric in ["gdop", "hdop", "vdop", "cond"]:
            vals = dop[metric][zone_mask]
            row[f"{zone_name}_{metric}_median"] = finite_percentile(vals, 50)
            row[f"{zone_name}_{metric}_p90"] = finite_percentile(vals, 90)
            row[f"{zone_name}_{metric}_p95"] = finite_percentile(vals, 95)
            row[f"{zone_name}_{metric}_max"] = finite_percentile(vals, 100)
        for name, (metric, thresh) in BAD_THRESHOLDS.items():
            vals = dop[metric][zone_mask]
            finite = vals[np.isfinite(vals)]
            row[f"{zone_name}_{name}_ratio"] = float(np.mean(finite > thresh)) if finite.size else float("nan")
    return row


def geometry_rows(
    root: Path,
    layout_ids: list[int],
    geometries: list[str],
    masks: list[str],
    grid_mm: float,
    device: str,
    chunk_size: int,
    out_dir: Path,
    *,
    weighted: bool,
    xy_window_mm: float = 5000.0,
    z_margin_mm: float = 250.0,
    z_window_mm: float | None = None,
) -> list[dict[str, Any]]:
    coord_rms = load_coord_rms(root)
    rows: list[dict[str, Any]] = []
    # Equal weights for Phase 1/3. Hook is here so weighted DOP can use anchor sigma later.
    weights_all = np.ones(8, dtype=np.float32) if weighted else None
    total = len(layout_ids) * len(geometries) * len(masks)
    done = 0
    for layout_id in layout_ids:
        family = family_for_layout(layout_id)
        for geometry in geometries:
            path = layout_path(root, layout_id, geometry)
            if not path.exists():
                continue
            xyz = load_layout_xyz(path)
            grid, _xs, _ys, _zs, zones = grid_for_layout(xyz, grid_mm, xy_window_mm, z_margin_mm, z_window_mm)
            for mask_name in masks:
                done += 1
                print(f"[xdop] {done}/{total} layout_{layout_id:04d} {geometry} {mask_name} grid={grid_mm:.0f}mm {device}", flush=True)
                idx = mask_indices(mask_name)
                anchors = xyz[idx]
                weights = None if weights_all is None else weights_all[idx]
                dop = compute_dop_torch(grid, anchors, device, chunk_size, weights)
                row: dict[str, Any] = {
                    "layout_id": layout_id,
                    "family": family,
                    "geometry": geometry,
                    "mask": mask_name,
                    "grid_mm": grid_mm,
                    "grid_points": int(grid.shape[0]),
                    "device": device,
                    "weighted": bool(weighted),
                    "coord_rms_mm": 0.0 if geometry == "true" else coord_rms.get((layout_id, geometry), float("nan")),
                }
                row.update(summarize_dop(dop, zones))
                rows.append(row)
    write_csv(out_dir / f"xdop_grid{int(grid_mm)}.csv", rows)
    return rows


def rank01(values: dict[Any, float], reverse: bool = False) -> dict[Any, float]:
    finite = [(k, v) for k, v in values.items() if np.isfinite(v)]
    finite.sort(key=lambda kv: kv[1], reverse=reverse)
    if not finite:
        return {k: float("nan") for k in values}
    denom = max(1, len(finite) - 1)
    ranks = {k: i / denom for i, (k, _v) in enumerate(finite)}
    return {k: ranks.get(k, float("nan")) for k in values}


def phase1_ranking(rows: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    all8 = [r for r in rows if r["mask"] == "all8" and r["geometry"] == "v3-lite"]
    coord = {int(r["layout_id"]): float(r["coord_rms_mm"]) for r in all8}
    vdop = {int(r["layout_id"]): float(r["full_vdop_p90"]) for r in all8}
    gdop = {int(r["layout_id"]): float(r["full_gdop_p90"]) for r in all8}
    bad = {int(r["layout_id"]): float(r["full_vdop_gt3_ratio"]) + float(r["full_gdop_gt4_ratio"]) for r in all8}
    rr_coord = rank01(coord)
    rr_vdop = rank01(vdop)
    rr_gdop = rank01(gdop)
    rr_bad = rank01(bad)
    ranked = []
    for layout_id in sorted(coord):
        score = 0.30 * rr_coord[layout_id] + 0.25 * rr_vdop[layout_id] + 0.20 * rr_gdop[layout_id] + 0.15 * rr_bad[layout_id]
        ranked.append(
            {
                "layout_id": layout_id,
                "family": family_for_layout(layout_id),
                "phase1_score": score,
                "coord_rms_mm": coord[layout_id],
                "vdop_p90": vdop[layout_id],
                "gdop_p90": gdop[layout_id],
                "bad_ratio_sum": bad[layout_id],
            }
        )
    ranked.sort(key=lambda r: float(r["phase1_score"]))
    write_csv(out_dir / "phase1_layout_ranking.csv", ranked)
    return ranked


def select_phase3_layouts(ranked: list[dict[str, Any]], top_n: int, bottom_n: int, rep_n: int) -> list[int]:
    if not ranked:
        return []
    selected: list[int] = []
    selected.extend(int(r["layout_id"]) for r in ranked[:top_n])
    selected.extend(int(r["layout_id"]) for r in ranked[-bottom_n:])
    if rep_n > 0:
        idxs = np.linspace(0, len(ranked) - 1, rep_n).round().astype(int)
        selected.extend(int(ranked[int(i)]["layout_id"]) for i in idxs)
    out: list[int] = []
    seen = set()
    for layout_id in selected:
        if layout_id not in seen:
            out.append(layout_id)
            seen.add(layout_id)
    return sorted(out)


def parse_layout_id_list(raw: str) -> list[int]:
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def phase3_robustness(rows: list[dict[str, Any]], out_dir: Path) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (int(row["layout_id"]), str(row["geometry"]))
        grouped.setdefault(key, {})[str(row["mask"])] = row
    out = []
    for (layout_id, geometry), masks in grouped.items():
        if "all8" not in masks:
            continue
        all8 = masks["all8"]
        drop_rows = [masks[m] for m in DROP_MASKS[1:] if m in masks]
        if not drop_rows:
            continue
        worst_vdop = max(float(r["full_vdop_p90"]) for r in drop_rows)
        worst_gdop = max(float(r["full_gdop_p90"]) for r in drop_rows)
        worst_bad = max(float(r["full_vdop_gt3_ratio"]) + float(r["full_gdop_gt4_ratio"]) for r in drop_rows)
        out.append(
            {
                "layout_id": layout_id,
                "family": family_for_layout(layout_id),
                "geometry": geometry,
                "all8_vdop_p90": float(all8["full_vdop_p90"]),
                "drop_worst_vdop_p90": worst_vdop,
                "drop_delta_vdop_p90": worst_vdop - float(all8["full_vdop_p90"]),
                "all8_gdop_p90": float(all8["full_gdop_p90"]),
                "drop_worst_gdop_p90": worst_gdop,
                "drop_delta_gdop_p90": worst_gdop - float(all8["full_gdop_p90"]),
                "drop_worst_bad_ratio_sum": worst_bad,
            }
        )
    out.sort(key=lambda r: (int(r["layout_id"]), str(r["geometry"])))
    write_csv(out_dir / "phase3_drop_robustness.csv", out)
    return out


def aggregate_by_geometry(rows: list[dict[str, Any]], out_dir: Path, name: str) -> list[dict[str, Any]]:
    out = []
    for geometry in GEOMETRIES:
        group = [r for r in rows if r["geometry"] == geometry and r["mask"] == "all8"]
        if not group:
            continue
        entry: dict[str, Any] = {"geometry": geometry, "n": len(group)}
        for metric in ["full_gdop_p90", "full_hdop_p90", "full_vdop_p90", "full_cond_p95", "full_vdop_gt3_ratio", "center3m_vdop_p90", "edge_ring_vdop_p90"]:
            vals = np.asarray([float(r[metric]) for r in group if np.isfinite(float(r[metric]))], dtype=float)
            entry[f"{metric}_median"] = float(np.median(vals)) if vals.size else float("nan")
            entry[f"{metric}_p90"] = float(np.percentile(vals, 90)) if vals.size else float("nan")
        out.append(entry)
    write_csv(out_dir / f"{name}_by_geometry.csv", out)
    return out


def make_figures(out_dir: Path, rows: list[dict[str, Any]], prefix: str) -> None:
    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    for metric in ["full_vdop_p90", "full_gdop_p90", "full_vdop_gt3_ratio", "full_cond_p95"]:
        data = []
        labels = []
        for geometry in GEOMETRIES:
            vals = [float(r[metric]) for r in rows if r["geometry"] == geometry and r["mask"] == "all8" and np.isfinite(float(r[metric]))]
            if vals:
                data.append(vals)
                labels.append(geometry)
        if not data:
            continue
        plt.figure(figsize=(9.2, 4.8))
        plt.boxplot(data, tick_labels=labels, showfliers=False)
        plt.ylabel(metric)
        plt.xticks(rotation=20, ha="right")
        plt.tight_layout()
        plt.savefig(fig_dir / f"{prefix}_{metric}_by_geometry.png", dpi=160)
        plt.close()


def write_report(out_dir: Path, phase1_agg: list[dict[str, Any]], phase3_agg: list[dict[str, Any]], selected: list[int]) -> None:
    phase1_n = max((int(row["n"]) for row in phase1_agg), default=0)
    lines = [
        "# AutoPos xDOP Phase 3 Report",
        "",
        "## Scope",
        "",
        f"- Phase 1: all available layouts ({phase1_n}), true + five solver geometries, 100 mm grid, all8.",
        "- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.",
        "- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).",
        "",
        "## Selected Phase 3 Layouts",
        "",
        ", ".join(f"layout_{i:04d}" for i in selected),
        "",
        "## Phase 1 Geometry Summary",
        "",
        "| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in phase1_agg:
        lines.append(
            f"| {row['geometry']} | {row['n']} | {float(row['full_vdop_p90_median']):.3f} | {float(row['full_gdop_p90_median']):.3f} | {float(row['full_vdop_gt3_ratio_median']):.3f} |"
        )
    lines.extend(["", "## Phase 3 Geometry Summary", "", "| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |", "|---|---:|---:|---:|---|"])
    for row in phase3_agg:
        lines.append(
            f"| {row['geometry']} | {row['n']} | {float(row['full_vdop_p90_median']):.3f} | {float(row['full_gdop_p90_median']):.3f} | see `phase3_drop_robustness.csv` |"
        )
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `phase1_xdop_grid100.csv`",
            "- `phase1_layout_ranking.csv`",
            "- `phase3_xdop_grid50.csv`",
            "- `phase3_drop_robustness.csv`",
            "- `figures/*.png`",
        ]
    )
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def resolve_device(arg: str) -> str:
    if arg != "auto":
        return arg
    import torch

    return "cuda:0" if torch.cuda.is_available() else "cpu"


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1/3 xDOP analysis for AutoPos simulation layouts.")
    ap.add_argument("--root", default="AutoPos_simulation/out_100x1000")
    ap.add_argument("--out", default="AutoPos_simulation/out_100x1000/xdop_phase3")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--chunk-size", type=int, default=131072)
    ap.add_argument("--phase1-grid-mm", type=float, default=100.0)
    ap.add_argument("--phase3-grid-mm", type=float, default=50.0)
    ap.add_argument("--xy-window-mm", type=float, default=5000.0)
    ap.add_argument("--z-margin-mm", type=float, default=250.0)
    ap.add_argument("--z-window-mm", type=float, default=0.0, help="Fixed vertical evaluation window in mm. Use 0 to derive from anchor height plus margin.")
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument("--bottom-n", type=int, default=20)
    ap.add_argument("--representative-n", type=int, default=20)
    ap.add_argument("--layout-id-min", type=int, default=None)
    ap.add_argument("--layout-id-max", type=int, default=None)
    ap.add_argument("--phase3-layout-ids", default="", help="Comma-separated layout ids for a Phase 3 shard. Defaults to selected top/bottom/representative ids.")
    ap.add_argument("--skip-phase1", action="store_true")
    ap.add_argument("--skip-phase3", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    device = resolve_device(args.device)
    layout_ids = available_layout_ids(root)
    if args.layout_id_min is not None:
        layout_ids = [i for i in layout_ids if i >= args.layout_id_min]
    if args.layout_id_max is not None:
        layout_ids = [i for i in layout_ids if i <= args.layout_id_max]
    if not layout_ids:
        raise RuntimeError(f"no layouts under {root}")

    phase1_final_path = out_dir / f"phase1_xdop_grid{int(args.phase1_grid_mm)}.csv"
    if args.skip_phase1 and phase1_final_path.exists():
        phase1_rows = read_csv_rows(phase1_final_path)
    else:
        phase1_rows = geometry_rows(root, layout_ids, GEOMETRIES, ["all8"], args.phase1_grid_mm, device, args.chunk_size, out_dir, weighted=False, xy_window_mm=args.xy_window_mm, z_margin_mm=args.z_margin_mm, z_window_mm=args.z_window_mm)
        (out_dir / f"xdop_grid{int(args.phase1_grid_mm)}.csv").replace(phase1_final_path)
        phase1_rows = read_csv_rows(phase1_final_path)

    ranked = phase1_ranking(phase1_rows, out_dir)
    selected = parse_layout_id_list(args.phase3_layout_ids) if args.phase3_layout_ids else select_phase3_layouts(ranked, args.top_n, args.bottom_n, args.representative_n)
    (out_dir / "phase3_selected_layouts.json").write_text(json.dumps({"selected": selected}, indent=2), encoding="utf-8")
    phase1_agg = aggregate_by_geometry(phase1_rows, out_dir, "phase1")
    make_figures(out_dir, phase1_rows, "phase1")

    phase3_rows: list[dict[str, Any]] = []
    phase3_agg: list[dict[str, Any]] = []
    if not args.skip_phase3:
        phase3_rows = geometry_rows(root, selected, GEOMETRIES, DROP_MASKS, args.phase3_grid_mm, device, args.chunk_size, out_dir, weighted=False, xy_window_mm=args.xy_window_mm, z_margin_mm=args.z_margin_mm, z_window_mm=args.z_window_mm)
        (out_dir / f"xdop_grid{int(args.phase3_grid_mm)}.csv").replace(out_dir / f"phase3_xdop_grid{int(args.phase3_grid_mm)}.csv")
        phase3_rows = read_csv_rows(out_dir / f"phase3_xdop_grid{int(args.phase3_grid_mm)}.csv")
        phase3_robustness(phase3_rows, out_dir)
        phase3_agg = aggregate_by_geometry(phase3_rows, out_dir, "phase3")
        make_figures(out_dir, phase3_rows, "phase3")

    run_meta = {
        "root": str(root.resolve()),
        "out": str(out_dir.resolve()),
        "device": device,
        "phase1_grid_mm": args.phase1_grid_mm,
        "phase3_grid_mm": args.phase3_grid_mm,
        "xy_window_mm": args.xy_window_mm,
        "z_margin_mm": args.z_margin_mm,
        "z_window_mm": args.z_window_mm,
        "layout_count": len(layout_ids),
        "selected_count": len(selected),
        "geometries": GEOMETRIES,
        "phase3_masks": DROP_MASKS,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")
    write_report(out_dir, phase1_agg, phase3_agg, selected)
    print(f"[xdop] wrote {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
