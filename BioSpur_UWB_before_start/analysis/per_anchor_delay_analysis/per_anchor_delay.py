#!/usr/bin/env python3
"""Per-anchor antenna-delay calibration analysis (pure OFFLINE, baseline scan only).

Reuses the LOCKED primitives (pg_lib) and the APS011 recomputation module
(recompute) so parsing / trilateration / LOO are byte-identical to the
pre-registered pipeline.  No firmware, no hardware, no git.

Steps:
  1. Full 8-anchor signed-LOO bias table (mean/median/std) on baseline scan.
  2. Cross-reference the AutoPos v4-io per-anchor delay estimates (d_anchor_mm)
     and the +/-60 mm hard bound.
  3. LOO improvement ceiling: Scenario A (raw) / B (per-anchor offset) /
     C (offset+slope), in-sample AND split-half cross-validated for B.
  4. Firmware register deltas (counts from 16436) to null each anchor's bias.
"""
import os, sys, json, time
import numpy as np

REPO = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
sys.path.insert(0, os.path.join(REPO, "logs/geiger_scan_20260711_161258_8anchor/analysis"))
import pg_lib as L
sys.path.insert(0, os.path.join(REPO, "analysis/aps011_rsl_recomputation"))
import recompute as R

OUTDIR = os.path.join(REPO, "analysis/per_anchor_delay_analysis")

# ---- antenna-delay register conversion -----------------------------------
# DW1000 device time unit; task-specified vacuum c for the register conversion.
DWT_TIME_UNITS = 1.0 / (499.2e6 * 128.0)          # s  (= 15.650 ps)
C_VACUUM       = 299792458.0                       # m/s
MM_PER_COUNT   = C_VACUUM * DWT_TIME_UNITS * 1000.0  # one-way mm per antenna-delay count
NOMINAL_ANTD   = 16436                              # current uniform TX_ANTD & RX_ANTD


def per_anchor_bias(arr):
    """mean/median/std/n of signed LOO residual (measured-predicted) per anchor."""
    out = {}
    for a in range(8):
        r = arr["res"][arr["anch"] == a]
        if len(r):
            out[a] = dict(mean=float(np.mean(r)), median=float(np.median(r)),
                          std=float(np.std(r)), n=int(len(r)))
        else:
            out[a] = dict(mean=np.nan, median=np.nan, std=np.nan, n=0)
    return out


def apply_offsets(rng_list, bias_mean):
    """New rng_list with each valid range reduced by round(bias_mean[a])."""
    out = []
    for rg in rng_list:
        d = {}
        for a, mm in rg.items():
            if L.valid_range(mm) and a in bias_mean and np.isfinite(bias_mean[a]):
                d[a] = int(round(mm - bias_mean[a]))
            else:
                d[a] = mm
        out.append(d)
    return out


def apply_slope(rng_list, b_frac, pivot_mm):
    out = []
    for rg in rng_list:
        d = {a: (int(round(mm - b_frac * (mm - pivot_mm))) if L.valid_range(mm) else mm)
             for a, mm in rg.items()}
        out.append(d)
    return out


def bias_from_cycles(P, rng_sub):
    pos = R.solve_all(P, rng_sub)
    arr = R.loo_residuals(P, rng_sub, pos)
    return {a: float(np.mean(arr["res"][arr["anch"] == a]))
            if (arr["anch"] == a).any() else np.nan for a in range(8)}


def loo_abs(P, rng_list):
    """Pooled |LOO residual| array for a rng_list."""
    pos = R.solve_all(P, rng_list)
    arr = R.loo_residuals(P, rng_list, pos)
    return np.abs(arr["res"])


def main():
    t0 = time.time()
    cpu0 = time.process_time()
    ncpu = os.cpu_count()

    P, LBL, DLY, W, Wc = L.load_geometry()
    rows = L.parse_log(R.BASELINE)
    rng_list = [r["rng"] for r in rows]
    print(f"[data] {R.BASELINE}")
    print(f"[data] {len(rows)} LSCAN cycles")

    # ---- Step 1: full bias table ----------------------------------------
    pos = R.solve_all(P, rng_list)
    arr = R.loo_residuals(P, rng_list, pos)
    bias = per_anchor_bias(arr)
    bias_mean = {a: bias[a]["mean"] for a in range(8)}
    rms_const = float(np.sqrt(np.mean([bias[a]["mean"] ** 2 for a in range(8)])))
    print("\n[step1] per-anchor signed LOO residual (measured - predicted):")
    for a in range(8):
        print(f"   {LBL[a]}  mean {bias[a]['mean']:+7.1f}  median {bias[a]['median']:+7.1f}"
              f"  std {bias[a]['std']:6.1f}  n {bias[a]['n']}")
    print(f"[step1] per-anchor CONSTANT-bias RMS (about 0) = {rms_const:.1f} mm")

    # ---- Step 2: AutoPos cross-reference --------------------------------
    # Sign convention (both consistent): POSITIVE = anchor reads LONG.
    #   solver residual model: |x_i-x_j| + d_i + d_j - dist  => d_i>0 <=> reads long
    #   Geiger bias = measured - predicted                   => >0     <=> reads long
    # residual = what the +/-60 bound prevented the solver from absorbing.
    solver = {}
    for a in range(8):
        d = float(DLY[a])
        at_bound = abs(abs(d) - 60.0) < 0.5
        pinned = (a == 0)   # anchor A is the d_A=0 gauge reference
        solver[a] = dict(d_mm=d, at_bound=bool(at_bound), pinned=bool(pinned),
                         residual=float(bias_mean[a] - d))
    print("\n[step2] AutoPos cross-reference (residual = Geiger_bias - solver_d):")
    for a in range(8):
        tag = "PINNED(gauge)" if solver[a]["pinned"] else ("BOUND" if solver[a]["at_bound"] else "")
        print(f"   {LBL[a]}  solver_d {solver[a]['d_mm']:+7.1f} {tag:14s}"
              f"  Geiger {bias_mean[a]:+7.1f}  residual {solver[a]['residual']:+7.1f}")

    # ---- Step 3: LOO ceiling A / B / C ----------------------------------
    baseA = R.evaluate(P, rng_list)
    valid_all = [mm for rg in rng_list for mm in rg.values() if L.valid_range(mm)]
    pivot = float(np.median(valid_all))
    b_frac = baseA["pooled_slope_pct"] / 100.0

    rng_B = apply_offsets(rng_list, bias_mean)
    baseB = R.evaluate(P, rng_B)
    rng_C = apply_slope(rng_B, b_frac, pivot)
    baseC = R.evaluate(P, rng_C)

    # residual-space per-anchor demean = the OPTIMISTIC ceiling the brief cites
    mean_a = {a: float(arr["res"][arr["anch"] == a].mean()) for a in range(8)}
    e_demean = arr["res"] - np.array([mean_a[a] for a in arr["anch"]])
    demean_ceiling = float(np.median(np.abs(e_demean)))

    print(f"\n[step3] pooled slope = {baseA['pooled_slope_pct']:+.3f}%  pivot = {pivot:.0f} mm")
    print(f"[step3] residual-space demean CEILING     LOO median = {demean_ceiling:.1f} mm  (optimistic; brief's 134)")
    print(f"[step3] Scenario A (raw)                 LOO median = {baseA['loo_abs_median_mm']:.1f} mm  (n={baseA['n_loo']})")
    print(f"[step3] Scenario B (per-anchor offset)   LOO median = {baseB['loo_abs_median_mm']:.1f} mm  [IN-SAMPLE]")
    print(f"[step3] Scenario C (offset + slope)      LOO median = {baseC['loo_abs_median_mm']:.1f} mm  [IN-SAMPLE]")

    # split-half cross-validated Scenario B (honest, out-of-sample bias)
    idx = np.arange(len(rng_list))
    even = [rng_list[i] for i in idx if i % 2 == 0]
    odd  = [rng_list[i] for i in idx if i % 2 == 1]
    bias_even = bias_from_cycles(P, even)      # estimate on even
    bias_odd  = bias_from_cycles(P, odd)       # estimate on odd
    held_odd  = loo_abs(P, apply_offsets(odd,  bias_even))   # eval on odd
    held_even = loo_abs(P, apply_offsets(even, bias_odd))    # eval on even
    cv_pool = np.concatenate([held_odd, held_even])
    cv_median = float(np.median(cv_pool))
    # raw (uncorrected) split-half medians as the honest A reference on same folds
    rawA_pool = np.concatenate([loo_abs(P, odd), loo_abs(P, even)])
    rawA_median = float(np.median(rawA_pool))
    print(f"[step3] Scenario B (per-anchor offset)   LOO median = {cv_median:.1f} mm  [CROSS-VALIDATED split-half]")
    print(f"[step3]   (split-half raw reference      LOO median = {rawA_median:.1f} mm)")

    A2B = baseA["loo_abs_median_mm"] - baseB["loo_abs_median_mm"]
    B2C = baseB["loo_abs_median_mm"] - baseC["loo_abs_median_mm"]
    print(f"[step3] addressable by per-anchor delay (A->B, in-sample): {A2B:.1f} mm")
    print(f"[step3] added by slope (B->C):                             {B2C:.1f} mm")

    # ---- Step 4: firmware register deltas -------------------------------
    print(f"\n[step4] mm per antenna-delay count (one-way, both TX+RX regs) = {MM_PER_COUNT:.4f} mm")
    regs = {}
    for a in range(8):
        # positive bias (reads long) -> increase antenna delay -> range shrinks
        delta = int(round(bias_mean[a] / MM_PER_COUNT))
        new = NOMINAL_ANTD + delta
        regs[a] = dict(bias_mm=bias_mean[a], delta_counts=delta, new_antd=new,
                       residual_mm=bias_mean[a] - delta * MM_PER_COUNT)
        print(f"   {LBL[a]}  bias {bias_mean[a]:+7.1f}  delta {delta:+4d} counts"
              f"  new TX/RX_ANTD = {new}")

    cpu = time.process_time() - cpu0
    wall = time.time() - t0
    cores_busy = cpu / wall if wall > 0 else 0.0
    print(f"\n[compute] {ncpu} logical CPUs; single-process; "
          f"self-CPU {cpu:.1f}s / {wall:.1f}s wall -> peak {cores_busy:.2f} cores busy.")

    result = dict(
        baseline=R.BASELINE, n_cycles=len(rows),
        labels=LBL,
        mm_per_count=MM_PER_COUNT, dwt_time_units_s=DWT_TIME_UNITS,
        nominal_antd=NOMINAL_ANTD,
        sign_convention="positive = anchor reads LONG (both Geiger bias and solver d_anchor)",
        step1_bias={LBL[a]: bias[a] for a in range(8)},
        step1_const_rms_mm=rms_const,
        step2_solver={LBL[a]: solver[a] for a in range(8)},
        step3=dict(
            pooled_slope_pct=baseA["pooled_slope_pct"], pivot_mm=pivot,
            residual_demean_ceiling_mm=demean_ceiling,
            scenarioA_loo_median_mm=baseA["loo_abs_median_mm"],
            scenarioB_loo_median_mm_insample=baseB["loo_abs_median_mm"],
            scenarioB_loo_median_mm_cv=cv_median,
            scenarioB_cv_raw_reference_mm=rawA_median,
            scenarioC_loo_median_mm=baseC["loo_abs_median_mm"],
            A_to_B_mm=A2B, B_to_C_mm=B2C,
            n_loo=baseA["n_loo"],
        ),
        step4_registers={LBL[a]: regs[a] for a in range(8)},
        compute=dict(ncpu=ncpu, self_cpu_s=cpu, wall_s=wall, cores_busy=cores_busy),
    )
    outp = os.path.join(OUTDIR, "results.json")
    with open(outp, "w") as fp:
        json.dump(result, fp, indent=2, default=float)
    print(f"[done] wrote {outp}")
    return result


if __name__ == "__main__":
    main()
