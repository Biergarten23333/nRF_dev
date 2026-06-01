#!/usr/bin/env python3
"""Compute dense axis-wise DOP summaries with batched CUDA.

The script is designed for expensive production DOP runs, for example
117 layouts at 25 mm with all8 plus dropA-H masks. It requires an explicit
--execute flag before it will use GPUs.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import multiprocessing as mp
import os
import queue
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch


LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")

METRICS = ("xdop", "ydop", "vdop", "hdop", "gdop", "cond")


@dataclass(frozen=True)
class LayoutJob:
    index: int
    layout: dict[str, Any]
    mask_name: str
    keep_indices: list[int]
    point_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--grid-mm", type=float, default=25.0)
    parser.add_argument("--devices", default="0,1", help="Comma-separated CUDA device ids.")
    parser.add_argument(
        "--masks",
        default="all8,dropA-H",
        help=(
            "Mask set, e.g. all8,dropA-H,dropAB,drop1-4. "
            "drop1-4 expands to every combination that drops 1, 2, 3, and 4 anchors."
        ),
    )
    parser.add_argument("--chunk-points", type=int, default=262144)
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--output-prefix", default="axis_dop_gpu_dense")
    parser.add_argument("--execute", action="store_true", help="Actually run CUDA work. Without this, only dry-run estimates are printed.")
    parser.add_argument("--limit-layouts", type=int, default=0, help="Debug helper: process only the first N layouts.")
    parser.add_argument("--keep-cpu-threads", type=int, default=2, help="Torch CPU threads per worker.")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def anchor_records(layout: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, anchor in enumerate(layout.get("anchors", [])):
        try:
            out.append(
                {
                    "idx": idx,
                    "label": str(anchor.get("label", chr(ord("A") + idx))).upper(),
                    "xyz": [float(anchor["x_mm"]), float(anchor["y_mm"]), float(anchor["z_mm"])],
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def anchor_xyz(layout: dict[str, Any]) -> np.ndarray:
    records = anchor_records(layout)
    return np.array([record["xyz"] for record in records], dtype=np.float32)


def parse_devices(value: str) -> list[int]:
    devices = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not devices:
        raise ValueError("at least one CUDA device is required")
    return devices


def parse_masks(value: str, layout: dict[str, Any]) -> list[tuple[str, list[int]]]:
    records = anchor_records(layout)
    all_indices = [record["idx"] for record in records]
    by_label = {record["label"]: record["idx"] for record in records}

    tokens: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "dropA-H":
            tokens.extend([f"drop{chr(code)}" for code in range(ord("A"), ord("H") + 1)])
        elif part.startswith("drop") and "-" in part[4:] and all(piece.isdigit() for piece in part[4:].split("-", 1)):
            lo_text, hi_text = part[4:].split("-", 1)
            lo = int(lo_text)
            hi = int(hi_text)
            labels = [record["label"] for record in records]
            for count in range(lo, hi + 1):
                for combo in itertools.combinations(labels, count):
                    tokens.append("drop" + "".join(combo))
        else:
            tokens.append(part)

    masks: list[tuple[str, list[int]]] = []
    for token in tokens:
        if token == "all8" or token == "all":
            masks.append(("all8", all_indices))
            continue
        if token.startswith("drop"):
            labels = [ch.upper() for ch in token[4:] if ch.isalpha()]
            drop = {by_label[label] for label in labels if label in by_label}
            keep = [idx for idx in all_indices if idx not in drop]
            masks.append((token, keep))
            continue
        raise ValueError(f"unsupported mask token: {token}")
    return masks


def axis_values(min_v: float, max_v: float, spacing: float) -> np.ndarray:
    if max_v < min_v:
        min_v, max_v = max_v, min_v
    span = max_v - min_v
    if span <= 1e-9:
        return np.array([min_v], dtype=np.float32)
    n = max(2, int(math.ceil(span / spacing)) + 1)
    return np.linspace(min_v, max_v, n, dtype=np.float32)


def grid_axes(anchors: np.ndarray, grid_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mins = anchors.min(axis=0)
    maxs = anchors.max(axis=0)
    spans = maxs - mins
    margin = np.maximum(spans * 0.03, 100.0)
    mins = mins + margin
    maxs = maxs - margin
    for idx in range(3):
        if maxs[idx] <= mins[idx]:
            mid = (float(anchors[:, idx].min()) + float(anchors[:, idx].max())) / 2.0
            mins[idx] = mid
            maxs[idx] = mid
    return (
        axis_values(float(mins[0]), float(maxs[0]), grid_mm),
        axis_values(float(mins[1]), float(maxs[1]), grid_mm),
        axis_values(float(mins[2]), float(maxs[2]), grid_mm),
    )


def grid_point_count(anchors: np.ndarray, grid_mm: float) -> int:
    axes = grid_axes(anchors, grid_mm)
    return int(len(axes[0]) * len(axes[1]) * len(axes[2]))


def point_chunk_iter(axes: tuple[np.ndarray, np.ndarray, np.ndarray], chunk_points: int):
    xs, ys, zs = axes
    yz_count = len(ys) * len(zs)
    chunk_x = max(1, chunk_points // max(1, yz_count))
    for start in range(0, len(xs), chunk_x):
        x_chunk = xs[start : start + chunk_x]
        xx, yy, zz = np.meshgrid(x_chunk, ys, zs, indexing="ij")
        points = np.stack((xx.ravel(), yy.ravel(), zz.ravel()), axis=1)
        yield torch.from_numpy(points)


def percentile_tensor(values: torch.Tensor, q: float) -> float:
    if values.numel() == 0:
        return float("nan")
    return float(torch.quantile(values, q).item())


def summarize_metric(values: torch.Tensor) -> dict[str, float]:
    if values.numel() == 0:
        return {"mean": float("nan"), "median": float("nan"), "p90": float("nan"), "p95": float("nan"), "max": float("nan")}
    return {
        "mean": float(values.mean().item()),
        "median": percentile_tensor(values, 0.5),
        "p90": percentile_tensor(values, 0.90),
        "p95": percentile_tensor(values, 0.95),
        "max": float(values.max().item()),
    }


def score_summary(row: dict[str, Any]) -> float:
    return (
        float(row["gdop_p95"]) * 0.25
        + float(row["hdop_p95"]) * 0.20
        + float(row["vdop_p95"]) * 0.30
        + max(float(row["xdop_p95"]), float(row["ydop_p95"])) * 0.15
        + min(float(row["cond_p95"]) / 10.0, 10.0) * 0.10
    )


def dop_chunk(points_cpu: torch.Tensor, anchors_gpu: torch.Tensor, device: torch.device) -> dict[str, torch.Tensor]:
    points = points_cpu.to(device=device, dtype=torch.float32, non_blocking=False)
    vecs = anchors_gpu.unsqueeze(0) - points.unsqueeze(1)
    distances = torch.linalg.vector_norm(vecs, dim=2).clamp_min(1e-6)
    unit = vecs / distances.unsqueeze(2)
    normal = torch.einsum("bni,bnj->bij", unit, unit)
    cov, info = torch.linalg.inv_ex(normal)
    valid = (info == 0) & torch.isfinite(cov).all(dim=(1, 2)) & (torch.diagonal(cov, dim1=1, dim2=2) > 0).all(dim=1)
    if not bool(valid.any()):
        empty = torch.empty(0, device="cpu")
        return {metric: empty for metric in METRICS}

    normal = normal[valid]
    cov = cov[valid]
    cxx = cov[:, 0, 0]
    cyy = cov[:, 1, 1]
    czz = cov[:, 2, 2]
    cond = torch.linalg.matrix_norm(normal, ord="fro", dim=(1, 2)) * torch.linalg.matrix_norm(cov, ord="fro", dim=(1, 2))
    out = {
        "xdop": torch.sqrt(cxx).detach().cpu(),
        "ydop": torch.sqrt(cyy).detach().cpu(),
        "vdop": torch.sqrt(czz).detach().cpu(),
        "hdop": torch.sqrt(cxx + cyy).detach().cpu(),
        "gdop": torch.sqrt(cxx + cyy + czz).detach().cpu(),
        "cond": cond.detach().cpu(),
    }
    del points, vecs, distances, unit, normal, cov, cond
    return out


def summarize_layout_mask(
    layout: dict[str, Any],
    mask_name: str,
    keep_indices: list[int],
    grid_mm: float,
    chunk_points: int,
    device: torch.device,
) -> dict[str, Any]:
    anchors_np = anchor_xyz(layout)
    base = {
        "layout_id": layout.get("layout_id", ""),
        "capture_id": layout.get("capture_id", ""),
        "source_group": layout.get("source_group", ""),
        "solver_version": layout.get("solver_version", ""),
        "layout_variant": layout.get("layout_variant", ""),
        "source_path": layout.get("source_path", ""),
        "anchor_count": len(anchors_np),
        "mask": mask_name,
        "mask_anchor_count": len(keep_indices),
        "grid_mm": grid_mm,
    }
    if len(keep_indices) < 4:
        return {**base, "n_points": 0, "n_valid": 0, "status": "too_few_mask_anchors"}

    axes = grid_axes(anchors_np, grid_mm)
    n_points = int(len(axes[0]) * len(axes[1]) * len(axes[2]))
    anchors_gpu = torch.as_tensor(anchors_np[keep_indices], device=device, dtype=torch.float32)
    values: dict[str, list[torch.Tensor]] = {metric: [] for metric in METRICS}
    n_valid = 0

    for points_cpu in point_chunk_iter(axes, chunk_points):
        chunk = dop_chunk(points_cpu, anchors_gpu, device)
        if chunk["gdop"].numel() == 0:
            continue
        n_valid += int(chunk["gdop"].numel())
        for metric in METRICS:
            values[metric].append(chunk[metric])

    out: dict[str, Any] = {**base, "n_points": n_points, "n_valid": n_valid}
    if n_valid == 0:
        out["status"] = "no_valid_grid_points"
        return out

    for metric in METRICS:
        vals = torch.cat(values[metric])
        summary = summarize_metric(vals)
        for key, value in summary.items():
            out[f"{metric}_{key}"] = value
    out["axis_imbalance_p95"] = max(out["xdop_p95"], out["ydop_p95"], out["vdop_p95"]) / max(
        min(out["xdop_p95"], out["ydop_p95"], out["vdop_p95"]), 1e-9
    )
    out["axis_dop_score"] = score_summary(out)
    out["status"] = "ok"
    return out


def worker_main(
    device_id: int,
    jobs: list[LayoutJob],
    args_dict: dict[str, Any],
    result_queue: mp.Queue,
) -> None:
    os.environ["CUDA_VISIBLE_DEVICES"] = str(device_id)
    torch.set_num_threads(int(args_dict["keep_cpu_threads"]))
    torch.cuda.set_device(0)
    device = torch.device("cuda:0")
    start = time.time()
    total = len(jobs)
    for local_idx, job in enumerate(jobs, start=1):
        layout = job.layout
        try:
            row = summarize_layout_mask(
                layout,
                job.mask_name,
                job.keep_indices,
                float(args_dict["grid_mm"]),
                int(args_dict["chunk_points"]),
                device,
            )
            row["gpu_device"] = device_id
            result_queue.put(("row", row))
            result_queue.put(
                (
                    "progress",
                    {
                        "device": device_id,
                        "done": local_idx,
                        "total": total,
                        "points": job.point_count,
                        "mask": job.mask_name,
                        "elapsed_s": time.time() - start,
                    },
                )
            )
        except Exception as exc:  # noqa: BLE001 - long GPU jobs should continue.
            result_queue.put(
                (
                    "row",
                    {
                        "layout_id": layout.get("layout_id", ""),
                        "capture_id": layout.get("capture_id", ""),
                        "source_group": layout.get("source_group", ""),
                        "solver_version": layout.get("solver_version", ""),
                        "layout_variant": layout.get("layout_variant", ""),
                        "source_path": layout.get("source_path", ""),
                        "mask": job.mask_name,
                        "grid_mm": args_dict["grid_mm"],
                        "gpu_device": device_id,
                        "status": f"error:{exc.__class__.__name__}:{exc}",
                    },
                )
            )
    result_queue.put(("done", {"device": device_id, "elapsed_s": time.time() - start}))


def fmt(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return f"{value:.9g}"
    return "" if value is None else str(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: fmt(value) for key, value in row.items()})


def write_report(path: Path, rows: list[dict[str, Any]], devices: list[int], grid_mm: float, top_n: int) -> None:
    ok = [row for row in rows if row.get("status") == "ok"]
    by_group_mask: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in ok:
        by_group_mask[(str(row["source_group"]), str(row["mask"]))].append(row)

    lines = [
        "# Dense GPU Axis DOP Ranking",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Rows: `{len(rows)}`",
        f"- Valid rows: `{len(ok)}`",
        f"- Grid: `{grid_mm:g} mm`",
        f"- Devices: `{','.join(str(device) for device in devices)}`",
        "- Metrics are geometry-only DOP summaries; lower is better.",
        "",
        "## Top Layout Per Group And Mask",
        "",
        "| Group | Mask | Rank | Version | Variant | Score | xDOP p95 | yDOP p95 | VDOP p95 | GDOP p95 | Cond p95 |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for (group, mask), items in sorted(by_group_mask.items()):
        items.sort(key=lambda row: float(row["axis_dop_score"]))
        for rank, row in enumerate(items[:top_n], start=1):
            lines.append(
                f"| `{group}` | `{mask}` | {rank} | `{row['solver_version']}` | `{row['layout_variant']}` | "
                f"{float(row['axis_dop_score']):.3f} | {float(row['xdop_p95']):.3f} | "
                f"{float(row['ydop_p95']):.3f} | {float(row['vdop_p95']):.3f} | "
                f"{float(row['gdop_p95']):.3f} | {float(row['cond_p95']):.3f} |"
            )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "For each layout and mask, the script samples a dense 3D grid inside the inset anchor bounding box.",
            "It computes batched range-geometry matrices on CUDA and summarizes exact per-layout percentiles.",
            "Only summary rows are written; per-grid-point DOP values are not retained.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def estimate(layouts: list[dict[str, Any]], masks_arg: str, grid_mm: float) -> tuple[int, int]:
    total_points = 0
    total_evals = 0
    for layout in layouts:
        anchors = anchor_xyz(layout)
        if len(anchors) < 4:
            continue
        points = grid_point_count(anchors, grid_mm)
        total_points += points
        total_evals += points * len(parse_masks(masks_arg, layout))
    return total_points, total_evals


def split_jobs(layouts: list[dict[str, Any]], devices: list[int], grid_mm: float, masks_arg: str) -> dict[int, list[LayoutJob]]:
    weighted: list[tuple[int, LayoutJob]] = []
    for idx, layout in enumerate(layouts):
        anchors = anchor_xyz(layout)
        points = grid_point_count(anchors, grid_mm) if len(anchors) >= 4 else 0
        for mask_name, keep_indices in parse_masks(masks_arg, layout):
            weighted.append((points, LayoutJob(idx, layout, mask_name, keep_indices, points)))
    weighted.sort(key=lambda item: item[0], reverse=True)
    out = {device: [] for device in devices}
    loads = {device: 0 for device in devices}
    for points, job in weighted:
        device = min(devices, key=lambda dev: loads[dev])
        out[device].append(job)
        loads[device] += points
    print("planned device loads:")
    for device in devices:
        print(f"  gpu{device}: jobs={len(out[device])} point_evals={loads[device]:,}")
    return out


def main() -> int:
    args = parse_args()
    devices = parse_devices(args.devices)
    layouts = load_jsonl(args.layout_db)
    if args.limit_layouts > 0:
        layouts = layouts[: args.limit_layouts]

    points, evals = estimate(layouts, args.masks, args.grid_mm)
    print(f"layouts={len(layouts)} grid_mm={args.grid_mm:g} masks={args.masks}")
    print(f"base_grid_points={points:,}")
    print(f"dop_evaluations={evals:,}")
    print(f"devices={devices}")
    jobs_by_device = split_jobs(layouts, devices, args.grid_mm, args.masks)

    if not args.execute:
        print("dry-run only; pass --execute to use CUDA")
        return 0

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is not available")
    if max(devices) >= torch.cuda.device_count():
        raise SystemExit(f"requested devices {devices}, but torch sees {torch.cuda.device_count()} CUDA devices")

    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    ctx = mp.get_context("spawn")
    result_queue: mp.Queue = ctx.Queue()
    args_dict = {
        "grid_mm": args.grid_mm,
        "masks": args.masks,
        "chunk_points": args.chunk_points,
        "keep_cpu_threads": args.keep_cpu_threads,
    }
    processes = [
        ctx.Process(target=worker_main, args=(device, jobs_by_device[device], args_dict, result_queue), daemon=False)
        for device in devices
    ]
    for process in processes:
        process.start()

    rows: list[dict[str, Any]] = []
    done = 0
    while done < len(processes):
        try:
            kind, payload = result_queue.get(timeout=5.0)
        except queue.Empty:
            continue
        if kind == "row":
            rows.append(payload)
        elif kind == "progress":
            print(
                f"gpu{payload['device']} progress {payload['done']}/{payload['total']} "
                f"mask={payload['mask']} points={payload['points']:,} elapsed={payload['elapsed_s']:.1f}s",
                flush=True,
            )
        elif kind == "done":
            done += 1
            print(f"gpu{payload['device']} done elapsed={payload['elapsed_s']:.1f}s", flush=True)

    for process in processes:
        process.join()
        if process.exitcode != 0:
            raise SystemExit(f"worker pid {process.pid} failed with exit code {process.exitcode}")

    rows.sort(key=lambda row: (str(row.get("source_group", "")), str(row.get("mask", "")), float(row.get("axis_dop_score", "inf") or "inf")))
    csv_path = args.feature_dir / f"{args.output_prefix}.csv"
    report_path = args.report_dir / f"{args.output_prefix}.md"
    write_csv(csv_path, rows)
    write_report(report_path, rows, devices, args.grid_mm, args.top_n)
    print(f"wrote {csv_path}")
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
