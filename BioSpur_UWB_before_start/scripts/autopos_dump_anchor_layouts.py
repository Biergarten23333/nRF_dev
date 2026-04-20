#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_layout(path: Path) -> dict[str, tuple[float, float, float]]:
    """
    Return anchor coords in meters: { "A": (x_m, y_m, z_m), ... }.

    Supported formats:
    - {"units":"m","anchors":{"A":[x,y,z], ...}}
    - {"units":"mm","anchors":[{"label":"A","x_mm":..,"y_mm":..,"z_mm":..}, ...]}
    """
    obj = _load_json(path)
    units = str(obj.get("units", "m")).lower()
    anchors = obj.get("anchors")
    out: dict[str, tuple[float, float, float]] = {}

    if isinstance(anchors, dict):
        if units not in ("m", "meter", "meters"):
            raise ValueError(f"{path}: unexpected units={units!r} for dict anchors format")
        for k, v in anchors.items():
            if not isinstance(k, str) or len(k) != 1:
                continue
            if (
                isinstance(v, (list, tuple))
                and len(v) >= 3
                and all(isinstance(x, (int, float)) for x in v[:3])
            ):
                out[k.upper()] = (float(v[0]), float(v[1]), float(v[2]))
        return out

    if isinstance(anchors, list):
        if units not in ("mm", "millimeter", "millimeters"):
            # Allow meters list variant if it ever shows up.
            if units in ("m", "meter", "meters"):
                scale = 1.0
            else:
                raise ValueError(f"{path}: unexpected units={units!r} for list anchors format")
        else:
            scale = 0.001
        for row in anchors:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label", "")).upper()
            if len(label) != 1:
                continue
            if "x_mm" in row:
                x = float(row.get("x_mm", 0.0)) * 0.001
                y = float(row.get("y_mm", 0.0)) * 0.001
                z = float(row.get("z_mm", 0.0)) * 0.001
            elif "x_m" in row:
                x = float(row.get("x_m", 0.0))
                y = float(row.get("y_m", 0.0))
                z = float(row.get("z_m", 0.0))
            else:
                x = float(row.get("x", 0.0)) * scale
                y = float(row.get("y", 0.0)) * scale
                z = float(row.get("z", 0.0)) * scale
            out[label] = (x, y, z)
        return out

    raise ValueError(f"{path}: unsupported anchors format type={type(anchors)}")


def _format_table(coords: dict[str, tuple[float, float, float]]) -> str:
    lines = []
    lines.append("| Anchor | x_m | y_m | z_m |")
    lines.append("|---|---:|---:|---:|")
    for label in "ABCDEFGH":
        if label not in coords:
            lines.append(f"| {label} | - | - | - |")
            continue
        x, y, z = coords[label]
        lines.append(f"| {label} | {x:.6f} | {y:.6f} | {z:.6f} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Dump one or more anchor layout JSONs into a single Markdown file.")
    ap.add_argument("--out", required=True, help="Output markdown path")
    ap.add_argument("--title", default="Anchor Layout Dump", help="Markdown title")
    ap.add_argument("layouts", nargs="+", help="Layout json paths, optionally as name=path")
    args = ap.parse_args()

    blocks = [f"# {args.title}", ""]
    for item in args.layouts:
        if "=" in item:
            name, p = item.split("=", 1)
            name = name.strip()
            path = Path(p).expanduser().resolve()
        else:
            path = Path(item).expanduser().resolve()
            name = path.name
        coords = _parse_layout(path)
        blocks.append(f"## {name}")
        blocks.append("")
        blocks.append(f"- source: `{path}`")
        blocks.append("")
        blocks.append(_format_table(coords))
        blocks.append("")

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(blocks).rstrip() + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

