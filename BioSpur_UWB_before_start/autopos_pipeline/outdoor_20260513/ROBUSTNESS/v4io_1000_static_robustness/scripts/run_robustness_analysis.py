#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[5]
BASE = ROOT / "autopos_pipeline/outdoor_20260513"
OUT = BASE / "ROBUSTNESS/v4io_1000_static_robustness"
TABLES = OUT / "tables"
FIGS = OUT / "figures"

LAYOUT_JSON = BASE / "FULL-COMPARE-1000/v4-io/layout.json"
ANCHOR_SIGMA_JSON = BASE / "FULL-COMPARE-1000/tables/anchor_sigma.json"
STATIC_SUMMARY = BASE / "FULL-COMPARE-1000/v4-io/static_all_captures.csv"
STATIC_ROOT = BASE / "Static_Test"

ANCHORS = tuple("ABCDEFGH")
RNG = random.Random(20260513)
MONTE_CARLO_REPEATS = 500
MAX_WORKERS = min(12, max(1, os.cpu_count() or 1))

G_CASES = None
G_XYZ = None
G_DLY = None
G_SIGMA = None
G_TAG_DELAY = 0.0


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def pct(vals: list[float], q: float) -> float:
    vals = sorted(v for v in vals if math.isfinite(v))
    if not vals:
        return float("nan")
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return vals[lo]
    return vals[lo] * (hi - pos) + vals[hi] * (pos - lo)


def med(vals: list[float]) -> float:
    return pct(vals, 50)


def safe_float(x, default=float("nan")) -> float:
    try:
        return float(x)
    except Exception:
        return default


def load_layout() -> tuple[dict[int, np.ndarray], dict[int, float], float]:
    raw = json.loads(LAYOUT_JSON.read_text())
    xyz = {}
    dly = {}
    for row in raw["anchors"]:
        aid = int(row["id"])
        xyz[aid] = np.array([float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])], dtype=float)
        dly[aid] = float(row.get("d_anchor_mm", 0.0) or 0.0)
    return xyz, dly, float(raw.get("tag_delay_mm", 0.0) or 0.0)


def load_sigma() -> dict[int, float]:
    raw = json.loads(ANCHOR_SIGMA_JSON.read_text())
    return {i: max(5.0, float(raw.get(ANCHORS[i], 50.0))) for i in range(8)}


def read_capture(path: Path) -> list[dict]:
    by_sweep: dict[int, dict] = {}
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            try:
                if int(float(row.get("valid") or 0)) != 1:
                    continue
                aid = int(float(row["anchor_id"]))
                sweep = int(float(row["sweep"]))
                rng = float(row["range_mm"])
                q = float(row.get("quality_percent") or 0)
                t = float(row.get("host_elapsed_s") or 0)
            except Exception:
                continue
            if aid < 0 or aid >= 8 or rng <= 0:
                continue
            rec = by_sweep.setdefault(sweep, {"sweep": sweep, "t": t, "obs": []})
            rec["obs"].append((aid, rng, q))
    return [by_sweep[k] for k in sorted(by_sweep)]


def solve_position(
    obs: list[tuple[int, float, float]],
    xyz: dict[int, np.ndarray],
    dly: dict[int, float],
    sigma: dict[int, float],
    tag_delay: float,
    x0: np.ndarray | None = None,
) -> tuple[np.ndarray | None, list[dict]]:
    if len(obs) < 4:
        return None, []
    if x0 is None or not np.all(np.isfinite(x0)):
        x0 = np.mean([xyz[a] for a, _r, _q in obs], axis=0)
    p = np.asarray(x0, dtype=float).copy()
    last_details: list[dict] = []
    for _ in range(8):
        j_rows = []
        r_rows = []
        w_rows = []
        details = []
        for a, measured, q in obs:
            diff = p - xyz[a]
            dist = float(np.linalg.norm(diff))
            if dist < 1e-6:
                continue
            pred = dist + dly.get(a, 0.0) + tag_delay
            residual = pred - measured
            sig = sigma.get(a, 50.0)
            rn = residual / sig
            hw = 1.0 if abs(rn) <= 2.0 else 2.0 / max(abs(rn), 1e-9)
            j_rows.append(diff / dist / sig)
            r_rows.append(rn)
            w_rows.append(math.sqrt(hw))
            details.append(
                {
                    "anchor": ANCHORS[a],
                    "anchor_id": a,
                    "measured_mm": measured,
                    "predicted_mm": pred,
                    "residual_mm": residual,
                    "quality_percent": q,
                    "sigma_mm": sig,
                    "huber_weight": hw,
                    "downweighted": int(hw < 0.999),
                }
            )
        last_details = details
        if len(j_rows) < 3:
            break
        j = np.asarray(j_rows) * np.asarray(w_rows)[:, None]
        r = np.asarray(r_rows) * np.asarray(w_rows)
        try:
            step, *_ = np.linalg.lstsq(j, -r, rcond=None)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(step)):
            break
        n = float(np.linalg.norm(step))
        if n > 500.0:
            step *= 500.0 / n
        p += step
        if float(np.linalg.norm(step)) < 0.02:
            break
    return p, last_details


def position_summary(points: list[np.ndarray], counts: list[int], total_frames: int) -> dict:
    solved = len(points)
    fail_rate = 100.0 * (total_frames - solved) / max(1, total_frames)
    if solved < 2:
        return {
            "status": "insufficient",
            "total_frames": total_frames,
            "solved_frames": solved,
            "solved_rate": 100.0 - fail_rate,
            "fail_rate": fail_rate,
        }
    arr = np.asarray(points, dtype=float)
    std = np.std(arr, axis=0, ddof=1)
    mean = np.mean(arr, axis=0)
    radial = np.linalg.norm(arr - mean[None, :], axis=1)
    d3 = float(np.linalg.norm(std))
    z_share = 100.0 * std[2] * std[2] / max(1e-9, float(np.sum(std * std)))
    return {
        "status": "ok",
        "total_frames": total_frames,
        "solved_frames": solved,
        "solved_rate": 100.0 * solved / max(1, total_frames),
        "fail_rate": fail_rate,
        "median_anchors_seen_after_drop": float(np.median(counts)) if counts else float("nan"),
        "X_std": float(std[0]),
        "Y_std": float(std[1]),
        "Z_std": float(std[2]),
        "D3_std": d3,
        "Z_share": z_share,
        "radial_p95": float(np.percentile(radial, 95)),
        "radial_max": float(np.max(radial)),
    }


def eval_records(records, xyz, dly, sigma, tag_delay, active_set: set[int] | None = None, keep_k: int | None = None, drop_p: float | None = None, rng: random.Random | None = None):
    rng = rng or RNG
    points = []
    counts = []
    last = None
    for rec in records:
        obs = [(a, r, q) for a, r, q in rec["obs"] if active_set is None or a in active_set]
        if keep_k is not None and len(obs) > keep_k:
            obs = rng.sample(obs, keep_k)
        if drop_p is not None:
            obs = [o for o in obs if rng.random() >= drop_p]
        counts.append(len(obs))
        if len(obs) < 4:
            continue
        p, _details = solve_position(obs, xyz, dly, sigma, tag_delay, last)
        if p is not None:
            points.append(p)
            last = p
    return position_summary(points, counts, len(records))


def init_worker(cases, xyz, dly, sigma, tag_delay):
    global G_CASES, G_XYZ, G_DLY, G_SIGMA, G_TAG_DELAY
    G_CASES = cases
    G_XYZ = xyz
    G_DLY = dly
    G_SIGMA = sigma
    G_TAG_DELAY = tag_delay


def eval_condition_repeat_worker(args):
    condition, rep, active_tuple, keep_k, drop_p, seed = args
    active_set = set(active_tuple) if active_tuple is not None else None
    rng = random.Random(seed)
    out = []
    assert G_CASES is not None
    for case in G_CASES:
        s = eval_records(case["records"], G_XYZ, G_DLY, G_SIGMA, G_TAG_DELAY, active_set, keep_k, drop_p, rng)
        out.append(
            {
                "condition": condition,
                "repeat": rep,
                "ID": case["ID"],
                "location": case["location"],
                "height": case["height"],
                "facing": case["facing"],
                **s,
            }
        )
    return out


def static_cases() -> list[dict]:
    out = []
    for row in read_csv(STATIC_SUMMARY):
        if row.get("status") != "ok":
            continue
        path = Path(row["path"]) / "tr_all.csv"
        if not path.exists():
            continue
        out.append({**row, "tr_path": path, "records": read_capture(path)})
    return out


def summarize_condition(rows: list[dict], condition: str, group_key: str | None = None) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r.get(group_key, "all") if group_key else "all"].append(r)
    out = []
    for group, rs in groups.items():
        ok = [r for r in rs if r.get("status") == "ok"]
        out.append(
            {
                "condition": condition,
                "group_by": group_key or "all",
                "group": group,
                "captures": len(rs),
                "ok_captures": len(ok),
                "solved_rate_median": med([safe_float(r.get("solved_rate")) for r in ok]),
                "fail_rate_median": med([safe_float(r.get("fail_rate")) for r in ok]),
                "X_med": med([safe_float(r.get("X_std")) for r in ok]),
                "Y_med": med([safe_float(r.get("Y_std")) for r in ok]),
                "Z_med": med([safe_float(r.get("Z_std")) for r in ok]),
                "D3_med": med([safe_float(r.get("D3_std")) for r in ok]),
                "Z_share_med": med([safe_float(r.get("Z_share")) for r in ok]),
                "D3_p95": pct([safe_float(r.get("D3_std")) for r in ok], 95),
                "Z_p95": pct([safe_float(r.get("Z_std")) for r in ok], 95),
                "worst_capture": max(ok, key=lambda r: safe_float(r.get("D3_std"), -1)).get("ID", "") if ok else "",
                "worst_D3": max([safe_float(r.get("D3_std")) for r in ok], default=float("nan")),
            }
        )
    return out


def residual_diagnostic(cases, xyz, dly, sigma, tag_delay):
    residual_rows = []
    for case in cases:
        last = None
        for rec in case["records"]:
            obs = [(a, r, q) for a, r, q in rec["obs"]]
            if len(obs) < 4:
                continue
            p, details = solve_position(obs, xyz, dly, sigma, tag_delay, last)
            if p is None:
                continue
            last = p
            for d in details:
                residual_rows.append(
                    {
                        "ID": case["ID"],
                        "location": case["location"],
                        "height": case["height"],
                        "facing": case["facing"],
                        "sweep": rec["sweep"],
                        **d,
                    }
                )
    write_csv(TABLES / "per_observation_residuals.csv", residual_rows)
    by_anchor = defaultdict(list)
    for r in residual_rows:
        by_anchor[r["anchor"]].append(r)
    rows = []
    for a in ANCHORS:
        rs = by_anchor[a]
        vals = [safe_float(r["residual_mm"]) for r in rs]
        absvals = [abs(v) for v in vals]
        rows.append(
            {
                "anchor": a,
                "N_obs": len(rs),
                "residual_mean": float(np.mean(vals)) if vals else float("nan"),
                "residual_median": med(vals),
                "residual_rms": float(np.sqrt(np.mean(np.asarray(vals) ** 2))) if vals else float("nan"),
                "abs_residual_p95": pct(absvals, 95),
                "abs_residual_max": max(absvals) if absvals else float("nan"),
                "quality_median": med([safe_float(r["quality_percent"]) for r in rs]),
                "low_q_lt80_rate": 100.0 * sum(safe_float(r["quality_percent"]) < 80 for r in rs) / max(1, len(rs)),
                "downweighted_rate": 100.0 * sum(int(r["downweighted"]) for r in rs) / max(1, len(rs)),
                "large_abs_res_gt100_rate": 100.0 * sum(abs(safe_float(r["residual_mm"])) > 100 for r in rs) / max(1, len(rs)),
            }
        )
    write_csv(TABLES / "residual_by_anchor.csv", rows)
    return rows


def run_condition_set(cases, xyz, dly, sigma, tag_delay):
    all_details = []
    condition_summaries = []

    def add_condition(name: str, active_set: set[int] | None = None, keep_k=None, drop_p=None, repeats=1):
        detail_rows = []
        if repeats > 1:
            active_tuple = tuple(sorted(active_set)) if active_set is not None else None
            name_seed = sum((i + 1) * ord(ch) for i, ch in enumerate(name))
            tasks = [
                (name, rep, active_tuple, keep_k, drop_p, 20260513 + rep * 1009 + name_seed)
                for rep in range(repeats)
            ]
            with ProcessPoolExecutor(
                max_workers=MAX_WORKERS,
                initializer=init_worker,
                initargs=(cases, xyz, dly, sigma, tag_delay),
            ) as ex:
                futs = [ex.submit(eval_condition_repeat_worker, t) for t in tasks]
                for fut in as_completed(futs):
                    detail_rows.extend(fut.result())
            detail_rows.sort(key=lambda r: (int(r["repeat"]), str(r["ID"])))
        else:
            for rep in range(repeats):
                for case in cases:
                    s = eval_records(case["records"], xyz, dly, sigma, tag_delay, active_set, keep_k, drop_p, RNG)
                    detail_rows.append(
                        {
                            "condition": name,
                            "repeat": rep,
                            "ID": case["ID"],
                            "location": case["location"],
                            "height": case["height"],
                            "facing": case["facing"],
                            **s,
                        }
                    )
        all_details.extend(detail_rows)
        condition_summaries.extend(summarize_condition(detail_rows, name))
        for g in ("location", "height", "facing"):
            condition_summaries.extend(summarize_condition(detail_rows, name, g))

    add_condition("baseline_all_available")
    for aid, label in enumerate(ANCHORS):
        add_condition(f"no_{label}", active_set=set(range(8)) - {aid})
    for k in (8, 7, 6, 5, 4):
        add_condition(f"random_keep_{k}", keep_k=k, repeats=MONTE_CARLO_REPEATS if k < 8 else 1)
    for p in (0.05, 0.10, 0.20, 0.30, 0.40):
        add_condition(f"dropout_p{int(p*100):02d}", drop_p=p, repeats=MONTE_CARLO_REPEATS)

    write_csv(TABLES / "condition_capture_details.csv", all_details)
    write_csv(TABLES / "condition_summary_all_groups.csv", condition_summaries)
    write_csv(TABLES / "condition_summary_overall.csv", [r for r in condition_summaries if r["group_by"] == "all"])
    return all_details, condition_summaries


def fim_uncertainty(anchor_points: list[np.ndarray], point: np.ndarray, sigmas: list[float]) -> tuple[float, float, float, float]:
    j = []
    for a, sig in zip(anchor_points, sigmas):
        diff = point - a
        dist = float(np.linalg.norm(diff))
        if dist < 1e-6:
            continue
        j.append(diff / dist / sig)
    if len(j) < 3:
        return (float("nan"),) * 4
    fim = np.asarray(j).T @ np.asarray(j)
    try:
        cov = np.linalg.pinv(fim)
    except Exception:
        return (float("nan"),) * 4
    sx, sy, sz = [math.sqrt(max(0.0, float(cov[i, i]))) for i in range(3)]
    return sx, sy, sz, math.sqrt(sx * sx + sy * sy + sz * sz)


def candidate_anchor_sim(cases, xyz, sigma):
    # Use solved static mean positions from the existing V4-io summary as coverage points.
    pts = []
    for c in cases:
        pts.append(np.array([safe_float(c["mean_x"]), safe_float(c["mean_y"]), safe_float(c["mean_z"])], dtype=float))
    base_anchor_pts = [xyz[i] for i in range(8)]
    base_sigmas = [sigma[i] for i in range(8)]
    xs = [p[0] for p in base_anchor_pts]
    ys = [p[1] for p in base_anchor_pts]
    zs = [p[2] for p in base_anchor_pts]
    cx, cy = float(np.mean(xs)), float(np.mean(ys))
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    z_low, z_high = max(zs), min(zs)  # layout is mirrored; use existing levels numerically.
    z_mid = float(np.mean(zs))
    candidates = []
    margin = 800.0
    for name, x, y in [
        ("west_mid", xmin - margin, cy),
        ("east_mid", xmax + margin, cy),
        ("south_mid", cx, ymin - margin),
        ("north_mid", cx, ymax + margin),
        ("sw_corner", xmin - margin, ymin - margin),
        ("se_corner", xmax + margin, ymin - margin),
        ("ne_corner", xmax + margin, ymax + margin),
        ("nw_corner", xmin - margin, ymax + margin),
        ("center", cx, cy),
    ]:
        for zname, z in [("low_level", z_low), ("mid_level", z_mid), ("high_level", z_high), ("extra_high", z_high - 800.0)]:
            candidates.append((f"{name}_{zname}", np.array([x, y, z], dtype=float)))

    rows = []
    for cname, cand in candidates:
        z_improve = []
        d3_improve = []
        for p in pts:
            _sx, _sy, base_sz, base_d3 = fim_uncertainty(base_anchor_pts, p, base_sigmas)
            _sx2, _sy2, cand_sz, cand_d3 = fim_uncertainty(base_anchor_pts + [cand], p, base_sigmas + [50.0])
            if math.isfinite(base_sz) and math.isfinite(cand_sz):
                z_improve.append(base_sz - cand_sz)
                d3_improve.append(base_d3 - cand_d3)
        rows.append(
            {
                "candidate": cname,
                "x_mm": cand[0],
                "y_mm": cand[1],
                "z_mm": cand[2],
                "z_uncertainty_improve_med": med(z_improve),
                "z_uncertainty_improve_p05": pct(z_improve, 5),
                "d3_uncertainty_improve_med": med(d3_improve),
                "d3_uncertainty_improve_p05": pct(d3_improve, 5),
            }
        )
    rows.sort(key=lambda r: safe_float(r["z_uncertainty_improve_med"]), reverse=True)
    write_csv(TABLES / "candidate_anchor_simulation.csv", rows)
    write_csv(TABLES / "candidate_anchor_top10.csv", rows[:10])
    return rows


def save_figures():
    overall = [r for r in read_csv(TABLES / "condition_summary_overall.csv") if r["condition"].startswith("no_") or r["condition"] == "baseline_all_available"]
    labels = [r["condition"].replace("baseline_all_available", "baseline").replace("no_", "no ") for r in overall]
    z = [safe_float(r["Z_med"]) for r in overall]
    d3 = [safe_float(r["D3_med"]) for r in overall]
    plt.figure(figsize=(10, 4))
    x = np.arange(len(labels))
    plt.bar(x - 0.18, z, width=0.36, label="Z median std")
    plt.bar(x + 0.18, d3, width=0.36, label="3D median std")
    plt.xticks(x, labels, rotation=45, ha="right")
    plt.ylabel("mm")
    plt.title("Leave-one-anchor-out robustness")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGS / "leave_one_anchor_out_z_3d.png", dpi=180)
    plt.close()

    keep = [r for r in read_csv(TABLES / "condition_summary_overall.csv") if r["condition"].startswith("random_keep_")]
    keep.sort(key=lambda r: int(r["condition"].split("_")[-1]), reverse=True)
    plt.figure(figsize=(7, 4))
    plt.plot([r["condition"].replace("random_keep_", "keep ") for r in keep], [safe_float(r["D3_med"]) for r in keep], marker="o", label="3D median")
    plt.plot([r["condition"].replace("random_keep_", "keep ") for r in keep], [safe_float(r["Z_med"]) for r in keep], marker="o", label="Z median")
    plt.ylabel("mm")
    plt.title("Random keep-k anchor robustness")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGS / "random_keep_k_z_3d.png", dpi=180)
    plt.close()

    res = read_csv(TABLES / "residual_by_anchor.csv")
    plt.figure(figsize=(8, 4))
    plt.bar([r["anchor"] for r in res], [safe_float(r["abs_residual_p95"]) for r in res])
    plt.ylabel("abs residual p95 (mm)")
    plt.title("Per-anchor residual tail")
    plt.tight_layout()
    plt.savefig(FIGS / "residual_abs_p95_by_anchor.png", dpi=180)
    plt.close()


def make_readme():
    overall = read_csv(TABLES / "condition_summary_overall.csv")
    residual = read_csv(TABLES / "residual_by_anchor.csv")
    cand = read_csv(TABLES / "candidate_anchor_top10.csv")
    def cond(name):
        return next(r for r in overall if r["condition"] == name)
    baseline = cond("baseline_all_available")
    no_rows = [r for r in overall if r["condition"].startswith("no_")]
    worst_no_z = max(no_rows, key=lambda r: safe_float(r["Z_med"]))
    worst_no_d3 = max(no_rows, key=lambda r: safe_float(r["D3_med"]))
    keep4 = cond("random_keep_4")
    keep6 = cond("random_keep_6")
    drop30 = cond("dropout_p30")
    worst_res = max(residual, key=lambda r: safe_float(r["abs_residual_p95"]))

    lines = []
    lines.append("# V4-io 1000 Static Robustness Analysis")
    lines.append("")
    lines.append("本目录是独立 robustness 子分析，不修改 `FULL-COMPARE-*` 结果。")
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- Layout: `{LAYOUT_JSON}`")
    lines.append(f"- Static captures: `{STATIC_ROOT}`")
    lines.append(f"- Anchor sigma: `{ANCHOR_SIGMA_JSON}`")
    lines.append("- Solver: V4-io downstream sigma-weighted Huber position solve.")
    lines.append(f"- Monte Carlo repeats for random dropout / keep-k: `{MONTE_CARLO_REPEATS}`.")
    lines.append(f"- Parallel workers: `{MAX_WORKERS}`.")
    lines.append("")
    lines.append("## Main Findings")
    lines.append("")
    lines.append(f"- Baseline static median: Z `{safe_float(baseline['Z_med']):.1f}mm`, 3D `{safe_float(baseline['D3_med']):.1f}mm`, Z share `{safe_float(baseline['Z_share_med']):.1f}%`.")
    lines.append(f"- Worst leave-one-out by Z median: `{worst_no_z['condition']}` with Z `{safe_float(worst_no_z['Z_med']):.1f}mm`, 3D `{safe_float(worst_no_z['D3_med']):.1f}mm`.")
    lines.append(f"- Worst leave-one-out by 3D median: `{worst_no_d3['condition']}` with 3D `{safe_float(worst_no_d3['D3_med']):.1f}mm`.")
    lines.append(f"- Random keep-6 already shows clear degradation: Z `{safe_float(keep6['Z_med']):.1f}mm`, 3D `{safe_float(keep6['D3_med']):.1f}mm`.")
    lines.append(f"- Random keep-4 shows low-redundancy degradation: Z `{safe_float(keep4['Z_med']):.1f}mm`, 3D `{safe_float(keep4['D3_med']):.1f}mm`, fail rate `{safe_float(keep4['fail_rate_median']):.1f}%`.")
    lines.append(f"- 30% independent dropout: Z `{safe_float(drop30['Z_med']):.1f}mm`, 3D `{safe_float(drop30['D3_med']):.1f}mm`, fail rate `{safe_float(drop30['fail_rate_median']):.1f}%`.")
    lines.append(f"- Largest per-anchor residual tail: anchor `{worst_res['anchor']}`, abs residual p95 `{safe_float(worst_res['abs_residual_p95']):.1f}mm`.")
    lines.append("")
    lines.append("## Per-Anchor Residual Diagnostic")
    lines.append("")
    lines.append("| Anchor | N | residual med | residual RMS | abs p95 | low-Q<80 | Huber downweighted | large >100mm |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in residual:
        lines.append(f"| {r['anchor']} | {r['N_obs']} | {safe_float(r['residual_median']):.1f} | {safe_float(r['residual_rms']):.1f} | {safe_float(r['abs_residual_p95']):.1f} | {safe_float(r['low_q_lt80_rate']):.1f}% | {safe_float(r['downweighted_rate']):.1f}% | {safe_float(r['large_abs_res_gt100_rate']):.1f}% |")
    lines.append("")
    lines.append("## Leave-One-Anchor-Out")
    lines.append("")
    lines.append("| Condition | solved rate | fail rate | X med | Y med | Z med | 3D med | Z share | D3 p95 | worst capture |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for r in [baseline] + no_rows:
        lines.append(f"| {r['condition']} | {safe_float(r['solved_rate_median']):.1f}% | {safe_float(r['fail_rate_median']):.1f}% | {safe_float(r['X_med']):.1f} | {safe_float(r['Y_med']):.1f} | {safe_float(r['Z_med']):.1f} | {safe_float(r['D3_med']):.1f} | {safe_float(r['Z_share_med']):.1f}% | {safe_float(r['D3_p95']):.1f} | {r['worst_capture']} |")
    lines.append("")
    lines.append("## Random Keep-K")
    lines.append("")
    lines.append("| Condition | solved rate | fail rate | Z med | 3D med | Z share | D3 p95 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in [x for x in overall if x["condition"].startswith("random_keep_")]:
        lines.append(f"| {r['condition']} | {safe_float(r['solved_rate_median']):.1f}% | {safe_float(r['fail_rate_median']):.1f}% | {safe_float(r['Z_med']):.1f} | {safe_float(r['D3_med']):.1f} | {safe_float(r['Z_share_med']):.1f}% | {safe_float(r['D3_p95']):.1f} |")
    lines.append("")
    lines.append("## Independent Dropout")
    lines.append("")
    lines.append("| Condition | solved rate | fail rate | Z med | 3D med | Z share | D3 p95 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in [x for x in overall if x["condition"].startswith("dropout_p")]:
        lines.append(f"| {r['condition']} | {safe_float(r['solved_rate_median']):.1f}% | {safe_float(r['fail_rate_median']):.1f}% | {safe_float(r['Z_med']):.1f} | {safe_float(r['D3_med']):.1f} | {safe_float(r['Z_share_med']):.1f}% | {safe_float(r['D3_p95']):.1f} |")
    lines.append("")
    lines.append("## Candidate Anchor Simulation")
    lines.append("")
    lines.append("这是几何/FIM 仿真，不是实测。它不能证明新 anchor 一定提高精度，只能给出候选位置的 Z-observability 方向。新 anchor sigma 假设为 50mm。")
    lines.append("")
    lines.append("| Candidate | x | y | z | median Z uncertainty improvement | p05 Z improvement |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for r in cand:
        lines.append(f"| {r['candidate']} | {safe_float(r['x_mm']):.0f} | {safe_float(r['y_mm']):.0f} | {safe_float(r['z_mm']):.0f} | {safe_float(r['z_uncertainty_improve_med']):.2f} | {safe_float(r['z_uncertainty_improve_p05']):.2f} |")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("- `tables/per_observation_residuals.csv`: 每条 observation 的 residual / QF / Huber weight。")
    lines.append("- `tables/residual_by_anchor.csv`: per-anchor NLOS/low-QF/residual summary。")
    lines.append("- `tables/condition_capture_details.csv`: 每个 condition 每个 static capture 的 XYZ 结果。")
    lines.append("- `tables/condition_summary_overall.csv`: baseline / leave-one-out / dropout 总表。")
    lines.append("- `tables/condition_summary_all_groups.csv`: 同上，并按 location / height / facing 分组。")
    lines.append("- `tables/candidate_anchor_simulation.csv`: candidate anchor FIM 几何仿真。")
    lines.append("- `figures/`: leave-one-out、keep-k、residual tail 图。")
    (OUT / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGS.mkdir(parents=True, exist_ok=True)
    xyz, dly, tag_delay = load_layout()
    sigma = load_sigma()
    cases = static_cases()
    residual_diagnostic(cases, xyz, dly, sigma, tag_delay)
    run_condition_set(cases, xyz, dly, sigma, tag_delay)
    candidate_anchor_sim(cases, xyz, sigma)
    save_figures()
    make_readme()


if __name__ == "__main__":
    main()
