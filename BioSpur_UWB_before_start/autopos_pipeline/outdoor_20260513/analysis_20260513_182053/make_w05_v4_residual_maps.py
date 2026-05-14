#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares


ROOT = Path(__file__).resolve().parents[3]
ANALYSIS = Path(__file__).resolve().parent
DATA = ROOT / "autopos_pipeline" / "outdoor_20260513"
OUT = ANALYSIS / "figures" / "w05_dynamic_probe" / "residual_maps" / "V4_solver"

ANCHORS = list("ABCDEFGH")
PEERS = ["BSCCF4", "BS9336", "BS955A"]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            for k in row:
                if k not in fields:
                    fields.append(k)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})


def load_layout(path: Path):
    obj = json.loads(path.read_text())
    layout = {a: np.asarray(obj["anchors"][a], dtype=float) for a in ANCHORS}
    delays = {a: float(obj.get("delays_mm", {}).get(a, 0.0)) for a in ANCHORS}
    return layout, delays


def load_sigma(path: Path) -> dict[str, float]:
    return {r["anchor"]: float(r["sigma_mm"]) for r in read_csv(path)}


def physicalize_z(points: np.ndarray, z_sign: float, z_offset: float) -> np.ndarray:
    out = np.array(points, dtype=float, copy=True)
    out[..., 2] = z_sign * out[..., 2] + z_offset
    return out


def latest_w05() -> Path:
    dirs = sorted((DATA / "Wand_Test").glob("W05_*"))
    if not dirs:
        raise SystemExit("No W05_* directory found")
    return dirs[-1] / "tr_all.csv"


def iter_grouped_ranges(path: Path):
    grouped = defaultdict(dict)
    meta = {}
    for row in read_csv(path):
        try:
            if int(float(row.get("valid", "0") or 0)) != 1:
                continue
            if row.get("status") not in ("", "O"):
                continue
            aid = int(float(row.get("anchor_id", -1)))
            if not 0 <= aid < 8:
                continue
            rng = float(row.get("range_mm") or row.get("raw_mm"))
            if rng <= 0:
                continue
            peer = row.get("peer_name", "")
            sweep = int(float(row.get("sweep", 0)))
            key = (peer, sweep)
            a = ANCHORS[aid]
            grouped[key][a] = {
                "range_mm": rng,
                "quality_percent": float(row.get("quality_percent") or 0),
                "host_elapsed_s": float(row.get("host_elapsed_s") or 0),
                "host_epoch_s": float(row.get("host_epoch_s") or 0),
            }
            meta[key] = {
                "peer_name": peer,
                "sweep": sweep,
                "host_elapsed_s": float(row.get("host_elapsed_s") or 0),
                "host_epoch_s": float(row.get("host_epoch_s") or 0),
            }
        except Exception:
            continue
    return grouped, meta


def solve_point(ranges, layout, delays, sigma):
    valid = [(a, v["range_mm"]) for a, v in ranges.items() if a in layout and np.isfinite(v["range_mm"]) and v["range_mm"] > 0]
    if len(valid) < 4:
        return None
    anchors = np.asarray([layout[a] for a, _ in valid], dtype=float)
    ds = np.asarray([d for _, d in valid], dtype=float)
    dly = np.asarray([delays.get(a, 0.0) for a, _ in valid], dtype=float)
    sig = np.asarray([max(5.0, sigma.get(a, 30.0)) for a, _ in valid], dtype=float)
    x0 = anchors.mean(axis=0)

    def res(x):
        return (np.linalg.norm(anchors - x, axis=1) + dly - ds) / sig

    ans = least_squares(res, x0, loss="huber", f_scale=2.0, max_nfev=100)
    return ans.x


def build_rows():
    OUT.mkdir(parents=True, exist_ok=True)
    layout, delays = load_layout(ANALYSIS / "solves" / "v4_io_layout.json")
    sigma = load_sigma(ANALYSIS / "tables" / "anchor_sigma.csv")
    zobj = json.loads((ANALYSIS / "physical_z_transform_20260513.json").read_text())
    z_sign = float(zobj["z_sign"])
    z_offset = float(zobj["z_offset_mm"])

    grouped, meta = iter_grouped_ranges(latest_w05())
    rows = []
    point_rows = []
    for key, ranges in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        p = solve_point(ranges, layout, delays, sigma)
        if p is None:
            continue
        pp = physicalize_z(p, z_sign, z_offset)
        peer, sweep = key
        m = meta[key]
        used = len(ranges)
        point_rows.append({
            "peer_name": peer, "sweep": sweep, "host_elapsed_s": m["host_elapsed_s"],
            "x_mm": pp[0], "y_mm": pp[1], "z_mm": pp[2], "used_anchors": used,
        })
        for a, obs in ranges.items():
            pred = float(np.linalg.norm(layout[a] - p) + delays.get(a, 0.0))
            residual = pred - float(obs["range_mm"])
            rows.append({
                "peer_name": peer,
                "sweep": sweep,
                "host_elapsed_s": m["host_elapsed_s"],
                "anchor": a,
                "x_mm": pp[0],
                "y_mm": pp[1],
                "z_mm": pp[2],
                "range_mm": obs["range_mm"],
                "pred_mm": pred,
                "delay_mm": delays.get(a, 0.0),
                "residual_mm": residual,
                "abs_residual_mm": abs(residual),
                "quality_percent": obs["quality_percent"],
                "used_anchors": used,
            })
    return rows, point_rows


def summarize(rows):
    out = []
    for a in ANCHORS:
        vals = np.asarray([float(r["residual_mm"]) for r in rows if r["anchor"] == a], dtype=float)
        q = np.asarray([float(r["quality_percent"]) for r in rows if r["anchor"] == a], dtype=float)
        if vals.size == 0:
            continue
        med = float(np.median(vals))
        mad = float(1.4826 * np.median(np.abs(vals - med)))
        out.append({
            "anchor": a,
            "N": int(vals.size),
            "mean_residual_mm": float(np.mean(vals)),
            "median_residual_mm": med,
            "mad_sigma_mm": mad,
            "rms_residual_mm": float(np.sqrt(np.mean(vals * vals))),
            "p10_residual_mm": float(np.percentile(vals, 10)),
            "p90_residual_mm": float(np.percentile(vals, 90)),
            "median_abs_residual_mm": float(np.median(np.abs(vals))),
            "median_quality_percent": float(np.median(q)) if q.size else "",
        })
    return out


def scatter_grid(rows, value_key: str, projection: str, filename: str, title: str, cmap: str, symmetric: bool):
    fig, axs = plt.subplots(2, 4, figsize=(17, 8), sharex=False, sharey=False)
    vals_all = np.asarray([float(r[value_key]) for r in rows], dtype=float)
    if symmetric:
        lim = float(np.nanpercentile(np.abs(vals_all), 98))
        vmin, vmax = -lim, lim
    else:
        vmin, vmax = 0.0, float(np.nanpercentile(vals_all, 95))
    axes = {"xy": ("x_mm", "y_mm"), "xz": ("x_mm", "z_mm"), "yz": ("y_mm", "z_mm")}[projection]
    labels = {"x_mm": "X mm", "y_mm": "Y mm", "z_mm": "Z mm"}
    last = None
    for ax, a in zip(axs.ravel(), ANCHORS):
        sub = [r for r in rows if r["anchor"] == a]
        x = [float(r[axes[0]]) for r in sub]
        y = [float(r[axes[1]]) for r in sub]
        c = [float(r[value_key]) for r in sub]
        last = ax.scatter(x, y, c=c, s=8, alpha=0.65, cmap=cmap, vmin=vmin, vmax=vmax, linewidths=0)
        ax.set_title(f"Anchor {a}  N={len(sub)}")
        ax.set_xlabel(labels[axes[0]])
        ax.set_ylabel(labels[axes[1]])
        ax.grid(True, alpha=0.25)
    fig.suptitle(title)
    if last is not None:
        fig.colorbar(last, ax=axs.ravel().tolist(), shrink=0.86, label=value_key)
    fig.savefig(OUT / filename, dpi=300, bbox_inches="tight")
    plt.close(fig)


def boxplot(rows):
    data = [[float(r["residual_mm"]) for r in rows if r["anchor"] == a] for a in ANCHORS]
    plt.figure(figsize=(10, 5))
    plt.axhline(0, color="0.3", lw=1)
    plt.boxplot(data, labels=ANCHORS, showfliers=False)
    plt.ylabel("V4 residual mm = ||p-A|| + delay - measured")
    plt.title("W05 V4 residual distribution per anchor")
    plt.grid(True, axis="y", alpha=0.25)
    plt.tight_layout()
    plt.savefig(OUT / "fig_w05_v4_residual_boxplot_per_anchor.png", dpi=300)
    plt.close()


def histograms(rows):
    fig, axs = plt.subplots(2, 4, figsize=(16, 7), sharex=True, sharey=True)
    for ax, a in zip(axs.ravel(), ANCHORS):
        vals = [float(r["residual_mm"]) for r in rows if r["anchor"] == a]
        ax.hist(vals, bins=80, color="#4c78a8", alpha=0.82)
        ax.axvline(0, color="0.25", lw=1)
        ax.axvline(np.median(vals), color="#f58518", lw=1.5)
        ax.set_title(f"{a}: med={np.median(vals):.1f} mm")
        ax.grid(True, alpha=0.2)
    fig.suptitle("W05 V4 residual histograms per anchor")
    fig.supxlabel("residual mm")
    fig.supylabel("count")
    fig.savefig(OUT / "fig_w05_v4_residual_histograms_per_anchor.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def used_anchor_projection(point_rows):
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
    specs = [("x_mm", "y_mm", "XY"), ("x_mm", "z_mm", "XZ"), ("y_mm", "z_mm", "YZ")]
    labels = {"x_mm": "X mm", "y_mm": "Y mm", "z_mm": "Z mm"}
    for ax, (xk, yk, name) in zip(axs, specs):
        sc = ax.scatter([float(r[xk]) for r in point_rows], [float(r[yk]) for r in point_rows],
                        c=[float(r["used_anchors"]) for r in point_rows], s=9, cmap="viridis", vmin=4, vmax=8, alpha=0.75)
        ax.set_title(name)
        ax.set_xlabel(labels[xk])
        ax.set_ylabel(labels[yk])
        ax.grid(True, alpha=0.25)
    fig.colorbar(sc, ax=axs.tolist(), label="used anchors")
    fig.suptitle("W05 V4 solved tag positions colored by used anchor count")
    fig.savefig(OUT / "fig_w05_v4_used_anchor_count_projections.png", dpi=300, bbox_inches="tight")
    plt.close(fig)


def main():
    rows, point_rows = build_rows()
    if not rows:
        raise SystemExit("No residual rows generated")
    write_csv(OUT / "w05_per_anchor_residuals_v4_physical_z.csv", rows)
    write_csv(OUT / "w05_v4_solved_points_physical_z.csv", point_rows)
    write_csv(OUT / "w05_per_anchor_residual_summary_v4.csv", summarize(rows))
    boxplot(rows)
    histograms(rows)
    used_anchor_projection(point_rows)
    for proj in ["xy", "xz", "yz"]:
        scatter_grid(rows, "residual_mm", proj, f"fig_w05_v4_per_anchor_residual_map_{proj}.png",
                     f"W05 V4 signed residual map ({proj.upper()})", "coolwarm", True)
        scatter_grid(rows, "abs_residual_mm", proj, f"fig_w05_v4_per_anchor_abs_residual_map_{proj}.png",
                     f"W05 V4 absolute residual map ({proj.upper()})", "magma", False)
    print(f"Wrote {len(rows)} residual rows and {len(point_rows)} solved points to {OUT}")


if __name__ == "__main__":
    main()
