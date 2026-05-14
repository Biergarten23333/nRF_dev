#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path


def load_pairs(csv_path: Path) -> dict[str, float]:
    """
    Load a symmetric pair distance CSV.

    Expected headers include: a,b,distance_mm
    Output key format: "A-B" (sorted)
    """
    out: dict[str, float] = {}
    with csv_path.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            if not a or not b or a == b:
                continue
            d = row.get("distance_mm")
            if d is None or str(d).strip() == "":
                continue
            key = "-".join(sorted([a, b]))
            out[key] = float(d)
    return out


def stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "mean": None, "rms": None, "max": None}
    mean = sum(values) / len(values)
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    return {"n": float(len(values)), "mean": mean, "rms": rms, "max": max(values)}


def fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.2f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare V1/V2/V3-lite/V3-full pair-distance CSV outputs.")
    ap.add_argument("--v1", required=True, help="V1 final_pair_distances.csv")
    ap.add_argument("--v2", required=True, help="V2 final_pair_distances_v2.csv")
    ap.add_argument("--v3", required=True, help="V3-lite final_pair_distances_v2.csv")
    ap.add_argument("--v3full", required=True, help="V3-full final_pair_distances_v3.csv")
    ap.add_argument(
        "--zero-as-missing",
        action="store_true",
        help="Treat distance_mm==0 as missing (useful when a pipeline writes placeholder zeros).",
    )
    ap.add_argument("--out", required=True, help="Output markdown path")
    args = ap.parse_args()

    p1 = Path(args.v1)
    p2 = Path(args.v2)
    p3 = Path(args.v3)
    p4 = Path(args.v3full)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    d1 = load_pairs(p1)
    d2 = load_pairs(p2)
    d3 = load_pairs(p3)
    d4 = load_pairs(p4)

    if args.zero_as_missing:
        d1 = {k: v for k, v in d1.items() if v != 0.0}
        d2 = {k: v for k, v in d2.items() if v != 0.0}
        d3 = {k: v for k, v in d3.items() if v != 0.0}
        d4 = {k: v for k, v in d4.items() if v != 0.0}

    keys = sorted(set(d1) | set(d2) | set(d3) | set(d4))

    delta_21: list[float] = []
    delta_31: list[float] = []
    delta_41: list[float] = []
    delta_32: list[float] = []
    delta_42: list[float] = []
    delta_43: list[float] = []

    lines: list[str] = []
    lines.append("# AutoPos V1 / V2 / V3-lite / V3-full Pair Distance Compare")
    lines.append("")
    lines.append(f"- V1: `{p1}`")
    lines.append(f"- V2: `{p2}`")
    lines.append(f"- V3-lite: `{p3}`")
    lines.append(f"- V3-full: `{p4}`")
    lines.append("")
    lines.append("## Per-Pair Table (mm)")
    lines.append("")
    if args.zero_as_missing:
        lines.append("- Note: `distance_mm==0` treated as missing for all versions (`--zero-as-missing`).")
        lines.append("")
    lines.append("| Pair | V1 | V2 | V3 | V3full | abs(V2-V1) | abs(V3-V1) | abs(V3full-V1) | abs(V3-V2) | abs(V3full-V2) | abs(V3full-V3) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    for k in keys:
        v1 = d1.get(k)
        v2 = d2.get(k)
        v3 = d3.get(k)
        v4 = d4.get(k)

        a21 = abs(v2 - v1) if (v1 is not None and v2 is not None) else None
        a31 = abs(v3 - v1) if (v1 is not None and v3 is not None) else None
        a41 = abs(v4 - v1) if (v1 is not None and v4 is not None) else None
        a32 = abs(v3 - v2) if (v2 is not None and v3 is not None) else None
        a42 = abs(v4 - v2) if (v2 is not None and v4 is not None) else None
        a43 = abs(v4 - v3) if (v3 is not None and v4 is not None) else None

        if a21 is not None:
            delta_21.append(a21)
        if a31 is not None:
            delta_31.append(a31)
        if a41 is not None:
            delta_41.append(a41)
        if a32 is not None:
            delta_32.append(a32)
        if a42 is not None:
            delta_42.append(a42)
        if a43 is not None:
            delta_43.append(a43)

        lines.append(
            f"| {k} | {fmt(v1)} | {fmt(v2)} | {fmt(v3)} | {fmt(v4)} | {fmt(a21)} | {fmt(a31)} | {fmt(a41)} | {fmt(a32)} | {fmt(a42)} | {fmt(a43)} |"
        )

    lines.append("")
    lines.append("## Summary (absolute delta, mm)")
    lines.append("")
    s21 = stats(delta_21)
    s31 = stats(delta_31)
    s41 = stats(delta_41)
    s32 = stats(delta_32)
    s42 = stats(delta_42)
    s43 = stats(delta_43)
    lines.append("| Compare | n | mean | rms | max |")
    lines.append("|---|---:|---:|---:|---:|")

    def srow(name: str, s: dict[str, float | None]) -> str:
        n = int(s["n"] or 0)
        mean = "-" if s["mean"] is None else f"{s['mean']:.2f}"
        rms = "-" if s["rms"] is None else f"{s['rms']:.2f}"
        mx = "-" if s["max"] is None else f"{s['max']:.2f}"
        return f"| {name} | {n} | {mean} | {rms} | {mx} |"

    lines.append(srow("V2 vs V1", s21))
    lines.append(srow("V3 vs V1", s31))
    lines.append(srow("V3full vs V1", s41))
    lines.append(srow("V3 vs V2", s32))
    lines.append(srow("V3full vs V2", s42))
    lines.append(srow("V3full vs V3", s43))
    lines.append("")
    lines.append("Notes:")
    lines.append("- Differences here are *algorithmic output deltas* given the same captured data.")
    lines.append("- If some version is missing a pair, the corresponding deltas are omitted from summary stats.")
    lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

