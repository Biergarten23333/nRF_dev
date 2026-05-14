#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import math
import re
from types import SimpleNamespace
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares


REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "autopos_pipeline" / "outdoor_20260513"
OUT = DATA / "FULL-COMPARE"
ANALYSIS = DATA / "analysis_20260513_182053" / "run_full_evaluation_same_pipeline_20260513.py"
ANCHORS = "ABCDEFGH"

VERSION_MAP = {
    "v1": "AutoPos V1",
    "v2": "AutoPos V2",
    "v3lite": "V3-lite",
    "v3full": "V3-full",
    "v4": "V4-interonly",
}

MAX_STATIC_FRAMES_PER_PEER = 150
MAX_ROTO_FRAMES_PER_PEER = 180


def load_eval_module():
    spec = importlib.util.spec_from_file_location("same_pipeline_eval", ANALYSIS)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ANALYSIS}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for k in row:
                if k not in fields:
                    fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def latest_dirs(base: Path, prefix: str) -> dict[str, Path]:
    pat = re.compile(rf"^({prefix}\d+)(?:_|$)")
    out: dict[str, Path] = {}
    for p in sorted(base.iterdir()) if base.exists() else []:
        if not p.is_dir():
            continue
        m = pat.match(p.name)
        if m and (p / "tr_all.csv").exists():
            out[m.group(1)] = p
    return out


def load_frames_by_peer(path: Path) -> dict[str, list[list[tuple[int, float]]]]:
    grouped: dict[tuple[str, int], list[tuple[int, float]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                if int(float(row.get("valid", "0") or 0)) != 1:
                    continue
                if (row.get("status") or "") not in {"", "O"}:
                    continue
                aid = int(float(row["anchor_id"]))
                rng = float(row.get("range_mm") or row.get("raw_mm"))
                sweep = int(float(row["sweep"]))
                peer = (row.get("peer_name") or "unknown").strip() or "unknown"
            except Exception:
                continue
            if 0 <= aid < 8 and rng > 0:
                grouped[(peer, sweep)].append((aid, rng))
    by_peer: dict[str, list[list[tuple[int, float]]]] = defaultdict(list)
    for (peer, _sweep), obs in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(obs) >= 4:
            by_peer[peer].append(obs)
    return by_peer


def eval_peer_frames(mod, frames, x, dly, anchor_ids):
    return mod.eval_positioning(frames, x, dly, anchor_ids)


def downsample_frames(frames, limit: int):
    if len(frames) <= limit:
        return frames
    idx = np.linspace(0, len(frames) - 1, limit).round().astype(int)
    return [frames[int(i)] for i in idx]


def solve_positions(mod, frames, x, dly, anchor_ids):
    global_xyz, global_delay = mod.to_global_layout(x, dly, anchor_ids)
    active = set(anchor_ids)
    positions = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        pos = mod.solve_position_weighted(obs, global_xyz, global_delay, last)
        positions.append(pos)
        last = pos
    return np.asarray(positions, dtype=float)


def solve_position_unweighted(obs, global_xyz, global_delay, x0=None):
    if x0 is None:
        x0 = np.nanmean([global_xyz[a] for a, _r in obs], axis=0)

    def fun(p):
        out = []
        for a, r in obs:
            pred = np.linalg.norm(p - global_xyz[a]) + (0.0 if np.isnan(global_delay[a]) else global_delay[a])
            out.append(pred - r)
        return np.asarray(out)

    result = least_squares(fun, x0, loss="linear", max_nfev=100)
    return result.x


def solve_positions_unweighted(mod, frames, x, dly, anchor_ids):
    global_xyz, global_delay = mod.to_global_layout(x, dly, anchor_ids)
    active = set(anchor_ids)
    positions = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        pos = solve_position_unweighted(obs, global_xyz, global_delay, last)
        positions.append(pos)
        last = pos
    return np.asarray(positions, dtype=float)


def solve_archive_v1_classical_mds(mod, pair_dists, anchor_ids):
    """Archive V1 baseline: V1 fused distances + classical MDS only.

    This deliberately avoids the newer MDS+NLS refinement and delay-aware
    evaluator so V1 remains a true early baseline.
    """
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    x = mod.mds_init(lp, len(anchor_ids))
    dly = np.zeros(len(anchor_ids), dtype=float)
    res = SimpleNamespace(success=True)
    extra = {"implementation": "archive_v1_classical_mds_only", "delay_aware": False}
    return x, dly, res, extra


def fit_circle_3d(points: np.ndarray) -> dict:
    pts = np.asarray(points, dtype=float)
    if pts.shape[0] < 20:
        return {"N": int(pts.shape[0]), "status": "insufficient"}
    center0 = np.mean(pts, axis=0)
    u, s, vh = np.linalg.svd(pts - center0, full_matrices=False)
    normal = vh[-1]
    e1, e2 = vh[0], vh[1]
    uv = np.column_stack([(pts - center0) @ e1, (pts - center0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    A = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    radius = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - radius
    zplane = (pts - center0) @ normal
    total = np.sqrt(radial * radial + zplane * zplane)
    return {
        "N": int(pts.shape[0]),
        "status": "ok",
        "radius": float(radius),
        "radial_std": float(np.std(radial, ddof=1)),
        "z_plane_std": float(np.std(zplane, ddof=1)),
        "circle_3d_std": float(np.std(total, ddof=1)),
        "circle_3d_rms": float(np.sqrt(np.mean(total * total))),
        "plane_tilt_deg": float(np.degrees(np.arccos(np.clip(abs(normal[2]), -1.0, 1.0)))),
    }


def summarize(vals: list[float]) -> dict:
    arr = np.asarray([v for v in vals if np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "best": float(np.min(arr)),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "worst": float(np.max(arr)),
        "mean": float(np.mean(arr)),
    }


def compute_anchor_sigma(mod, raw) -> dict[int, float]:
    sig: dict[int, list[float]] = defaultdict(list)
    for i in range(8):
        for j in range(i + 1, 8):
            for vals in (raw.get((i, j), []), raw.get((j, i), [])):
                if len(vals) >= 3:
                    s = mod.mad_sigma(vals, 1.0)
                    sig[i].append(s)
                    sig[j].append(s)
    return {i: max(5.0, float(np.median(sig[i])) if sig[i] else 50.0) for i in range(8)}


def main() -> int:
    mod = load_eval_module()
    for sub in ["v1", "v2", "v3lite", "v3full", "v4", "reports", "figures", "tables"]:
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    raw = mod.load_sweep_raw()
    mod.ANCHOR_SIGMA = compute_anchor_sigma(mod, raw)
    anchor_ids = list(range(8))
    fused = {m: mod.fuse_from_directed(raw, m, anchor_ids) for m in ["v1", "v2", "v3"]}

    dump_json(OUT / "tables" / "anchor_sigma_20260513.json", {ANCHORS[i]: mod.ANCHOR_SIGMA[i] for i in range(8)})

    static_dirs = latest_dirs(DATA / "Static_Test", "ID")
    roto_dirs = latest_dirs(DATA / "Roto_Test", "ID")

    version_rows = []
    static_rows = []
    roto_rows = []
    layouts = {}

    for version_dir, solver_name in VERSION_MAP.items():
        print(f"[solve] {version_dir} -> {solver_name}", flush=True)
        out_dir = OUT / version_dir
        if version_dir == "v1":
            x, dly, res, extra = solve_archive_v1_classical_mds(mod, fused["v1"], anchor_ids)
        else:
            x, dly, res, extra = mod.solver_run(solver_name, fused, anchor_ids)
        method = "v3" if solver_name in {"V3-lite", "V3-full", "V4-interonly", "AutoPos V2"} else "v1"
        if solver_name == "AutoPos V2":
            method = "v2"
        inter = mod.inter_rms_local(x, dly, fused[method], anchor_ids)
        layouts[version_dir] = (x, dly)
        mod.save_layout_json(
            out_dir / "layout.json",
            solver_name,
            "Dual-layer 8anc",
            anchor_ids,
            x,
            dly,
            {"inter_rms": inter, "success": bool(getattr(res, "success", True))},
            extra,
        )
        residual_rows = []
        lp, *_ = mod.local_pairs(fused[method], anchor_ids)
        for (i, j), dist in lp.items():
            pred = float(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j])
            residual_rows.append({
                "pair": ANCHORS[i] + ANCHORS[j],
                "obs_mm": dist,
                "pred_mm": pred,
                "residual_mm": pred - dist,
            })
        write_csv(out_dir / "inter_anchor_residuals.csv", residual_rows)
        version_rows.append({
            "version": version_dir,
            "solver_name": solver_name,
            "inter_rms_mm": inter,
            "delay_min_mm": float(np.min(dly)),
            "delay_max_mm": float(np.max(dly)),
            "delay_l2_mm": float(np.linalg.norm(dly)),
            "extra": json.dumps(extra, sort_keys=True),
        })

        for sid, cap_dir in static_dirs.items():
            by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
            pts_all = []
            for frames in by_peer.values():
                frame_slice = downsample_frames(frames, MAX_STATIC_FRAMES_PER_PEER)
                if version_dir == "v1":
                    pts = solve_positions_unweighted(mod, frame_slice, x, dly, anchor_ids)
                else:
                    pts = solve_positions(mod, frame_slice, x, dly, anchor_ids)
                if pts.size:
                    pts_all.append(pts)
            if not pts_all:
                static_rows.append({"version": version_dir, "ID": sid, "status": "insufficient", "path": str(cap_dir)})
                continue
            pts = np.vstack(pts_all)
            std = np.std(pts, axis=0, ddof=1)
            static_rows.append({
                "version": version_dir,
                "ID": sid,
                "status": "ok",
                "N": int(len(pts)),
                "X_std": float(std[0]),
                "Y_std": float(std[1]),
                "Z_std": float(std[2]),
                "D3_std": float(np.linalg.norm(std)),
                "path": str(cap_dir),
            })

        for rid, cap_dir in roto_dirs.items():
            by_peer = load_frames_by_peer(cap_dir / "tr_all.csv")
            for peer, frames in by_peer.items():
                frame_slice = downsample_frames(frames, MAX_ROTO_FRAMES_PER_PEER)
                if version_dir == "v1":
                    pts = solve_positions_unweighted(mod, frame_slice, x, dly, anchor_ids)
                else:
                    pts = solve_positions(mod, frame_slice, x, dly, anchor_ids)
                fit = fit_circle_3d(pts)
                row = {"version": version_dir, "ID": rid, "peer": peer, "path": str(cap_dir), **fit}
                roto_rows.append(row)

    write_csv(OUT / "tables" / "version_solver_summary.csv", version_rows)
    write_csv(OUT / "tables" / "static_all_versions.csv", static_rows)
    write_csv(OUT / "tables" / "roto_all_versions.csv", roto_rows)

    summary_rows = []
    for version in VERSION_MAP:
        st_vals = [r["D3_std"] for r in static_rows if r.get("version") == version and r.get("status") == "ok"]
        ro_vals = [r["circle_3d_rms"] for r in roto_rows if r.get("version") == version and r.get("status") == "ok"]
        ssum = summarize(st_vals)
        rsum = summarize(ro_vals)
        summary_rows.append({
            "version": version,
            "solver_name": VERSION_MAP[version],
            "inter_rms_mm": next(r["inter_rms_mm"] for r in version_rows if r["version"] == version),
            "static_n": ssum.get("n", 0),
            "static_best": ssum.get("best", ""),
            "static_median": ssum.get("median", ""),
            "static_p75": ssum.get("p75", ""),
            "static_worst": ssum.get("worst", ""),
            "roto_n": rsum.get("n", 0),
            "roto_best_rms": rsum.get("best", ""),
            "roto_median_rms": rsum.get("median", ""),
            "roto_p75_rms": rsum.get("p75", ""),
            "roto_worst_rms": rsum.get("worst", ""),
        })
    write_csv(OUT / "tables" / "full_compare_summary.csv", summary_rows)

    labels = list(VERSION_MAP.keys())
    xidx = np.arange(len(labels))
    static_median = [float(next(r["static_median"] for r in summary_rows if r["version"] == v)) for v in labels]
    roto_median = [float(next(r["roto_median_rms"] for r in summary_rows if r["version"] == v)) for v in labels]
    inter = [float(next(r["inter_rms_mm"] for r in summary_rows if r["version"] == v)) for v in labels]

    plt.figure(figsize=(8, 4.5))
    plt.plot(labels, static_median, marker="o", label="Static median 3D std")
    plt.plot(labels, roto_median, marker="s", label="Roto median circle RMS")
    plt.ylabel("mm")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT / "figures" / "progression_static_roto.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 4.5))
    plt.bar(labels, inter)
    plt.ylabel("Inter-anchor RMS (mm)")
    plt.tight_layout()
    plt.savefig(OUT / "figures" / "progression_inter_rms.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 4.5))
    data = [[r["D3_std"] for r in static_rows if r.get("version") == v and r.get("status") == "ok"] for v in labels]
    plt.boxplot(data, labels=labels)
    plt.ylabel("Static 3D std distribution (mm)")
    plt.tight_layout()
    plt.savefig(OUT / "figures" / "static_distribution_by_version.png", dpi=300)
    plt.close()

    lines = []
    lines.append("# AutoPos FULL-COMPARE 20260513\n")
    lines.append("This comparison uses one clean progression line on the same 2026-05-13 data: V1, V2, V3-lite, V3-full, V4.\n")
    lines.append("## Version Definition\n")
    lines.append("| Folder | Solver label | Meaning |")
    lines.append("|---|---|---|")
    meaning = {
        "v1": "simple bidirectional mean + no-delay geometry solve",
        "v2": "weighted/IVW pair fusion + no-delay iterative solve",
        "v3lite": "MAD/MVUE robust fusion + no-delay layout",
        "v3full": "robust fusion + Tukey/median-style per-anchor delay estimation",
        "v4": "Huber bounded-delay production-style solve",
    }
    for v, s in VERSION_MAP.items():
        lines.append(f"| `{v}` | `{s}` | {meaning[v]} |")
    lines.append("\n## Main Summary\n")
    lines.append("| Version | Inter RMS | Static median | Static best/worst | Roto median RMS | Roto best/worst RMS |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for r in summary_rows:
        lines.append(
            f"| {r['version']} | {float(r['inter_rms_mm']):.2f} | {float(r['static_median']):.2f} | "
            f"{float(r['static_best']):.2f}/{float(r['static_worst']):.2f} | {float(r['roto_median_rms']):.2f} | "
            f"{float(r['roto_best_rms']):.2f}/{float(r['roto_worst_rms']):.2f} |"
        )
    lines.append("\n## Notes on RotArm / V4 from `main.pdf`\n")
    lines.append("- In the old concept, V4 meant RotArm Z-injection with known rotating-arm radii.")
    lines.append("- The current `v4` folder here is a production-style Huber bounded-delay solver, not the original RotArm-injection V4.")
    lines.append("- Roto-arm data is still useful as validation/diagnosis, but it is not necessary to include old RotArm-injection as a main solver unless we implement that constraint cleanly.")
    lines.append("\n## Files\n")
    lines.append("- `tables/full_compare_summary.csv`")
    lines.append("- `tables/static_all_versions.csv`")
    lines.append("- `tables/roto_all_versions.csv`")
    lines.append("- `figures/progression_static_roto.png`")
    lines.append("- `figures/progression_inter_rms.png`")
    lines.append("- `figures/static_distribution_by_version.png`")
    (OUT / "reports" / "full_compare_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[ok] report {OUT / 'reports' / 'full_compare_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
