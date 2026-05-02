#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


ANCHORS = tuple("ABCDEFGH")


def load_layout(path: Path) -> dict[str, list[float]]:
    """
    Accept common layout formats in this repo:
    - {"anchors": {"A":[x,y,z], ...}, "units":"m|mm"}
    - {"anchors": [{"label":"A","x_mm":..,"y_mm":..,"z_mm":..}, ...], "units":"mm"}
    - {"A":[x,y,z], ...}
    """
    raw: Any = json.loads(path.read_text(encoding="utf-8"))

    def norm_units(scale: float, m: dict[str, list[float]]) -> dict[str, list[float]]:
        out: dict[str, list[float]] = {}
        for k, v in m.items():
            if k in ANCHORS and isinstance(v, list) and len(v) >= 2:
                out[k] = [float(v[0]) * scale, float(v[1]) * scale, float(v[2] if len(v) > 2 else 0.0) * scale]
        return out

    if isinstance(raw, dict) and all(k in raw for k in ANCHORS):
        # Direct map A..H
        return norm_units(1.0, raw)

    units = "m"
    if isinstance(raw, dict):
        units = raw.get("units") or units

    scale = 0.001 if str(units).lower() == "mm" else 1.0
    anchors_raw = raw.get("anchors") if isinstance(raw, dict) else None
    if isinstance(anchors_raw, dict):
        return norm_units(scale, anchors_raw)
    if isinstance(anchors_raw, list):
        out: dict[str, list[float]] = {}
        for e in anchors_raw:
            if not isinstance(e, dict):
                continue
            lbl = str(e.get("label") or "").strip().upper()
            if lbl not in ANCHORS:
                continue
            # prefer *_mm keys
            if "x_mm" in e:
                out[lbl] = [float(e["x_mm"]) * 0.001, float(e["y_mm"]) * 0.001, float(e.get("z_mm", 0.0)) * 0.001]
            else:
                out[lbl] = [float(e.get("x", 0.0)) * scale, float(e.get("y", 0.0)) * scale, float(e.get("z", 0.0)) * scale]
        return out

    raise ValueError(f"Unsupported layout format: {path}")


def kabsch_rigid_align(a: dict[str, list[float]], b: dict[str, list[float]]) -> tuple[dict[str, list[float]], dict[str, float]]:
    """
    Rigid-align a->b using Kabsch (no scaling). Returns a_aligned and summary metrics.
    """
    keys = [k for k in ANCHORS if k in a and k in b]
    if len(keys) < 3:
        raise ValueError("Need >=3 anchors to align")

    import numpy as np

    A = np.array([a[k] for k in keys], dtype=float)
    B = np.array([b[k] for k in keys], dtype=float)

    ca = A.mean(axis=0)
    cb = B.mean(axis=0)
    A0 = A - ca
    B0 = B - cb

    H = A0.T @ B0
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    # Fix improper rotation (reflection)
    if np.linalg.det(R) < 0:
        Vt[-1, :] *= -1
        R = Vt.T @ U.T

    def apply(p):
        return ((np.array(p, dtype=float) - ca) @ R) + cb

    aligned: dict[str, list[float]] = {k: [float(x) for x in apply(a[k])] for k in a.keys() if k in ANCHORS}

    # Metrics
    errs = []
    per_anchor = {}
    for k in keys:
        dx = aligned[k][0] - b[k][0]
        dy = aligned[k][1] - b[k][1]
        dz = aligned[k][2] - b[k][2]
        e = math.sqrt(dx * dx + dy * dy + dz * dz)
        errs.append(e)
        per_anchor[k] = e

    rms = math.sqrt(sum(e * e for e in errs) / len(errs))
    mx = max(errs) if errs else 0.0
    return aligned, {"rms_m": rms, "max_m": mx, "n": float(len(errs)), **{f"err_{k}_m": per_anchor[k] for k in per_anchor}}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare V1/V2/V3 layouts with rigid alignment (Kabsch).")
    ap.add_argument("--v1", required=True, help="V1 layout json")
    ap.add_argument("--v2", required=True, help="V2 layout json")
    ap.add_argument("--v3", required=True, help="V3 layout json")
    ap.add_argument("--out", required=True, help="Output markdown path")
    args = ap.parse_args()

    v1 = load_layout(Path(args.v1))
    v2 = load_layout(Path(args.v2))
    v3 = load_layout(Path(args.v3))

    _, m21 = kabsch_rigid_align(v2, v1)  # align v2 onto v1
    _, m31 = kabsch_rigid_align(v3, v1)  # align v3 onto v1
    _, m32 = kabsch_rigid_align(v3, v2)  # align v3 onto v2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# AutoPos V1 / V2 / V3 Layout Compare (Rigid-Aligned)")
    lines.append("")
    lines.append(f"- V1: `{Path(args.v1).resolve()}`")
    lines.append(f"- V2: `{Path(args.v2).resolve()}`")
    lines.append(f"- V3: `{Path(args.v3).resolve()}`")
    lines.append("")
    lines.append("## Summary (meters)")
    lines.append("")
    lines.append("| Compare | n | rms | max |")
    lines.append("|---|---:|---:|---:|")
    lines.append(f"| V2 aligned->V1 | {int(m21['n'])} | {m21['rms_m']:.6f} | {m21['max_m']:.6f} |")
    lines.append(f"| V3 aligned->V1 | {int(m31['n'])} | {m31['rms_m']:.6f} | {m31['max_m']:.6f} |")
    lines.append(f"| V3 aligned->V2 | {int(m32['n'])} | {m32['rms_m']:.6f} | {m32['max_m']:.6f} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("- Uses rigid alignment (translate + rotate), no scaling.")
    lines.append("- RMS/max are across anchors A..H that exist in both layouts.")
    lines.append("")

    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

