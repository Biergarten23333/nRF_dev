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
    return {"n": len(values), "mean": mean, "rms": rms, "max": max(values)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare V1/V2/V3 pair-distance CSV outputs.")
    ap.add_argument("--v1", required=True, help="V1 final_pair_distances.csv")
    ap.add_argument("--v2", required=True, help="V2 final_pair_distances_v2.csv")
    ap.add_argument("--v3", required=True, help="V3-lite final_pair_distances_v2.csv")
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
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    d1 = load_pairs(p1)
    d2 = load_pairs(p2)
    d3 = load_pairs(p3)

    if args.zero_as_missing:
        d1 = {k: v for k, v in d1.items() if v != 0.0}
        d2 = {k: v for k, v in d2.items() if v != 0.0}
        d3 = {k: v for k, v in d3.items() if v != 0.0}

    keys = sorted(set(d1) | set(d2) | set(d3))
    delta_12 = []
    delta_13 = []
    delta_23 = []

    lines: list[str] = []
    lines.append("# AutoPos V1 / V2 / V3-lite Pair Distance Compare")
    lines.append("")
    lines.append(f"- V1: `{p1}`")
    lines.append(f"- V2: `{p2}`")
    lines.append(f"- V3-lite: `{p3}`")
    lines.append("")
    lines.append("## Per-Pair Table (mm)")
    lines.append("")
    if args.zero_as_missing:
        lines.append("- Note: `distance_mm==0` treated as missing for all versions (`--zero-as-missing`).")
        lines.append("")
    lines.append("| Pair | V1 | V2 | V3 | abs(V2-V1) | abs(V3-V1) | abs(V3-V2) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for k in keys:
        v1 = d1.get(k)
        v2 = d2.get(k)
        v3 = d3.get(k)
        a12 = abs(v2 - v1) if (v1 is not None and v2 is not None) else None
        a13 = abs(v3 - v1) if (v1 is not None and v3 is not None) else None
        a23 = abs(v3 - v2) if (v2 is not None and v3 is not None) else None
        if a12 is not None:
            delta_12.append(a12)
        if a13 is not None:
            delta_13.append(a13)
        if a23 is not None:
            delta_23.append(a23)

        def fmt(x: float | None) -> str:
            return "-" if x is None else f"{x:.2f}"

        lines.append(
            f"| {k} | {fmt(v1)} | {fmt(v2)} | {fmt(v3)} | {fmt(a12)} | {fmt(a13)} | {fmt(a23)} |"
        )

    lines.append("")
    lines.append("## Summary (absolute delta, mm)")
    lines.append("")
    s12 = stats(delta_12)
    s13 = stats(delta_13)
    s23 = stats(delta_23)
    lines.append("| Compare | n | mean | rms | max |")
    lines.append("|---|---:|---:|---:|---:|")

    def srow(name: str, s: dict[str, float | None]) -> str:
        n = int(s["n"])
        mean = "-" if s["mean"] is None else f"{s['mean']:.2f}"
        rms = "-" if s["rms"] is None else f"{s['rms']:.2f}"
        mx = "-" if s["max"] is None else f"{s['max']:.2f}"
        return f"| {name} | {n} | {mean} | {rms} | {mx} |"

    lines.append(srow("V2 vs V1", s12))
    lines.append(srow("V3 vs V1", s13))
    lines.append(srow("V3 vs V2", s23))
    lines.append("")
    lines.append("Notes:")
    lines.append("- Differences here are *algorithmic output deltas* given the captured data; if the runs used different sweep set counts or different hardware state, deltas can be dominated by data differences rather than solver differences.")
    lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
