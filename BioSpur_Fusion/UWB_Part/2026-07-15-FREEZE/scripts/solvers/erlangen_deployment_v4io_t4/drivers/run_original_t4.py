#!/usr/bin/env python3
"""Original (当年) full V4-IO + T4 on logs/system_calibration_20260710_233443.

- Layout: the V4-IO anchor_layout.json (production V4-IO output; unmodified solver).
- Tag solver: T4 = the ORIGINAL c_solver, built pristine from git HEAD (before the U5
  edits). Run this script with PYTHONPATH pointing at the pristine package so it imports
  the pristine c_solver/models/capture_io/layout_io and loads the pristine .so.

Pipeline: wand_tr.log -> tr_all.csv (production column layout) -> read_tr_all_frames ->
one TagPositionSolver(method="T4") per tag, time-ordered -> median static position ->
caliper (670/660/709). Compared against the deployed wand_positions.json (Task 4 ref).
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

import numpy as np

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.models import SolverConfig

# ═══════════════════════════════════════════════════════════════════════════════
# ⚠️  RELOCATION WARNING (added 2026-07-20) — the REPO path below is a HARDCODED
#     ABSOLUTE path back into the ORIGINAL repo. This script reads its input data
#     and/or imports its solver modules from OUTSIDE this 2026-07-15-FREEZE folder
#     (they live in the parent repo, not in the freeze snapshot). If you COPY this
#     freeze folder to another machine/path and the original repo is NOT at
#     /mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start, this WILL fail
#     (FileNotFoundError / ModuleNotFoundError). Repoint REPO to the real repo
#     location before running from a new location. (Firmware in ../firmware/ is
#     self-contained and unaffected — this caveat is solver-only.)
# ═══════════════════════════════════════════════════════════════════════════════
REPO = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start")
CAL = REPO / "logs/system_calibration_20260710_233443"
OUT = REPO / "analysis/v4io_t4_original"
WAND = ["BSCCF4", "BS9336", "BS955A"]
SHORT = {"BSCCF4": "CCF4", "BS9336": "9336", "BS955A": "955A"}
CALIPER = {("CCF4", "9336"): 670.0, ("CCF4", "955A"): 660.0, ("9336", "955A"): 709.0}
TOL = 50.0

TR_RE = re.compile(
    r"(BS[0-9A-Fa-f]+) notify: TR;(?P<ver>\d+);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);(?P<am>[0-9A-Fa-f]+);(?P<vm>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);(?P<ranges>\d+(?:,\d+)*);(?P<qs>\d+(?:,\d+)*);(?P<st>[ORTEPL]+)")


def convert_raw_to_tr_all(rawlog: Path, csvpath: Path) -> int:
    cols = ["host_elapsed_s", "host_epoch_s", "sweep", "peer_name",
            "anchor_id", "raw_mm", "range_mm", "quality_percent", "valid", "status"]
    n = 0
    with open(rawlog, errors="ignore") as f, open(csvpath, "w", newline="") as g:
        w = csv.writer(g)
        w.writerow(cols)
        for line in f:
            m = TR_RE.search(line)
            if not m:
                continue
            name = m.group(1)
            if name not in WAND:
                continue
            sweep = int(m.group("sweep"))
            am = int(m.group("am"), 16)
            ids = [i for i in range(8) if am & (1 << i)]
            raws = m.group("raws").split(",")
            ranges = m.group("ranges").split(",")
            qs = m.group("qs").split(",")
            st = m.group("st")
            for k, aid in enumerate(ids):
                if k >= len(ranges) or k >= len(st):
                    break
                status = st[k]
                valid = 1 if status == "O" else 0
                # host_epoch_s = sweep -> monotonic time ordering (raw log has no timestamp)
                w.writerow([0.0, float(sweep), sweep, name, aid,
                            raws[k] if k < len(raws) else 0, ranges[k],
                            qs[k] if k < len(qs) else 0, valid, status])
                n += 1
    return n


def solve_tag_static(layout, frames):
    """One T4 solver instance, frames time-ordered; return median static position + diagnostics."""
    solver = TagPositionSolver(layout, SolverConfig(method="T4"))
    pts, rms = [], []
    for fr in frames:
        res = solver.solve_frame(fr)
        if res is None:
            continue
        xyz = [res.x_mm, res.y_mm, res.z_mm]
        if not all(np.isfinite(xyz)):
            continue
        pts.append(xyz)
        rms.append(res.residual_rms_mm)
    if not pts:
        return None
    P = np.asarray(pts)
    pos = np.median(P, axis=0)
    scatter = float(np.median(np.linalg.norm(P - pos, axis=1)))
    return {"pos": [round(float(v), 1) for v in pos], "n_frames": len(pts),
            "solve_rms_med_mm": round(float(np.median(rms)), 1),
            "pos_scatter_med_mm": round(scatter, 1)}


def caliper(pos_by_name):
    short = {SHORT[n]: pos_by_name[n]["pos"] for n in WAND if pos_by_name.get(n)}
    out = {}
    for (a, b), truth in CALIPER.items():
        if a in short and b in short:
            meas = float(np.linalg.norm(np.array(short[a]) - np.array(short[b])))
            out[f"{a}_{b}"] = {"measured_mm": round(meas, 1), "truth_mm": truth,
                               "delta_mm": round(meas - truth, 1), "pass": bool(abs(meas - truth) <= TOL)}
    return out


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    ncpu = len(__import__("os").sched_getaffinity(0))

    tr_csv = OUT / "wand_tr_all.csv"
    nrows = convert_raw_to_tr_all(CAL / "raw/wand_tr.log", tr_csv)

    layout = load_layout_json(CAL / "anchor_layout.json")
    delays = {aid: a.d_anchor_mm for aid, a in layout.anchors.items()}

    frames = read_tr_all_frames(tr_csv, tags=set(WAND), min_anchors=4)
    by_tag = {}
    for fr in frames:
        by_tag.setdefault(fr.tag, []).append(fr)

    res = {}
    for tag in WAND:
        frs = sorted(by_tag.get(tag.upper(), []), key=lambda f: (f.host_epoch_s, f.sweep))
        res[tag] = solve_tag_static(layout, frs)

    cal = caliper(res)

    # deployed reference (Task 4 = Huber multilaterate, no delay)
    ref = json.loads((CAL / "wand_positions.json").read_text())["wand_tags"]
    ref_pos = {n: ref[n]["position_mm"] for n in WAND}
    ref_cal = caliper({n: {"pos": ref_pos[n]} for n in WAND})
    delta_vs_ref = {n: round(float(np.linalg.norm(np.array(res[n]["pos"]) - np.array(ref_pos[n]))), 1)
                    for n in WAND if res.get(n)}

    out = {
        "meta": {"solver": "ORIGINAL V4-IO layout + ORIGINAL T4 (pristine build from git HEAD)",
                 "data": str(CAL), "tr_rows": nrows, "cpu_cores": ncpu, "wall_s": round(time.time() - t0, 1),
                 "layout_delays_mm": {int(k): round(v, 1) for k, v in delays.items()},
                 "note": "T4 = adaptive: full-anchor frames -> T1 C least-squares, low-redundancy -> T3 EMA."},
        "t4_positions": res, "t4_caliper": cal,
        "deployed_ref_positions": {n: [round(v, 1) for v in ref_pos[n]] for n in WAND},
        "deployed_ref_caliper": ref_cal,
        "t4_vs_deployed_delta_mm": delta_vs_ref,
    }
    (OUT / "result.json").write_text(json.dumps(out, indent=2))
    write_report(out)
    print(f"[orig-T4] done {out['meta']['wall_s']}s cpu={ncpu} rows={nrows}")
    for n in WAND:
        print(f"  {n}: T4={res[n]['pos'] if res[n] else None} rms={res[n]['solve_rms_med_mm'] if res[n] else None} "
              f"scatter={res[n]['pos_scatter_med_mm'] if res[n] else None} Δvs_deployed={delta_vs_ref.get(n)}")
    print("  caliper T4:", {k: (v["delta_mm"], "PASS" if v["pass"] else "FAIL") for k, v in cal.items()})


def write_report(o):
    L = ["# Original V4-IO + T4 on system_calibration_20260710_233443", "",
         f"**Solver:** {o['meta']['solver']}.  ",
         f"CPU {o['meta']['cpu_cores']} cores, {o['meta']['wall_s']} s, {o['meta']['tr_rows']} TR rows. "
         f"T4 note: {o['meta']['note']}", "",
         f"Layout anchor delays (mm) from the V4-IO `anchor_layout.json`: {o['meta']['layout_delays_mm']}", "",
         "## Wand positions", "",
         "| wand | ORIGINAL T4 (mm) | deployed ref (Huber, Task 4) | Δ mm | T4 solve RMS | T4 scatter |",
         "|---|---|---|---|---|---|"]
    for n in WAND:
        r = o["t4_positions"][n]
        L.append(f"| {n} | {r['pos'] if r else None} | {o['deployed_ref_positions'][n]} | "
                 f"{o['t4_vs_deployed_delta_mm'].get(n)} | {r['solve_rms_med_mm'] if r else None} | "
                 f"{r['pos_scatter_med_mm'] if r else None} |")
    L += ["", "## Caliper (truth 670 / 660 / 709 mm, tol ±50 mm)", "",
          "| pair | truth | ORIGINAL T4 | deployed ref (Task 4) |", "|---|---|---|---|"]
    for k in ["CCF4_9336", "CCF4_955A", "9336_955A"]:
        t = o["t4_caliper"].get(k, {})
        d = o["deployed_ref_caliper"].get(k, {})
        tt = f"{t.get('measured_mm')} ({t.get('delta_mm'):+g}, {'PASS' if t.get('pass') else 'FAIL'})" if t else "-"
        dd = f"{d.get('measured_mm')} ({d.get('delta_mm'):+g}, {'PASS' if d.get('pass') else 'FAIL'})" if d else "-"
        L.append(f"| {k.replace('_', '–')} | {t.get('truth_mm')} | {tt} | {dd} |")
    tp = sum(1 for v in o["t4_caliper"].values() if v["pass"])
    dp = sum(1 for v in o["deployed_ref_caliper"].values() if v["pass"])
    L += ["", f"**Caliper pass: ORIGINAL T4 {tp}/3, deployed ref {dp}/3.**", ""]
    (OUT / "REPORT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
