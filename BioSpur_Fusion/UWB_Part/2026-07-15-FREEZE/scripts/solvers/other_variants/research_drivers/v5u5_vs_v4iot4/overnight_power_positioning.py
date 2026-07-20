#!/usr/bin/env python3
"""Wand positioning vs TX power on the overnight sweep, with BOTH production pipelines:
  A = V4-io layout + T4  (pristine package)
  B = V5 scale-lock layout + U5  (working package + anchor_sigma.json)
Per power level: concat that power's cell rawlogs -> run each pipeline (own PYTHONPATH) ->
per-tag median position + per-axis std (incl. z) + 3D scatter, rigid-baseline (caliper)
vs truth 670/660/709 mm, and mean-position drift vs MAX. Pure offline re-solve."""
import json, os, subprocess, sys, glob, time
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
BCAST = REPO / "SS-TWR/alt-SS-TWR/broadcast"
OVN = Path(os.environ.get("OVN_POS_ROOT", str(BCAST / "logs/overnight_power_20260714")))
CAL = REPO / "logs/system_calibration_20260710_233443"   # repo-root logs, not broadcast/logs
PRISTINE = Path("/tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/"
                "9e47773c-907b-4e3c-991f-6faed15f20a1/scratchpad/pristine_pkg")
WORKING = REPO / "biospur_tag_positioning_offline_solver"
WORKER = REPO / "analysis/v5u5_vs_v4iot4/worker_ext.py"
SCRATCH = Path("/tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/"
               "f50e2943-3acd-445a-b5e5-900f61051f16/scratchpad/ovn_pos")
SCRATCH.mkdir(parents=True, exist_ok=True)
OUTDIR = OVN / "positioning_v5u5_v4iot4"; OUTDIR.mkdir(exist_ok=True)

PRESETS = ["MAX", "M3", "M6", "M12", "POR"]
DB = {"MAX": 8.5, "M3": 5.5, "M6": 2.5, "M12": 0.0, "POR": 4.0}
WAND = ["BSCCF4", "BS9336", "BS955A"]
SHORT = {"BSCCF4": "CCF4", "BS9336": "9336", "BS955A": "955A"}
CALIPER = {("CCF4", "9336"): 670.0, ("CCF4", "955A"): 660.0, ("9336", "955A"): 709.0}
PIPES = {
    "A_v4io_t4": dict(pkg=PRISTINE, layout=CAL / "anchor_layout.json", sigma=None),
    "B_v5_u5":   dict(pkg=WORKING,  layout=CAL / "anchor_layout_v5_scalelock.json", sigma=CAL / "anchor_sigma.json"),
}

def concat_rawlogs(power):
    logs = sorted(glob.glob(str(OVN / "round_*" / f"{power}_3min_*" / "raw.log")))
    dst = SCRATCH / f"{power}.log"
    with open(dst, "w") as out:
        for lg in logs:
            with open(lg, errors="ignore") as f:
                out.write(f.read())
    return dst, len(logs)

def run_pipe(name, rawlog, power):
    p = PIPES[name]
    out = SCRATCH / f"{name}_{power}.json"
    env = dict(os.environ, PYTHONPATH=str(p["pkg"]))
    cmd = [sys.executable, str(WORKER), "--layout", str(p["layout"]),
           "--rawlog", str(rawlog), "--out", str(out), "--method", "T4"]
    if p["sigma"]:
        cmd += ["--sigma", str(p["sigma"])]
    subprocess.run(cmd, env=env, check=True, cwd=str(WORKER.parent),
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return json.loads(out.read_text())["positions"]

def caliper(pos):
    short = {SHORT[n]: pos[n]["pos"] for n in WAND if pos.get(n)}
    out = {}
    for (a, b), truth in CALIPER.items():
        if a in short and b in short:
            meas = float(np.linalg.norm(np.array(short[a]) - np.array(short[b])))
            out[f"{a}_{b}"] = {"meas": round(meas, 1), "truth": truth, "err": round(meas - truth, 1)}
    return out

def main():
    t0 = time.time()
    res = {name: {} for name in PIPES}
    ncells = {}
    for power in PRESETS:
        raw, n = concat_rawlogs(power); ncells[power] = n
        for name in PIPES:
            try:
                res[name][power] = run_pipe(name, raw, power)
            except subprocess.CalledProcessError as e:
                res[name][power] = None
                print(f"  {name} {power} FAILED: {e.stderr.decode()[-300:]}")
        print(f"  {power}: {n} cells solved  ({time.time()-t0:.0f}s)")

    out = {"cells_per_power": ncells, "pipelines": list(PIPES),
           "truth_baselines": {f"{a}_{b}": v for (a, b), v in CALIPER.items()}, "per_power": {}}
    ref = {}  # per (pipeline,tag) MAX position
    for name in PIPES:
        if res[name].get("MAX"):
            for t in WAND:
                if res[name]["MAX"].get(t):
                    ref[(name, t)] = np.array(res[name]["MAX"][t]["pos"])
    for name in PIPES:
        out["per_power"][name] = {}
        for power in PRESETS:
            pos = res[name].get(power)
            if not pos:
                continue
            cal = caliper(pos)
            tags = {}
            for t in WAND:
                d = pos.get(t)
                if not d:
                    continue
                drift = float(np.linalg.norm(np.array(d["pos"]) - ref[(name, t)])) if (name, t) in ref else None
                tags[t] = {"pos": d["pos"], "scatter_3d": d["scatter_med_mm"],
                           "std_x": d["std_x"], "std_y": d["std_y"], "std_z": d["std_z"],
                           "n": d["n_frames"], "drift_vs_MAX_mm": round(drift, 1) if drift is not None else None}
            out["per_power"][name][power] = {
                "tx_dB": DB[power], "tags": tags,
                "caliper": cal,
                "caliper_max_abs_err": round(max(abs(c["err"]) for c in cal.values()), 1) if cal else None,
                "median_scatter_3d": round(float(np.median([tags[t]["scatter_3d"] for t in tags])), 1) if tags else None,
                "median_std_z": round(float(np.median([tags[t]["std_z"] for t in tags])), 1) if tags else None,
                "median_drift_vs_MAX": round(float(np.median([tags[t]["drift_vs_MAX_mm"] for t in tags if tags[t]["drift_vs_MAX_mm"] is not None])), 1) if any(tags[t]["drift_vs_MAX_mm"] is not None for t in tags) else None}
    (OUTDIR / "positioning_vs_power.json").write_text(json.dumps(out, indent=1))

    # print tables
    for name in PIPES:
        print(f"\n===== {name} =====")
        print("pwr  dB  | med_scatter3D  med_std_z  med_drift_vs_MAX | caliper_err (CCF4-9336, CCF4-955A, 9336-955A) max|err|")
        for power in PRESETS:
            s = out["per_power"][name].get(power)
            if not s:
                continue
            ce = s["caliper"]
            errs = ", ".join(f"{ce[k]['err']:+.0f}" for k in ("CCF4_9336", "CCF4_955A", "9336_955A") if k in ce)
            print(f" {power:>3} {s['tx_dB']:>4} | {str(s['median_scatter_3d']):>12}  {str(s['median_std_z']):>8}  {str(s['median_drift_vs_MAX']):>15} | {errs}   max={s['caliper_max_abs_err']}")
    print(f"\ndone {time.time()-t0:.0f}s -> {OUTDIR/'positioning_vs_power.json'}")

if __name__ == "__main__":
    main()
