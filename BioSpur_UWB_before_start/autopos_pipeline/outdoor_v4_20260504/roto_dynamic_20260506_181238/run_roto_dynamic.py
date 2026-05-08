#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT.parents[1]
DATA_ROOT = PIPELINE / "outdoor_v4_20260504"
LAYOUT_JSON = DATA_ROOT / "solves/anchor_layout_interonly_linear_outdoor_500set_20260504.json"
CAPTURE_ROOT = DATA_ROOT / "tr_captures"
ROTO_CAPTURES = [
    CAPTURE_ROOT / "ID28_roto_small_abef_2roto_20260504_215544",
    CAPTURE_ROOT / "ID29_roto_small_bcgf_2roto_20260504_220009",
    CAPTURE_ROOT / "ID30_roto_small_cdhg_2roto_20260504_220550",
    CAPTURE_ROOT / "ID31_roto_small_adhe_2roto_20260504_221028",
]
ID02_DIR = CAPTURE_ROOT / "ID02_static_center_mid_20260504_192643"
ANCHORS = "ABCDEFGH"
ANCHOR_SIGMA = {0: 16.0, 1: 20.0, 2: 27.0, 3: 84.0, 4: 37.0, 5: 28.0, 6: 50.0, 7: 133.0}
TAGS = ["BS2DCE", "BSDC91"]


def log(msg: str) -> None:
    print(msg, flush=True)


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


def capture_id(path: Path) -> str:
    m = re.match(r"(ID\d+)", path.name)
    return m.group(1) if m else path.name


def load_layout():
    data = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
    xyz = np.zeros((8, 3), dtype=float)
    delay = np.zeros(8, dtype=float)
    for a in data["anchors"]:
        aid = int(a["id"])
        xyz[aid] = [float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])]
        delay[aid] = float(a.get("d_anchor_mm", 0.0))
    return xyz, delay


def find_tr_all(capture_dir: Path) -> Path:
    paths = sorted(capture_dir.glob("recv_*/tr_all.csv"))
    if not paths:
        raise FileNotFoundError(f"no tr_all.csv under {capture_dir}")
    return paths[0]


def load_frames_by_tag(capture_dir: Path):
    tr = find_tr_all(capture_dir)
    frames = defaultdict(lambda: defaultdict(list))
    with tr.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(float(row["valid"])) != 1:
                continue
            tag = row["peer_name"].strip()
            aid = int(row["anchor_id"])
            rng = float(row["range_mm"])
            if 0 <= aid < 8 and rng > 100:
                frames[tag][int(row["sweep"])].append((aid, rng))
    return frames


def solve_position(obs, anchor_xyz, anchor_delay, x0=None):
    valid = [(a, r) for a, r in obs if r > 100]
    if len(valid) < 4:
        return None
    if x0 is None:
        x0 = np.mean([anchor_xyz[a] for a, _r in valid], axis=0)

    def residuals(pos):
        return np.asarray([
            (np.linalg.norm(pos - anchor_xyz[a]) + anchor_delay[a] - r) / ANCHOR_SIGMA[a]
            for a, r in valid
        ])

    result = least_squares(residuals, x0, loss="huber", f_scale=2.0, max_nfev=50)
    return result.x


def solve_capture_positions(capture_dir, anchor_xyz, anchor_delay):
    frames = load_frames_by_tag(capture_dir)
    solved = {}
    for tag, sweep_map in frames.items():
        rows = []
        last = None
        for sweep in sorted(sweep_map):
            pos = solve_position(sweep_map[sweep], anchor_xyz, anchor_delay, last)
            if pos is None:
                continue
            rows.append({"capture": capture_id(capture_dir), "tag": tag, "sweep": sweep, "x": pos[0], "y": pos[1], "z": pos[2], "n_anchor": len(sweep_map[sweep])})
            last = pos
        solved[tag] = rows
    return solved


def fit_circle_3d(points):
    pts = np.asarray(points, dtype=float)
    centroid = np.mean(pts, axis=0)
    centered = pts - centroid
    _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
    basis1, basis2, normal = vh[0], vh[1], vh[2]
    if normal[2] < 0:
        normal = -normal
        basis2 = -basis2
    uv = np.column_stack([centered @ basis1, centered @ basis2])
    x = uv[:, 0]
    y = uv[:, 1]
    a = np.column_stack([x, y, np.ones(len(uv))])
    b = -(x * x + y * y)
    sol, *_ = np.linalg.lstsq(a, b, rcond=None)
    aa, bb, cc = sol
    center_2d = np.array([-aa / 2.0, -bb / 2.0])
    radius = math.sqrt(max(0.0, center_2d @ center_2d - cc))
    center_3d = centroid + center_2d[0] * basis1 + center_2d[1] * basis2
    rho = np.linalg.norm(uv - center_2d, axis=1)
    radial_signed = rho - radius
    radial_abs = np.abs(radial_signed)
    plane_signed = centered @ normal
    total = np.sqrt(radial_signed * radial_signed + plane_signed * plane_signed)
    sse = float(np.sum(radial_signed * radial_signed))
    sst = float(np.sum((rho - np.mean(rho)) ** 2))
    r2 = float(1.0 - sse / sst) if sst > 1e-9 else 1.0
    tilt = math.degrees(math.acos(np.clip(abs(normal[2]), -1.0, 1.0)))
    total_std = float(np.std(total, ddof=1))
    return {
        "center": center_3d,
        "normal": normal,
        "basis1": basis1,
        "basis2": basis2,
        "radius": float(radius),
        "radial_signed": radial_signed,
        "radial_abs": radial_abs,
        "z_plane": plane_signed,
        "total": total,
        "radial_std": float(np.std(radial_signed, ddof=1)),
        "z_plane_std": float(np.std(plane_signed, ddof=1)),
        "total_std": total_std,
        "total_rms": float(np.sqrt(np.mean(total * total))),
        "r2": r2,
        "tilt_deg": float(tilt),
        "outlier_pct": float(100.0 * np.mean(total > 3.0 * max(total_std, 1e-9))),
    }


def circle_points(fit, n=160):
    t = np.linspace(0, 2 * np.pi, n)
    return fit["center"] + fit["radius"] * (np.cos(t)[:, None] * fit["basis1"] + np.sin(t)[:, None] * fit["basis2"])


def static_position_stats(capture_dir, anchor_xyz, anchor_delay):
    frames = load_frames_by_tag(capture_dir)
    all_rows = []
    for tag, sweep_map in frames.items():
        last = None
        positions = []
        for sweep in sorted(sweep_map):
            pos = solve_position(sweep_map[sweep], anchor_xyz, anchor_delay, last)
            if pos is None:
                continue
            positions.append(pos)
            last = pos
        if positions:
            pts = np.asarray(positions)
            std = np.std(pts, axis=0, ddof=1)
            center = np.mean(pts, axis=0)
            scatter = np.linalg.norm(pts - center, axis=1)
            all_rows.append({
                "tag": tag,
                "N": len(pts),
                "X": float(std[0]),
                "Y": float(std[1]),
                "Z": float(std[2]),
                "3D": float(np.linalg.norm(std)),
                "scatter": scatter,
            })
    return all_rows


def load_all_static_stats(anchor_xyz, anchor_delay):
    rows = []
    for cap in sorted(CAPTURE_ROOT.glob("ID*"), key=lambda p: int(re.match(r"ID(\d+)", p.name).group(1)) if re.match(r"ID(\d+)", p.name) else 999):
        m = re.match(r"ID(\d+)", cap.name)
        if not m:
            continue
        cid = int(m.group(1))
        if not (1 <= cid <= 27):
            continue
        for row in static_position_stats(cap, anchor_xyz, anchor_delay):
            row["capture"] = f"ID{cid:02d}"
            rows.append(row)
    return rows


def save_positions_csv(path, rows):
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        fields = list(rows[0])
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    anchor_xyz, anchor_delay = load_layout()
    log(f"Loaded layout: {LAYOUT_JSON}")
    all_position_rows = []
    circle_rows = []
    fits = {}
    residual_records = []
    for cap in ROTO_CAPTURES:
        cid = capture_id(cap)
        log(f"Solving roto positions {cid}")
        solved = solve_capture_positions(cap, anchor_xyz, anchor_delay)
        for tag in TAGS:
            rows = solved.get(tag, [])
            if len(rows) < 10:
                log(f"  {cid} {tag}: insufficient frames")
                continue
            all_position_rows.extend(rows)
            pts = np.asarray([[r["x"], r["y"], r["z"]] for r in rows])
            fit = fit_circle_3d(pts)
            fits[(cid, tag)] = (rows, fit)
            circle_rows.append({
                "capture": cid,
                "tag": tag,
                "N": len(rows),
                "radius": fit["radius"],
                "radial_std": fit["radial_std"],
                "z_plane_std": fit["z_plane_std"],
                "total_std": fit["total_std"],
                "total_rms": fit["total_rms"],
                "tilt_deg": fit["tilt_deg"],
                "r2": fit["r2"],
                "outlier_pct": fit["outlier_pct"],
                "center_x": fit["center"][0],
                "center_y": fit["center"][1],
                "center_z": fit["center"][2],
                "normal_x": fit["normal"][0],
                "normal_y": fit["normal"][1],
                "normal_z": fit["normal"][2],
            })
            for r, radial, zres, total in zip(rows, fit["radial_signed"], fit["z_plane"], fit["total"]):
                residual_records.append({"capture": cid, "tag": tag, "sweep": r["sweep"], "radial_signed": radial, "z_plane": zres, "total": total})
            log(f"  {cid} {tag}: N={len(rows)} R={fit['radius']:.1f} total_rms={fit['total_rms']:.1f} r2={fit['r2']:.3f}")

    save_positions_csv(ROOT / "reports/roto_positions.csv", all_position_rows)
    save_positions_csv(ROOT / "reports/roto_circle_summary.csv", circle_rows)
    save_positions_csv(ROOT / "reports/roto_residuals.csv", residual_records)

    id02_stats = static_position_stats(ID02_DIR, anchor_xyz, anchor_delay)
    all_static = load_all_static_stats(anchor_xyz, anchor_delay)
    static_id02 = id02_stats[0] if id02_stats else {"X": float("nan"), "Y": float("nan"), "Z": float("nan"), "3D": float("nan"), "scatter": np.asarray([])}
    save_positions_csv(ROOT / "reports/static_all_summary.csv", [{k: v for k, v in r.items() if k != "scatter"} for r in all_static])

    dyn_mean = {
        "radial": float(np.mean([r["radial_std"] for r in circle_rows])),
        "z": float(np.mean([r["z_plane_std"] for r in circle_rows])),
        "std3d": float(np.mean([r["total_std"] for r in circle_rows])),
        "rms3d": float(np.mean([r["total_rms"] for r in circle_rows])),
    }
    radius_rows = []
    for cid in sorted({r["capture"] for r in circle_rows}):
        two = [r for r in circle_rows if r["capture"] == cid]
        if len(two) < 2:
            continue
        two_sorted = sorted(two, key=lambda r: r["radius"])
        inner, outer = two_sorted[0], two_sorted[1]
        dr = outer["radius"] - inner["radius"]
        radius_rows.append([cid, fmt(inner["radius"]), fmt(outer["radius"]), fmt(dr), "120.0", fmt(dr - 120.0)])

    # Figures
    fig = plt.figure(figsize=(14, 9))
    for idx, ((cid, tag), (rows, fit)) in enumerate(sorted(fits.items())):
        ax = fig.add_subplot(2, 4, idx + 1, projection="3d")
        pts = np.asarray([[r["x"], r["y"], r["z"]] for r in rows])
        sweeps = np.asarray([r["sweep"] for r in rows])
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], c=sweeps, s=3, cmap="viridis")
        circ = circle_points(fit)
        ax.plot(circ[:, 0], circ[:, 1], circ[:, 2], color="red", linewidth=1)
        ax.set_title(f"{cid} {tag}\nR={fit['radius']:.0f} RMS={fit['total_rms']:.0f}")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_zticks([])
    plt.tight_layout()
    plt.savefig(ROOT / "figures/roto_trajectory_3d.png", dpi=300)
    plt.close()

    all_total = np.asarray([r["total"] for r in residual_records], dtype=float)
    static_scatter = np.asarray(static_id02.get("scatter", []), dtype=float)
    plt.figure(figsize=(8, 5))
    plt.hist(all_total, bins=60, alpha=0.65, label="Roto circle residual")
    if static_scatter.size:
        plt.hist(static_scatter, bins=60, alpha=0.45, label="Static ID02 scatter")
    plt.xlabel("Residual / scatter (mm)")
    plt.ylabel("Count")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ROOT / "figures/roto_residual_histogram.png", dpi=300)
    plt.close()

    plt.figure(figsize=(7, 5))
    labels = ["Radial/X", "Z-plane/Z", "3D"]
    static_vals = [math.sqrt(static_id02["X"] ** 2 + static_id02["Y"] ** 2), static_id02["Z"], static_id02["3D"]]
    dyn_vals = [dyn_mean["radial"], dyn_mean["z"], dyn_mean["std3d"]]
    x = np.arange(len(labels))
    plt.bar(x - 0.18, static_vals, 0.36, label="Static ID02")
    plt.bar(x + 0.18, dyn_vals, 0.36, label="Dynamic roto")
    plt.xticks(x, labels)
    plt.ylabel("Std (mm)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ROOT / "figures/static_vs_dynamic_bar.png", dpi=300)
    plt.close()

    fig, axes = plt.subplots(4, 2, figsize=(13, 9), sharex=False, sharey=True)
    for ax, ((cid, tag), _val) in zip(axes.flat, sorted(fits.items())):
        rec = [r for r in residual_records if r["capture"] == cid and r["tag"] == tag]
        ax.plot([r["sweep"] for r in rec], [r["total"] for r in rec], linewidth=0.7)
        ax.set_title(f"{cid} {tag}")
        ax.set_ylabel("3D residual mm")
    plt.tight_layout()
    plt.savefig(ROOT / "figures/roto_residual_timeseries.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 5))
    cids = [r[0] for r in radius_rows]
    inner_vals = [float(r[1]) for r in radius_rows]
    outer_vals = [float(r[2]) for r in radius_rows]
    x = np.arange(len(cids))
    plt.bar(x - 0.18, inner_vals, 0.36, label="Inner fitted")
    plt.bar(x + 0.18, outer_vals, 0.36, label="Outer fitted")
    plt.axhline(440, color="tab:blue", linestyle="--", linewidth=1, label="Expected inner 440")
    plt.axhline(560, color="tab:orange", linestyle="--", linewidth=1, label="Expected outer 560")
    plt.xticks(x, cids)
    plt.ylabel("Radius (mm)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(ROOT / "figures/radius_verification.png", dpi=300)
    plt.close()

    table1 = [[r["capture"], r["tag"], r["N"], fmt(r["radius"]), fmt(r["radial_std"]), fmt(r["z_plane_std"]), fmt(r["total_std"]), fmt(r["total_rms"])] for r in circle_rows]
    table1.append(["MEAN", "", fmt(np.mean([r["N"] for r in circle_rows]), 0), fmt(np.mean([r["radius"] for r in circle_rows])), fmt(dyn_mean["radial"]), fmt(dyn_mean["z"]), fmt(dyn_mean["std3d"]), fmt(dyn_mean["rms3d"])])
    degradation_3d = dyn_mean["std3d"] / static_id02["3D"]
    table2 = [
        ["X/Radial std", fmt(math.sqrt(static_id02["X"] ** 2 + static_id02["Y"] ** 2)), fmt(dyn_mean["radial"]), f"{dyn_mean['radial'] / math.sqrt(static_id02['X'] ** 2 + static_id02['Y'] ** 2):.2f}x"],
        ["Z std", fmt(static_id02["Z"]), fmt(dyn_mean["z"]), f"{dyn_mean['z'] / static_id02['Z']:.2f}x"],
        ["3D std", fmt(static_id02["3D"]), fmt(dyn_mean["std3d"]), f"{degradation_3d:.2f}x"],
    ]
    table4 = [[r["capture"], r["tag"], fmt(r["tilt_deg"]), fmt(r["r2"], 4), fmt(r["outlier_pct"])] for r in circle_rows]
    table5 = [[r["capture"], r["tag"], fmt(r["center_x"]), fmt(r["center_y"]), fmt(r["center_z"]), f"({r['normal_x']:.3f},{r['normal_y']:.3f},{r['normal_z']:.3f})"] for r in circle_rows]
    lit_table = [
        ["AutoPos (this work)", f"{dyn_mean['std3d']:.1f} mm std / {dyn_mean['rms3d']:.1f} mm RMS", "Pure UWB", "Outdoor roto circle fit"],
        ["Pure UWB literature range", "100-300 mm", "Pure UWB", "Survey-level range"],
        ["UWB+IMU fusion range", "50-150 mm", "UWB+IMU", "Survey-level range"],
        ["UWB+VIO fusion range", "30-70 mm", "UWB+VIO", "Survey-level range"],
        ["DW1000 datasheet", "+/-300 mm", "Pure UWB", "Qorvo typical ranging accuracy"],
    ]
    center_diffs = []
    for cid in sorted({r["capture"] for r in circle_rows}):
        two = [r for r in circle_rows if r["capture"] == cid]
        if len(two) == 2:
            c0 = np.array([two[0]["center_x"], two[0]["center_y"], two[0]["center_z"]])
            c1 = np.array([two[1]["center_x"], two[1]["center_y"], two[1]["center_z"]])
            n0 = np.array([two[0]["normal_x"], two[0]["normal_y"], two[0]["normal_z"]])
            n1 = np.array([two[1]["normal_x"], two[1]["normal_y"], two[1]["normal_z"]])
            center_diffs.append((cid, float(np.linalg.norm(c0 - c1)), float(math.degrees(math.acos(np.clip(abs(np.dot(n0, n1)), -1, 1))))))
    drift_by_capture = []
    for cid in sorted({r["capture"] for r in residual_records}):
        rec = [r for r in residual_records if r["capture"] == cid]
        if len(rec) > 2:
            sweeps = np.asarray([r["sweep"] for r in rec], dtype=float)
            totals = np.asarray([r["total"] for r in rec], dtype=float)
            corr = float(np.corrcoef(sweeps, totals)[0, 1])
            drift_by_capture.append((cid, corr))

    report = []
    report.append("# Roto Tag Dynamic Positioning Error: Circle-Fit Residual Analysis\n")
    report.append(f"Output directory: `{ROOT}`\n")
    report.append(f"Layout: `{LAYOUT_JSON}`. Anchor delays loaded from layout, defaulting to 0 where absent.\n")
    report.append("## Table 1: Per-Capture Dynamic Error\n")
    report.append(md_table(["Capture", "Tag", "N frames", "Radius(mm)", "Radial sigma", "Z-plane sigma", "3D sigma", "3D RMS"], table1))
    report.append("\n\n## Table 2: Static vs Dynamic Comparison\n")
    report.append(md_table(["Metric", "Static (ID02)", "Dynamic (roto mean)", "Degradation"], table2))
    report.append("\n\n## Table 3: Roto Arm Geometry Verification\n")
    report.append(md_table(["Capture", "Inner R (mm)", "Outer R (mm)", "Delta R (mm)", "Expected Delta R", "Error"], radius_rows))
    report.append("\n\n## Table 4: Circle Fit Quality\n")
    report.append(md_table(["Capture", "Tag", "Plane tilt (deg)", "Circle fit R2", "Outlier % (>3sigma)"], table4))
    report.append("\n\n## Table 5: Per-Capture Fitted Circle Parameters\n")
    report.append(md_table(["Capture", "Tag", "Center X", "Center Y", "Center Z", "Normal (nx,ny,nz)"], table5))
    report.append("\n\n## Table 6: Literature Comparison\n")
    report.append(md_table(["System", "Dynamic 3D error", "Sensors", "Source"], lit_table))
    report.append("\n\n## Figures\n")
    for fig_name in ["roto_trajectory_3d.png", "roto_residual_histogram.png", "static_vs_dynamic_bar.png", "roto_residual_timeseries.png", "radius_verification.png"]:
        report.append(f"- `figures/{fig_name}`")
    report.append("\n\n## Key Findings\n")
    report.append(f"1. Mean dynamic circle-fit error is **{dyn_mean['std3d']:.1f} mm 3D sigma** and **{dyn_mean['rms3d']:.1f} mm 3D RMS** across {len(circle_rows)} tag/capture fits.")
    report.append(f"2. Static ID02 3D std is **{static_id02['3D']:.1f} mm**, so dynamic circle-fit sigma is **{degradation_3d:.2f}x** static by this metric.")
    report.append(f"3. Radial scatter averages **{dyn_mean['radial']:.1f} mm**, while plane-normal scatter averages **{dyn_mean['z']:.1f} mm**. The dominant component is {'plane-normal/Z' if dyn_mean['z'] > dyn_mean['radial'] else 'radial/in-plane'}.")
    if radius_rows:
        mean_dr = np.mean([float(r[3]) for r in radius_rows])
        report.append(f"4. Fitted radius separation averages **{mean_dr:.1f} mm** versus expected **120 mm**, error **{mean_dr - 120.0:+.1f} mm**.")
    if center_diffs:
        report.append("5. Same-capture two-tag center/normal consistency: " + "; ".join(f"{cid} center_diff={cd:.1f}mm normal_diff={nd:.1f}deg" for cid, cd, nd in center_diffs) + ".")
    report.append(f"6. Compared with broad pure-UWB dynamic literature ranges (100-300 mm), this setup is {'better than' if dyn_mean['rms3d'] < 100 else 'within'} the usual pure-UWB range, and approaches lower-end UWB+IMU numbers depending on whether sigma or RMS is used.")
    if drift_by_capture:
        report.append("7. Sweep-vs-residual drift correlations: " + "; ".join(f"{cid} r={corr:+.2f}" for cid, corr in drift_by_capture) + ". Values near zero indicate no strong monotonic time drift.")
    report.append("8. Circle-fit R2 and outlier percentage should be checked before treating a radius as physical; poor R2 means the solved trajectory is not well described by a single rigid circle.")
    (ROOT / "reports/roto_dynamic_report.md").write_text("\n".join(report), encoding="utf-8")
    print("\n" + md_table(["Capture", "Tag", "N", "Radius", "Radial sigma", "Z-plane sigma", "3D sigma", "3D RMS"], table1), flush=True)
    print(f"\nReport: {ROOT / 'reports/roto_dynamic_report.md'}", flush=True)


if __name__ == "__main__":
    main()
