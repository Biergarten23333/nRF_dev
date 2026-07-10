#!/usr/bin/env python3
"""Trace anchor B health across every inter-anchor sweep since 2026-05-28.

For each sweep: date (from path), clean-5 (A,C,D,E,G) self-consistency RMS,
B RMS vs clean-5, B worst link, and key B-link medians. Normalizing by the
core RMS makes B comparable across any global drift / setup change.
"""
import csv, glob, json, os, re, sys, statistics as st
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnostic as dg

REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
CORE = list("ACDEG")
DATE_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})_(\d{6})")
DATE_RE2 = re.compile(r"(20\d{2})(\d{2})(\d{2})")


def path_date(p):
    m = DATE_RE.search(p) or DATE_RE2.search(p)
    if not m:
        return None
    g = m.groups()
    return f"{g[0]}-{g[1]}-{g[2]}" + (f" {g[3][:2]}:{g[3][2:4]}" if len(g) > 3 else "")


def directed_from_pairs_csv(path):
    D = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            a, b = row.get("a"), row.get("b")
            master = row.get("master", a)
            try:
                d = float(row["dist_mm"]); q = float(row.get("quality_percent") or 100)
            except (ValueError, KeyError, TypeError):
                continue
            ok = int(float(row.get("ok") or 1))
            if not a or not b or a == b or d <= 0 or q <= 0 or not ok:
                continue
            key = (master, b) if master in (a, b) else (a, b)
            # store as directed master->peer; if master==b treat as b->a
            if master == b:
                D.setdefault((b, a), []).append(d)
            else:
                D.setdefault((a, b), []).append(d)
    return D


def evaluate(D):
    have = {a for k in D for a in k}
    if not set(CORE).issubset(have) or "B" not in have:
        return None
    try:
        pos, rms5 = dg.clean6_frame(D, clean=CORE)
    except Exception:
        return None
    mlB = dg.multilat_vs_clean6(pos, D, "B", clean=CORE)
    if mlB is None:
        return None
    worst = max(mlB["resid"].items(), key=lambda kv: abs(kv[1]))
    ba = dg.undirected_med(D, "B", "A")
    n_b = sum(len(D.get((m, p), [])) for (m, p) in D if "B" in (m, p))
    return {"core_rms": round(rms5, 1), "B_rms": mlB["rms"],
            "B_worst": f"B-{worst[0]}", "B_worst_resid": round(worst[1], 1),
            "BA_med": round(ba, 1) if ba else None, "B_ratio": round(mlB["rms"] / max(rms5, 1), 1),
            "n_B_samples": n_b}


def main():
    cands = set()
    for pat in ["**/summary.json", "**/pairs_all.csv"]:
        cands.update(glob.glob(os.path.join(REPO, pat), recursive=True))
    rows = []
    seen_dirs = {}
    for p in cands:
        d = path_date(p)
        if not d or d < "2026-05-28":
            continue
        # dedupe: one entry per sweep dir (prefer summary.json)
        sweepdir = os.path.dirname(p)
        key = re.sub(r"/(sweep\d*|solve_v3_box|cir_[^/]*|layout_compare|autopos)$", "", sweepdir)
        if p.endswith("summary.json"):
            try:
                if "SW-A," not in open(p).read(200000):
                    continue
            except Exception:
                continue
            D = dg.load_directed(p)
        else:
            D = directed_from_pairs_csv(p)
        ev = evaluate(D)
        if ev is None:
            continue
        # keep the richest (most B samples) per sweep-key
        if key in seen_dirs and seen_dirs[key][1]["n_B_samples"] >= ev["n_B_samples"]:
            continue
        seen_dirs[key] = (d, ev, os.path.relpath(p, REPO))
    for d, ev, rel in seen_dirs.values():
        rows.append((d, ev, rel))
    rows.sort(key=lambda r: r[0])
    print(f"{'date':17}{'coreRMS':>8}{'B_RMS':>8}{'B/core':>7}{'worst':>7}{'resid':>9}{'B-A med':>9}  path")
    for d, ev, rel in rows:
        short = rel.replace("autopos_pipeline/", "…/").replace("SS-TWR/alt-SS-TWR/broadcast/", "bcast/")
        short = re.sub(r"/(summary.json|pairs_all.csv)$", "", short)
        print(f"{d:17}{ev['core_rms']:>8}{ev['B_rms']:>8}{ev['B_ratio']:>7}{ev['B_worst']:>7}"
              f"{ev['B_worst_resid']:>9}{str(ev['BA_med']):>9}  {short[-70:]}")


if __name__ == "__main__":
    main()
