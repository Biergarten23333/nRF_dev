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


REPO = Path(__file__).resolve().parents[3]
DATA = REPO / "autopos_pipeline" / "outdoor_v4_20260504"
OUT = DATA / "FULL-COMPARE"
EVAL_SCRIPT = DATA / "full_evaluation_20260506_162829" / "run_full_evaluation.py"
ANCHORS = "ABCDEFGH"
VERSIONS = {
    "v1": "AutoPos V1",
    "v2": "AutoPos V2",
    "v3lite": "V3-lite",
    "v3full": "V3-full",
    "v4": "V4-interonly",
}
MAX_STATIC_FRAMES = 180
MAX_ROTO_FRAMES = 220


def load_mod():
    spec = importlib.util.spec_from_file_location("old_eval", EVAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(EVAL_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def dump_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def capture_dirs():
    out = {}
    for p in sorted((DATA / "tr_captures").iterdir()):
        if p.is_dir() and re.match(r"ID\d+", p.name):
            tr = find_tr_all(p)
            if tr:
                out[p.name.split("_", 1)[0]] = p
    return out


def find_tr_all(cap: Path) -> Path | None:
    paths = sorted(cap.glob("recv_*/tr_all.csv"))
    return paths[0] if paths else None


def frames_by_peer(cap: Path):
    tr = find_tr_all(cap)
    grouped = defaultdict(list)
    if tr is None:
        return {}
    with tr.open(newline="", encoding="utf-8") as f:
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
    by_peer = defaultdict(list)
    for (peer, _sw), obs in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if len(obs) >= 4:
            by_peer[peer].append(obs)
    return by_peer


def downsample(frames, limit):
    if len(frames) <= limit:
        return frames
    idx = np.linspace(0, len(frames) - 1, limit).round().astype(int)
    return [frames[int(i)] for i in idx]


def solve_positions(mod, frames, x, dly, anchor_ids):
    xyz, delay = mod.to_global_layout(x, dly, anchor_ids)
    active = set(anchor_ids)
    pts = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        p = mod.solve_position_weighted(obs, xyz, delay, last)
        pts.append(p)
        last = p
    return np.asarray(pts, dtype=float)


def solve_position_unweighted(obs, xyz, delay, x0=None):
    if x0 is None:
        x0 = np.nanmean([xyz[a] for a, _r in obs], axis=0)

    def fun(p):
        out = []
        for a, r in obs:
            pred = np.linalg.norm(p - xyz[a]) + (0.0 if np.isnan(delay[a]) else delay[a])
            out.append(pred - r)
        return np.asarray(out)

    result = mod_least_squares(fun, x0)
    return result.x


def mod_least_squares(fun, x0):
    # Keep old V1 deliberately plain: no sigma weighting, no robust Huber loss.
    from scipy.optimize import least_squares

    return least_squares(fun, x0, loss="linear", max_nfev=100)


def solve_positions_unweighted(mod, frames, x, dly, anchor_ids):
    xyz, delay = mod.to_global_layout(x, dly, anchor_ids)
    active = set(anchor_ids)
    pts = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        p = solve_position_unweighted(obs, xyz, delay, last)
        pts.append(p)
        last = p
    return np.asarray(pts, dtype=float)


def solve_archive_v1_classical_mds(mod, pair_dists, anchor_ids):
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    x = mod.mds_init(lp, len(anchor_ids))
    dly = np.zeros(len(anchor_ids), dtype=float)
    res = SimpleNamespace(success=True)
    extra = {"implementation": "archive_v1_classical_mds_only", "delay_aware": False}
    return x, dly, res, extra


def circle_rms(pts: np.ndarray):
    if len(pts) < 20:
        return None
    c0 = pts.mean(axis=0)
    _u, _s, vh = np.linalg.svd(pts - c0, full_matrices=False)
    normal, e1, e2 = vh[-1], vh[0], vh[1]
    uv = np.column_stack([(pts - c0) @ e1, (pts - c0) @ e2])
    x, y = uv[:, 0], uv[:, 1]
    A = np.column_stack([2 * x, 2 * y, np.ones_like(x)])
    b = x * x + y * y
    cx, cy, c = np.linalg.lstsq(A, b, rcond=None)[0]
    rad = math.sqrt(max(0.0, c + cx * cx + cy * cy))
    radial = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) - rad
    zplane = (pts - c0) @ normal
    total = np.sqrt(radial * radial + zplane * zplane)
    return float(np.sqrt(np.mean(total * total)))


def summarize(vals):
    arr = np.asarray([v for v in vals if np.isfinite(v)], dtype=float)
    if not len(arr):
        return {"n": 0}
    return {
        "n": int(len(arr)),
        "best": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "worst": float(arr.max()),
        "mean": float(arr.mean()),
    }


def main():
    mod = load_mod()
    for sub in [*VERSIONS, "tables", "reports", "figures"]:
        (OUT / sub).mkdir(parents=True, exist_ok=True)
    raw = mod.load_sweep_raw()
    anchor_ids = list(range(8))
    fused = {m: mod.fuse_from_directed(raw, m, anchor_ids) for m in ["v1", "v2", "v3"]}
    caps = capture_dirs()
    static_caps = {k: v for k, v in caps.items() if int(k[2:]) <= 27}
    roto_caps = {k: v for k, v in caps.items() if int(k[2:]) >= 28}

    version_rows, static_rows, roto_rows = [], [], []
    for vdir, solver in VERSIONS.items():
        print(f"[solve] {vdir} -> {solver}", flush=True)
        if vdir == "v1":
            x, dly, res, extra = solve_archive_v1_classical_mds(mod, fused["v1"], anchor_ids)
        else:
            x, dly, res, extra = mod.solver_run(solver, fused, anchor_ids)
        method = "v3" if solver in {"V3-lite", "V3-full", "V4-interonly", "AutoPos V2"} else "v1"
        if solver == "AutoPos V2":
            method = "v2"
        inter = mod.inter_rms_local(x, dly, fused[method], anchor_ids)
        mod.save_layout_json(
            OUT / vdir / "layout.json", solver, "Dual-layer 8anc", anchor_ids, x, dly,
            {"inter_rms": inter, "success": bool(getattr(res, "success", True))}, extra,
        )
        version_rows.append({
            "version": vdir, "solver_name": solver, "inter_rms_mm": inter,
            "delay_min_mm": float(np.min(dly)), "delay_max_mm": float(np.max(dly)),
            "delay_l2_mm": float(np.linalg.norm(dly)), "extra": json.dumps(extra, sort_keys=True),
        })
        for sid, cap in static_caps.items():
            allp = []
            for frames in frames_by_peer(cap).values():
                frame_slice = downsample(frames, MAX_STATIC_FRAMES)
                if vdir == "v1":
                    pts = solve_positions_unweighted(mod, frame_slice, x, dly, anchor_ids)
                else:
                    pts = solve_positions(mod, frame_slice, x, dly, anchor_ids)
                if pts.size:
                    allp.append(pts)
            if not allp:
                continue
            pts = np.vstack(allp)
            std = np.std(pts, axis=0, ddof=1)
            static_rows.append({
                "version": vdir, "ID": sid, "N": int(len(pts)),
                "X_std": float(std[0]), "Y_std": float(std[1]), "Z_std": float(std[2]),
                "D3_std": float(np.linalg.norm(std)), "path": str(cap),
            })
        for rid, cap in roto_caps.items():
            for peer, frames in frames_by_peer(cap).items():
                frame_slice = downsample(frames, MAX_ROTO_FRAMES)
                if vdir == "v1":
                    pts = solve_positions_unweighted(mod, frame_slice, x, dly, anchor_ids)
                else:
                    pts = solve_positions(mod, frame_slice, x, dly, anchor_ids)
                rms = circle_rms(pts)
                if rms is not None:
                    roto_rows.append({"version": vdir, "ID": rid, "peer": peer, "N": int(len(pts)), "circle_3d_rms": rms, "path": str(cap)})

    write_csv(OUT / "tables" / "version_solver_summary.csv", version_rows)
    write_csv(OUT / "tables" / "static_all_versions.csv", static_rows)
    write_csv(OUT / "tables" / "roto_all_versions.csv", roto_rows)

    summary = []
    for vdir, solver in VERSIONS.items():
        ss = summarize([r["D3_std"] for r in static_rows if r["version"] == vdir])
        rr = summarize([r["circle_3d_rms"] for r in roto_rows if r["version"] == vdir])
        summary.append({
            "version": vdir, "solver_name": solver,
            "inter_rms_mm": next(r["inter_rms_mm"] for r in version_rows if r["version"] == vdir),
            "static_n": ss.get("n", 0), "static_best": ss.get("best", ""), "static_median": ss.get("median", ""),
            "static_p75": ss.get("p75", ""), "static_worst": ss.get("worst", ""),
            "roto_n": rr.get("n", 0), "roto_best_rms": rr.get("best", ""), "roto_median_rms": rr.get("median", ""),
            "roto_p75_rms": rr.get("p75", ""), "roto_worst_rms": rr.get("worst", ""),
        })
    write_csv(OUT / "tables" / "full_compare_summary.csv", summary)

    labels = list(VERSIONS)
    plt.figure(figsize=(8, 4.5))
    plt.plot(labels, [float(next(r["static_median"] for r in summary if r["version"] == v)) for v in labels], marker="o", label="Static median")
    plt.plot(labels, [float(next(r["roto_median_rms"] for r in summary if r["version"] == v)) for v in labels], marker="s", label="Roto median RMS")
    plt.ylabel("mm")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(OUT / "figures" / "progression_static_roto.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 4.5))
    plt.boxplot([[r["D3_std"] for r in static_rows if r["version"] == v] for v in labels], labels=labels)
    plt.ylabel("Static 3D std distribution (mm)")
    plt.tight_layout()
    plt.savefig(OUT / "figures" / "static_distribution_by_version.png", dpi=300)
    plt.close()

    lines = ["# AutoPos FULL-COMPARE 20260504", "", "This reruns the V1/V2/V3-lite/V3-full/V4 progression on the old 20260504 dataset where D/H were known weak.", ""]
    lines += ["## Main Summary", "", "| Version | Inter RMS | Static median | Static best/worst | Roto median RMS | Roto best/worst RMS |", "|---|---:|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f"| {r['version']} | {float(r['inter_rms_mm']):.2f} | {float(r['static_median']):.2f} | {float(r['static_best']):.2f}/{float(r['static_worst']):.2f} | {float(r['roto_median_rms']):.2f} | {float(r['roto_best_rms']):.2f}/{float(r['roto_worst_rms']):.2f} |")
    lines += ["", "## Interpretation", "", "- This evaluation keeps the sigma-weighted Huber tag solver, so D/H are downweighted rather than allowed to dominate every fix.", "- Use this dataset to discuss robustness under bad-anchor quality; use 20260513 to discuss the clean-data floor.", ""]
    lines += ["## Files", "", "- `tables/full_compare_summary.csv`", "- `tables/static_all_versions.csv`", "- `tables/roto_all_versions.csv`", "- `figures/progression_static_roto.png`", "- `figures/static_distribution_by_version.png`"]
    (OUT / "reports" / "full_compare_report.md").write_text("\\n".join(lines) + "\\n", encoding="utf-8")
    print(f"[ok] {OUT / 'reports/full_compare_report.md'}")


if __name__ == "__main__":
    main()
