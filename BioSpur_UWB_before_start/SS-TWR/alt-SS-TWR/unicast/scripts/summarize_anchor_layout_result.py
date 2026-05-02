#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Summarize anchor layout JSON into Markdown.")
    ap.add_argument("--layout-json", required=True)
    ap.add_argument("--output-md", required=True)
    ap.add_argument("--title", default="Anchor Layout Result Summary")
    args = ap.parse_args()

    p = Path(args.layout_json)
    raw = json.loads(p.read_text(encoding="utf-8"))
    anchors = {a["label"]: (float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])) for a in raw["anchors"]}
    q = raw.get("quality", {})
    edge_fit = q.get("edge_fit", {})
    delays = raw.get("antenna_delays_ns", {})
    refs = raw.get("floating_reference", [])

    lines: list[str] = []
    lines.append(f"# {args.title}")
    lines.append("")
    lines.append(f"- layout_json: `{p}`")
    lines.append(f"- rms_edges_mm: `{q.get('rms_edges_mm')}`")
    lines.append(f"- rms_inlier_mm: `{edge_fit.get('rms_inlier_mm')}`")
    lines.append(f"- inlier_count: `{edge_fit.get('inlier_count')}`")
    lines.append(f"- outlier_count: `{edge_fit.get('outlier_count')}`")
    lines.append("")
    lines.append("## Anchor Coordinates")
    lines.append("")
    lines.append("| Anchor | X (mm) | Y (mm) | Z (mm) | Delay (ns) |")
    lines.append("|---|---:|---:|---:|---:|")
    for label in "ABCDEFGH":
        x, y, z = anchors[label]
        lines.append(f"| {label} | {x:.3f} | {y:.3f} | {z:.3f} | {float(delays.get(label, 0.0)):.6f} |")
    lines.append("")

    lines.append("## Box Pair Geometry")
    lines.append("")
    lines.append("| Pair | dZ (mm) | dXY (mm) |")
    lines.append("|---|---:|---:|")
    for lo, up in [("A", "E"), ("B", "F"), ("C", "G"), ("D", "H")]:
        x1, y1, z1 = anchors[lo]
        x2, y2, z2 = anchors[up]
        dz = z2 - z1
        dxy = math.hypot(x2 - x1, y2 - y1)
        lines.append(f"| {lo}-{up} | {dz:.3f} | {dxy:.3f} |")
    lines.append("")

    lines.append("## Top Outlier Edges")
    lines.append("")
    lines.append("| Pair | Weight | Error (mm) | Abs Error (mm) |")
    lines.append("|---|---:|---:|---:|")
    for row in edge_fit.get("top_outliers", []):
        lines.append(
            f"| {row['pair']} | {float(row['w']):.6f} | {float(row['err_mm']):.3f} | {float(row['abs_err_mm']):.3f} |"
        )
    lines.append("")

    if refs:
        lines.append("## Floating Reference")
        lines.append("")
        for ref in refs:
            xyz = ref["ref_point_mm"]
            lines.append(
                f"- `{ref['label']}`: x=`{float(xyz['x']):.3f}` mm, y=`{float(xyz['y']):.3f}` mm, z=`{float(xyz['z']):.3f}` mm, ref_delay_ns=`{float(ref['ref_delay_ns']):.6f}`"
            )
        lines.append("")

    out = Path(args.output_md)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
