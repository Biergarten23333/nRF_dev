#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.optimize import least_squares  # type: ignore
except Exception:  # pragma: no cover
    least_squares = None


ANCHORS = tuple("ABCDEFGH")
ANCHOR_ID_TO_LABEL = {i: ANCHORS[i] for i in range(8)}


def load_layout_coords_m(path: Path) -> dict[str, np.ndarray]:
    """
    Load anchor coordinates and return meters.

    Supported formats:
    - V1: {"anchors": {"A":[x,y,z],...}, "units":"mm"|"m"}
    - Solver output: {"anchors":[{"label":"A","x_mm":...,"y_mm":...,"z_mm":...},...], "units":"m"|"mm"}
    - Solver output dict: {"anchors":{"A":[x,y,z],...}, "units":...}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors_raw = raw.get("anchors")
    units = (raw.get("units") or "m").lower()
    scale = 0.001 if units == "mm" else 1.0

    out: dict[str, np.ndarray] = {}

    if isinstance(anchors_raw, dict):
        for k, v in anchors_raw.items():
            if k not in ANCHORS:
                continue
            out[k] = np.array(v, dtype=float) * scale
        return out

    if isinstance(anchors_raw, list):
        for ent in anchors_raw:
            label = ent.get("label")
            if label not in ANCHORS:
                continue
            # Most repo layouts use *_mm fields.
            if "x_mm" in ent and "y_mm" in ent and "z_mm" in ent:
                out[label] = np.array(
                    [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])],
                    dtype=float,
                ) * 0.001
            else:
                # Fallback: already meters.
                out[label] = np.array(
                    [float(ent["x"]), float(ent["y"]), float(ent["z"])],
                    dtype=float,
                )
        return out

    raise ValueError(f"Unsupported layout format: {path}")


def load_distances_m(path: Path) -> dict[tuple[str, str], float]:
    """
    Load measured inter-anchor distances in meters.

    Supported:
    - inter_anchor_matrix*.json: {"distances": {"A-B": 1234, ...}, "units":"mm"}
    - fused CSV (final_pair_distances*.csv): columns a,b,distance_mm (or dist_mm)
    - raw pairs_all.csv: a,b,master,dist_mm repeated; we average per pair.
    """
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        units = (raw.get("units") or "mm").lower()
        scale = 0.001 if units == "mm" else 1.0
        dist_raw = raw.get("distances") or {}
        out: dict[tuple[str, str], float] = {}
        for key, v in dist_raw.items():
            if not isinstance(key, str) or "-" not in key:
                continue
            a, b = key.split("-", 1)
            a = a.strip().upper()
            b = b.strip().upper()
            if a not in ANCHORS or b not in ANCHORS or a == b:
                continue
            try:
                d = float(v) * scale
            except Exception:
                continue
            # Treat 0 as missing. Some capture paths encode "no ranging" as 0.
            if d <= 0:
                continue
            out[(a, b)] = d
        return out

    # CSV
    out_lists: dict[tuple[str, str], list[float]] = {}
    with path.open(encoding="utf-8") as f:
        r = csv.DictReader(f)
        # pick best distance column
        cols = [c.lower() for c in (r.fieldnames or [])]
        def has(col: str) -> bool:
            return col in cols

        dist_col = None
        for cand in ["distance_mm", "dist_mm", "filt_mm", "raw_mm", "filt"]:
            if has(cand):
                dist_col = cand
                break
        if dist_col is None:
            raise ValueError(f"No distance column found in {path}")

        # Map to actual fieldname (case preserving)
        field_map = {c.lower(): c for c in (r.fieldnames or [])}
        dist_field = field_map[dist_col]
        a_field = field_map.get("a") or "a"
        b_field = field_map.get("b") or "b"

        for row in r:
            a = (row.get(a_field) or "").strip().upper()
            b = (row.get(b_field) or "").strip().upper()
            if a not in ANCHORS or b not in ANCHORS or a == b:
                continue
            d_raw = row.get(dist_field)
            if d_raw is None or str(d_raw).strip() == "":
                continue
            try:
                d_mm = float(d_raw)
            except Exception:
                continue
            if d_mm <= 0:
                continue
            key = (a, b) if a < b else (b, a)
            out_lists.setdefault(key, []).append(d_mm * 0.001)

    # average if multiple samples per pair
    out: dict[tuple[str, str], float] = {}
    for k, vals in out_lists.items():
        if vals:
            out[k] = float(sum(vals) / len(vals))
    return out


def eval_distance_residuals(layout_m: dict[str, np.ndarray], distances_m: dict[tuple[str, str], float]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    abs_errs_mm: list[float] = []
    errs_mm: list[float] = []

    for (a, b), d_meas_m in distances_m.items():
        if a not in layout_m or b not in layout_m:
            continue
        pred_m = float(np.linalg.norm(layout_m[a] - layout_m[b]))
        err_mm = (pred_m - d_meas_m) * 1000.0
        rows.append(
            {
                "a": a,
                "b": b,
                "meas_mm": d_meas_m * 1000.0,
                "pred_mm": pred_m * 1000.0,
                "err_mm": err_mm,
                "abs_err_mm": abs(err_mm),
            }
        )
        errs_mm.append(err_mm)
        abs_errs_mm.append(abs(err_mm))

    if not abs_errs_mm:
        return {"pair_count": 0}

    abs_sorted = sorted(abs_errs_mm)
    rms = math.sqrt(float(sum(e * e for e in errs_mm) / len(errs_mm)))
    out = {
        "pair_count": len(abs_errs_mm),
        "rms_err_mm": rms,
        "max_abs_err_mm": max(abs_errs_mm),
        "p50_abs_err_mm": abs_sorted[len(abs_sorted) // 2],
        "p90_abs_err_mm": abs_sorted[int(len(abs_sorted) * 0.9)],
        "worst_pairs": sorted(rows, key=lambda r: r["abs_err_mm"], reverse=True)[:8],
    }
    return out


def load_ranges_mean_m(session_dir: Path) -> dict[str, float]:
    ranges_path = session_dir / "ranges.csv"
    if not ranges_path.exists():
        raise FileNotFoundError(ranges_path)
    by_anchor: dict[str, list[float]] = {}
    with ranges_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            aid_raw = row.get("anchor_id")
            if aid_raw is None:
                continue
            try:
                aid = int(aid_raw)
            except Exception:
                continue
            label = ANCHOR_ID_TO_LABEL.get(aid)
            if not label:
                continue
            d_raw = row.get("filt_mm") or row.get("raw_mm")
            if d_raw is None or str(d_raw).strip() == "":
                continue
            try:
                d_m = float(d_raw) * 0.001
            except Exception:
                continue
            by_anchor.setdefault(label, []).append(d_m)

    means: dict[str, float] = {}
    for a, vals in by_anchor.items():
        if vals:
            means[a] = float(sum(vals) / len(vals))
    return means


def fit_floating_tag(layout_m: dict[str, np.ndarray], ranges_mean_m: dict[str, float]) -> dict[str, Any]:
    if least_squares is None:
        return {"ok": False, "reason": "scipy_not_available"}

    anchors = [a for a in ANCHORS if a in layout_m and a in ranges_mean_m]
    if len(anchors) < 4:
        return {"ok": False, "reason": f"need>=4 anchors, got {len(anchors)}"}

    P = np.stack([layout_m[a] for a in anchors], axis=0)
    d = np.array([ranges_mean_m[a] for a in anchors], dtype=float)

    x0 = np.mean(P, axis=0)
    x0[2] = max(float(np.min(P[:, 2]) + 0.5), 0.3)

    def fun(x: np.ndarray) -> np.ndarray:
        pred = np.linalg.norm(P - x[None, :], axis=1)
        return (pred - d) * 1000.0  # mm residual

    res = least_squares(fun, x0=x0, method="trf", max_nfev=200)
    r = fun(res.x)
    abs_r = np.abs(r)
    return {
        "ok": True,
        "anchors_used": anchors,
        "tag_xyz_m": [float(res.x[0]), float(res.x[1]), float(res.x[2])],
        "rms_mm": float(math.sqrt(float(np.mean(r * r)))),
        "max_abs_mm": float(np.max(abs_r)),
        "p50_abs_mm": float(np.median(abs_r)),
        "cost": float(res.cost),
        "status": int(res.status),
        "success": bool(res.success),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Evaluate AutoPos layout quality vs measured inter-anchor distances, optionally using Tag CM ranges as floating reference."
    )
    ap.add_argument("--distances", required=True, help="CSV or JSON containing inter-anchor distances")
    ap.add_argument(
        "--layout",
        action="append",
        required=True,
        help="Repeatable: NAME=PATH (e.g. V1=.../anchor_coords_v1.json)",
    )
    ap.add_argument("--floating-ref-session", default=None, help="Directory containing ranges.csv (Tag115 CM converted)")
    ap.add_argument("--out-json", default=None)
    ap.add_argument("--out-md", default=None)
    args = ap.parse_args()

    distances_m = load_distances_m(Path(args.distances))

    ranges_mean = None
    if args.floating_ref_session:
        ranges_mean = load_ranges_mean_m(Path(args.floating_ref_session))

    results: dict[str, Any] = {
        "distances_path": str(Path(args.distances).resolve()),
        "pair_count_available": len(distances_m),
        "floating_ref_session": str(Path(args.floating_ref_session).resolve()) if args.floating_ref_session else None,
        "layouts": {},
    }

    for item in args.layout:
        if "=" not in item:
            raise SystemExit("--layout must be NAME=PATH")
        name, p = item.split("=", 1)
        path = Path(p)
        coords = load_layout_coords_m(path)
        entry: dict[str, Any] = {
            "path": str(path.resolve()),
            "anchor_count": len(coords),
            "distance_fit": eval_distance_residuals(coords, distances_m),
        }
        if ranges_mean is not None:
            entry["floating_tag_fit"] = fit_floating_tag(coords, ranges_mean)
        results["layouts"][name] = entry

    if args.out_json:
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")

    if args.out_md:
        out_md = Path(args.out_md)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        lines.append("# AutoPos Layout Quality Report")
        lines.append("")
        lines.append(f"- Distances: `{results['distances_path']}`")
        lines.append(f"- Pair count available: `{results['pair_count_available']}`")
        if results["floating_ref_session"]:
            lines.append(f"- Floating ref session: `{results['floating_ref_session']}`")
        lines.append("")
        for name, e in results["layouts"].items():
            df = e["distance_fit"]
            lines.append(f"## {name}")
            lines.append(f"- Layout: `{e['path']}`")
            lines.append(f"- Anchors: `{e['anchor_count']}`")
            if df.get("pair_count", 0) > 0:
                lines.append(f"- Distance fit: pairs={df['pair_count']} rms={df['rms_err_mm']:.2f}mm max={df['max_abs_err_mm']:.2f}mm p50={df['p50_abs_err_mm']:.2f}mm p90={df['p90_abs_err_mm']:.2f}mm")
            else:
                lines.append("- Distance fit: no pairs matched")
            ft = e.get("floating_tag_fit")
            if ft:
                if ft.get("ok"):
                    lines.append(f"- Floating tag fit: rms={ft['rms_mm']:.2f}mm max={ft['max_abs_mm']:.2f}mm anchors_used={len(ft['anchors_used'])}")
                else:
                    lines.append(f"- Floating tag fit: skipped ({ft.get('reason')})")
            # Worst pairs
            wp = df.get("worst_pairs") or []
            if wp:
                lines.append("- Worst pairs (abs err mm):")
                for r in wp:
                    lines.append(f"  - {r['a']}-{r['b']}: {r['abs_err_mm']:.2f} (pred={r['pred_mm']:.1f} meas={r['meas_mm']:.1f})")
            lines.append("")
        out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
