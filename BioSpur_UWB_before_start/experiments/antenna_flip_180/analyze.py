#!/usr/bin/env python3
"""180 deg Antenna Flip Experiment -- ANALYSIS.

Parses raw_tr.log, bins each TR report into phase 1 / phase 2 by the offsets in
metadata.json, and asks whether flipping the wand 180 deg produces the per-anchor
range signature of PCB-antenna directionality:

    tags 9336 and 955A share antenna orientation  -> their deltas should track
    tag CCF4 is mounted 180 deg opposite           -> its delta should invert

Q1  anti-correlation  corr(D_9336, -D_CCF4) across anchors  (>0.7 => directionality)
Q2  effect size       max|D|, median|D|, RMS(D)             (RMS>30 => major; <10 => minor)
Q3  which anchors     rank by |D|; anchors on the flip axis should dominate
Q4  scatter           std_phase1 vs std_phase2 per tag x anchor
2.4 caliper           inter-tag distance change between phases (measured), plus the
                      CCF4-specific "relative delta" that a solver absorbs as a shift

CONFOUND: a physical 180 deg flip also TRANSLATES any off-pivot tag, injecting a
real geometric range change on the same ADHE/BCFG axis.  The report solves each
tag's position per phase (when an anchor layout is available) so displacement is
quantified and separated from the antenna signal; a positive Q1 alone is necessary
but not sufficient.

Ranges used: RAW (TR field 7), status=='O' only, within [500, 7000] mm.
Outputs: REPORT.md, results.json.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def find_repo_root(start: str) -> str:
    d = start
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "logs", "listener_calibration")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return os.path.abspath(os.path.join(start, "..", ".."))


REPO = find_repo_root(HERE)

# Optional reuse of the production multilaterator (numpy+scipy) for the geometric
# / caliper section.  Analysis degrades gracefully if it or the layout is absent.
_cal = None
try:
    sys.path.insert(0, os.path.join(REPO, "logs", "listener_calibration"))
    import calibrate_listener_positions as _cal  # noqa: E402
except Exception:  # pragma: no cover
    _cal = None

RANGE_MIN_MM = getattr(_cal, "RANGE_MIN_MM", 500.0)
RANGE_MAX_MM = getattr(_cal, "RANGE_MAX_MM", 7000.0)
DEFAULT_LAYOUT = os.path.join(REPO, "logs", "system_calibration_20260710_233443", "anchor_layout.json")

LABELS = "ABCDEFGH"
MAX_ANCHORS = 8

# "<...>BSxxxx notify: TR;<fields...>"
TR_RE = re.compile(r"(?P<name>BS[0-9A-Fa-f]{4}) notify: (?P<tr>TR;[^\r\n]*)")


def mask_ids(mask_hex: str) -> list[int]:
    m = int(mask_hex, 16)
    return [i for i in range(MAX_ANCHORS) if m & (1 << i)]


def parse_tr(tr: str):
    """Return (list_of_anchor_ids, list_of_raw_mm, statuses) or None.

    Field layout (12 ';'-fields, verified from the live master stream):
      0 TR  1 ver  2 sweep  3 plan  4 pmode  5 active_mask  6 valid_mask
      7 raws  8 filt  9 quals  10 statuses  11 trailer
    ranges/statuses align to the active_mask set bits; status 'O' == fresh OK.
    """
    f = tr.split(";")
    if len(f) < 11:
        return None
    try:
        ids = mask_ids(f[5])
    except ValueError:
        return None
    try:
        raws = [int(x) for x in f[7].split(",") if x != ""]
    except ValueError:
        return None
    statuses = f[10]
    return ids, raws, statuses


def load_metadata(path: str) -> dict:
    if os.path.isfile(path):
        with open(path) as fh:
            return json.load(fh)
    # Defaults matching capture.py if metadata is missing.
    return {
        "tags": ["BS9336", "BS955A", "BSCCF4"],
        "phase1": {"start_s": 0.0, "end_s": 120.0},
        "buffer": {"start_s": 120.0, "end_s": 140.0},
        "phase2": {"start_s": 140.0, "end_s": 260.0},
        "note": "metadata.json missing; using default 120/20/120 offsets",
    }


def phase_of(el: float, meta: dict) -> str | None:
    p1, p2 = meta["phase1"], meta["phase2"]
    if p1["start_s"] <= el < p1["end_s"]:
        return "phase1"
    if p2["start_s"] <= el < p2["end_s"]:
        return "phase2"
    return None  # buffer / pre / post -> excluded from stats


def parse_log(path: str, meta: dict):
    """samples[tag][phase][aid] -> list[float raw_mm]."""
    tags = [t.upper() for t in meta["tags"]]
    samples = {t: {"phase1": {a: [] for a in range(MAX_ANCHORS)},
                   "phase2": {a: [] for a in range(MAX_ANCHORS)}} for t in tags}
    n_lines = n_tr = n_kept = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            n_lines += 1
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t", 2)
            if len(parts) != 3:
                continue
            try:
                el = float(parts[0])
            except ValueError:
                continue
            content = parts[2]
            m = TR_RE.search(content)
            if not m:
                continue
            n_tr += 1
            nm = m.group("name").upper()
            if nm not in samples:
                continue
            ph = phase_of(el, meta)
            if ph is None:
                continue
            parsed = parse_tr(m.group("tr"))
            if not parsed:
                continue
            ids, raws, statuses = parsed
            for k, aid in enumerate(ids):
                if k < len(raws) and k < len(statuses) and statuses[k] == "O":
                    r = raws[k]
                    if RANGE_MIN_MM <= r <= RANGE_MAX_MM:
                        samples[nm][ph][aid].append(float(r))
                        n_kept += 1
    return samples, {"lines": n_lines, "tr": n_tr, "kept_ranges": n_kept}


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0, "mean": None, "median": None, "std": None}
    return {
        "n": len(vals),
        "mean": float(statistics.mean(vals)),
        "median": float(statistics.median(vals)),
        "std": float(statistics.pstdev(vals)) if len(vals) > 1 else 0.0,
    }


def pearson(x: list[float], y: list[float]):
    if len(x) < 3 or len(y) < 3:
        return None
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    if xa.std() < 1e-9 or ya.std() < 1e-9:
        return None
    return float(np.corrcoef(xa, ya)[0, 1])


def solve_positions(samples: dict, tags: list[str], layout_path: str):
    """Per-tag per-phase multilateration from median ranges. Best-effort."""
    if _cal is None or not os.path.isfile(layout_path):
        return None, "solver or anchor layout unavailable"
    try:
        anchors = _cal.load_anchor_layout(layout_path)
    except Exception as exc:  # pragma: no cover
        return None, f"layout load failed: {exc}"
    out = {}
    for t in tags:
        out[t] = {}
        for ph in ("phase1", "phase2"):
            med = {a: float(np.median(v)) for a, v in samples[t][ph].items() if v}
            pos, rms, n = _cal.multilaterate(anchors, med)
            out[t][ph] = {
                "position_mm": None if pos is None else [round(float(x), 1) for x in pos],
                "rms_mm": None if rms is None else round(float(rms), 1),
                "n_ranges": int(n),
            }
    return out, os.path.relpath(layout_path, REPO)


def dist(a, b):
    if a is None or b is None:
        return None
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def fmt(v, nd=1):
    return "n/a" if v is None else f"{v:.{nd}f}"


def main() -> int:
    t_wall0, t_cpu0 = time.perf_counter(), time.process_time()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=HERE, help="Experiment dir (raw_tr.log + metadata.json)")
    ap.add_argument("--layout", default=DEFAULT_LAYOUT, help="Anchor layout JSON for the caliper/geometry section")
    args = ap.parse_args()

    raw_log = os.path.join(args.dir, "raw_tr.log")
    meta_path = os.path.join(args.dir, "metadata.json")
    if not os.path.isfile(raw_log):
        sys.exit(f"raw_tr.log not found in {args.dir}; run capture.py first")

    meta = load_metadata(meta_path)
    tags = [t.upper() for t in meta["tags"]]
    samples, counts = parse_log(raw_log, meta)

    # ---- per tag x anchor x phase stats + delta ----
    per = {}          # per[tag][aid] = {p1:stats, p2:stats, delta, delta_med}
    anchors_present = set()
    for t in tags:
        per[t] = {}
        for a in range(MAX_ANCHORS):
            s1 = stats(samples[t]["phase1"][a])
            s2 = stats(samples[t]["phase2"][a])
            delta = None
            delta_med = None
            if s1["mean"] is not None and s2["mean"] is not None:
                delta = s2["mean"] - s1["mean"]
                delta_med = s2["median"] - s1["median"]
                anchors_present.add(a)
            per[t][a] = {"phase1": s1, "phase2": s2, "delta": delta, "delta_med": delta_med}
    anchor_list = sorted(anchors_present)

    # ---- Q1: anti-correlation across anchors ----
    def delta_vec(tag):
        return {a: per[tag][a]["delta"] for a in anchor_list if per[tag][a]["delta"] is not None}

    def paired(t_a, t_b):
        common = [a for a in anchor_list
                  if per[t_a][a]["delta"] is not None and per[t_b][a]["delta"] is not None]
        return common, [per[t_a][a]["delta"] for a in common], [per[t_b][a]["delta"] for a in common]

    q1 = {}
    tag_9336 = next((t for t in tags if "9336" in t), None)
    tag_955a = next((t for t in tags if "955A" in t), None)
    tag_ccf4 = next((t for t in tags if "CCF4" in t), None)
    if tag_9336 and tag_ccf4:
        c, d9, dc = paired(tag_9336, tag_ccf4)
        q1["corr_9336_vs_negCCF4"] = pearson(d9, [-x for x in dc])
        q1["corr_9336_vs_CCF4"] = pearson(d9, dc)
        q1["n_anchors"] = len(c)
    if tag_955a and tag_ccf4:
        c, d5, dc = paired(tag_955a, tag_ccf4)
        q1["corr_955A_vs_negCCF4"] = pearson(d5, [-x for x in dc])
    if tag_9336 and tag_955a:
        c, d9, d5 = paired(tag_9336, tag_955a)
        q1["corr_9336_vs_955A"] = pearson(d9, d5)  # siblings: expect strongly +

    anti = [q1.get("corr_9336_vs_negCCF4"), q1.get("corr_955A_vs_negCCF4")]
    anti = [x for x in anti if x is not None]
    q1["antiacorr_mean"] = float(np.mean(anti)) if anti else None
    q1["verdict"] = (
        "anti-correlated (directionality signature)" if q1.get("antiacorr_mean") is not None
        and q1["antiacorr_mean"] > 0.7 else
        "no clear anti-correlation" if q1.get("antiacorr_mean") is not None else "insufficient data")

    # ---- Q2: effect size across all tag x anchor deltas ----
    all_deltas = [per[t][a]["delta"] for t in tags for a in anchor_list
                  if per[t][a]["delta"] is not None]
    q2 = {}
    if all_deltas:
        arr = np.asarray(all_deltas)
        q2 = {
            "n": int(arr.size),
            "max_abs_mm": float(np.max(np.abs(arr))),
            "median_abs_mm": float(np.median(np.abs(arr))),
            "rms_mm": float(np.sqrt(np.mean(arr ** 2))),
            "mean_signed_mm": float(np.mean(arr)),
        }
        q2["classification"] = (
            "major (RMS>30mm)" if q2["rms_mm"] > 30 else
            "minor (RMS<10mm)" if q2["rms_mm"] < 10 else "moderate (10-30mm)")

    # ---- Q3: which anchors dominate ----
    q3 = []
    for a in anchor_list:
        ds = [abs(per[t][a]["delta"]) for t in tags if per[t][a]["delta"] is not None]
        if ds:
            q3.append({"anchor": LABELS[a], "mean_abs_delta_mm": float(np.mean(ds))})
    q3.sort(key=lambda r: r["mean_abs_delta_mm"], reverse=True)

    # ---- Q4: scatter change ----
    q4 = []
    for t in tags:
        for a in anchor_list:
            s1, s2 = per[t][a]["phase1"], per[t][a]["phase2"]
            if s1["std"] is not None and s2["std"] is not None:
                q4.append({"tag": t, "anchor": LABELS[a],
                           "std1_mm": s1["std"], "std2_mm": s2["std"],
                           "dstd_mm": s2["std"] - s1["std"]})
    q4_summary = {}
    if q4:
        q4_summary = {
            "mean_std_phase1_mm": float(np.mean([r["std1_mm"] for r in q4])),
            "mean_std_phase2_mm": float(np.mean([r["std2_mm"] for r in q4])),
            "mean_dstd_mm": float(np.mean([r["dstd_mm"] for r in q4])),
        }

    # ---- 2.4 caliper: measured inter-tag distance change + CCF4 relative delta ----
    positions, layout_ref = solve_positions(samples, tags, args.layout)
    caliper = {"layout": layout_ref}
    if positions:
        def pos(t, ph):
            return positions[t][ph]["position_mm"]
        # physical displacement of each tag between phases (geometric confound)
        caliper["displacement_mm"] = {
            t: round(dist(pos(t, "phase1"), pos(t, "phase2")), 1)
            if dist(pos(t, "phase1"), pos(t, "phase2")) is not None else None
            for t in tags}
        # inter-tag distances per phase; a rigid wand => these are constant.
        pairs = []
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                ti, tj = tags[i], tags[j]
                d1 = dist(pos(ti, "phase1"), pos(tj, "phase1"))
                d2 = dist(pos(ti, "phase2"), pos(tj, "phase2"))
                pairs.append({
                    "pair": f"{ti}-{tj}",
                    "dist_phase1_mm": None if d1 is None else round(d1, 1),
                    "dist_phase2_mm": None if d2 is None else round(d2, 1),
                    "change_mm": None if (d1 is None or d2 is None) else round(d2 - d1, 1),
                })
        caliper["intertag_distance"] = pairs
        caliper["positions"] = positions

    # CCF4-specific relative delta: subtract the sibling-mean delta (removes the
    # common rigid-body translation shared by all 3 tags) -> the residual is the
    # CCF4 antenna-opposite signal the solver would absorb as a position shift.
    rel = {}
    if tag_ccf4 and tag_9336 and tag_955a:
        vals = []
        for a in anchor_list:
            dcc = per[tag_ccf4][a]["delta"]
            d9 = per[tag_9336][a]["delta"]
            d5 = per[tag_955a][a]["delta"]
            if None in (dcc, d9, d5):
                continue
            r = dcc - 0.5 * (d9 + d5)
            rel[LABELS[a]] = round(r, 1)
            vals.append(r)
        if vals:
            caliper["ccf4_relative_delta_mm"] = rel
            caliper["ccf4_relative_delta_rms_mm"] = round(float(np.sqrt(np.mean(np.square(vals)))), 1)
            caliper["ccf4_relative_delta_max_abs_mm"] = round(float(np.max(np.abs(vals))), 1)

    # ---- verdict ----
    # The antenna-directionality signature requires BOTH: (a) the two same-orientation
    # siblings (9336, 955A) to move TOGETHER, and (b) CCF4 to invert.  A large physical
    # displacement means the flip translated the tags (they are off the rotation axis)
    # and rigid-rotation geometry -- not the antenna -- drives the deltas, so the effect
    # cannot be isolated regardless of how the correlations land.
    rms = q2.get("rms_mm")
    anti_m = q1.get("antiacorr_mean")
    sib = q1.get("corr_9336_vs_955A")
    disp_vals = [v for v in caliper.get("displacement_mm", {}).values() if v is not None]
    median_disp = float(np.median(disp_vals)) if disp_vals else None
    max_disp = float(np.max(disp_vals)) if disp_vals else None
    DISP_CONFOUND_MM = 80.0  # a flip should not move an on-axis tag more than this
    confounded = median_disp is not None and median_disp > DISP_CONFOUND_MM
    signature = (sib is not None and sib > 0.6) and (anti_m is not None and anti_m > 0.7)
    q1["signature_present"] = bool(signature)
    q1["signature_note"] = (
        "antenna signature requires sibling corr(9336,955A)>0.6 AND anti-corr>0.7")
    caliper["median_displacement_mm"] = None if median_disp is None else round(median_disp, 1)
    caliper["displacement_confound"] = bool(confounded)

    if rms is None:
        verdict = "INSUFFICIENT DATA"
    elif confounded:
        verdict = (
            "INCONCLUSIVE -- CONFOUNDED BY PHYSICAL DISPLACEMENT: the tags are off the "
            f"rotation axis (median flip displacement {median_disp:.0f} mm, max {max_disp:.0f} mm), "
            "so the 180 deg flip TRANSLATED them and rigid-body rotation geometry -- not PCB-antenna "
            "directionality -- dominates the per-anchor deltas. The antenna effect cannot be isolated "
            "from this run. Re-run with the flip axis passing through the tags (or place ONE tag on the "
            "rotation axis at a time).")
    elif signature and rms > 30:
        verdict = "DIRECTIONALITY IS a major error source"
    elif rms < 10:
        verdict = "DIRECTIONALITY IS NOT a major error source"
    elif signature:
        verdict = "DIRECTIONALITY present but modest (10-30mm)"
    else:
        verdict = ("INCONCLUSIVE -- effect size is large but the antenna signature is absent "
                   "(siblings 9336/955A do not track); deltas are not explained by antenna "
                   "directionality alone")

    elapsed_wall = time.perf_counter() - t_wall0
    elapsed_cpu = time.process_time() - t_cpu0

    results = {
        "experiment": "antenna_flip_180",
        "date": meta.get("date"),
        "tags": tags,
        "range_field": "raw (TR field 7), status=='O', bounds [%.0f,%.0f] mm" % (RANGE_MIN_MM, RANGE_MAX_MM),
        "counts": counts,
        "samples_per_tag_phase": {
            t: {ph: {LABELS[a]: len(samples[t][ph][a]) for a in anchor_list}
                for ph in ("phase1", "phase2")} for t in tags},
        "delta_table_mm": {t: {LABELS[a]: per[t][a]["delta"] for a in anchor_list} for t in tags},
        "delta_median_table_mm": {t: {LABELS[a]: per[t][a]["delta_med"] for a in anchor_list} for t in tags},
        "q1_anticorrelation": q1,
        "q2_effect_size": q2,
        "q3_anchor_ranking": q3,
        "q4_scatter": q4_summary,
        "q4_detail": q4,
        "caliper_prediction": caliper,
        "verdict": verdict,
        "runtime": {"wall_s": round(elapsed_wall, 3), "cpu_s": round(elapsed_cpu, 3),
                    "cpu_count": os.cpu_count()},
    }
    with open(os.path.join(args.dir, "results.json"), "w") as fh:
        json.dump(results, fh, indent=2)

    # ---- REPORT.md ----
    md = []
    md.append("# 180 deg Antenna Flip Experiment -- Report\n")
    md.append(f"- Date: {meta.get('date','?')}")
    md.append(f"- Tags: {', '.join(tags)}  (CCF4 antenna mounted 180 deg opposite to 9336/955A)")
    md.append(f"- Wand pose: {meta.get('wand_pose','?')}")
    md.append(f"- Phase 1 ({meta['phase1']['start_s']:.0f}-{meta['phase1']['end_s']:.0f}s): "
              f"{meta['phase1'].get('orientation','9336_955A_face_ADHE')}")
    md.append(f"- Phase 2 ({meta['phase2']['start_s']:.0f}-{meta['phase2']['end_s']:.0f}s): "
              f"{meta['phase2'].get('orientation','9336_955A_face_BCFG')}")
    md.append(f"- Ranges: RAW, status=='O', bounds [{RANGE_MIN_MM:.0f},{RANGE_MAX_MM:.0f}] mm")
    md.append(f"- TR lines parsed: {counts['tr']}, ranges kept: {counts['kept_ranges']}\n")

    md.append("## Per-tag per-anchor delta (phase2 - phase1), mean mm\n")
    hdr = "| anchor | " + " | ".join(f"{t} D (mm)" for t in tags) + " | max\\|D\\| | note |"
    sep = "|" + "---|" * (len(tags) + 3)
    md.append(hdr)
    md.append(sep)
    for a in anchor_list:
        cells = []
        row_abs = []
        for t in tags:
            d = per[t][a]["delta"]
            cells.append(fmt(d))
            if d is not None:
                row_abs.append(abs(d))
        mx = max(row_abs) if row_abs else 0.0
        note = "large" if mx > 30 else ("mod" if mx > 10 else "")
        md.append(f"| {LABELS[a]} | " + " | ".join(cells) + f" | {mx:.1f} | {note} |")
    md.append("")

    md.append("## Q1 -- Do 9336/955A shift opposite to CCF4?\n")
    md.append(f"- corr(D_9336, -D_CCF4)  = {fmt(q1.get('corr_9336_vs_negCCF4'),3)}")
    md.append(f"- corr(D_955A, -D_CCF4)  = {fmt(q1.get('corr_955A_vs_negCCF4'),3)}")
    md.append(f"- mean anti-correlation  = {fmt(q1.get('antiacorr_mean'),3)}  (n={q1.get('n_anchors','?')} anchors)")
    md.append(f"- sibling check corr(D_9336, D_955A) = {fmt(q1.get('corr_9336_vs_955A'),3)} (expect strongly +)")
    md.append(f"- **{q1.get('verdict','?')}**  (>0.7 => antenna directionality)\n")

    md.append("## Q2 -- Effect size\n")
    if q2:
        md.append(f"- max\\|D\\|   = {q2['max_abs_mm']:.1f} mm")
        md.append(f"- median\\|D\\|= {q2['median_abs_mm']:.1f} mm")
        md.append(f"- RMS(D)    = {q2['rms_mm']:.1f} mm  -> **{q2['classification']}**")
        md.append(f"- mean signed D = {q2['mean_signed_mm']:.1f} mm (common-mode across all tag x anchor)\n")
    else:
        md.append("- insufficient data\n")

    md.append("## Q3 -- Which anchors are most affected? (rank by mean |D|)\n")
    md.append("| rank | anchor | mean \\|D\\| (mm) |")
    md.append("|---|---|---|")
    for i, r in enumerate(q3, 1):
        md.append(f"| {i} | {r['anchor']} | {r['mean_abs_delta_mm']:.1f} |")
    md.append("\nAnchors on the ADHE/BCFG (flip-facing) axis should dominate; anchors on the "
              "perpendicular axis should be near zero. A layout table is in results.json.\n")

    md.append("## Q4 -- Within-phase scatter\n")
    if q4_summary:
        md.append(f"- mean std phase1 = {q4_summary['mean_std_phase1_mm']:.1f} mm")
        md.append(f"- mean std phase2 = {q4_summary['mean_std_phase2_mm']:.1f} mm")
        md.append(f"- mean d(std)     = {q4_summary['mean_dstd_mm']:+.1f} mm "
                  "(rising scatter on back-facing anchors => SNR drop, as expected)\n")
    else:
        md.append("- insufficient data\n")

    md.append("## 2.4 -- Caliper prediction\n")
    if "displacement_mm" in caliper:
        md.append("Physical displacement of each tag between phases (geometric confound):")
        for t in tags:
            md.append(f"- {t}: {fmt(caliper['displacement_mm'].get(t))} mm")
        md.append("")
        md.append("Measured inter-tag distance (rigid wand => should be constant; change = "
                  "flip-induced caliper distortion, antenna + residual geometry):")
        md.append("| pair | phase1 (mm) | phase2 (mm) | change (mm) |")
        md.append("|---|---|---|---|")
        for p in caliper["intertag_distance"]:
            md.append(f"| {p['pair']} | {fmt(p['dist_phase1_mm'])} | {fmt(p['dist_phase2_mm'])} "
                      f"| {fmt(p['change_mm'])} |")
        md.append("")
    else:
        md.append(f"- position solve unavailable ({layout_ref}); geometric/caliper section skipped\n")
    if "ccf4_relative_delta_rms_mm" in caliper:
        md.append("CCF4-specific relative delta (D_CCF4 - mean(D_9336, D_955A), removes the common "
                  "rigid-body translation shared by all 3 tags):")
        md.append(f"- RMS = {caliper['ccf4_relative_delta_rms_mm']} mm, "
                  f"max|.| = {caliper['ccf4_relative_delta_max_abs_mm']} mm")
        md.append("  This residual is the CCF4 antenna-opposite ranging swing a solver absorbs as a "
                  "position shift, directly distorting the CCF4-955A caliper.\n")

    md.append("## Confounds\n")
    md.append("- A 180 deg flip translates any off-pivot tag by 2x its radius, injecting a REAL "
              "geometric range change on the same ADHE/BCFG axis. Compare the per-tag displacement "
              "above: if it is large (>~50 mm), the raw deltas are geometry-dominated and a positive "
              "Q1 is necessary but not sufficient. The CCF4-relative-delta metric cancels the common "
              "translation and is the cleaner antenna signal.")
    md.append("- Siblings 9336 & 955A sit at different points on the T-bar, so their deltas match "
              "only to the extent geometry is common; a high sibling correlation supports a shared "
              "(orientation) mechanism over per-tag geometry.\n")

    md.append("## VERDICT\n")
    md.append(f"**{verdict}**\n")
    md.append(f"_runtime: wall={elapsed_wall:.2f}s cpu={elapsed_cpu:.2f}s on {os.cpu_count()} CPUs_")

    report_txt = "\n".join(md) + "\n"
    with open(os.path.join(args.dir, "REPORT.md"), "w") as fh:
        fh.write(report_txt)

    # ---- console summary ----
    print(report_txt)
    print(f"wrote: {os.path.join(args.dir, 'REPORT.md')}")
    print(f"wrote: {os.path.join(args.dir, 'results.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
