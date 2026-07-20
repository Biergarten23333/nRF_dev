#!/usr/bin/env python3
"""Compare two FULL pipelines on system_calibration_20260710_233443 wand data:

  A = V4-io layout + T4 tag solver   (ORIGINAL, pristine build from git HEAD)
  B = V5 (scale-lock) layout + U5 tag solver  (working tree: modified c_solver + .so,
      anchor_sigma.json uniform 25 mm; RF-informed sigma is INERT here because the wand
      TR log carries no FP-SNR, so U5's adaptation reduces to uniform-sigma + never-drop).

Each pipeline runs in its own subprocess with its own PYTHONPATH (the two package
versions cannot coexist in one interpreter). Pure offline.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

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
OUT = REPO / "analysis/v5u5_vs_v4iot4"
SCRATCH = Path("/tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/"
               "9e47773c-907b-4e3c-991f-6faed15f20a1/scratchpad")
PRISTINE_PKG = SCRATCH / "pristine_pkg"                      # original T4
WORKING_PKG = REPO / "biospur_tag_positioning_offline_solver"  # U5 (modified)
WORKER = OUT / "worker.py"
RAWLOG = CAL / "raw/wand_tr.log"

WAND = ["BSCCF4", "BS9336", "BS955A"]
SHORT = {"BSCCF4": "CCF4", "BS9336": "9336", "BS955A": "955A"}
CALIPER = {("CCF4", "9336"): 670.0, ("CCF4", "955A"): 660.0, ("9336", "955A"): 709.0}
TOL = 50.0


def run_pipeline(pkg, layout, sigma, out_json):
    env = dict(os.environ, PYTHONPATH=str(pkg))
    cmd = [sys.executable, str(WORKER), "--layout", str(layout), "--rawlog", str(RAWLOG), "--out", str(out_json)]
    if sigma:
        cmd += ["--sigma", str(sigma)]
    subprocess.run(cmd, env=env, check=True, cwd=str(OUT))
    return json.loads(Path(out_json).read_text())


def caliper(positions):
    short = {SHORT[n]: positions[n]["pos"] for n in WAND if positions.get(n)}
    out = {}
    for (a, b), truth in CALIPER.items():
        if a in short and b in short:
            meas = float(np.linalg.norm(np.array(short[a]) - np.array(short[b])))
            out[f"{a}_{b}"] = {"measured_mm": round(meas, 1), "truth_mm": truth,
                               "delta_mm": round(meas - truth, 1), "pass": bool(abs(meas - truth) <= TOL)}
    return out


def npass(cal):
    return sum(1 for v in cal.values() if v["pass"])


def main():
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)

    A = run_pipeline(PRISTINE_PKG, CAL / "anchor_layout.json", None, OUT / "_v4io_t4.json")
    B = run_pipeline(WORKING_PKG, CAL / "anchor_layout_v5_scalelock.json", CAL / "anchor_sigma.json", OUT / "_v5_u5.json")
    calA, calB = caliper(A["positions"]), caliper(B["positions"])
    dpos = {n: round(float(np.linalg.norm(np.array(A["positions"][n]["pos"]) - np.array(B["positions"][n]["pos"]))), 1)
            for n in WAND if A["positions"].get(n) and B["positions"].get(n)}

    result = {
        "meta": {"data": str(CAL), "tr_rows": A["tr_rows"], "wall_s": round(time.time() - t0, 1),
                 "cpu_cores": len(os.sched_getaffinity(0)),
                 "A": "V4-io layout + T4 (original, pristine git HEAD)",
                 "B": "V5 scale-lock layout + U5 (working tree; RF-sigma inert w/o FP-SNR -> uniform25 + never-drop)"},
        "A_v4io_t4": {"positions": A["positions"], "caliper": calA, "delays_mm": A["delays_mm"], "sigma_mm": A["sigma_mm"]},
        "B_v5_u5": {"positions": B["positions"], "caliper": calB, "delays_mm": B["delays_mm"], "sigma_mm": B["sigma_mm"]},
        "pos_delta_AB_mm": dpos,
        "caliper_pass": {"A_v4io_t4": npass(calA), "B_v5_u5": npass(calB)},
    }
    (OUT / "comparison.json").write_text(json.dumps(result, indent=2))
    write_report(result)
    print(f"[v5u5-v4iot4] done {result['meta']['wall_s']}s")
    for n in WAND:
        print(f"  {n}: V4io+T4={A['positions'][n]['pos']}  V5+U5={B['positions'][n]['pos']}  Δ={dpos.get(n)}")
    print(f"  caliper: V4io+T4={npass(calA)}/3  V5+U5={npass(calB)}/3")


def _cell(cal, k):
    v = cal.get(k)
    return f"{v['measured_mm']} ({v['delta_mm']:+g}, {'PASS' if v['pass'] else 'FAIL'})" if v else "-"


def write_report(r):
    A, B = r["A_v4io_t4"], r["B_v5_u5"]
    L = ["# V5 + U5  vs  V4-io + T4  — wand positioning (system_calibration_20260710_233443)", "",
         f"**Pure offline.** {r['meta']['cpu_cores']} cores, {r['meta']['wall_s']} s, {r['meta']['tr_rows']} TR rows.",
         "",
         f"- **A = {r['meta']['A']}**",
         f"- **B = {r['meta']['B']}**", "",
         "> Note: the wand TR log carries no FP-SNR, so **U5's RF-informed σ is inert here** — U5 reduces to "
         "uniform-25 mm σ + never-drop-anchor on the V5 layout. The A↔B difference is therefore driven by the "
         "**V5-vs-V4-io layout** and the never-drop behavior, not the RF metric.", "",
         "## Wand positions", "",
         "| wand | A: V4-io+T4 (mm) | B: V5+U5 (mm) | Δ A↔B | A rms | B rms | A scatter | B scatter |",
         "|---|---|---|---|---|---|---|---|"]
    for n in WAND:
        a, b = A["positions"][n], B["positions"][n]
        L.append(f"| {n} | {a['pos']} | {b['pos']} | {r['pos_delta_AB_mm'].get(n)} | "
                 f"{a['solve_rms_med_mm']} | {b['solve_rms_med_mm']} | {a['scatter_med_mm']} | {b['scatter_med_mm']} |")
    L += ["", "## Caliper (truth 670 / 660 / 709 mm, tol ±50 mm)", "",
          "| pair | truth | A: V4-io+T4 | B: V5+U5 |", "|---|---|---|---|"]
    for k, truth in [("CCF4_9336", 670), ("CCF4_955A", 660), ("9336_955A", 709)]:
        L.append(f"| {k.replace('_', '–')} | {truth} | {_cell(A['caliper'], k)} | {_cell(B['caliper'], k)} |")
    L += ["", f"**Caliper pass: A (V4-io+T4) {r['caliper_pass']['A_v4io_t4']}/3, "
          f"B (V5+U5) {r['caliper_pass']['B_v5_u5']}/3.**", "",
          "## Layout parameters used", "",
          f"- A delays (mm): {A['delays_mm']}  ·  σ: {A['sigma_mm'][ '0'] if '0' in A['sigma_mm'] else list(A['sigma_mm'].values())[0]} uniform",
          f"- B delays (mm): {B['delays_mm']}  ·  σ: {list(B['sigma_mm'].values())[0]} uniform (anchor_sigma.json)", ""]
    (OUT / "REPORT.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
