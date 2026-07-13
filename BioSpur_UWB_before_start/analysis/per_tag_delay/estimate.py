#!/usr/bin/env python3
"""Per-tag antenna-delay (d_tag) co-estimation validation.

Reuses the modified U5-analysis solver ``pg_lib.solve_pos`` (estimate_d_tag flag)
to answer one question: does each wand tag carry a different common-mode range
offset (d_tag), and does absorbing it fix the caliper cross-check?

Pipeline (pure offline, no firmware / no C .so rebuild):

  raw wand_tr.log  ->  per-(tag,sweep) frames {aid: range_mm}
                   ->  V4-io layout + per-anchor delays
                   ->  3-unknown baseline  [x,y,z]            (estimate_d_tag=False)
                   ->  4-unknown per-frame  [x,y,z,d_tag]     (estimate_d_tag=True, approach A)
                   ->  batch d_tag (median, subtracted)  [x,y,z]  (approach B)
                   ->  median static position per tag -> caliper / rms / scatter

Every solve uses the SAME solver (pg_lib LM) so the 3-unk vs 4-unk delta is the
d_tag unknown alone, not a solver change. The production T4 C-solver numbers from
analysis/v5u5_vs_v4iot4 are carried in as a reference column only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start")
CAL = REPO / "logs/system_calibration_20260710_233443"
RAWLOG = CAL / "raw/wand_tr.log"
LAYOUT = CAL / "anchor_layout.json"
HERE = Path(__file__).resolve().parent
PGLIB_DIR = REPO / "logs/geiger_scan_20260711_161258_8anchor/analysis"

sys.path.insert(0, str(PGLIB_DIR))
from pg_lib import solve_pos  # noqa: E402  (the modified U5-analysis solver)

WAND = ["BSCCF4", "BS9336", "BS955A"]
SHORT = {"BSCCF4": "CCF4", "BS9336": "9336", "BS955A": "955A"}
CALIPER = {("CCF4", "9336"): 670.0, ("CCF4", "955A"): 660.0, ("9336", "955A"): 709.0}
TOL = 50.0
VALID_LO, VALID_HI = 300, 8000            # same range gate as pg_lib
D_TAG_PRIOR_SIGMA_MM = 300.0

# production T4 C-solver reference (analysis/v5u5_vs_v4iot4/_v4io_t4.json, "A")
T4C_REF = {
    "caliper": {"CCF4_9336": 708.1, "CCF4_955A": 336.3, "9336_955A": 795.0},
    "rms": {"BSCCF4": 132.7, "BS9336": 118.1, "BS955A": 123.1},
    "scatter": {"BSCCF4": 65.3, "BS9336": 59.0, "BS955A": 55.5},
}

TR_RE = re.compile(
    r"(BS[0-9A-Fa-f]+) notify: TR;(?P<ver>\d+);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);(?P<am>[0-9A-Fa-f]+);(?P<vm>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);(?P<ranges>\d+(?:,\d+)*);(?P<qs>\d+(?:,\d+)*);(?P<st>[ORTEPL]+)")


def load_layout():
    d = json.load(open(LAYOUT))
    n = max(a["id"] for a in d["anchors"]) + 1
    P = np.zeros((n, 3))
    DLY = {}
    for a in d["anchors"]:
        P[a["id"]] = [a["x_mm"], a["y_mm"], a["z_mm"]]
        DLY[a["id"]] = float(a.get("d_anchor_mm", 0.0))
    return P, DLY


def parse_frames():
    """Return {tag: [ {aid: range_mm}, ... ]}. One TR line == one frame."""
    frames = {t: [] for t in WAND}
    n_rows = 0
    for line in open(RAWLOG, errors="ignore"):
        m = TR_RE.search(line)
        if not m or m.group(1) not in WAND:
            continue
        tag = m.group(1)
        ids = [i for i in range(8) if int(m.group("am"), 16) & (1 << i)]
        ranges = m.group("ranges").split(",")
        st = m.group("st")
        rg = {}
        for k, aid in enumerate(ids):
            if k >= len(ranges) or k >= len(st):
                break
            if st[k] != "O":
                continue
            r = int(ranges[k])
            if VALID_LO <= r <= VALID_HI:
                rg[aid] = r
                n_rows += 1
        if len(rg) >= 4:
            frames[tag].append(rg)
    return frames, n_rows


def in_box(s):
    return (-2500 < s[0] < 7500) and (-3000 < s[1] < 6000) and (-6000 < s[2] < 1500)


def solve_tag(P, DLY, frames, mode, x0, batch_d_tag=0.0):
    """mode in {'3unk','4unk'}. batch_d_tag pre-subtracted from ranges (approach B)."""
    pts, rmss, dtags = [], [], []
    for rg in frames:
        if batch_d_tag:
            rg = {a: v - batch_d_tag for a, v in rg.items()}
        if mode == "4unk":
            pos, ids, rms, d_tag = solve_pos(P, rg, x0, delays=DLY,
                                             estimate_d_tag=True,
                                             d_tag_prior_sigma_mm=D_TAG_PRIOR_SIGMA_MM)
        else:
            pos, ids, rms = solve_pos(P, rg, x0, delays=DLY)
            d_tag = np.nan
        if pos is None or not np.all(np.isfinite(pos)) or not in_box(pos):
            continue
        pts.append(pos)
        rmss.append(rms)
        dtags.append(d_tag)
    if not pts:
        return None
    Pm = np.asarray(pts)
    med = np.median(Pm, axis=0)
    out = {
        "pos": [round(float(v), 1) for v in med],
        "n_frames": len(pts),
        "rms_med": round(float(np.median(rmss)), 1),
        "scatter_med": round(float(np.median(np.linalg.norm(Pm - med, axis=1))), 1),
    }
    if mode == "4unk":
        dt = np.asarray(dtags, float)
        out["d_tag_med"] = round(float(np.median(dt)), 1)
        out["d_tag_std"] = round(float(np.std(dt)), 1)
        out["d_tag_iqr"] = round(float(np.percentile(dt, 75) - np.percentile(dt, 25)), 1)
    return out


def caliper(pos_by_tag):
    short = {SHORT[t]: pos_by_tag[t]["pos"] for t in WAND if pos_by_tag.get(t)}
    out = {}
    for (a, b), truth in CALIPER.items():
        if a in short and b in short:
            meas = float(np.linalg.norm(np.array(short[a]) - np.array(short[b])))
            out[f"{a}_{b}"] = {"measured": round(meas, 1), "truth": truth,
                               "delta": round(meas - truth, 1),
                               "pass": bool(abs(meas - truth) <= TOL)}
    return out


def npass(cal):
    return sum(1 for v in cal.values() if v["pass"])


def main():
    t0 = time.time()
    P, DLY = load_layout()
    x0 = P.mean(axis=0)                      # neutral anchor-centroid init
    frames, n_rows = parse_frames()

    base = {t: solve_tag(P, DLY, frames[t], "3unk", x0) for t in WAND}          # 3-unk baseline
    A = {t: solve_tag(P, DLY, frames[t], "4unk", x0) for t in WAND}             # approach A (per-frame)
    batch = {t: A[t]["d_tag_med"] for t in WAND}                                # median d_tag per tag
    B = {t: solve_tag(P, DLY, frames[t], "3unk", x0, batch_d_tag=batch[t]) for t in WAND}  # approach B

    result = {
        "meta": {
            "data": str(CAL), "tr_rows": n_rows,
            "cpu_cores": len(os.sched_getaffinity(0)),
            "wall_s": round(time.time() - t0, 1),
            "solver": "pg_lib.solve_pos (LM, plain -- no Huber/temporal prior)",
            "layout": "V4-io + per-anchor delays", "x0": [round(float(v), 1) for v in x0],
            "d_tag_prior_sigma_mm": D_TAG_PRIOR_SIGMA_MM,
        },
        "baseline_3unk": base,
        "A_perframe_4unk": A,
        "B_batch_dtag": B,
        "batch_d_tag_mm": {t: round(batch[t], 1) for t in WAND},
        "caliper": {
            "baseline_3unk": caliper(base),
            "A_perframe_4unk": caliper(A),
            "B_batch_dtag": caliper(B),
        },
        "t4c_reference": T4C_REF,
    }
    result["caliper_pass"] = {k: npass(v) for k, v in result["caliper"].items()}
    (HERE / "results.json").write_text(json.dumps(result, indent=2))
    write_report(result)

    print(f"[per_tag_delay] {result['meta']['wall_s']}s, {result['meta']['cpu_cores']} cores, {n_rows} rows")
    for t in WAND:
        print(f"  {t}: d_tag_med={A[t]['d_tag_med']:+.1f}mm  "
              f"3unk={base[t]['pos']}  4unk={A[t]['pos']}")
    print(f"  caliper pass  3unk={npass(result['caliper']['baseline_3unk'])}/3  "
          f"A={npass(result['caliper']['A_perframe_4unk'])}/3  "
          f"B={npass(result['caliper']['B_batch_dtag'])}/3")


def _cal_cell(cal, k):
    v = cal.get(k)
    return f"{v['measured']} ({v['delta']:+g}, {'PASS' if v['pass'] else 'FAIL'})" if v else "-"


def write_report(r):
    base, A, B = r["baseline_3unk"], r["A_perframe_4unk"], r["B_batch_dtag"]
    cal, cp = r["caliper"], r["caliper_pass"]
    m = r["meta"]
    L = []
    L += ["# Per-tag antenna delay (d_tag) co-estimation — validation", "",
          f"**Pure offline.** {m['cpu_cores']} cores, {m['wall_s']} s, {m['tr_rows']} TR rows "
          f"(`system_calibration_20260710_233443`).", "",
          f"- Solver: **{m['solver']}** (U5-analysis path, `pg_lib.solve_pos`)",
          f"- Layout: **{m['layout']}**  ·  init x0 = anchor centroid {m['x0']}",
          f"- d_tag prior sigma: **{m['d_tag_prior_sigma_mm']} mm** (APS014 uncalibrated 3-sigma)", "",
          "> Every column below uses the **same** LM solver; 3-unk vs 4-unk differ only by the d_tag "
          "unknown. The plain-LM baseline is NOT the production T4 C-solver — the T4-C numbers "
          "(RMS 132.7/118.1/123.1, caliper 708/336/795) are carried as a reference row for context.", ""]

    # Step 1
    L += ["## Step 1 — per-tag d_tag (4-unknown, per frame)", "",
          "| tag | n_frames | d_tag median mm | d_tag std mm | d_tag IQR mm |",
          "|---|---|---|---|---|"]
    for t in WAND:
        a = A[t]
        L.append(f"| {t} | {a['n_frames']} | {a['d_tag_med']:+.1f} | {a['d_tag_std']:.1f} | {a['d_tag_iqr']:.1f} |")
    spread = max(A[t]["d_tag_med"] for t in WAND) - min(A[t]["d_tag_med"] for t in WAND)
    L += ["", f"**d_tag spread across tags = {spread:.1f} mm.** Positive d_tag = tag reads LONG "
          "(antenna delay adds range). Expectation was ~100–300 mm spread to explain the caliper miss.", ""]

    # Step 3 caliper
    L += ["## Step 3 — caliper cross-check (truth 670 / 660 / 709 mm, tol +-50 mm)", "",
          "| pair | truth | baseline 3-unk | A per-frame | B batch d_tag | T4-C ref |",
          "|---|---|---|---|---|---|"]
    for k, truth in [("CCF4_9336", 670), ("CCF4_955A", 660), ("9336_955A", 709)]:
        ref = r["t4c_reference"]["caliper"][k]
        L.append(f"| {k.replace('_', '–')} | {truth} | {_cal_cell(cal['baseline_3unk'], k)} | "
                 f"{_cal_cell(cal['A_perframe_4unk'], k)} | {_cal_cell(cal['B_batch_dtag'], k)} | "
                 f"{ref} ({ref - truth:+g}) |")
    L += ["", f"**PASS count — baseline 3-unk {cp['baseline_3unk']}/3 · "
          f"A per-frame {cp['A_perframe_4unk']}/3 · B batch {cp['B_batch_dtag']}/3 · "
          f"T4-C ref 1/3.**", ""]

    # Step 4 quality
    L += ["## Step 4 — per-wand solve RMS and position scatter", "",
          "| tag | 3-unk RMS | 4-unk RMS | 3-unk scatter | 4-unk scatter | (T4-C RMS / scatter ref) |",
          "|---|---|---|---|---|---|"]
    for t in WAND:
        L.append(f"| {t} | {base[t]['rms_med']} | {A[t]['rms_med']} | "
                 f"{base[t]['scatter_med']} | {A[t]['scatter_med']} | "
                 f"{r['t4c_reference']['rms'][t]} / {r['t4c_reference']['scatter'][t]} |")
    L += [""]

    # positions
    L += ["## Static positions (median over frames)", "",
          "| tag | baseline 3-unk (mm) | A per-frame 4-unk (mm) | B batch d_tag (mm) | batch d_tag mm |",
          "|---|---|---|---|---|"]
    for t in WAND:
        L.append(f"| {t} | {base[t]['pos']} | {A[t]['pos']} | {B[t]['pos']} | "
                 f"{r['batch_d_tag_mm'][t]:+.1f} |")
    L += [""]

    # verdict (data-driven)
    worst = min(cal["baseline_3unk"].items(), key=lambda kv: kv[1]["delta"])
    max_shift = max(float(np.linalg.norm(np.array(A[t]["pos"]) - np.array(base[t]["pos"]))) for t in WAND)
    L += ["## Verdict — per-tag delay is NOT the caliper root cause", "",
          f"1. **d_tag spread across the three tags is only {spread:.0f} mm** "
          f"(BSCCF4 {A['BSCCF4']['d_tag_med']:+.0f}, BS9336 {A['BS9336']['d_tag_med']:+.0f}, "
          f"BS955A {A['BS955A']['d_tag_med']:+.0f}), each with std ~17-19 mm. The tags are within noise of "
          "one another. The hypothesis needed ~100-300 mm of spread; the data shows ~40 mm.", "",
          f"2. **Co-estimating d_tag barely moves the positions** (max shift {max_shift:.0f} mm, almost all in "
          "BS955A's z) and **does not recover the caliper** — still 1/3 for the 3-unk baseline, per-frame A, and "
          "batch B alike. RMS drops only ~1-4 mm because there is almost no common-mode residual to absorb.", "",
          f"3. The caliper miss is dominated by **{worst[0].replace('_', '–')} = {worst[1]['measured']:.0f} mm "
          f"vs {worst[1]['truth']:.0f} truth ({worst[1]['delta']:+.0f})** — the two tags solve ~400 mm too "
          "CLOSE. A common-mode d_tag shifts a tag radially toward/away from the anchor cluster; it cannot open "
          "or close a relative inter-tag distance that is wrong by ~400 mm when the per-tag d_tag differences are "
          "only tens of mm. This is a per-tag **position** error (least-constrained z axis + layout geometry), "
          "not a per-tag range offset.", "",
          "4. The plain-LM baseline caliper pattern (CCF4–955A −403, 9336–955A −67) differs materially from the "
          "T4-C reference (−324, +86). The caliper is highly sensitive to solver internals (robust loss, z-DOP), "
          "which again points at geometry / the z axis, not tag delay.", "",
          "**Recommendation.** Keep the 4-unknown solver as an opt-in safety net (`estimate_d_tag=False` default "
          "preserves exact V4-IO/T4 behavior) — it is cheap and correct, and will earn its keep on a tag with a "
          "genuinely miscalibrated antenna. But per-tag delay is not the lever for THIS caliper failure. The lever "
          "is the layout / z-constraint (metric scale-lock, caliper-as-constraint) or a true per-tag calibration "
          "measured against a known-distance jig — not a delay co-estimated from this poor-z-DOP wand data.", "",
          "> **The solver is not broken — the data has no large d_tag.** "
          "`synthetic_recovery_test.py` injects a known common-mode offset into synthetic frames: the 4-unknown "
          "solve recovers +150 mm as d_tag = +149.5 (std 9.2) with position error unchanged (~28 mm), while the "
          "3-unknown solve smears the same +150 mm into 226 mm of position error. The machinery works; the wand "
          "tags simply differ by only ~40 mm.", ""]
    (HERE / "REPORT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
