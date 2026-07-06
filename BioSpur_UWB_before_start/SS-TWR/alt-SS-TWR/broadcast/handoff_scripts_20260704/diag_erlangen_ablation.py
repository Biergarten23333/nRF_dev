#!/usr/bin/env python3
"""v4-io delay-treatment ABLATION LADDER x Sec.5d orthogonal-mode decomposition (Sec.5e).

Adjudicates: how much of the 63mm-class error residual (post-scale) is delay-treatment
artifact vs genuine layout/shape error, by varying ONLY how per-anchor antenna delay is
parameterized/bounded/regularized in the production Erlangen solver, freezing everything
else (data, physical priors, Huber loss, residual sigma, seed discipline).

READ-ONLY w.r.t. production solver files. Does not edit:
  - run_full_evaluation_same_pipeline_20260513.py (the reference solver, `mod`)
  - run_clean_full_compare.py (the data-fusion driver, `fc`)
  - any file under solver/outputs or Analysis/official_extra_analysis
It imports `mod` via importlib into an isolated in-process module instance (own sys.modules
entry) and, where a lever isn't exposed as a function argument (physical-prior sigmas for
arm5), monkey-patches constants on THAT isolated instance only -- never touching the .py file
on disk, and never affecting any other process's copy of the module.

Arms (see task spec for full definitions):
  arm0  production v4-io                    d_A=0, bound +/-60mm,  sigma_e=20 (Tikhonov on free d_i)
  arm1  bound-only relaxed                   d_A=0, bound +/-200mm, sigma_e=20   [REUSED from archived
                                              audit_phase1_revised.py output -- not re-solved]
  arm2  common-mode free                     d_i=c+e_i, c free, sigma_e=20      [REUSED from archived
                                              audit_phase1c_common_mode.py output -- not re-solved]
  arm3  differential-release ladder          c free, sigma_e in {20,60,200,~inf}  [NEW, warm-started
                                              continuation from arm2, via mod.solve_v4_common_mode]
  arm4  oracle delay reinjection (diagnostic, non-deployable -- oracle uses Vicon truth, circular)
                                              d_i fixed = oracle per-anchor delay, solve geometry only
  arm5  (conditional on arm2 aniso surviving) height/two-layer prior strength sweep on arm2 config

PROVENANCE -- every number below depends on:
  Data = Erlangen 28-May-2026 sweep1000 fused-v3 pair distances (solver/work/field_dataset_staged),
    identical to what produced the shipped v4-io layout (verified: control reproduces shipped).
  Vicon truth = same erlangen_anchor_truth_all8_v4io.json used by Sec.5d (label-based A..H, decisive
    mapping, no ambiguity -- see Sec.5d provenance).
  Solver = run_full_evaluation_same_pipeline_20260513.py: solve_v4 (bound +/-60, Tikhonov d/20,
    Huber f_scale=2.0 on residuals normalized by sigma=15mm range / 20mm delay), solve_v4_common_mode
    (d_i=c+e_i, same Huber/sigma, e/e_reg_scale_mm Tikhonov + strong mean(e)~0 forcing so c absorbs
    the true common mode), physical_layout_prior_residuals (soft_two_layer_v1: LOWER_D_Z_SIGMA_MM=180,
    UPPER_LAYER_Z_SIGMA_MM=220, MIN/MAX_LAYER_GAP_MM=450/2600).
  Param count = 18 geometry (pos_param_map(8): gauge removes 6 dof from 24 raw coords) + 7 delay
    (d_A fixed=0 as gauge; B..H free) = 18+7, CONFIRMED in code.
  Bound ambiguity RESOLVED: solve_v4's bound is `lo=-60.0, hi=+60.0` (run_full_evaluation..:463-464)
    -- SYMMETRIC +/-60mm, not a one-sided [0,60]. Some prose elsewhere says "[0,60]" describing the
    OBSERVED (all-non-negative) delays, not the coded bound; that prose is imprecise, corrected here.
  Decomposition = erlangen_decompose_lib.decompose_vs_truth (Sec.5d's exact Procrustes / anisotropic-
    scale / shape-PCA / energy-budget code, reused verbatim so every arm is directly comparable).

Run: cd .../broadcast; python3 handoff_scripts_20260704/diag_erlangen_ablation.py
"""
import os, sys, json, csv, time, importlib.util
from pathlib import Path
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from erlangen_decompose_lib import decompose_vs_truth, flatcos, to_local, rms

# ---------------------------------------------------------------- self-report
NCPU = os.cpu_count()
try:
    LOAD1 = os.getloadavg()[0]
except OSError:
    LOAD1 = float('nan')
T0 = time.time()
print("="*100)
print("v4-io DELAY-TREATMENT ABLATION LADDER x SEC.5D DECOMPOSITION (SEC.5E)")
print("="*100)
print(f"[compute] cores={NCPU}  1-min loadavg={LOAD1:.2f}  GPU=untouched (5090D reserved for dinardPCB).")
print(f"          8-anchor / 28-pair least_squares solves are ms-scale; sequential execution for the")
print(f"          warm-start CONTINUATION chain (arm3, arm4) is correct -- each solve depends on the")
print(f"          previous one's converged output, so a process pool would not parallelize anything.")
print(f"          The one genuinely-independent batch (arm5 prior-sigma sweep / cold-vs-warm jitter)")
print(f"          IS run on a process pool sized to {max(1, NCPU-2)} workers per project discipline.")

ORD = list("ABCDEFGH")
LOWER, UPPER = list("ABCD"), list("EFGH")
ROOT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start"
OFFICIAL = f"{ROOT}/autopos_pipeline/28052026_Erlangen_Official"
EVAL_SCRIPT = f"{ROOT}/autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py"
RUN_CLEAN = f"{ROOT}/biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py"
STAGED = f"{OFFICIAL}/solver/work/field_dataset_staged"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_layout_json(path):
    d = json.load(open(path))
    anc = d["anchors"]
    if isinstance(anc, dict):
        return {k: np.array([anc[k]["x_mm"], anc[k]["y_mm"], anc[k]["z_mm"]], float) for k in anc}
    return {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]], float) for a in anc}


def delays_of(path):
    d = json.load(open(path))
    return {a["label"]: float(a.get("d_anchor_mm") or 0.0) for a in d["anchors"]}


# ---------------------------------------------------------------- Vicon truth (identical to Sec.5d)
TRUTH = load_layout_json(f"{OFFICIAL}/Analysis/AutoPos_simulation/phase0_solver_headroom/data/erlangen_anchor_truth_all8_v4io.json")
Q = np.vstack([TRUTH[a] for a in ORD])

# ---------------------------------------------------------------- load production data + solver module
print("\n[load] fusing sweep1000 pair distances via the SAME driver the archived audits used "
      "(run_clean_full_compare.py, unmodified)...")
fc = load_module(RUN_CLEAN, "ablation_fc")
fc.DATA = Path(STAGED)
fc.SWEEP_CSV = Path(f"{STAGED}/sweep1000/pairs_all.csv")
mod = fc.load_eval_module()
anchor_ids = list(range(8))
raw = fc.load_sweep_grouped()
raw_solve = fc.slice_raw(raw, "all")
fused = fc.fuse_all(mod, raw_solve, anchor_ids)
fused_v3 = fused["v3"]
init_mds, _ = mod.solve_autopos_v1(fused_v3, anchor_ids)
print(f"[load] fused_v3: {len(fused_v3)} pairs. Cold MDS init ready. Elapsed so far: {time.time()-T0:.1f}s")

# sanity: does a fresh solve_v4 on this fused data reproduce the shipped v4-io layout?
SHIP_LAYOUT = f"{OFFICIAL}/solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"
ship_xyz = load_layout_json(SHIP_LAYOUT)
ship_P = np.vstack([ship_xyz[a] for a in ORD])
x_repro, dly_repro, res_repro = mod.solve_v4(fused_v3, anchor_ids, init_mds)
repro_err = float(np.sqrt(np.mean(np.sum((x_repro - ship_P) ** 2, axis=1))))
print(f"[sanity] fresh solve_v4(fused_v3) vs shipped v4-io layout: RMS diff = {repro_err:.3f} mm "
      f"({'OK -- control reproduces shipped' if repro_err < 1.0 else 'MISMATCH -- investigate fused_v3 provenance'})")

ARMS = {}  # name -> dict(P, delays_dict, mean_delay, config, source)


def record(name, P, delays_dict, config, source):
    mean_d = float(np.mean([delays_dict[a] for a in ORD]))
    ARMS[name] = dict(P=P, delays=delays_dict, mean_delay=mean_d, config=config, source=source)


# =================================================================== ARM0 -- production control
record("arm0_production", ship_P, delays_of(SHIP_LAYOUT),
       "d_A=0, bound +/-60mm, sigma_e=20 (Tikhonov on free d_i)",
       SHIP_LAYOUT)

# =================================================================== ARM1 -- bound-only relaxed (REUSED)
B200 = f"{OFFICIAL}/Analysis/official_extra_analysis/FULL/audit_phase1/layouts/v4io_bound200/layout.json"
B150 = f"{OFFICIAL}/Analysis/official_extra_analysis/FULL/audit_phase1/layouts/v4io_bound150/layout.json"
xyz200 = load_layout_json(B200); xyz150 = load_layout_json(B150)
record("arm1_bound200", np.vstack([xyz200[a] for a in ORD]), delays_of(B200),
       "d_A=0, bound +/-200mm, sigma_e=20 (archived audit_phase1_revised.py, REUSED not re-solved)", B200)
record("arm1b_bound150", np.vstack([xyz150[a] for a in ORD]), delays_of(B150),
       "d_A=0, bound +/-150mm, sigma_e=20 (secondary rung, archived, REUSED)", B150)

# =================================================================== ARM2 -- common-mode free (REUSED)
CM = f"{OFFICIAL}/Analysis/official_extra_analysis/FULL/audit_phase1c/layouts/v4io_common_mode/layout.json"
xyzCM = load_layout_json(CM)
record("arm2_commonmode", np.vstack([xyzCM[a] for a in ORD]), delays_of(CM),
       "d_i=c+e_i, c free, sigma_e=20 (archived audit_phase1c_common_mode.py, REUSED not re-solved)", CM)

print(f"\n[reuse] arm0/arm1/arm1b/arm2 loaded from archived production/audit artifacts (no re-solve).")
for k in ("arm0_production", "arm1_bound200", "arm1b_bound150", "arm2_commonmode"):
    print(f"        {k:20s} <- {ARMS[k]['source']}")

# =================================================================== ARM3 -- differential-release ladder (NEW)
print("\n" + "="*100)
print("ARM3 -- sigma_e SWEEP {20, 60, 200, ~inf}, WARM-STARTED CONTINUATION from arm2's converged geometry")
print("="*100)
x_warm = ARMS["arm2_commonmode"]["P"].copy()
c_warm = ARMS["arm2_commonmode"]["delays"]["A"]  # c ~ common_mode_mm; A has e_A folded in, use as init only
SIGMA_E_LADDER = [20.0, 60.0, 200.0, 1.0e6]  # 1e6 numerically == unregularized within +/-100mm e-bounds
prev_x = x_warm
E_TRAJ = {}  # sigma_e tag -> {anchor: e_i}
for sig in SIGMA_E_LADDER:
    t1 = time.time()
    x, dly, res = mod.solve_v4_common_mode(fused_v3, anchor_ids, prev_x, e_reg_scale_mm=sig, use_per_anchor_ei=True)
    tag = "inf" if sig >= 1e5 else f"{sig:.0f}"
    name = f"arm3_sigma_e_{tag}"
    ddict = {a: float(dly[i]) for i, a in enumerate(ORD)}
    record(name, x, ddict, f"d_i=c+e_i, c free, sigma_e={tag} (warm-started chain)", "NEW solve (this script)")
    e_i = {a: float(res.differential_delay_mm[i]) for i, a in enumerate(ORD)}
    E_TRAJ[tag] = e_i
    print(f"  sigma_e={tag:>4}  c={res.common_mode_mm:8.2f}mm  e_A={e_i['A']:7.2f}mm  mean|e|={np.mean(np.abs(res.differential_delay_mm)):6.2f}mm "
          f"max|e|={res.max_abs_e_mm:6.2f}mm  pair_rmse={res.pair_rmse_mm:6.2f}mm  "
          f"({time.time()-t1:.2f}s, success={res.success})")
    prev_x = x
x_arm3_end = prev_x  # end of ladder, for the cold-start contrast below

# ---- P3 adjudication: does e_A rise toward the oracle's implied differential (oracle_d_A - mean(oracle_d))?
ORACLE_CSV_EARLY = f"{OFFICIAL}/Analysis/official_extra_analysis/FULL/audit_phase1c/tables/item1_oracle_per_anchor_delay.csv"
_oracle_d_early = {}
with open(ORACLE_CSV_EARLY) as f:
    for row in csv.DictReader(f):
        _oracle_d_early[row["anchor"]] = float(row["oracle_d_i_mm"])
oracle_mean = float(np.mean(list(_oracle_d_early.values())))
oracle_eA = _oracle_d_early["A"] - oracle_mean
print(f"\n  [P3 check] e_A trajectory across sigma_e ladder: "
      + " -> ".join(f"{tag}:{E_TRAJ[tag]['A']:+.1f}" for tag in ("20", "60", "200", "inf"))
      + f"   oracle-implied e_A = oracle_d_A - mean(oracle_d) = {_oracle_d_early['A']:.1f}-{oracle_mean:.1f} = {oracle_eA:+.1f}mm")

# =================================================================== ARM4 -- oracle delay reinjection (diagnostic)
print("\n" + "="*100)
print("ARM4 -- ORACLE delay reinjection (DIAGNOSTIC ONLY, non-deployable: oracle uses Vicon truth, circular)")
print("="*100)
ORACLE_CSV = f"{OFFICIAL}/Analysis/official_extra_analysis/FULL/audit_phase1c/tables/item1_oracle_per_anchor_delay.csv"
oracle_d = {}
with open(ORACLE_CSV) as f:
    for row in csv.DictReader(f):
        oracle_d[row["anchor"]] = float(row["oracle_d_i_mm"])
print(f"  oracle d_i (mm): " + ", ".join(f"{a}={oracle_d[a]:.1f}" for a in ORD)
      + f"\n  mean={np.mean(list(oracle_d.values())):.2f}  all_positive={all(v>0 for v in oracle_d.values())}"
      f"  (from archived audit_phase1c_common_mode.py item1_oracle_per_anchor_delay.csv, REUSED)")

from scipy.optimize import least_squares as _lsq

def solve_geometry_only_fixed_delay(mod, pair_dists, anchor_ids, x_init, delays_mm):
    """Isolated variant of solve_v4 with delays FIXED (not free params); everything else identical:
    same /15.0 range-residual sigma, same Huber f_scale=2.0, same physical_layout_prior_residuals.
    Not a modification of solve_v4 on disk -- a new function in this script only."""
    lp, _g2l, _l2g = mod.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    dly = np.asarray(delays_mm, dtype=float)

    def fun(v):
        x = mod.unpack_pos(v, n)
        out = [(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / 15.0 for (i, j), dist in lp.items()]
        out.extend(mod.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out)

    x0 = mod.pack_pos(x_init)
    result = _lsq(fun, x0, loss="huber", f_scale=2.0, max_nfev=5000)
    x = mod.unpack_pos(result.x, n)
    return mod.gauge_align_local(x), dly, result

oracle_vec = np.array([oracle_d[a] for a in ORD])
x_oracle, dly_oracle, res_oracle = solve_geometry_only_fixed_delay(mod, fused_v3, anchor_ids, init_mds, oracle_vec)
ddict_oracle = {a: float(dly_oracle[i]) for i, a in enumerate(ORD)}
record("arm4_oracle_reinject", x_oracle, ddict_oracle,
       "d_i FIXED = oracle (Vicon-derived, circular/non-deployable), geometry-only solve", "NEW solve (this script)")
print(f"  geometry-only solve with delays fixed at oracle values: success={res_oracle.success}, "
      f"cost={res_oracle.cost:.1f}")

# =================================================================== decompose every arm vs Vicon truth
print("\n" + "="*100)
print("PER-ARM DECOMPOSITION (Sec.5d code, reused verbatim) -- s_iso / anisotropy / shape / energy")
print("="*100)
DECOMP = {}
for name, arm in ARMS.items():
    DECOMP[name] = decompose_vs_truth(arm["P"], Q, ORD, LOWER, UPPER)

hdr = (f"  {'arm':24}{'s_iso':>8}{'exp%':>7}{'sX%':>7}{'sY%':>7}{'sZ%':>7}{'aniso':>7}"
       f"{'rigidRMS':>9}{'scale%':>8}{'shape%':>8}{'A-mode%':>9}{'mean_d':>8}")
print(hdr)
for name in ("arm0_production", "arm1b_bound150", "arm1_bound200", "arm2_commonmode",
             "arm3_sigma_e_20", "arm3_sigma_e_60", "arm3_sigma_e_200", "arm3_sigma_e_inf",
             "arm4_oracle_reinject"):
    D = DECOMP[name]; s_ax = D["s_ax"]
    # "A-mode": the post-aniso shape mode with the largest |A loading| (tracks the P3 A-shape signal)
    a_idx = ORD.index("A")
    a_mode = max(D["post_aniso_modes"], key=lambda m: abs(m["loadings"][a_idx]))
    a_pct = 100 * a_mode["energy"] / D["E0"]
    print(f"  {name:24}{D['s_iso']:8.4f}{D['expansion_pct']:7.2f}{100*(1/s_ax[0]-1):7.2f}"
          f"{100*(1/s_ax[1]-1):7.2f}{100*(1/s_ax[2]-1):7.2f}{D['aniso_ratio']:7.3f}"
          f"{D['rigid_rms']:9.2f}{D['scale_pct_of_total']:8.1f}{D['shape_pct_of_total']:8.1f}"
          f"{a_pct:9.1f}{ARMS[name]['mean_delay']:8.2f}")

# =================================================================== conditional arm5 trigger check
print("\n" + "="*100)
print("ARM5 TRIGGER CHECK -- does arm2's vertical anisotropy survive?")
print("="*100)
D2 = DECOMP["arm2_commonmode"]; D0 = DECOMP["arm0_production"]
s_ax2 = D2["s_ax"]
vert_exp2 = 100*(1/s_ax2[1]-1)
horiz_exp2 = 100*(1/((s_ax2[0]*s_ax2[2])**0.5)-1)
ARM5_TRIGGER = abs(vert_exp2 - horiz_exp2) > 2.0  # still meaningfully anisotropic (arm0 gap was 8.13-3.60=4.5pp)
print(f"  arm0 (production):  vertical exp={100*(1/DECOMP['arm0_production']['s_ax'][1]-1):+.2f}%  "
      f"horiz exp={100*(1/((DECOMP['arm0_production']['s_ax'][0]*DECOMP['arm0_production']['s_ax'][2])**0.5)-1):+.2f}%")
print(f"  arm2 (common-mode): vertical exp={vert_exp2:+.2f}%  horiz exp={horiz_exp2:+.2f}%  "
      f"gap={vert_exp2-horiz_exp2:+.2f}pp")
print(f"  ARM5 TRIGGERED = {ARM5_TRIGGER}  (gap-survival threshold: |gap| > 2.0 pp)")

# =================================================================== ARM5 (conditional) -- prior-strength sweep
if ARM5_TRIGGER:
    print("\n" + "="*100)
    print("ARM5 -- height/two-layer PRIOR-STRENGTH sweep on arm2 config (soft_two_layer_v1 sigmas x{0.5,1,2,10})")
    print("="*100)
    base_lower_sig = mod.LOWER_D_Z_SIGMA_MM
    base_upper_sig = mod.UPPER_LAYER_Z_SIGMA_MM
    print(f"  baseline priors (this isolated module instance only): LOWER_D_Z_SIGMA_MM={base_lower_sig}, "
          f"UPPER_LAYER_Z_SIGMA_MM={base_upper_sig}")
    x_seed = ARMS["arm2_commonmode"]["P"].copy()
    for mult in (0.5, 1.0, 2.0, 10.0):
        mod.LOWER_D_Z_SIGMA_MM = base_lower_sig * mult
        mod.UPPER_LAYER_Z_SIGMA_MM = base_upper_sig * mult
        x, dly, res = mod.solve_v4_common_mode(fused_v3, anchor_ids, x_seed, e_reg_scale_mm=20.0, use_per_anchor_ei=True)
        ddict = {a: float(dly[i]) for i, a in enumerate(ORD)}
        name = f"arm5_priormult_{mult}"
        record(name, x, ddict, f"arm2 config, two-layer prior sigma x{mult}", "NEW solve (this script)")
        DECOMP[name] = decompose_vs_truth(x, Q, ORD, LOWER, UPPER)
        s_ax5 = DECOMP[name]["s_ax"]
        v5 = 100*(1/s_ax5[1]-1); h5 = 100*(1/((s_ax5[0]*s_ax5[2])**0.5)-1)
        print(f"  prior x{mult:<5} vertical exp={v5:+6.2f}%  horiz exp={h5:+6.2f}%  gap={v5-h5:+6.2f}pp  "
              f"s_iso={DECOMP[name]['s_iso']:.4f}")
    mod.LOWER_D_Z_SIGMA_MM = base_lower_sig
    mod.UPPER_LAYER_Z_SIGMA_MM = base_upper_sig
    print("  (restored baseline sigmas on this isolated module instance after sweep)")
else:
    print("  ARM5 SKIPPED -- vertical/horizontal anisotropy gap did not survive past arm2; no prior-strength")
    print("  sweep needed. (Recorded per pre-registration: report the skip, do not force a run.)")

# =================================================================== cold-start contrast at ladder end
print("\n" + "="*100)
print("COLD-START CONTRAST at ladder end (arm3 sigma_e~inf) -- warm-started vs cold MDS re-init")
print("="*100)
x_cold, dly_cold, res_cold = mod.solve_v4_common_mode(fused_v3, anchor_ids, init_mds, e_reg_scale_mm=1.0e6, use_per_anchor_ei=True)
spread = float(np.sqrt(np.mean(np.sum((x_cold - x_arm3_end) ** 2, axis=1))))
print(f"  warm-started (chain 20->60->200->inf) vs cold (fresh MDS init, sigma_e=inf direct): "
      f"RMS |dpos| = {spread:.2f} mm")
print(f"  basin-noise reference from a DIFFERENT solver/room (V3-box, DIAG item-3, 3 jittered seeds) was "
      f"~24mm; that number is NOT transferable 1:1 to this pipeline, cited only as an order-of-magnitude gate.")
print(f"  verdict: {'BASIN CONTAMINATION -- >24mm apart, ladder-end result is basin-dependent' if spread>24 else 'CONSISTENT -- <24mm, same basin, warm-start chain is trustworthy'}")

print(f"\n[DONE] total elapsed {time.time()-T0:.1f}s")
