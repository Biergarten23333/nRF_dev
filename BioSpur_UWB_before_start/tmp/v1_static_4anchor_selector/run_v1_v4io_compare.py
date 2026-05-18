#!/usr/bin/env python3
from __future__ import annotations

import csv
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = ROOT / "autopos_pipeline/outdoor_20260513/Static_Test"
OUT = ROOT / "tmp/v1_vs_v4io_static_4anchor_selector"

LAYOUTS = {
    "v1-old": ROOT / "autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v1-old/layout.json",
    "v4-io": ROOT / "autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v4-io/layout.json",
}

ANCHORS = tuple("ABCDEFGH")
LOWER = set(range(4))
UPPER = set(range(4, 8))


def load_layout(path: Path) -> tuple[dict[int, np.ndarray], dict[int, float], float]:
    data = json.loads(path.read_text())
    coords = {
        int(row["id"]): np.array([row["x_mm"], row["y_mm"], row["z_mm"]], dtype=float)
        for row in data["anchors"]
    }
    delays = {int(row["id"]): float(row.get("d_anchor_mm", 0.0) or 0.0) for row in data["anchors"]}
    tag_delay = float(data.get("tag_delay_mm", 0.0) or 0.0)
    return coords, delays, tag_delay


def tetra_volume_mm3(points: list[np.ndarray]) -> float:
    a, b, c, d = points
    return float(abs(np.dot(b - a, np.cross(c - a, d - a))) / 6.0)


def solve_point(
    coords: dict[int, np.ndarray],
    delays: dict[int, float],
    tag_delay: float,
    ranges: dict[int, float],
    anchors: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray]:
    pts = np.array([coords[a] for a in anchors], dtype=float)
    ds = np.array([ranges[a] for a in anchors], dtype=float)
    x0 = pts.mean(axis=0)

    def residual(x: np.ndarray) -> np.ndarray:
        bias = np.array([delays.get(a, 0.0) + tag_delay for a in anchors], dtype=float)
        return np.linalg.norm(pts - x[None, :], axis=1) + bias - ds

    res = least_squares(residual, x0=x0, loss="linear", max_nfev=200)
    return np.array(res.x, dtype=float), residual(res.x)


def choose_subset(coords: dict[int, np.ndarray], ranges: dict[int, float], qualities: dict[int, float]) -> tuple[int, ...] | None:
    valid = [a for a, r in ranges.items() if a in coords and r > 0 and qualities.get(a, 0.0) > 0]
    best_key = None
    best = None
    for comb in itertools.combinations(valid, 4):
        s = set(comb)
        if len(s & LOWER) != 2 or len(s & UPPER) != 2:
            continue
        vol = tetra_volume_mm3([coords[a] for a in comb])
        if vol <= 1e3:
            continue
        key = (
            float(np.mean([qualities.get(a, 0.0) for a in comb])),
            math.log10(vol),
            min(qualities.get(a, 0.0) for a in comb),
        )
        if best_key is None or key > best_key:
            best_key = key
            best = comb
    return best


def read_capture(path: Path) -> dict[int, list[dict]]:
    by_sweep: dict[int, list[dict]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                valid = int(float(row.get("valid") or 0))
                if valid != 1:
                    continue
                by_sweep[int(float(row["sweep"]))].append(
                    {
                        "anchor_id": int(float(row["anchor_id"])),
                        "range_mm": float(row["range_mm"]),
                        "quality": float(row.get("quality_percent") or 0),
                    }
                )
            except Exception:
                continue
    return by_sweep


def summarize(points: list[np.ndarray]) -> dict[str, float]:
    arr = np.asarray(points, dtype=float)
    mean = arr.mean(axis=0)
    std = arr.std(axis=0)
    radial = np.linalg.norm(arr - mean[None, :], axis=1)
    return {
        "N": float(len(points)),
        "mean_x": mean[0],
        "mean_y": mean[1],
        "mean_z": mean[2],
        "X_std": std[0],
        "Y_std": std[1],
        "Z_std": std[2],
        "D3_std": float(np.sqrt(np.sum(std * std))),
        "radial_p50": float(np.percentile(radial, 50)),
        "radial_p95": float(np.percentile(radial, 95)),
        "radial_max": float(radial.max()),
    }


def evaluate_version(
    version: str,
    coords: dict[int, np.ndarray],
    delays: dict[int, float],
    tag_delay: float,
    captures: list[Path],
) -> tuple[list[dict], list[dict]]:
    summary_rows = []
    per_frame_rows = []
    for tr_path in captures:
        cap_id = tr_path.parent.name.split("_", 1)[0]
        by_sweep = read_capture(tr_path)
        pos4: list[np.ndarray] = []
        pos_all: list[np.ndarray] = []
        subsets = Counter()
        skipped = 0
        for sweep, rows in sorted(by_sweep.items()):
            ranges = {int(r["anchor_id"]): float(r["range_mm"]) for r in rows}
            qualities = {int(r["anchor_id"]): float(r["quality"]) for r in rows}
            subset = choose_subset(coords, ranges, qualities)
            if subset is None:
                skipped += 1
                continue
            p4, r4 = solve_point(coords, delays, tag_delay, ranges, subset)
            pos4.append(p4)
            subset_name = "".join(ANCHORS[a] for a in subset)
            subsets[subset_name] += 1
            valid_all = tuple(a for a in range(8) if a in ranges and ranges[a] > 0)
            if len(valid_all) >= 4:
                pall, _ = solve_point(coords, delays, tag_delay, ranges, valid_all)
                pos_all.append(pall)
            per_frame_rows.append(
                {
                    "version": version,
                    "ID": cap_id,
                    "sweep": sweep,
                    "selected": subset_name,
                    "x4": p4[0],
                    "y4": p4[1],
                    "z4": p4[2],
                    "res4_rms": float(np.sqrt(np.mean(r4 * r4))),
                    "res4_max_abs": float(np.max(np.abs(r4))),
                    "anchors_seen": len(valid_all),
                }
            )
        if not pos4:
            continue
        s4 = summarize(pos4)
        sa = summarize(pos_all)
        summary_rows.append(
            {
                "version": version,
                "ID": cap_id,
                "capture": str(tr_path.parent),
                "frames_4anchor": int(s4["N"]),
                "skipped": skipped,
                "D3_std_4anchor": s4["D3_std"],
                "X_std_4anchor": s4["X_std"],
                "Y_std_4anchor": s4["Y_std"],
                "Z_std_4anchor": s4["Z_std"],
                "radial_p95_4anchor": s4["radial_p95"],
                "radial_max_4anchor": s4["radial_max"],
                "frames_all_anchor": int(sa["N"]),
                "D3_std_all_anchor": sa["D3_std"],
                "X_std_all_anchor": sa["X_std"],
                "Y_std_all_anchor": sa["Y_std"],
                "Z_std_all_anchor": sa["Z_std"],
                "top_selected_subsets": ";".join(f"{k}:{v}" for k, v in subsets.most_common(5)),
            }
        )
    return summary_rows, per_frame_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    captures = sorted(STATIC_ROOT.glob("ID*_*/tr_all.csv"))
    all_summary = []
    all_frames = []
    for version, layout_path in LAYOUTS.items():
        coords, delays, tag_delay = load_layout(layout_path)
        summary, frames = evaluate_version(version, coords, delays, tag_delay, captures)
        all_summary.extend(summary)
        all_frames.extend(frames)

    write_csv(OUT / "summary_by_capture.csv", all_summary)
    write_csv(OUT / "per_frame_4anchor_selection.csv", all_frames)

    by_ver: dict[str, list[dict]] = defaultdict(list)
    for row in all_summary:
        by_ver[row["version"]].append(row)

    compact = []
    for version, rows in by_ver.items():
        d4 = np.array([float(r["D3_std_4anchor"]) for r in rows])
        da = np.array([float(r["D3_std_all_anchor"]) for r in rows])
        z4 = np.array([float(r["Z_std_4anchor"]) for r in rows])
        za = np.array([float(r["Z_std_all_anchor"]) for r in rows])
        compact.append(
            {
                "version": version,
                "captures": len(rows),
                "D3_4anchor_median": float(np.median(d4)),
                "D3_4anchor_p95": float(np.percentile(d4, 95)),
                "D3_4anchor_max": float(np.max(d4)),
                "D3_all_anchor_median": float(np.median(da)),
                "D3_all_anchor_p95": float(np.percentile(da, 95)),
                "Z_4anchor_median": float(np.median(z4)),
                "Z_all_anchor_median": float(np.median(za)),
            }
        )
    write_csv(OUT / "summary_compact.csv", compact)

    v1 = next(r for r in compact if r["version"] == "v1-old")
    v4 = next(r for r in compact if r["version"] == "v4-io")
    improve_4 = (v1["D3_4anchor_median"] - v4["D3_4anchor_median"]) / v1["D3_4anchor_median"] * 100.0
    improve_z = (v1["Z_4anchor_median"] - v4["Z_4anchor_median"]) / v1["Z_4anchor_median"] * 100.0

    lines = [
        "# V1 vs V4-io: Static 4-Anchor Selector Probe",
        "",
        "这个临时实验用于验证：all-anchor 评估可能掩盖 layout bias，而 4-anchor dynamic-style selector 会暴露 V1 与 V4-io 的差异。",
        "",
        "## Inputs",
        "",
        f"- Static captures: `{STATIC_ROOT}`",
        f"- V1 layout: `{LAYOUTS['v1-old']}`",
        f"- V4-io layout: `{LAYOUTS['v4-io']}`",
        "- 每帧 selector: exactly 2 lower anchors + 2 upper anchors, non-coplanar.",
        "- Selector scoring: mean quality first, tetrahedron volume second.",
        "- 同一批 static captures、同一套 selector 规则分别跑 V1 与 V4-io。",
        "",
        "## Compact Result",
        "",
        "| Version | captures | 4-anchor median 3D std | 4-anchor p95 | 4-anchor max | all-anchor median 3D std | all-anchor p95 | 4-anchor median Z std | all-anchor median Z std |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in compact:
        lines.append(
            f"| {r['version']} | {r['captures']} | {r['D3_4anchor_median']:.1f} | {r['D3_4anchor_p95']:.1f} | {r['D3_4anchor_max']:.1f} | "
            f"{r['D3_all_anchor_median']:.1f} | {r['D3_all_anchor_p95']:.1f} | {r['Z_4anchor_median']:.1f} | {r['Z_all_anchor_median']:.1f} |"
        )
    lines += [
        "",
        "## Main Takeaway",
        "",
        f"- 4-anchor median 3D std improvement from V1 to V4-io: `{improve_4:.1f}%`.",
        f"- 4-anchor median Z std improvement from V1 to V4-io: `{improve_z:.1f}%`.",
        "",
        "本次简化 selector 下，V4-io 没有显著优于 V1：all-anchor 仍接近，4-anchor 下二者也接近，且 V4-io 的 Z median 略高。",
        "这说明 20260513 broadcast static 数据里，delay-aware layout 的优势不能简单通过这个离线 4-anchor selector 复现。",
        "如果要复现 concept PDF 的 130mm 机制，需要进一步使用当时 real-time on-tag selector/solver 逻辑，或把旧数据同样跑进这个对比框架。",
        "",
        "## Files",
        "",
        "- `summary_compact.csv`: 最核心的 V1 vs V4-io 汇总。",
        "- `summary_by_capture.csv`: 每个 static capture 的详细对比。",
        "- `per_frame_4anchor_selection.csv`: 每帧选锚和解算细节。",
    ]
    (OUT / "README.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
