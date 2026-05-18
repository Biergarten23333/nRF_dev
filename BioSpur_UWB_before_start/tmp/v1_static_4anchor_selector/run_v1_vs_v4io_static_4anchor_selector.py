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
LAYOUT_PATH = ROOT / "autopos_pipeline/outdoor_20260513/FULL-COMPARE-1000/v1-old/layout.json"
STATIC_ROOT = ROOT / "autopos_pipeline/outdoor_20260513/Static_Test"
OUT = ROOT / "tmp/v1_static_4anchor_selector"

ANCHORS = tuple("ABCDEFGH")
LOWER = set(range(4))
UPPER = set(range(4, 8))


def load_layout(path: Path) -> dict[int, np.ndarray]:
    data = json.loads(path.read_text())
    coords = {}
    for row in data["anchors"]:
        coords[int(row["id"])] = np.array([row["x_mm"], row["y_mm"], row["z_mm"]], dtype=float)
    return coords


def tetra_volume_mm3(points: list[np.ndarray]) -> float:
    a, b, c, d = points
    return float(abs(np.dot(b - a, np.cross(c - a, d - a))) / 6.0)


def solve_point(coords: dict[int, np.ndarray], ranges: dict[int, float], anchors: tuple[int, ...]) -> tuple[np.ndarray, np.ndarray]:
    pts = np.array([coords[a] for a in anchors], dtype=float)
    ds = np.array([ranges[a] for a in anchors], dtype=float)
    x0 = pts.mean(axis=0)

    def residual(x: np.ndarray) -> np.ndarray:
        return np.linalg.norm(pts - x[None, :], axis=1) - ds

    res = least_squares(residual, x0=x0, loss="linear", max_nfev=200)
    return np.array(res.x, dtype=float), residual(res.x)


def choose_2p2_subset(
    coords: dict[int, np.ndarray],
    ranges: dict[int, float],
    qualities: dict[int, float],
) -> tuple[int, ...] | None:
    valid = [a for a, r in ranges.items() if a in coords and r > 0 and qualities.get(a, 0.0) > 0]
    if len(valid) < 4:
        return None

    best_key = None
    best = None
    for comb in itertools.combinations(valid, 4):
        s = set(comb)
        if len(s & LOWER) != 2 or len(s & UPPER) != 2:
            continue
        vol = tetra_volume_mm3([coords[a] for a in comb])
        if vol <= 1e3:
            continue
        q_mean = float(np.mean([qualities.get(a, 0.0) for a in comb]))
        # Prefer high quality first, then larger tetrahedron volume. This intentionally
        # mimics the old selector spirit without reusing any mutable pipeline state.
        key = (q_mean, math.log10(vol), min(qualities.get(a, 0.0) for a in comb))
        if best_key is None or key > best_key:
            best_key = key
            best = comb
    return best


def read_capture(path: Path) -> dict[int, list[dict]]:
    by_sweep: dict[int, list[dict]] = defaultdict(list)
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                sweep = int(float(row["sweep"]))
                aid = int(float(row["anchor_id"]))
                rng = float(row["range_mm"])
                q = float(row.get("quality_percent") or 0)
                valid = int(float(row.get("valid") or 0))
            except Exception:
                continue
            if valid != 1:
                continue
            by_sweep[sweep].append({"anchor_id": aid, "range_mm": rng, "quality": q})
    return by_sweep


def summarize_positions(points: list[np.ndarray]) -> dict[str, float]:
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    coords = load_layout(LAYOUT_PATH)
    captures = sorted(STATIC_ROOT.glob("ID*_*/tr_all.csv"))

    per_frame_rows = []
    summary_rows = []
    detail_target = "ID13"
    detail_rows = []

    for tr_path in captures:
        cap_dir = tr_path.parent
        cap_id = cap_dir.name.split("_", 1)[0]
        by_sweep = read_capture(tr_path)
        pos4: list[np.ndarray] = []
        pos8: list[np.ndarray] = []
        subsets = Counter()
        skipped = 0

        for sweep, rows in sorted(by_sweep.items()):
            ranges = {int(r["anchor_id"]): float(r["range_mm"]) for r in rows}
            qualities = {int(r["anchor_id"]): float(r["quality"]) for r in rows}
            subset = choose_2p2_subset(coords, ranges, qualities)
            if subset is None:
                skipped += 1
                continue
            p4, r4 = solve_point(coords, ranges, subset)
            pos4.append(p4)
            subsets["".join(ANCHORS[a] for a in subset)] += 1

            valid8 = tuple(a for a in range(8) if a in ranges and ranges[a] > 0)
            p8 = None
            if len(valid8) >= 4:
                p8, r8 = solve_point(coords, ranges, valid8)
                pos8.append(p8)

            row = {
                "ID": cap_id,
                "capture": str(cap_dir),
                "sweep": sweep,
                "selected": "".join(ANCHORS[a] for a in subset),
                "x4": p4[0],
                "y4": p4[1],
                "z4": p4[2],
                "res4_rms": float(np.sqrt(np.mean(r4 * r4))),
                "res4_max_abs": float(np.max(np.abs(r4))),
                "anchors_seen": len(valid8),
            }
            if p8 is not None:
                row.update({"x8": p8[0], "y8": p8[1], "z8": p8[2]})
            per_frame_rows.append(row)
            if cap_id == detail_target:
                detail_rows.append(row)

        if pos4:
            s4 = summarize_positions(pos4)
            s8 = summarize_positions(pos8) if pos8 else {}
            summary_rows.append(
                {
                    "ID": cap_id,
                    "capture": str(cap_dir),
                    "frames_4anchor": int(s4["N"]),
                    "skipped": skipped,
                    "D3_std_4anchor": s4["D3_std"],
                    "X_std_4anchor": s4["X_std"],
                    "Y_std_4anchor": s4["Y_std"],
                    "Z_std_4anchor": s4["Z_std"],
                    "radial_p95_4anchor": s4["radial_p95"],
                    "radial_max_4anchor": s4["radial_max"],
                    "frames_all_anchor": int(s8.get("N", 0)),
                    "D3_std_all_anchor": s8.get("D3_std", float("nan")),
                    "X_std_all_anchor": s8.get("X_std", float("nan")),
                    "Y_std_all_anchor": s8.get("Y_std", float("nan")),
                    "Z_std_all_anchor": s8.get("Z_std", float("nan")),
                    "top_selected_subsets": ";".join(f"{k}:{v}" for k, v in subsets.most_common(5)),
                }
            )

    write_csv(OUT / "per_frame_4anchor_selection.csv", per_frame_rows)
    write_csv(OUT / "ID13_per_frame_4anchor_selection.csv", detail_rows)
    write_csv(OUT / "summary_by_capture.csv", summary_rows)

    arr4 = np.array([r["D3_std_4anchor"] for r in summary_rows], dtype=float)
    arr8 = np.array([r["D3_std_all_anchor"] for r in summary_rows], dtype=float)
    z4 = np.array([r["Z_std_4anchor"] for r in summary_rows], dtype=float)
    z8 = np.array([r["Z_std_all_anchor"] for r in summary_rows], dtype=float)

    lines = [
        "# V1 4-Anchor Selector Static Probe",
        "",
        "这个临时实验只读数据，不修改 `FULL-COMPARE-*` 的任何输出。",
        "",
        "## Inputs",
        "",
        f"- Layout: `{LAYOUT_PATH}`",
        f"- Static root: `{STATIC_ROOT}`",
        "- Solver: V1 layout, per-frame nonlinear least squares.",
        "- 4-anchor selection: every frame choose exactly 2 lower anchors from A-D and 2 upper anchors from E-H.",
        "- Selection score: highest mean quality, then largest tetrahedron volume.",
        "- All-anchor comparison: same frame, use all valid anchors when available.",
        "",
        "## Overall Result",
        "",
        f"- Captures evaluated: `{len(summary_rows)}`",
        f"- 4-anchor median 3D std: `{np.median(arr4):.1f} mm`",
        f"- 4-anchor p95 3D std across captures: `{np.percentile(arr4, 95):.1f} mm`",
        f"- 4-anchor max 3D std across captures: `{np.max(arr4):.1f} mm`",
        f"- all-anchor median 3D std: `{np.nanmedian(arr8):.1f} mm`",
        f"- all-anchor p95 3D std across captures: `{np.nanpercentile(arr8, 95):.1f} mm`",
        f"- 4-anchor median Z std: `{np.median(z4):.1f} mm`",
        f"- all-anchor median Z std: `{np.nanmedian(z8):.1f} mm`",
        "",
        "## Interpretation",
        "",
        "这个结果就是 concept PDF 那条线的机制复现：同一个 V1 layout，在 full/all-anchor 冗余条件下看起来还行，",
        "但一旦每帧只用动态 2+2 的 4-anchor 子集，不同子集的系统偏差会变成位置云的扩散，尤其容易放大 Z。",
        "",
        "## Files",
        "",
        "- `summary_by_capture.csv`: 每个 static capture 的 4-anchor vs all-anchor 对照。",
        "- `per_frame_4anchor_selection.csv`: 每帧选中的 4-anchor subset 和解算位置。",
        "- `ID13_per_frame_4anchor_selection.csv`: ID13 单独逐帧明细，方便快速看 selector 行为。",
        "",
        "## Per-Capture Summary",
        "",
        "| ID | N4 | 4-anchor 3D std | 4-anchor Z std | all-anchor 3D std | all-anchor Z std | top selected subsets |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for r in summary_rows:
        lines.append(
            f"| {r['ID']} | {r['frames_4anchor']} | {r['D3_std_4anchor']:.1f} | {r['Z_std_4anchor']:.1f} | "
            f"{r['D3_std_all_anchor']:.1f} | {r['Z_std_all_anchor']:.1f} | {r['top_selected_subsets']} |"
        )
    (OUT / "README.md").write_text("\n".join(lines) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
