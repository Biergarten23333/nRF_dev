#!/usr/bin/env python3
"""Parametrized wand solver worker. Imports the biospur package from PYTHONPATH
(pristine=original T4, or working-tree=U5) so the caller picks the solver version.

Converts a raw wand_tr.log -> tr_all.csv -> production read_tr_all_frames -> one
TagPositionSolver per tag (time-ordered) -> median static position. Writes JSON.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import numpy as np

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver
from biospur_tag_positioning_offline_solver.models import SolverConfig

WAND = ["BSCCF4", "BS9336", "BS955A"]
TR_RE = re.compile(
    r"(BS[0-9A-Fa-f]+) notify: TR;(?P<ver>\d+);(?P<sweep>\d+);(?P<plan>[A-Za-z0-9_]+);"
    r"(?P<pmode>\d+);(?P<am>[0-9A-Fa-f]+);(?P<vm>[0-9A-Fa-f]+);"
    r"(?P<raws>-?\d+(?:,-?\d+)*);(?P<ranges>\d+(?:,\d+)*);(?P<qs>\d+(?:,\d+)*);(?P<st>[ORTEPL]+)")


def convert(rawlog, csvpath):
    cols = ["host_elapsed_s", "host_epoch_s", "sweep", "peer_name",
            "anchor_id", "raw_mm", "range_mm", "quality_percent", "valid", "status"]
    n = 0
    with open(rawlog, errors="ignore") as f, open(csvpath, "w", newline="") as g:
        w = csv.writer(g)
        w.writerow(cols)
        for line in f:
            m = TR_RE.search(line)
            if not m or m.group(1) not in WAND:
                continue
            sweep = int(m.group("sweep"))
            ids = [i for i in range(8) if int(m.group("am"), 16) & (1 << i)]
            raws, ranges, qs, st = (m.group("raws").split(","), m.group("ranges").split(","),
                                    m.group("qs").split(","), m.group("st"))
            for k, aid in enumerate(ids):
                if k >= len(ranges) or k >= len(st):
                    break
                w.writerow([0.0, float(sweep), sweep, m.group(1), aid,
                            raws[k] if k < len(raws) else 0, ranges[k],
                            qs[k] if k < len(qs) else 0, 1 if st[k] == "O" else 0, st[k]])
                n += 1
    return n


def solve_tag(layout, frames, method):
    solver = TagPositionSolver(layout, SolverConfig(method=method))
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
    return {"pos": [round(float(v), 1) for v in pos], "n_frames": len(pts),
            "solve_rms_med_mm": round(float(np.median(rms)), 1),
            "scatter_med_mm": round(float(np.median(np.linalg.norm(P - pos, axis=1))), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", required=True)
    ap.add_argument("--sigma", default=None)
    ap.add_argument("--rawlog", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--method", default="T4")
    a = ap.parse_args()

    tr_csv = Path(a.out).with_suffix(".tr_all.csv")
    nrows = convert(a.rawlog, tr_csv)
    layout = load_layout_json(a.layout, anchor_sigma_path=a.sigma)
    frames = read_tr_all_frames(tr_csv, tags=set(WAND), min_anchors=4)
    by = {}
    for fr in frames:
        by.setdefault(fr.tag, []).append(fr)
    res = {t: solve_tag(layout, sorted(by.get(t.upper(), []), key=lambda f: (f.host_epoch_s, f.sweep)), a.method)
           for t in WAND}
    Path(a.out).write_text(json.dumps({
        "positions": res, "tr_rows": nrows, "method": a.method,
        "delays_mm": {int(k): round(v.d_anchor_mm, 1) for k, v in layout.anchors.items()},
        "sigma_mm": {int(k): round(v.sigma_mm, 1) for k, v in layout.anchors.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
