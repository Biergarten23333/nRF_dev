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

try:
    from scipy.optimize import least_squares  # type: ignore
except Exception:
    least_squares = None

ANCHORS = "ABCDEFGH"


def load_layout_coords_mm(path: Path) -> dict[int, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors = raw.get("anchors")
    units = str(raw.get("units") or "m").lower()
    scale = 1.0 if units == "mm" else 1000.0
    out: dict[int, np.ndarray] = {}
    if isinstance(anchors, list):
        for e in anchors:
            if not isinstance(e, dict):
                continue
            label = str(e.get("label") or "").strip().upper()
            if label not in ANCHORS:
                continue
            aid = ANCHORS.index(label)
            if "x_mm" in e and "y_mm" in e and "z_mm" in e:
                out[aid] = np.array([float(e["x_mm"]), float(e["y_mm"]), float(e["z_mm"])], dtype=float)
            else:
                out[aid] = np.array(
                    [
                        float(e.get("x", 0.0)) * scale,
                        float(e.get("y", 0.0)) * scale,
                        float(e.get("z", 0.0)) * scale,
                    ],
                    dtype=float,
                )
            continue
    elif isinstance(anchors, dict):
        for label, xyz in anchors.items():
            label = str(label).strip().upper()
            if label not in ANCHORS or not isinstance(xyz, list) or len(xyz) < 3:
                continue
            aid = ANCHORS.index(label)
            out[aid] = np.array([float(xyz[0]) * scale, float(xyz[1]) * scale, float(xyz[2]) * scale], dtype=float)
    if len(out) < 4:
        raise SystemExit(f"[error] layout has only {len(out)} anchors")
    return out


def solve_pos_mm(anchor_pts_mm: np.ndarray, ranges_mm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x0 = np.mean(anchor_pts_mm, axis=0)

    def residuals(x: np.ndarray) -> np.ndarray:
        return np.linalg.norm(anchor_pts_mm - x[None, :], axis=1) - ranges_mm

    if least_squares is not None:
        res = least_squares(residuals, x0=x0, method="trf", max_nfev=200)
        x = np.asarray(res.x, dtype=float)
        r = residuals(x)
        return x, r

    p1 = anchor_pts_mm[0]
    d1 = ranges_mm[0]
    A = []
    b = []
    for i in range(1, len(anchor_pts_mm)):
        pi = anchor_pts_mm[i]
        di = ranges_mm[i]
        A.append(2.0 * (pi - p1))
        b.append((np.dot(pi, pi) - np.dot(p1, p1)) - (di * di - d1 * d1))
    x, *_ = np.linalg.lstsq(np.asarray(A), np.asarray(b), rcond=None)
    x = np.asarray(x, dtype=float)
    return x, residuals(x)


def fit_circle_2d(points_uv_mm: np.ndarray) -> dict[str, float]:
    c0 = np.mean(points_uv_mm, axis=0)
    r0 = float(np.mean(np.linalg.norm(points_uv_mm - c0[None, :], axis=1)))

    def residuals(v: np.ndarray) -> np.ndarray:
        cu, cv, r = v
        d = np.linalg.norm(points_uv_mm - np.array([[cu, cv]], dtype=float), axis=1)
        return d - r

    if least_squares is not None:
        res = least_squares(residuals, x0=np.array([c0[0], c0[1], r0], dtype=float), method="trf", max_nfev=200)
        cu, cv, r = [float(v) for v in res.x]
        rr = residuals(res.x)
    else:
        cu, cv, r = float(c0[0]), float(c0[1]), float(r0)
        rr = residuals(np.array([cu, cv, r], dtype=float))

    return {
        "center_u_mm": cu,
        "center_v_mm": cv,
        "radius_mm": abs(float(r)),
        "radial_rms_mm": float(math.sqrt(float(np.mean(rr * rr)))) if len(rr) else 0.0,
        "radial_p95_abs_mm": float(np.percentile(np.abs(rr), 95)) if len(rr) else 0.0,
    }


def fit_circle_3d(points_xyz_mm: np.ndarray) -> dict[str, Any]:
    if len(points_xyz_mm) < 3:
        raise ValueError("need at least 3 points for 3D circle fit")

    center0 = np.mean(points_xyz_mm, axis=0)
    centered = points_xyz_mm - center0[None, :]
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    basis_u = vh[0]
    basis_v = vh[1]
    normal = vh[2]

    uv = np.stack(
        [
            centered @ basis_u,
            centered @ basis_v,
        ],
        axis=1,
    )
    fit2 = fit_circle_2d(uv)
    center3 = center0 + fit2["center_u_mm"] * basis_u + fit2["center_v_mm"] * basis_v
    radial_dist = np.linalg.norm(uv - np.array([[fit2["center_u_mm"], fit2["center_v_mm"]]], dtype=float), axis=1)
    plane_offset = centered @ normal

    return {
        "center_x_mm": float(center3[0]),
        "center_y_mm": float(center3[1]),
        "center_z_mm": float(center3[2]),
        "radius_mm": float(fit2["radius_mm"]),
        "radial_rms_mm": float(fit2["radial_rms_mm"]),
        "radial_p95_abs_mm": float(fit2["radial_p95_abs_mm"]),
        "plane_normal": {
            "x": float(normal[0]),
            "y": float(normal[1]),
            "z": float(normal[2]),
        },
        "plane_rms_mm": float(math.sqrt(float(np.mean(plane_offset * plane_offset)))),
        "plane_p95_abs_mm": float(np.percentile(np.abs(plane_offset), 95)),
        "radial_distance_mean_mm": float(np.mean(radial_dist)),
    }


def load_cm_rows(path: Path) -> dict[int, dict[int, dict[str, Any]]]:
    by_sweep: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                sweep = int(row["sweep"])
                aid = int(row["anchor_id"])
                quality = int(row["quality_percent"])
                filt_mm = float(row["filt_mm"])
            except Exception:
                continue
            if row.get("status", "").strip().lower() != "ok":
                continue
            if filt_mm <= 0:
                continue
            prev = by_sweep[sweep].get(aid)
            if prev is None or quality > int(prev["quality_percent"]):
                by_sweep[sweep][aid] = row
    return by_sweep


def analyze_tag(cm_csv: Path, layout_mm: dict[int, np.ndarray], mode: str) -> dict[str, Any]:
    grouped = load_cm_rows(cm_csv)
    solved = []
    per_sample_rms = []
    per_sample_max = []
    used_anchor_counts = []

    def append_solution(sample_id: int, rows_by_anchor: dict[int, dict[str, Any]]) -> None:
        aids = [aid for aid in sorted(rows_by_anchor) if aid in layout_mm]
        if len(aids) < 4:
            return
        pts = np.asarray([layout_mm[aid] for aid in aids], dtype=float)
        ds = np.asarray([float(rows_by_anchor[aid]["filt_mm"]) for aid in aids], dtype=float)
        pos_mm, res_mm = solve_pos_mm(pts, ds)
        solved.append(
            {
                "sweep": sample_id,
                "x_mm": float(pos_mm[0]),
                "y_mm": float(pos_mm[1]),
                "z_mm": float(pos_mm[2]),
            }
        )
        per_sample_rms.append(float(math.sqrt(float(np.mean(res_mm * res_mm)))))
        per_sample_max.append(float(np.max(np.abs(res_mm))))
        used_anchor_counts.append(len(aids))

    if mode == "static":
        acc: dict[int, dict[str, Any]] = {}
        start_sweep = None
        last_sweep = None
        for sweep, rows_by_anchor in sorted(grouped.items()):
            if start_sweep is None:
                start_sweep = sweep
            if last_sweep is not None and (sweep - last_sweep) > 8 and len(acc) >= 4:
                append_solution(start_sweep, acc)
                acc = {}
                start_sweep = sweep
            for aid, row in rows_by_anchor.items():
                prev = acc.get(aid)
                if prev is None or int(row["quality_percent"]) >= int(prev["quality_percent"]):
                    acc[aid] = row
            if len(acc) >= 4:
                append_solution(start_sweep, acc)
                acc = {}
                start_sweep = None
            last_sweep = sweep
        if len(acc) >= 4 and start_sweep is not None:
            append_solution(start_sweep, acc)
    else:
        for sweep, rows_by_anchor in sorted(grouped.items()):
            append_solution(sweep, rows_by_anchor)

    out: dict[str, Any] = {
        "cm_csv": str(cm_csv.resolve()),
        "mode": mode,
        "cm_sweeps_total": len(grouped),
        "position_samples": len(solved),
    }
    if not solved:
        out["error"] = "no position samples"
        return out

    P = np.asarray([[r["x_mm"], r["y_mm"], r["z_mm"]] for r in solved], dtype=float)
    center = np.mean(P, axis=0)
    centered = P - center[None, :]
    dist3 = np.linalg.norm(centered, axis=1)
    out["position_mean_mm"] = {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])}
    out["position_std_mm"] = {
        "x": float(np.std(P[:, 0])),
        "y": float(np.std(P[:, 1])),
        "z": float(np.std(P[:, 2])),
    }
    out["solve_residual_mm"] = {
        "mean_rms": float(np.mean(per_sample_rms)),
        "p95_rms": float(np.percentile(per_sample_rms, 95)),
        "mean_max_abs": float(np.mean(per_sample_max)),
    }
    out["anchors_per_solution"] = {
        "mean": float(np.mean(used_anchor_counts)),
        "min": int(min(used_anchor_counts)),
        "max": int(max(used_anchor_counts)),
    }
    out["samples_csv"] = solved

    if mode == "static":
        out["static_rms_mm"] = float(math.sqrt(float(np.mean(dist3 * dist3))))
        out["static_p95_3d_mm"] = float(np.percentile(dist3, 95))
        return out

    circle = fit_circle_3d(P)
    radial_dist = np.linalg.norm(P - np.array([[circle["center_x_mm"], circle["center_y_mm"], circle["center_z_mm"]]], dtype=float), axis=1)
    out["circle_fit_3d"] = circle
    out["z_mean_mm"] = float(np.mean(P[:, 2]))
    out["z_std_mm"] = float(np.std(P[:, 2]))
    out["radius_mm"] = float(circle["radius_mm"])
    out["radius_p95_abs_mm"] = float(np.percentile(np.abs(radial_dist - circle["radius_mm"]), 95))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze a recv_tdma_capture session with a solved anchor layout.")
    ap.add_argument("--session-dir", required=True)
    ap.add_argument("--layout-json", required=True)
    ap.add_argument("--static-tag", default="BSF66F")
    ap.add_argument("--roto-tag", action="append", default=[])
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    session_dir = Path(args.session_dir)
    layout_mm = load_layout_coords_mm(Path(args.layout_json))

    targets: list[tuple[str, str]] = [(args.static_tag, "static")]
    for tag in args.roto_tag:
        targets.append((tag, "roto"))

    results: dict[str, Any] = {}
    for tag, mode in targets:
        cm_csv = session_dir / tag / "cm.csv"
        if not cm_csv.exists():
            results[tag] = {"error": f"missing {cm_csv}"}
            continue
        results[tag] = analyze_tag(cm_csv, layout_mm, mode)

    payload = {
        "session_dir": str(session_dir.resolve()),
        "layout_json": str(Path(args.layout_json).resolve()),
        "results": results,
    }
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# RECV TDMA Session Analysis",
        "",
        f"- session_dir: `{session_dir.resolve()}`",
        f"- layout_json: `{Path(args.layout_json).resolve()}`",
        "",
        "| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |",
        "|---|---:|---:|---:|---:|",
    ]
    for tag, result in results.items():
        if "error" in result:
            lines.append(f"| {tag} | - | 0 | - | error |")
            continue
        metric = f"{result['static_rms_mm']:.2f} RMS" if result["mode"] == "static" else f"{result['radius_mm']:.2f} radius"
        lines.append(
            f"| {tag} | {result['mode']} | {result['position_samples']} | {result['solve_residual_mm']['mean_rms']:.2f} | {metric} |"
        )
    lines.append("")
    for tag, result in results.items():
        lines.append(f"## {tag}")
        lines.append("")
        if "error" in result:
            lines.append(f"- error: `{result['error']}`")
            lines.append("")
            continue
        lines.append(f"- mode: `{result['mode']}`")
        lines.append(f"- position_samples: `{result['position_samples']}`")
        lines.append(f"- position_mean_mm: x=`{result['position_mean_mm']['x']:.2f}` y=`{result['position_mean_mm']['y']:.2f}` z=`{result['position_mean_mm']['z']:.2f}`")
        lines.append(f"- solve_residual_mean_rms_mm: `{result['solve_residual_mm']['mean_rms']:.2f}`")
        if result["mode"] == "static":
            lines.append(f"- static_rms_mm: `{result['static_rms_mm']:.2f}`")
            lines.append(f"- static_p95_3d_mm: `{result['static_p95_3d_mm']:.2f}`")
        else:
            lines.append(f"- radius_mm: `{result['radius_mm']:.2f}`")
            lines.append(
                f"- circle_center_3d_mm: x=`{result['circle_fit_3d']['center_x_mm']:.2f}` "
                f"y=`{result['circle_fit_3d']['center_y_mm']:.2f}` z=`{result['circle_fit_3d']['center_z_mm']:.2f}`"
            )
            lines.append(
                f"- plane_normal: x=`{result['circle_fit_3d']['plane_normal']['x']:.4f}` "
                f"y=`{result['circle_fit_3d']['plane_normal']['y']:.4f}` "
                f"z=`{result['circle_fit_3d']['plane_normal']['z']:.4f}`"
            )
            lines.append(f"- plane_rms_mm: `{result['circle_fit_3d']['plane_rms_mm']:.2f}`")
            lines.append(f"- radial_rms_mm: `{result['circle_fit_3d']['radial_rms_mm']:.2f}`")
            lines.append(f"- z_std_mm: `{result['z_std_mm']:.2f}`")
        lines.append("")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_json}")
    print(f"[ok] wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
