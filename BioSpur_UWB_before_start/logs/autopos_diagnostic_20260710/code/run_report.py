#!/usr/bin/env python3
"""B/F/H AutoPos diagnostic after the B+F stand was moved off the wall.

Datasets (inter-anchor sweeps, all 2026-07-10):
  overnight = sweep_SW01_1000 121550 (1000 sets, A-G only; H would not promote)
  field     = sweep_SW01_100  140144 (100 sets, A-H; post-B-rotation)
  premove   = raw/sweep        (250 sets, A-H; evening, BEFORE the wall move)
  postmove  = raw/sweep_postmove (250 sets, A-H; AFTER moving B+F stand off wall) = CURRENT

Reference core = the 5 UNTOUCHED anchors A,C,D,E,G (B and F both moved with the
stand, so neither can be trusted in the reference now). We evaluate B, F, H
against that clean-5, and compare premove vs postmove to see if moving B off the
wall improved it (and whether it disturbed F).
"""
import json
import os
import sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnostic as dg

REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
OUT = os.path.join(REPO, "logs/autopos_diagnostic_20260710")
CAP = os.path.join(REPO, "autopos_pipeline/erlangen_20260528_mocap/captures/erlangen_20260528_optitrack")
SWEEPS = {
    "overnight": f"{CAP}/sweep_SW01_1000_prewarm10_20260710_121550/sweep1000/summary.json",
    "field":     f"{CAP}/sweep_SW01_100_prewarm10_20260710_140144/sweep100/summary.json",
    "premove":   f"{OUT}/raw/sweep/summary.json",
    "postmove":  f"{OUT}/raw/sweep_postmove/summary.json",
}
CURRENT = "postmove"
CORE = list("ACDEG")          # 5 untouched anchors = reference core
FLAG = ["B", "F", "H"]        # B,F moved with the stand; H still flagged
MOVED = ["B", "F"]
OUTLIER_MM = 150.0
STABLE_MM = 100.0


def classify_bias(rows):
    resid = {r["peer"]: r["resid_mm"] for r in rows}
    vals = np.array(list(resid.values()))
    bad = {k: v for k, v in resid.items() if abs(v) > OUTLIER_MM}
    clean = {k: v for k, v in resid.items() if abs(v) <= OUTLIER_MM}
    mean_abs = float(np.mean(np.abs(vals)))
    spread = float(vals.max() - vals.min())
    same_sign = bool(np.all(vals > 0) or np.all(vals < 0))
    if mean_abs < 60 and not bad:
        kind = "negligible"
    elif len(bad) >= 1 and len(clean) >= 2 and spread > 250:
        kind = "link-specific"
    elif same_sign and spread < 220 and mean_abs > 80:
        kind = "uniform"
    else:
        kind = "mixed"
    return {"kind": kind, "mean_abs_resid_mm": round(mean_abs, 1), "spread_mm": round(spread, 1),
            "same_sign": same_sign, "bad_links": {k: round(v, 1) for k, v in bad.items()},
            "clean_links": {k: round(v, 1) for k, v in clean.items()}}


def make_figure(anchor, direc, path):
    rows = direc["rows"]
    az = np.array([r["az_deg"] for r in rows])
    rs = np.array([r["resid_mm"] for r in rows])
    fit = direc["fit"]
    order = np.argsort(az)
    labels = [rows[i]["link"] for i in order]
    az, rs = az[order], rs[order]
    fig, ax = plt.subplots(figsize=(8.4, 5.2))
    sa = np.sort(az)
    gaps = np.diff(np.concatenate([sa, [sa[0] + 360]]))
    gi = int(np.argmax(gaps))
    max_gap = float(gaps[gi])
    covered_span = 360.0 - max_gap
    gap_lo, gap_hi = float(sa[gi]), float(sa[(gi + 1) % len(sa)])
    wrap = (gi == len(sa) - 1)
    xx = np.linspace(0, 360, 721)
    yy = fit["offset_mm"] + fit["amplitude_mm"] * np.cos(np.radians(xx) - np.radians(fit["phase_deg"]))
    mask = ((xx >= sa[0]) & (xx <= sa[-1])) if wrap else ((xx <= gap_lo) | (xx >= gap_hi))
    ax.plot(xx, np.where(mask, yy, np.nan), "--", color="#c44", lw=1.4, alpha=0.7,
            label=f"low-R² sinusoid fit (R²={fit['r2']:.2f}, amp={fit['amplitude_mm']:.0f}) — not a smooth lobe")
    ax.axhline(float(np.mean(rs)), color="#6a6", lw=1.1, ls=":",
               label=f"mean residual = {np.mean(rs):.0f} mm")
    ax.axhline(0, color="#888", lw=0.8, ls="--")
    ax.axhspan(-OUTLIER_MM, OUTLIER_MM, color="#3a3", alpha=0.08, label=f"±{OUTLIER_MM:.0f} mm (in-tolerance)")
    for i in range(len(az)):
        bad = abs(rs[i]) > OUTLIER_MM
        ax.scatter([az[i]], [rs[i]], s=90, zorder=5, color=("#c33" if bad else "#357"), edgecolor="k", lw=0.6)
        ax.annotate(labels[i], (az[i], rs[i]), textcoords="offset points", xytext=(7, 7), fontsize=10, fontweight="bold")
    if covered_span < 200:
        ax.text(0.015, 0.03, f"peers span only {covered_span:.0f}° of azimuth → corner geometry\n"
                             f"({max_gap:.0f}° arc has no peers; antenna pattern under-constrained)",
                transform=ax.transAxes, fontsize=8.5, style="italic", color="#444",
                bbox=dict(boxstyle="round", fc="#fff6e0", ec="#ddb", alpha=0.9))
    ax.set_xlabel(f"azimuth from {anchor} to peer  (deg, clean-5 frame)")
    ax.set_ylabel("range residual  measured − predicted  (mm)")
    ax.set_title(f"Anchor {anchor} directional-bias pattern (POST-MOVE, 250 sets)\n"
                 f"multilat vs clean-5 (A,C,D,E,G): RMS={direc['rms']} mm, robust wRMS={direc['wrms']} mm")
    ax.set_xlim(-10, 370)
    ax.set_xticks(range(0, 361, 45))
    ax.grid(alpha=0.25)
    ax.legend(loc="best", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def anchor_block(D_all, pos6, v4io, anc):
    Dc = D_all[CURRENT]
    pw_cur = dg.pairwise(Dc, anc)
    pw_pre = dg.pairwise(D_all["premove"], anc)
    pw_field = dg.pairwise(D_all["field"], anc)
    pairwise = {}
    for l in dg.LABELS:
        if l == anc:
            continue
        f = pw_cur[l]
        d_pre = (f["median_mm"] - pw_pre[l]["median_mm"]) if (f["median_mm"] is not None and pw_pre[l]["median_mm"] is not None) else None
        d_field = (f["median_mm"] - pw_field[l]["median_mm"]) if (f["median_mm"] is not None and pw_field[l]["median_mm"] is not None) else None
        asym = (f["median_TX"] - f["median_RX"]) if (f["median_TX"] is not None and f["median_RX"] is not None) else None
        flag = (f["mad_mm"] is not None and f["mad_mm"] > 100) or (d_pre is not None and abs(d_pre) > 50)
        pairwise[f"{anc}-{l}"] = {
            "median_mm": round(f["median_mm"], 1) if f["median_mm"] is not None else None,
            "mad_mm": round(f["mad_mm"], 1) if f["mad_mm"] is not None else None,
            "n_TX": f["n_TX"], "n_RX": f["n_RX"],
            "median_TX_mm": round(f["median_TX"], 1) if f["median_TX"] is not None else None,
            "median_RX_mm": round(f["median_RX"], 1) if f["median_RX"] is not None else None,
            "asymmetry_TXminusRX_mm": round(asym, 1) if asym is not None else None,
            "delta_vs_premove_mm": round(d_pre, 1) if d_pre is not None else None,
            "delta_vs_field_mm": round(d_field, 1) if d_field is not None else None,
            "flag": bool(flag),
        }
    direc = dg.directional_analysis(pos6, Dc, anc, clean=CORE, flagged=FLAG)
    make_figure(anc, direc, f"{OUT}/figures/{anc}_directional_bias.png")
    bias = classify_bias(direc["rows"])
    ml = dg.multilat_vs_clean6(pos6, Dc, anc, clean=CORE)
    v4pos = v4io[anc]
    worst = sorted(ml["resid"].items(), key=lambda kv: -abs(kv[1]))
    links_c = [(l, dg.undirected_med(Dc, anc, l)) for l in CORE if dg.undirected_med(Dc, anc, l) is not None]
    basins = dg.basin_scan(pos6, links_c, seeds=[v4pos])
    ill = len(basins) >= 2 and basins[1]["wrms"] <= 1.4 * basins[0]["wrms"] and basins[1]["worst_link"] != basins[0]["worst_link"]
    consistency = {
        "multilat_pos_mm": [round(float(v), 1) for v in ml["pos"]],
        "v4io_pos_mm": [round(float(v), 1) for v in v4pos],
        "multilat_vs_v4io_sep_mm": round(float(np.linalg.norm(np.array(ml["pos"]) - v4pos)), 1),
        "per_link_resid_mm": {k: round(v, 1) for k, v in ml["resid"].items()},
        "total_rms_mm": ml["rms"], "robust_wrms_mm": ml["wrms"],
        "suspect_links": [f"{anc}-{k}" for k, _ in worst[:2]],
        "suspect_resid_mm": [round(worst[0][1], 1), round(worst[1][1], 1)],
        "n_distinct_basins": len(basins),
        "basins": [{"pos_mm": b["pos"], "wrms_mm": b["wrms"], "worst_link": f"{anc}-{b['worst_link']}",
                    "worst_resid_mm": b["worst_resid_mm"]} for b in basins[:3]],
        "ill_conditioned": bool(ill),
    }
    temporal = dg.temporal_chunks(pos6, Dc, anc, clean=CORE)
    stable = temporal.get("available") and temporal.get("max_pairwise_mm", 1e9) < STABLE_MM
    return {
        "pairwise": pairwise,
        "directional": {"fit": direc["fit"], "bias_class": bias, "rows": direc["rows"]},
        "consistency": consistency, "temporal": temporal,
        "signals": {"bias_kind": bias["kind"], "worst_link": consistency["suspect_links"][0],
                    "worst_resid_mm": consistency["suspect_resid_mm"][0],
                    "ill_conditioned_position": consistency["ill_conditioned"],
                    "temporally_stable": bool(stable)},
    }


def main():
    D = {k: dg.load_directed(v) for k, v in SWEEPS.items()}
    Dc = D[CURRENT]
    pos6, rms5 = dg.clean6_frame(Dc, clean=CORE)
    v4raw = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]])
             for a in json.load(open(f"{OUT}/layout_full8.json"))["anchors"]}
    Rv, tv = dg.procrustes([v4raw[l] for l in CORE], [pos6[l] for l in CORE])
    v4io = {l: Rv @ v4raw[l] + tv for l in dg.LABELS}

    per_ds = {}
    for k in SWEEPS:
        rmap, r5 = dg.per_anchor_rms(D[k], clean=CORE)
        per_ds[k] = {"core_rms_mm": r5, "anchors": rmap}

    # before/after-move comparison for the moved anchors (B,F) vs clean-5
    move_cmp = {}
    for anc in MOVED:
        pre = per_ds["premove"]["anchors"][anc]
        post = per_ds["postmove"]["anchors"][anc]
        move_cmp[anc] = {
            "rms_premove_mm": pre["rms_mm"], "rms_postmove_mm": post["rms_mm"],
            "rms_change_mm": (round(post["rms_mm"] - pre["rms_mm"], 1)
                              if (pre["rms_mm"] is not None and post["rms_mm"] is not None) else None),
            "worst_link_premove": f"{pre['worst_link']} ({pre['worst_resid_mm']})",
            "worst_link_postmove": f"{post['worst_link']} ({post['worst_resid_mm']})",
        }
    # rigid check: B-F distance should be ~unchanged (same stand)
    bf_pre = dg.undirected_med(D["premove"], "B", "F")
    bf_post = dg.undirected_med(D["postmove"], "B", "F")
    move_cmp["BF_rigid_check"] = {"BF_median_premove_mm": bf_pre, "BF_median_postmove_mm": bf_post,
                                  "BF_change_mm": (round(bf_post - bf_pre, 1) if (bf_pre and bf_post) else None),
                                  "note": "same stand -> should be near-constant if moved rigidly"}

    result = {
        "meta": {
            "generated_for": "AutoPos B/F/H diagnostic AFTER moving B+F stand off the wall",
            "reference_core": "".join(CORE) + " (5 untouched anchors)",
            "moved_anchors": MOVED, "flagged": FLAG, "current_dataset": CURRENT,
            "postmove_core_selfconsistency_rms_mm": round(rms5, 1),
            "datasets": {k: os.path.relpath(v, REPO) for k, v in SWEEPS.items()},
            "outlier_threshold_mm": OUTLIER_MM, "stable_threshold_mm": STABLE_MM,
            "resid_convention": "measured - predicted (positive = link reads long vs solved position)",
        },
        "move_comparison": move_cmp,
        "comparison_table": per_ds,
        "anchors": {anc: anchor_block(D, pos6, v4io, anc) for anc in FLAG},
    }
    with open(f"{OUT}/diagnostic_summary.json", "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
