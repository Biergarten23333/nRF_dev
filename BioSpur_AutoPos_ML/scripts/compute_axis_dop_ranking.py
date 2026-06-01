#!/usr/bin/env python3
"""Compute axis-wise DOP summaries for every canonical layout.

This is a geometry-only check. It does not need OptiTrack labels and should be
used as a proxy for observability, not as measured localization accuracy.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--grid-mm", type=float, default=250.0)
    parser.add_argument("--max-points", type=int, default=30000)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def fmt(value: float | int | str) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return ""
    return f"{value:.9g}"


def anchor_xyz(layout: dict[str, Any]) -> np.ndarray:
    anchors = layout.get("anchors", [])
    points: list[list[float]] = []
    for anchor in anchors:
        try:
            points.append([float(anchor["x_mm"]), float(anchor["y_mm"]), float(anchor["z_mm"])])
        except (KeyError, TypeError, ValueError):
            continue
    return np.array(points, dtype=float)


def axis_values(min_v: float, max_v: float, spacing: float) -> np.ndarray:
    if max_v < min_v:
        min_v, max_v = max_v, min_v
    span = max_v - min_v
    if span <= 1e-9:
        return np.array([min_v], dtype=float)
    n = max(2, int(math.ceil(span / spacing)) + 1)
    return np.linspace(min_v, max_v, n)


def sample_points(anchors: np.ndarray, grid_mm: float, max_points: int) -> np.ndarray:
    mins = anchors.min(axis=0)
    maxs = anchors.max(axis=0)
    spans = maxs - mins
    margin = np.maximum(spans * 0.03, 100.0)
    mins = mins + margin
    maxs = maxs - margin
    for idx in range(3):
        if maxs[idx] <= mins[idx]:
            mid = (anchors[:, idx].min() + anchors[:, idx].max()) / 2.0
            mins[idx] = mid
            maxs[idx] = mid

    xs = axis_values(float(mins[0]), float(maxs[0]), grid_mm)
    ys = axis_values(float(mins[1]), float(maxs[1]), grid_mm)
    zs = axis_values(float(mins[2]), float(maxs[2]), grid_mm)

    points = np.array(np.meshgrid(xs, ys, zs, indexing="xy")).reshape(3, -1).T
    if len(points) <= max_points:
        return points

    stride = math.ceil(len(points) / max_points)
    return points[::stride]


def dop_at_point(anchors: np.ndarray, point: np.ndarray) -> dict[str, float] | None:
    vecs = anchors - point
    distances = np.linalg.norm(vecs, axis=1)
    valid = distances > 1e-6
    if int(valid.sum()) < 4:
        return None
    unit = vecs[valid] / distances[valid, None]
    normal = unit.T @ unit
    try:
        cov = np.linalg.inv(normal)
    except np.linalg.LinAlgError:
        return None
    diag = np.diag(cov)
    if np.any(diag < 0) or not np.all(np.isfinite(diag)):
        return None
    xdop = math.sqrt(float(cov[0, 0]))
    ydop = math.sqrt(float(cov[1, 1]))
    zdop = math.sqrt(float(cov[2, 2]))
    return {
        "xdop": xdop,
        "ydop": ydop,
        "vdop": zdop,
        "hdop": math.sqrt(float(cov[0, 0] + cov[1, 1])),
        "gdop": math.sqrt(float(np.trace(cov))),
        "cond": float(np.linalg.cond(normal)),
    }


def score_summary(summary: dict[str, Any]) -> float:
    return (
        float(summary["gdop_p95"]) * 0.25
        + float(summary["hdop_p95"]) * 0.20
        + float(summary["vdop_p95"]) * 0.30
        + max(float(summary["xdop_p95"]), float(summary["ydop_p95"])) * 0.15
        + min(float(summary["cond_p95"]) / 10.0, 10.0) * 0.10
    )


def summarize_layout(layout: dict[str, Any], grid_mm: float, max_points: int) -> dict[str, Any]:
    anchors = anchor_xyz(layout)
    base = {
        "layout_id": layout.get("layout_id", ""),
        "capture_id": layout.get("capture_id", ""),
        "source_group": layout.get("source_group", ""),
        "solver_version": layout.get("solver_version", ""),
        "layout_variant": layout.get("layout_variant", ""),
        "source_path": layout.get("source_path", ""),
        "anchor_count": len(anchors),
        "grid_mm": grid_mm,
    }
    if len(anchors) < 4:
        return {**base, "n_points": 0, "n_valid": 0, "status": "too_few_anchors"}

    points = sample_points(anchors, grid_mm, max_points)
    values: dict[str, list[float]] = defaultdict(list)
    for point in points:
        dop = dop_at_point(anchors, point)
        if dop is None:
            continue
        for key, value in dop.items():
            values[key].append(value)

    out: dict[str, Any] = {**base, "n_points": len(points), "n_valid": len(values.get("gdop", []))}
    if out["n_valid"] == 0:
        out["status"] = "no_valid_grid_points"
        return out

    for field in ("xdop", "ydop", "vdop", "hdop", "gdop", "cond"):
        vals = values[field]
        out[f"{field}_mean"] = sum(vals) / len(vals)
        out[f"{field}_median"] = percentile(vals, 0.5)
        out[f"{field}_p90"] = percentile(vals, 0.90)
        out[f"{field}_p95"] = percentile(vals, 0.95)
        out[f"{field}_max"] = max(vals)
    out["axis_imbalance_p95"] = max(out["xdop_p95"], out["ydop_p95"], out["vdop_p95"]) / max(
        min(out["xdop_p95"], out["ydop_p95"], out["vdop_p95"]), 1e-9
    )
    out["axis_dop_score"] = score_summary(out)
    out["status"] = "ok"
    return out


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


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    ok = [row for row in rows if row.get("status") == "ok"]
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ok:
        by_group[str(row["source_group"])].append(row)

    lines = [
        "# Axis DOP Ranking",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Layouts evaluated: `{len(rows)}`",
        f"- Valid layouts: `{len(ok)}`",
        "- Metrics are geometry-only proxy scores; lower is better.",
        "- `vdop` is the z-axis DOP. `xdop` and `ydop` expose horizontal-axis weakness separately.",
        "",
        "## Top Layout Per Group",
        "",
        "| Group | Rank | Version | Variant | Score | xDOP p95 | yDOP p95 | VDOP p95 | GDOP p95 | Cond p95 | Note |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for group, items in sorted(by_group.items()):
        items.sort(key=lambda row: float(row["axis_dop_score"]))
        for rank, row in enumerate(items[:3], start=1):
            note = ""
            if float(row["axis_imbalance_p95"]) > 1.8:
                note = "axis imbalance"
            lines.append(
                "| "
                f"`{group}` | {rank} | `{row['solver_version']}` | `{row['layout_variant']}` | "
                f"{float(row['axis_dop_score']):.3f} | {float(row['xdop_p95']):.3f} | "
                f"{float(row['ydop_p95']):.3f} | {float(row['vdop_p95']):.3f} | "
                f"{float(row['gdop_p95']):.3f} | {float(row['cond_p95']):.3f} | {note} |"
            )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "For each canonical layout, the script samples points inside a lightly inset anchor bounding box.",
            "At each point it builds a range-geometry matrix from line-of-sight unit vectors and computes",
            "`Q = inv(G^T G)`. Axis DOP values are `sqrt(Qxx)`, `sqrt(Qyy)`, and `sqrt(Qzz)`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    layouts = load_jsonl(args.layout_db)
    rows = [summarize_layout(layout, args.grid_mm, args.max_points) for layout in layouts]
    write_csv(args.feature_dir / "axis_dop_summary.csv", rows)
    write_report(args.report_dir / "axis_dop_ranking.md", rows)
    print(f"axis_dop_layouts={len(rows)} valid={sum(1 for row in rows if row.get('status') == 'ok')}")
    print(f"wrote {args.feature_dir / 'axis_dop_summary.csv'}")
    print(f"wrote {args.report_dir / 'axis_dop_ranking.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
