#!/usr/bin/env python3
from __future__ import annotations

import argparse
import itertools
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

try:
    from scipy.optimize import least_squares  # type: ignore
except Exception:
    least_squares = None

ANCHORS = tuple("ABCDEFGH")


def parse_cm_line(line: str) -> dict[str, Any] | None:
    if " notify: " not in line or "CM;" not in line:
        return None
    payload = line.split(" notify: ", 1)[1].strip()
    parts = [p.strip() for p in payload.split("|") if p.strip()]
    rows = []
    sweep = None
    for p in parts:
        cols = p.split(";")
        if len(cols) < 10 or cols[0] != "CM":
            continue
        try:
            ver = int(cols[1])
            sw = int(cols[2])
            aid = int(cols[3])
            status = cols[4].strip().lower()
            raw_mm = float(cols[5])
            filt_mm = float(cols[6])
            q = int(cols[7])
            ok_count = int(cols[8])
            fail_count = int(cols[9])
        except Exception:
            continue
        if sweep is None:
            sweep = sw
        rows.append(
            {
                "ver": ver,
                "sweep": sw,
                "anchor_id": aid,
                "status": status,
                "raw_mm": raw_mm,
                "filt_mm": filt_mm,
                "quality": q,
                "ok_count": ok_count,
                "fail_count": fail_count,
            }
        )
    if not rows:
        return None
    return {"sweep": sweep, "rows": rows, "raw": payload}


def parse_cm_runlog(path: Path) -> list[dict[str, Any]]:
    out = []
    for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        rec = parse_cm_line(ln)
        if rec is not None:
            out.append(rec)
    return out


def load_layout_coords_m(path: Path) -> dict[str, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    anchors = raw.get("anchors")
    units = str(raw.get("units") or "m").lower()
    scale = 0.001 if units == "mm" else 1.0

    out: dict[str, np.ndarray] = {}
    if isinstance(anchors, dict):
        for k in ANCHORS:
            v = anchors.get(k)
            if isinstance(v, list) and len(v) >= 3:
                out[k] = np.array([float(v[0]), float(v[1]), float(v[2])], dtype=float) * scale
        return out

    if isinstance(anchors, list):
        for e in anchors:
            if not isinstance(e, dict):
                continue
            lbl = str(e.get("label") or "").strip().upper()
            if lbl not in ANCHORS:
                continue
            if "x_mm" in e and "y_mm" in e and "z_mm" in e:
                out[lbl] = np.array([float(e["x_mm"]), float(e["y_mm"]), float(e["z_mm"])], dtype=float) * 0.001
            else:
                out[lbl] = np.array([float(e.get("x", 0.0)), float(e.get("y", 0.0)), float(e.get("z", 0.0))], dtype=float) * scale
        return out

    raise ValueError(f"unsupported layout format: {path}")


def solve_point_m(pts_m: np.ndarray, d_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x0 = np.mean(pts_m, axis=0)

    def residuals(x: np.ndarray) -> np.ndarray:
        return np.linalg.norm(pts_m - x[None, :], axis=1) - d_m

    if least_squares is not None:
        res = least_squares(residuals, x0=x0, method="trf", max_nfev=200)
        x = np.array(res.x, dtype=float)
        r = residuals(x)
        return x, r

    # Linearized fallback.
    p1 = pts_m[0]
    d1 = d_m[0]
    A = []
    b = []
    for i in range(1, len(pts_m)):
        pi = pts_m[i]
        di = d_m[i]
        A.append(2.0 * (pi - p1))
        b.append((np.dot(pi, pi) - np.dot(p1, p1)) - (di * di - d1 * d1))
    x, *_ = np.linalg.lstsq(np.asarray(A), np.asarray(b), rcond=None)
    x = np.asarray(x, dtype=float)
    r = residuals(x)
    return x, r


def tetra_volume_m3(a: np.ndarray, b: np.ndarray, c: np.ndarray, d: np.ndarray) -> float:
    return float(abs(np.dot((b - a), np.cross((c - a), (d - a)))) / 6.0)


def choose_best_subset_4(
    rows_by_anchor: dict[int, dict[str, Any]],
    coords: dict[str, np.ndarray],
    rolling_quality: dict[int, list[int]],
    lower_ids: set[int],
    upper_ids: set[int],
    min_quality: int,
    quality_window: int,
    volume_min_m3: float,
    volume_max_m3: float,
    require_two_level: bool,
) -> list[dict[str, Any]] | None:
    valid_ids = []
    for aid, row in rows_by_anchor.items():
        if row["status"] != "ok":
            continue
        if row["quality"] < min_quality:
            continue
        if row["filt_mm"] <= 0:
            continue
        label = ANCHORS[aid]
        if label not in coords:
            continue
        valid_ids.append(aid)

    if len(valid_ids) < 4:
        return None

    best_key = None
    best_rows = None
    for comb in itertools.combinations(valid_ids, 4):
        ids = set(comb)
        if require_two_level:
            if len(ids & lower_ids) != 2 or len(ids & upper_ids) != 2:
                continue

        p = [coords[ANCHORS[aid]] for aid in comb]
        vol = tetra_volume_m3(p[0], p[1], p[2], p[3])
        if vol < volume_min_m3 or vol > volume_max_m3:
            continue

        hist_score = 0.0
        for aid in comb:
            hist = rolling_quality.get(aid, [])
            if hist:
                win = hist[-quality_window:]
                hist_score += float(sum(win)) / float(len(win))
            else:
                hist_score += 0.0
        cur_score = float(sum(rows_by_anchor[aid]["quality"] for aid in comb))
        key = (hist_score, cur_score, vol)
        if best_key is None or key > best_key:
            best_key = key
            best_rows = [rows_by_anchor[aid] for aid in comb]

    return best_rows


def eval_layout(
    layout_name: str,
    coords: dict[str, np.ndarray],
    cm_samples: list[dict[str, Any]],
    min_quality: int,
    min_anchors: int,
    quality_window: int,
    volume_min_m3: float,
    volume_max_m3: float,
    require_two_level: bool,
) -> dict[str, Any]:
    pos = []
    per_sample_rms = []
    per_sample_max = []
    all_res_mm = []
    solved = 0
    skipped_no_valid4 = 0
    skipped_geom_filter = 0

    lower_ids = set([0, 1, 2, 3])  # A-D
    upper_ids = set([4, 5, 6, 7])  # E-H
    rolling_quality: dict[int, list[int]] = {i: [] for i in range(8)}

    for sample in cm_samples:
        rows_by_anchor: dict[int, dict[str, Any]] = {}
        for row in sample["rows"]:
            aid = row["anchor_id"]
            if aid < 0 or aid >= 8:
                continue
            rows_by_anchor[aid] = row

        selected_rows = choose_best_subset_4(
            rows_by_anchor=rows_by_anchor,
            coords=coords,
            rolling_quality=rolling_quality,
            lower_ids=lower_ids,
            upper_ids=upper_ids,
            min_quality=min_quality,
            quality_window=quality_window,
            volume_min_m3=volume_min_m3,
            volume_max_m3=volume_max_m3,
            require_two_level=require_two_level,
        )

        # Update rolling quality after selection decision to avoid using current frame's quality as history.
        for aid, row in rows_by_anchor.items():
            q = int(row.get("quality", 0))
            rolling_quality[aid].append(q)
            if len(rolling_quality[aid]) > quality_window:
                rolling_quality[aid] = rolling_quality[aid][-quality_window:]

        if selected_rows is None:
            # Distinguish between "valid points <4" and "geometry/2-level filtered out".
            valid_count = 0
            for row in rows_by_anchor.values():
                aid = row["anchor_id"]
                if aid < 0 or aid >= 8:
                    continue
                if row["status"] == "ok" and row["quality"] >= min_quality and row["filt_mm"] > 0 and ANCHORS[aid] in coords:
                    valid_count += 1
            if valid_count < 4:
                skipped_no_valid4 += 1
            else:
                skipped_geom_filter += 1
            continue

        pts = []
        ds = []
        used = 0
        for row in selected_rows:
            aid = row["anchor_id"]
            if aid < 0 or aid >= 8:
                continue
            label = ANCHORS[aid]
            if label not in coords:
                continue
            if row["status"] != "ok":
                continue
            if row["quality"] < min_quality:
                continue
            d_m = row["filt_mm"] * 0.001
            if d_m <= 0:
                continue
            pts.append(coords[label])
            ds.append(d_m)
            used += 1

        if used < min_anchors:
            continue

        pts_m = np.asarray(pts, dtype=float)
        d_m = np.asarray(ds, dtype=float)
        x_m, r_m = solve_point_m(pts_m, d_m)
        r_mm = r_m * 1000.0

        pos.append(x_m)
        per_sample_rms.append(float(math.sqrt(float(np.mean(r_mm * r_mm)))))
        per_sample_max.append(float(np.max(np.abs(r_mm))))
        all_res_mm.extend(float(v) for v in r_mm.tolist())
        solved += 1

    out: dict[str, Any] = {
        "layout": layout_name,
        "cm_samples_total": len(cm_samples),
        "position_samples": solved,
        "selection": {
            "mode": "best4_non_coplanar",
            "require_two_level": require_two_level,
            "quality_window": quality_window,
            "volume_min_m3": volume_min_m3,
            "volume_max_m3": volume_max_m3,
            "skipped_no_valid4": skipped_no_valid4,
            "skipped_geom_filter": skipped_geom_filter,
        },
    }
    if solved == 0:
        out["error"] = "no solvable samples"
        return out

    P = np.asarray(pos, dtype=float)
    mean_m = np.mean(P, axis=0)
    std_m = np.std(P, axis=0)
    out["position_mean_mm"] = {
        "x": float(mean_m[0] * 1000.0),
        "y": float(mean_m[1] * 1000.0),
        "z": float(mean_m[2] * 1000.0),
    }
    out["position_std_mm"] = {
        "x": float(std_m[0] * 1000.0),
        "y": float(std_m[1] * 1000.0),
        "z": float(std_m[2] * 1000.0),
    }
    out["position_std_3d_mm"] = float(math.sqrt(float(np.sum((std_m * 1000.0) ** 2))))
    out["residual_mean_mm"] = {
        "rms": float(np.mean(per_sample_rms)),
        "max": float(np.mean(per_sample_max)),
    }
    arr = np.asarray(all_res_mm, dtype=float)
    out["residual_global_mm"] = {
        "mean": float(np.mean(arr)),
        "rms": float(math.sqrt(float(np.mean(arr * arr)))),
        "p95_abs": float(np.percentile(np.abs(arr), 95)),
        "max_abs": float(np.max(np.abs(arr))),
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline Tag position RMS from CM run.log across multiple anchor layouts")
    ap.add_argument("--cm-run-log", required=True)
    ap.add_argument("--layout", action="append", required=True, help="NAME=PATH, repeatable")
    ap.add_argument("--min-quality", type=int, default=0)
    ap.add_argument("--min-anchors", type=int, default=4)
    ap.add_argument("--quality-window", type=int, default=10, help="Rolling window size for quality ranking")
    ap.add_argument("--volume-min-m3", type=float, default=1e-6, help="Min tetrahedron volume for selected 4 anchors")
    ap.add_argument("--volume-max-m3", type=float, default=0.1, help="Max tetrahedron volume for selected 4 anchors")
    ap.add_argument("--disable-two-level", action="store_true", help="Disable strict 2 lower + 2 upper anchor selection")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args()

    cm_path = Path(args.cm_run_log)
    samples = parse_cm_runlog(cm_path)
    if not samples:
        raise SystemExit("[error] no CM samples found in run log")

    results = []
    for item in args.layout:
        if "=" not in item:
            raise SystemExit("[error] --layout must be NAME=PATH")
        name, p = item.split("=", 1)
        coords = load_layout_coords_m(Path(p))
        results.append(
            eval_layout(
                name,
                coords,
                samples,
                args.min_quality,
                args.min_anchors,
                args.quality_window,
                args.volume_min_m3,
                args.volume_max_m3,
                not args.disable_two_level,
            )
        )

    payload = {
        "cm_run_log": str(cm_path.resolve()),
        "cm_samples_total": len(samples),
        "min_quality": args.min_quality,
        "min_anchors": args.min_anchors,
        "quality_window": args.quality_window,
        "volume_min_m3": args.volume_min_m3,
        "volume_max_m3": args.volume_max_m3,
        "require_two_level": (not args.disable_two_level),
        "results": results,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = []
    lines.append("# Offline Tag Position RMS Compare")
    lines.append("")
    lines.append(f"- cm_run_log: `{cm_path.resolve()}`")
    lines.append(f"- cm_samples_total: `{len(samples)}`")
    lines.append(f"- min_quality: `{args.min_quality}`")
    lines.append(f"- min_anchors: `{args.min_anchors}`")
    lines.append(f"- quality_window: `{args.quality_window}`")
    lines.append(f"- volume_min_m3: `{args.volume_min_m3}`")
    lines.append(f"- volume_max_m3: `{args.volume_max_m3}`")
    lines.append(f"- require_two_level(2 lower + 2 upper): `{not args.disable_two_level}`")
    lines.append("")
    lines.append("| Layout | solved_samples | pos_std_x(mm) | pos_std_y(mm) | pos_std_z(mm) | pos_std_3d(mm) | residual_mean_rms(mm) | residual_global_rms(mm) |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")

    for r in results:
        if "error" in r:
            lines.append(f"| {r['layout']} | 0 | - | - | - | - | - | - |")
            continue
        s = r["position_std_mm"]
        lines.append(
            f"| {r['layout']} | {r['position_samples']} | {s['x']:.3f} | {s['y']:.3f} | {s['z']:.3f} | {r['position_std_3d_mm']:.3f} | {r['residual_mean_mm']['rms']:.3f} | {r['residual_global_mm']['rms']:.3f} |"
        )
    lines.append("")
    lines.append("## Selection Filter Stats")
    lines.append("")
    lines.append("| Layout | skipped_no_valid4 | skipped_geom_filter |")
    lines.append("|---|---:|---:|")
    for r in results:
        sel = r.get("selection", {})
        lines.append(
            f"| {r['layout']} | {int(sel.get('skipped_no_valid4', 0))} | {int(sel.get('skipped_geom_filter', 0))} |"
        )

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[ok] wrote {out_json}")
    print(f"[ok] wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
