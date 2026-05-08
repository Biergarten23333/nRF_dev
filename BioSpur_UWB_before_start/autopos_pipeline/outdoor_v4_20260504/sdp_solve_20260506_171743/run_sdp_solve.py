#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT.parents[1]
DATA_ROOT = PIPELINE / "outdoor_v4_20260504"
SWEEP_CSV = DATA_ROOT / "sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv"
ID02_DIR = DATA_ROOT / "tr_captures/ID02_static_center_mid_20260504_192643"
ANCHORS = "ABCDEFGH"
ANCHOR_SIGMA = {0: 16.0, 1: 20.0, 2: 27.0, 3: 84.0, 4: 37.0, 5: 28.0, 6: 50.0, 7: 133.0}
CONFIGS = {
    "Dual-layer 8anc": [0, 1, 2, 3, 4, 5, 6, 7],
    "Upper only EFGH": [4, 5, 6, 7],
    "Lower only ABCD": [0, 1, 2, 3],
    "Best6 no DH": [0, 1, 2, 4, 5, 6],
    "Upper+AB": [0, 1, 4, 5, 6, 7],
    "Lower+EF": [0, 1, 2, 3, 4, 5],
}
REFERENCE = {
    "MDS+NLS": {"Dual-layer 8anc": 41.3, "Upper only EFGH": 109.5, "Lower only ABCD": 67.6, "Best6 no DH": 44.0, "Upper+AB": 48.6, "Lower+EF": 43.9},
    "Ridolfi": {"Dual-layer 8anc": 41.3, "Upper only EFGH": 109.5, "Lower only ABCD": 67.6, "Best6 no DH": 44.0, "Upper+AB": 48.6, "Lower+EF": 43.9},
    "V4-io": {"Dual-layer 8anc": 40.8, "Upper only EFGH": 109.7, "Lower only ABCD": 67.0, "Best6 no DH": 41.5, "Upper+AB": 48.6, "Lower+EF": 43.3},
    "V3-full": {"Dual-layer 8anc": 40.7, "Upper only EFGH": 109.7, "Lower only ABCD": 67.0, "Best6 no DH": 44.4, "Upper+AB": 47.9, "Lower+EF": 43.5},
}


def log(msg: str) -> None:
    print(msg, flush=True)


def ensure_cvxpy():
    try:
        import cvxpy as cp
        log(f"cvxpy version: {cp.__version__}")
        log(f"Available solvers: {cp.installed_solvers()}")
        if "SCS" not in cp.installed_solvers():
            log("SCS not installed. Installing cvxpy[scs]...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "cvxpy[scs]", "--break-system-packages"])
            import cvxpy as cp2
            return cp2
        return cp
    except ImportError:
        log("cvxpy not installed. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "cvxpy", "--break-system-packages"])
        import cvxpy as cp
        log(f"cvxpy version: {cp.__version__}")
        log(f"Available solvers: {cp.installed_solvers()}")
        if "SCS" not in cp.installed_solvers():
            subprocess.check_call([sys.executable, "-m", "pip", "install", "cvxpy[scs]", "--break-system-packages"])
            import cvxpy as cp2
            return cp2
        return cp


def anchor_idx(v: str) -> int:
    s = str(v).strip().upper()
    if s in ANCHORS:
        return ANCHORS.index(s)
    return int(s)


def rms(vals) -> float:
    arr = np.asarray(vals, dtype=float)
    return float(np.sqrt(np.mean(arr * arr))) if arr.size else float("nan")


def fmt(v, nd=1) -> str:
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "nan"
    return f"{float(v):.{nd}f}"


def md_table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def load_pair_means() -> dict[tuple[int, int], float]:
    vals: dict[tuple[int, int], list[float]] = defaultdict(list)
    with SWEEP_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            a, b = anchor_idx(row["a"]), anchor_idx(row["b"])
            i, j = sorted((a, b))
            d = float(row["dist_mm"])
            q = float(row.get("quality_percent") or 100)
            ok = int(float(row.get("ok") or 1))
            if i != j and d > 0 and q > 0 and ok:
                vals[(i, j)].append(d)
    return {k: float(np.mean(v)) for k, v in vals.items()}


def filter_pairs(pair_dists, anchor_ids):
    active = set(anchor_ids)
    return {k: v for k, v in pair_dists.items() if k[0] in active and k[1] in active}


def align_gauge(x):
    x = np.asarray(x, dtype=float).copy()
    x -= x[0]
    if len(x) < 2:
        return x
    b = x[1].copy()
    bn = np.linalg.norm(b)
    if bn < 1e-9:
        return x
    ex = b / bn
    if len(x) >= 3:
        c = x[2]
        c_perp = c - np.dot(c, ex) * ex
        ey = np.array([0.0, 1.0, 0.0]) if np.linalg.norm(c_perp) < 1e-9 else c_perp / np.linalg.norm(c_perp)
    else:
        ey = np.array([0.0, 1.0, 0.0])
    ez = np.cross(ex, ey)
    rot = np.vstack([ex, ey, ez]).T
    y = x @ rot
    y[0] = 0
    if len(y) >= 2:
        y[1, 1:] = 0
        if y[1, 0] < 0:
            y[:, 0] *= -1
    if len(y) >= 3:
        y[2, 2] = 0
        if y[2, 1] < 0:
            y[:, 1] *= -1
    return y


def pack(x):
    x = align_gauge(x)
    n = len(x)
    vals = []
    if n >= 2:
        vals.append(x[1, 0])
    if n >= 3:
        vals.extend([x[2, 0], x[2, 1]])
    for i in range(3, n):
        vals.extend(x[i].tolist())
    return np.asarray(vals)


def unpack(v, n):
    x = np.zeros((n, 3))
    k = 0
    if n >= 2:
        x[1, 0] = v[k]
        k += 1
    if n >= 3:
        x[2, 0], x[2, 1] = v[k], v[k + 1]
        k += 2
    for i in range(3, n):
        x[i] = v[k:k + 3]
        k += 3
    return x


def solve_sdp(cp, pair_dists, anchor_ids):
    n = len(anchor_ids)
    local_idx = {a: i for i, a in enumerate(anchor_ids)}
    g = cp.Variable((n, n), symmetric=True)
    constraints = [g >> 0]
    constraints.extend([g[i, i] >= 0 for i in range(n)])
    terms = []
    for (i, j), d_mm in pair_dists.items():
        if i not in local_idx or j not in local_idx:
            continue
        li, lj = local_idx[i], local_idx[j]
        d_m = d_mm / 1000.0
        dist_sq = g[li, li] - 2 * g[li, lj] + g[lj, lj]
        terms.append(cp.square(dist_sq - d_m * d_m))
    prob = cp.Problem(cp.Minimize(cp.sum(terms)), constraints)
    diagnostics = {"solver": None, "status": None, "objective": None, "error": None}
    solved = False
    candidates = []
    if "SCS" in cp.installed_solvers():
        candidates.append(cp.SCS)
    if "ECOS" in cp.installed_solvers():
        candidates.append(cp.ECOS)
    candidates.append(None)
    for solver in candidates:
        try:
            if solver == cp.SCS:
                prob.solve(solver=solver, verbose=False, max_iters=50000, eps=1e-8)
            elif solver is not None:
                prob.solve(solver=solver, verbose=False)
            else:
                prob.solve(verbose=False)
            diagnostics.update({"solver": str(solver), "status": prob.status, "objective": None if prob.value is None else float(prob.value)})
            if prob.status in {"optimal", "optimal_inaccurate"} and g.value is not None:
                solved = True
                break
        except Exception as exc:
            diagnostics.update({"solver": str(solver), "status": "exception", "error": str(exc)})
            log(f"  solver {solver} failed: {exc}")
    if not solved:
        return None, None, diagnostics
    gval = np.asarray(g.value, dtype=float)
    eigvals, eigvecs = np.linalg.eigh(gval)
    order = np.argsort(eigvals)[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    k = min(3, n)
    x_m = np.zeros((n, 3))
    x_m[:, :k] = eigvecs[:, :k] * np.sqrt(np.maximum(eigvals[:k], 0.0))
    x_mm = align_gauge(x_m * 1000.0)
    diagnostics["top6_eigenvalues"] = [float(v) for v in eigvals[:6]]
    diagnostics["rank3_gap"] = float(eigvals[3] / (eigvals[2] + 1e-12)) if len(eigvals) > 3 else 0.0
    return x_mm, eigvals, diagnostics


def nls_refine(x_init, pair_dists, anchor_ids):
    n = len(anchor_ids)
    local_idx = {a: i for i, a in enumerate(anchor_ids)}
    pairs = [(local_idx[i], local_idx[j], d) for (i, j), d in pair_dists.items() if i in local_idx and j in local_idx]

    def residuals(v):
        x = unpack(v, n)
        return np.asarray([np.linalg.norm(x[i] - x[j]) - d for i, j, d in pairs])

    init_res = residuals(pack(x_init))
    method = "lm" if len(init_res) >= len(pack(x_init)) else "trf"
    result = least_squares(residuals, pack(x_init), method=method, max_nfev=2000)
    x_refined = unpack(result.x, n)
    final_res = residuals(result.x)
    return align_gauge(x_refined), rms(init_res), rms(final_res), result


def find_tr_all(capture_dir):
    paths = sorted(capture_dir.glob("recv_*/tr_all.csv"))
    return paths[0] if paths else None


def load_id02_frames():
    path = find_tr_all(ID02_DIR)
    frames = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(float(row["valid"])) != 1:
                continue
            aid = int(row["anchor_id"])
            rng = float(row["range_mm"])
            if 0 <= aid < 8 and rng > 0:
                frames[int(row["sweep"])].append((aid, rng))
    return [v for _k, v in sorted(frames.items()) if len(v) >= 4]


def to_global(x, anchor_ids):
    g = np.full((8, 3), np.nan)
    for li, gi in enumerate(anchor_ids):
        g[gi] = x[li]
    return g


def eval_id02(frames, x_layout, anchor_ids):
    global_x = to_global(x_layout, anchor_ids)
    active = set(anchor_ids)
    positions = []
    last = None
    for frame in frames:
        obs = [(a, r) for a, r in frame if a in active]
        if len(obs) < 4:
            continue
        if last is None:
            last = np.nanmean([global_x[a] for a, _r in obs], axis=0)

        def fun(p):
            return np.asarray([(np.linalg.norm(p - global_x[a]) - r) / ANCHOR_SIGMA[a] for a, r in obs])

        res = least_squares(fun, last, loss="huber", f_scale=2.0, max_nfev=100)
        positions.append(res.x)
        last = res.x
    arr = np.asarray(positions)
    if arr.size == 0:
        return {"N": 0, "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan")}
    std = np.std(arr, axis=0, ddof=1)
    return {"N": len(arr), "X": float(std[0]), "Y": float(std[1]), "Z": float(std[2]), "3D": float(np.linalg.norm(std))}


def save_layout(path, config, anchor_ids, x, stats, diag):
    data = {
        "solver": "SDP+NLS",
        "config": config,
        "anchor_ids": anchor_ids,
        "anchors": [{"id": gi, "label": ANCHORS[gi], "x_mm": float(x[li, 0]), "y_mm": float(x[li, 1]), "z_mm": float(x[li, 2])} for li, gi in enumerate(anchor_ids)],
        "stats": stats,
        "diagnostics": diag,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def main():
    cp = ensure_cvxpy()
    pair_dists = load_pair_means()
    frames = load_id02_frames()
    results = []
    diag_rows = []
    for config, anchor_ids in CONFIGS.items():
        log(f"\n=== SDP {config} ({''.join(ANCHORS[i] for i in anchor_ids)}) ===")
        filt = filter_pairs(pair_dists, anchor_ids)
        x_sdp, eigvals, diag = solve_sdp(cp, filt, anchor_ids)
        if x_sdp is None:
            log(f"  SDP failed: {diag}")
            row = {"config": config, "success": False, "3D": float("nan"), "X": float("nan"), "Y": float("nan"), "Z": float("nan"), "inter_rms": float("nan")}
            results.append(row)
            diag_rows.append({"config": config, **diag})
            continue
        x_ref, init_rms, final_rms, nls = nls_refine(x_sdp, filt, anchor_ids)
        pos = eval_id02(frames, x_ref, anchor_ids)
        stats = {"sdp_initial_inter_rms_mm": init_rms, "nls_final_inter_rms_mm": final_rms, **pos, "nls_success": bool(nls.success)}
        log(f"  status={diag['status']} solver={diag['solver']} obj={diag['objective']}")
        log(f"  top6 eig={diag.get('top6_eigenvalues')}")
        log(f"  rank3_gap={diag.get('rank3_gap'):.4f} init_rms={init_rms:.2f} final_rms={final_rms:.2f} ID02_3D={pos['3D']:.2f}")
        save_layout(ROOT / f"solves/sdp_nls_{config.replace(' ', '_').replace('+', 'p')}.json", config, anchor_ids, x_ref, stats, diag)
        results.append({"config": config, "success": True, "inter_rms": final_rms, **pos})
        diag_rows.append({"config": config, "init_rms": init_rms, "final_rms": final_rms, **diag})

    with (ROOT / "positioning/sdp_id02_results.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["config", "success", "inter_rms", "N", "X", "Y", "Z", "3D"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows([{k: r.get(k) for k in fields} for r in results])
    with (ROOT / "reports/sdp_diagnostics.json").open("w", encoding="utf-8") as f:
        json.dump(diag_rows, f, indent=2)

    table_rows = []
    for config in CONFIGS:
        sdp = next((r for r in results if r["config"] == config), None)
        table_rows.append([
            config,
            fmt(sdp["3D"] if sdp else float("nan")),
            fmt(REFERENCE["MDS+NLS"][config]),
            fmt(REFERENCE["Ridolfi"][config]),
            fmt(REFERENCE["V4-io"][config]),
            fmt(REFERENCE["V3-full"][config]),
        ])
    diag_table = []
    for d in diag_rows:
        eig = d.get("top6_eigenvalues") or []
        diag_table.append([
            d["config"],
            d.get("solver", ""),
            d.get("status", ""),
            fmt(d.get("objective"), 6),
            ", ".join(fmt(v, 4) for v in eig[:6]),
            fmt(d.get("rank3_gap"), 4),
            fmt(d.get("init_rms")),
            fmt(d.get("final_rms")),
        ])
    findings = []
    for config in CONFIGS:
        sdp = next((r for r in results if r["config"] == config), None)
        if sdp and np.isfinite(sdp["3D"]):
            findings.append(f"{config}: SDP+NLS ID02 3D={sdp['3D']:.1f} mm vs MDS+NLS={REFERENCE['MDS+NLS'][config]:.1f} mm; difference={sdp['3D'] - REFERENCE['MDS+NLS'][config]:+.1f} mm.")
        else:
            findings.append(f"{config}: SDP failed or produced no valid positioning result.")
    report = []
    report.append("# SDP+NLS Standalone Results\n")
    report.append(f"Output directory: `{ROOT}`\n")
    report.append(f"cvxpy version: `{cp.__version__}`; installed solvers: `{cp.installed_solvers()}`\n")
    report.append("## SDP vs Other Solvers - ID02 3D std (mm)\n")
    report.append(md_table(["Config", "SDP+NLS", "MDS+NLS", "Ridolfi", "V4-io", "V3-full"], table_rows))
    report.append("\n\n## SDP Diagnostics\n")
    report.append(md_table(["Config", "Solver", "Status", "Objective", "Top 6 eigenvalues", "Rank-3 gap", "Initial RMS", "Final RMS"], diag_table))
    report.append("\n\n## Key Question\n")
    report.append("Does SDP initialization give better results than MDS initialization?\n")
    for fnd in findings:
        report.append(f"- {fnd}")
    report.append("\nIf SDP+NLS and MDS+NLS converge to the same ID02 3D std, then initialization method is not the limiting factor for this dataset.")
    (ROOT / "reports/sdp_results.md").write_text("\n".join(report), encoding="utf-8")
    print("\n" + md_table(["Config", "SDP+NLS", "MDS+NLS", "Ridolfi", "V4-io", "V3-full"], table_rows), flush=True)
    print(f"\nReport: {ROOT / 'reports/sdp_results.md'}", flush=True)


if __name__ == "__main__":
    main()
