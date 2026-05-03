#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ANCHORS = "ABCDEFGH"


def load_layout(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    coords = np.zeros((8, 3), dtype=float)
    if "anchors" not in raw:
        raise KeyError(f"{path} has no anchors array")
    for ent in raw["anchors"]:
        idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
        coords[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    return coords


def load_pairs(path: Path) -> dict[tuple[int, int], list[float]]:
    out: dict[tuple[int, int], list[float]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                a = ANCHORS.index(str(row["a"]).strip().upper())
                b = ANCHORS.index(str(row["b"]).strip().upper())
                if a == b:
                    continue
                i, j = sorted((a, b))
                dist = float(row.get("dist_mm") or row.get("raw_mm") or row.get("range_mm"))
            except Exception:
                continue
            out[(i, j)].append(dist)
    return out


def pair_stats(pairs: dict[tuple[int, int], list[float]], layout: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for (i, j), vals in sorted(pairs.items()):
        arr = np.asarray(vals, dtype=float)
        geom = float(np.linalg.norm(layout[i] - layout[j]))
        med = float(np.median(arr))
        err = med - geom
        rows.append(
            {
                "pair": f"{ANCHORS[i]}-{ANCHORS[j]}",
                "i": i,
                "j": j,
                "n": int(arr.size),
                "median_mm": med,
                "mean_mm": float(np.mean(arr)),
                "std_mm": float(np.std(arr)),
                "min_mm": float(np.min(arr)),
                "max_mm": float(np.max(arr)),
                "geom_mm": geom,
                "error_mm": err,
                "abs_error_mm": abs(err),
            }
        )
    rows.sort(key=lambda r: r["abs_error_mm"], reverse=True)
    return rows


def per_anchor_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        groups[ANCHORS[int(r["i"])]].append(float(r["error_mm"]))
        groups[ANCHORS[int(r["j"])]].append(float(r["error_mm"]))
    out = []
    for a in ANCHORS:
        vals = np.asarray(groups.get(a, []), dtype=float)
        if vals.size == 0:
            continue
        out.append(
            {
                "anchor": a,
                "mean_error_mm": float(np.mean(vals)),
                "median_error_mm": float(np.median(vals)),
                "mean_abs_error_mm": float(np.mean(np.abs(vals))),
                "max_abs_error_mm": float(np.max(np.abs(vals))),
                "neg_count": int(np.sum(vals < 0)),
                "pos_count": int(np.sum(vals > 0)),
            }
        )
    out.sort(key=lambda r: r["mean_abs_error_mm"], reverse=True)
    return out


def fmt(x: float) -> str:
    return f"{x:.1f}"


def write_markdown(
    output: Path,
    pairs_csv: Path,
    layout: Path,
    rows: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]] | None,
    previous_csv: Path | None,
) -> None:
    prev_by_pair = {r["pair"]: r for r in previous_rows or []}
    lines = []
    lines.append("# Post Physical Fix 500-set Inter-anchor Pair Analysis\n\n")
    lines.append(f"- Current pairs: `{pairs_csv}`\n")
    lines.append(f"- Layout reference: `{layout}`\n")
    if previous_csv:
        lines.append(f"- Previous comparison pairs: `{previous_csv}`\n")
    lines.append("- Error convention: `median_measured - geometric_distance_from_layout`.\n")
    lines.append("- Note: `n=1000` means 500 sets with both directions aggregated.\n\n")

    lines.append("## Top Pair Errors\n\n")
    lines.append("| Pair | n | median | std | geom | error | previous error | delta vs previous |\n")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|\n")
    for r in rows:
        prev = prev_by_pair.get(r["pair"])
        if prev:
            prev_err = float(prev["error_mm"])
            delta = float(r["error_mm"]) - prev_err
            prev_s = fmt(prev_err)
            delta_s = fmt(delta)
        else:
            prev_s = ""
            delta_s = ""
        lines.append(
            f"| {r['pair']} | {r['n']} | {fmt(r['median_mm'])} | {fmt(r['std_mm'])} | "
            f"{fmt(r['geom_mm'])} | {fmt(r['error_mm'])} | {prev_s} | {delta_s} |\n"
        )

    focus = ["B-D", "B-E", "A-F", "A-C"]
    lines.append("\n## Requested Focus Pairs\n\n")
    lines.append("| Pair | current error | current std | previous error | previous std | verdict |\n")
    lines.append("|---|---:|---:|---:|---:|---|\n")
    by_pair = {r["pair"]: r for r in rows}
    for pair in focus:
        r = by_pair.get(pair)
        prev = prev_by_pair.get(pair)
        if not r:
            continue
        verdict = ""
        if prev:
            improvement = abs(float(prev["error_mm"])) - abs(float(r["error_mm"]))
            if improvement > 50:
                verdict = f"improved by {improvement:.0f}mm"
            elif improvement < -50:
                verdict = f"worse by {-improvement:.0f}mm"
            else:
                verdict = "roughly unchanged"
            lines.append(
                f"| {pair} | {fmt(r['error_mm'])} | {fmt(r['std_mm'])} | "
                f"{fmt(prev['error_mm'])} | {fmt(prev['std_mm'])} | {verdict} |\n"
            )
        else:
            lines.append(f"| {pair} | {fmt(r['error_mm'])} | {fmt(r['std_mm'])} |  |  | no previous |\n")

    lines.append("\n## Per-anchor Mean Signed Error\n\n")
    lines.append("| Anchor | mean error | median error | mean abs | max abs | neg/pos |\n")
    lines.append("|---|---:|---:|---:|---:|---:|\n")
    for a in anchors:
        lines.append(
            f"| {a['anchor']} | {fmt(a['mean_error_mm'])} | {fmt(a['median_error_mm'])} | "
            f"{fmt(a['mean_abs_error_mm'])} | {fmt(a['max_abs_error_mm'])} | "
            f"{a['neg_count']}/{a['pos_count']} |\n"
        )
    output.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze AutoPos inter-anchor pair measurements against a layout.")
    ap.add_argument("--pairs-csv", required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--previous-pairs-csv")
    args = ap.parse_args()

    pairs_csv = Path(args.pairs_csv)
    layout_path = Path(args.layout)
    output = Path(args.output)
    layout = load_layout(layout_path)
    rows = pair_stats(load_pairs(pairs_csv), layout)
    anchors = per_anchor_summary(rows)
    prev_rows = None
    prev_path = Path(args.previous_pairs_csv) if args.previous_pairs_csv else None
    if prev_path:
        prev_rows = pair_stats(load_pairs(prev_path), layout)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_markdown(output, pairs_csv, layout_path, rows, anchors, prev_rows, prev_path)
    print(f"[ok] wrote {output}")
    print("top 8 pair errors:")
    for r in rows[:8]:
        print(
            f"{r['pair']}: median={r['median_mm']:.0f} std={r['std_mm']:.1f} "
            f"geom={r['geom_mm']:.0f} error={r['error_mm']:+.0f}mm n={r['n']}"
        )
    print("per-anchor:")
    for a in anchors:
        print(
            f"{a['anchor']}: mean={a['mean_error_mm']:+.1f} "
            f"mean_abs={a['mean_abs_error_mm']:.1f} max_abs={a['max_abs_error_mm']:.1f} "
            f"neg/pos={a['neg_count']}/{a['pos_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
