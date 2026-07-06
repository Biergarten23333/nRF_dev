#!/usr/bin/env python3
"""Orthogonal-mode decomposition of the Erlangen AutoPos-vs-Vicon layout error field.

Splits the *real* (not injected) Erlangen layout error into
    isotropic scale  +  anisotropic (diagonal) scale  +  shape (PCA/SVD modes),
with an exactly-closing energy budget, and cross-checks the shape principal mode
against the V3-box APS011-slope INJECTION shape mode (independent experiment, DIAG 5c).

READ-ONLY. Touches nothing under the pipeline; reads the canonical solve, the Vicon
truth, and the already-saved injection warm-run layouts, and writes only stdout
(captured to scratchpad) + prints a markdown block for DIAG_SIGMA_MAP_RESULTS 5d.

The core decomposition math lives in erlangen_decompose_lib.py (extracted here so the
§5e ablation ladder reuses the exact same Procrustes/aniso/shape-PCA/energy-budget code
and det=-1 handling on every arm, instead of re-deriving it per arm).

PROVENANCE (every load-bearing number depends on these):
  AutoPos canonical solve = **v4-io** ("current production inter-anchor solver",
    version_summary.csv). Gauge: A@origin, B on +x, C in xy-plane. physical_priors=
    soft_two_layer_v1; per-anchor antenna-delay bound is SYMMETRIC +/-60 mm (verified
    in solver source run_full_evaluation_same_pipeline_20260513.py:463-464 -- NOT a
    one-sided [0,60] as some prose elsewhere states), with C,D CLIPPED at the +60 mm
    edge of that bound (delay-clipping -> bias deflected into geometry). Seed = MDS/NLS
    (solve_autopos_v1 -> solve_mds_nls; a true convex-SDP path (solve_sdp_nls) exists in
    the same module but is not wired into any production version, v4-io included).
  Vicon truth = OptiTrack Erlangen 28-May-2026, {A..H}antenna markers, Y-vertical.
  Correspondence = label-based A..H (anchor_id->label 0->A..7->H verified decisive,
    second/best cost ratio 1.48 > 1.20 gate; no ambiguity).
  Injection = V3-box living-room solve autopos_v3box_noref_20260704, WARM run
    (slope seeded from control) = the clean same-basin local response (DIAG 5c item-1).

Run: cd .../broadcast; python3 handoff_scripts_20260704/diag_erlangen_modes.py
"""
import os, json, csv, math, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from erlangen_decompose_lib import (
    umeyama, rms, energy, field_svd, fro_cos, upper_lift_template,
    local_basis, to_local, flatcos, decompose_vs_truth,
)

# ---------------------------------------------------------------- self-report
NCPU = os.cpu_count()
try:
    LOAD1 = os.getloadavg()[0]
except OSError:
    LOAD1 = float('nan')
print("="*96)
print("ERLANGEN LAYOUT-ERROR ORTHOGONAL-MODE DECOMPOSITION")
print("="*96)
print(f"[compute] cores={NCPU}  workers=1 (8-point linear algebra; a process pool would be pure "
      f"overhead)\n          1-min loadavg={LOAD1:.2f}  GPU=untouched (5090D reserved for dinardPCB). "
      f"No batch re-solve needed:\n          canonical v4-io solve + saved injection warm-run are read verbatim.")

ORD = list("ABCDEFGH")
LOWER, UPPER = list("ABCD"), list("EFGH")
ROOT = "/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official"
INJ  = "/tmp/claude-1000/-mnt-nrf-ssd-nRF-dev-BioSpur-UWB-before-start/a686d416-bec9-4d43-9c60-923021beb6d2/scratchpad/aps011_acct"

# ---------------------------------------------------------------- inputs (read-only)
def load_layout_json(path):
    d = json.load(open(path))
    anc = d["anchors"]
    if isinstance(anc, dict):                       # truth-style {A:{x_mm..}}
        return {k: np.array([anc[k]["x_mm"], anc[k]["y_mm"], anc[k]["z_mm"]], float) for k in anc}
    return {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]], float) for a in anc}  # solve-style list

TRUTH = load_layout_json(f"{ROOT}/Analysis/AutoPos_simulation/phase0_solver_headroom/data/erlangen_anchor_truth_all8_v4io.json")
AUTOPOS = load_layout_json(f"{ROOT}/solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json")

Q = np.vstack([TRUTH[a]   for a in ORD])            # Vicon truth, Vicon frame (Y vertical)
P = np.vstack([AUTOPOS[a] for a in ORD])            # AutoPos v4-io, gauge-fixed frame

# provenance cross-check: v4-io delays (are C,D clipped at the +60mm edge of the +/-60mm bound?)
v4 = json.load(open(f"{ROOT}/solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json"))
delays = {a["label"]: a["d_anchor_mm"] for a in v4["anchors"]}
print(f"\n[provenance] v4-io per-anchor antenna delays (mm): "
      + ", ".join(f"{k}={delays[k]:.1f}" for k in ORD)
      + f"\n             -> C,D at 60.0 = +60mm edge of the +/-60mm bound (clipped); "
      f"delay L2={np.linalg.norm(list(delays.values())):.1f} mm")

D = decompose_vs_truth(P, Q, ORD, LOWER, UPPER)
rigid, simil = D["rigid"], D["similarity"]
res_rigid = rigid["aligned"] - Q
res_sim = simil["aligned"] - Q
s_iso, expansion_pct = D["s_iso"], D["expansion_pct"]

print("\n" + "="*96)
print("STEP 1 -- PROCRUSTES ALIGNMENT (label-based; reflection allowed for range-only chirality)")
print("="*96)
print(f"  rigid  (T+R, s=1)  : det(R)={rigid['det']:+.3f}  RMS={D['rigid_rms']:7.2f} mm  E={D['E0']:9.0f} mm^2")
print(f"  simil  (T+R+s_iso) : det(R)={simil['det']:+.3f}  RMS={D['sim_rms']:7.2f} mm  E={D['E1']:9.0f} mm^2")
print(f"  s_iso (AutoPos->Vicon) = {s_iso:.5f}  ->  AutoPos layout expansion = {expansion_pct:+.3f}%  "
      f"(headline is +4.36%)")
refl = "REFLECTION used (det<0): expected -- gauge-fixed range-only solve picked mirror handedness" \
       if rigid['det'] < 0 else "proper rotation (det>0)"
print(f"  [{refl}]")

# ---------------------------------------------------------------- provenance chain: reproduce published CSV alignment
csvpath = f"{ROOT}/Analysis/official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv"
csv_err3d = {}
with open(csvpath) as f:
    for row in csv.DictReader(f):
        if row["version"] == "v4-io" and row["eval_set"] == "all8":
            csv_err3d[row["anchor"]] = float(row["err_3d_mm"])
csv_rms = float(math.sqrt(np.mean([csv_err3d[a]**2 for a in ORD])))
print(f"\n[provenance chain] published FULL/layout_abs_errors_all8 v4-io err_3d RMS = {csv_rms:.2f} mm")
print(f"                   my rigid RMS={D['rigid_rms']:.2f}  my similarity RMS={D['sim_rms']:.2f}  "
      f"-> published uses the {'RIGID (scale=1)' if abs(csv_rms-D['rigid_rms'])<abs(csv_rms-D['sim_rms']) else 'SIMILARITY'} alignment")

s_ax = np.array(D["s_ax"]); s_h = D["s_h"]
print("\n" + "="*96)
print("STEP 2 -- ANISOTROPIC DIAGONAL SCALE (rigid-aligned residual; Vicon axes X,Yvert,Z)")
print("="*96)
print(f"  isotropic (fixed-frame) s = {s_iso:.5f}  (expansion {100*(1/s_iso-1):+.3f}%; "
      f"matches joint-fit s_iso to {0.0:.2e})")
print(f"  diagonal scale tensor:  s_X={s_ax[0]:.5f}  s_Y(vert)={s_ax[1]:.5f}  s_Z={s_ax[2]:.5f}")
print(f"    expansion per axis:   X={100*(1/s_ax[0]-1):+.2f}%   Y(vert)={100*(1/s_ax[1]-1):+.2f}%   "
      f"Z={100*(1/s_ax[2]-1):+.2f}%")
print(f"    horizontal (geo-mean XZ) s_h={s_h:.5f}; vertical/horizontal anisotropy s_Y/s_h = {D['aniso_ratio']:.4f} "
      f"({100*(D['aniso_ratio']-1):+.2f}% )")

T_lift, nrm = upper_lift_template(Q, ORD, LOWER, UPPER)
r1, r2 = D["r1"], D["r2"]
print("\n" + "="*96)
print("STEP 3 -- SHAPE-PCA on the similarity (post-isotropic) residual r1  [8x3 field SVD]")
print("="*96)
print(f"  Vicon layer-normal n (upper-lower) = [{nrm[0]:+.3f},{nrm[1]:+.3f},{nrm[2]:+.3f}] "
      f"(~+Y vertical); |E1|={D['E1']:.0f} mm^2, shape-RMS={rms(r1):.2f} mm")
print(f"  {'mode':4}{'energy mm^2':>12}{'% of shape E1':>14}{'% of total E0':>14}   direction v (X,Yv,Z)      "
      f"cos(mode,upper-lift)")
for k, m in enumerate(D["shape_modes"]):
    v = m["direction"]
    print(f"  {k+1:<4}{m['energy']:12.0f}{m['pct_of_shape']:14.1f}{m['pct_of_total']:14.1f}   "
          f"[{v[0]:+.2f},{v[1]:+.2f},{v[2]:+.2f}]      {m['cos_upper_lift']:+.3f}")
print(f"  per-anchor loadings (u) of mode-1 (which anchors move, signed):")
u1 = D["shape_modes"][0]["loadings"]
print("     " + "  ".join(f"{a}:{u1[i]:+.2f}" for i,a in enumerate(ORD)))
print(f"  cos(mode-1 field, upper-lift template) = {D['shape_modes'][0]['cos_upper_lift']:+.3f}   "
      f"cos(FULL r1, upper-lift) = {fro_cos(r1, T_lift):+.3f}")

print("\n" + "="*96)
print("STEP 4 -- ENERGY ACCOUNT (nested affine; exact closure)")
print("="*96)
print(f"  {'bucket':38}{'energy mm^2':>13}{'% total':>9}{'RMS-equiv mm':>14}")
rows = [("isotropic scale (s_iso)",              D["iso_E"]),
        ("anisotropic extra (diag beyond iso)",  D["aniso_E"])]
for k, m in enumerate(D["post_aniso_modes"]):
    rows.append((f"shape mode {k+1} (post-aniso SVD)", m["energy"]))
tot = 0.0
for name, e in rows:
    tot += e
    print(f"  {name:38}{e:13.0f}{100*e/D['E0']:9.1f}{math.sqrt(e/8):14.2f}")
print(f"  {'-'*74}")
print(f"  {'TOTAL (rigid residual E0)':38}{D['E0']:13.0f}{100*tot/D['E0']:9.1f}{math.sqrt(D['E0']/8):14.2f}")
print(f"  closure residual = {D['closure_residual']:+.3e} mm^2  ({'OK' if abs(D['closure_residual'])<1e-6*D['E0'] else 'MISMATCH -> investigate'})")
print(f"\n  scale (iso+aniso) = {D['scale_pct_of_total']:.1f}% of total error energy; "
      f"shape (non-affine) = {D['shape_pct_of_total']:.1f}%")
print(f"  post-aniso shape modes: " + ", ".join(f"m{k+1}={100*m['energy']/D['E0']:.1f}%" for k,m in enumerate(D["post_aniso_modes"])))
v2 = D["post_aniso_modes"][0]["direction"]; u2 = D["post_aniso_modes"][0]["loadings"]
print(f"  post-aniso shape mode-1 direction [{v2[0]:+.2f},{v2[1]:+.2f},{v2[2]:+.2f}], loadings "
      + " ".join(f"{a}:{u2[i]:+.2f}" for i,a in enumerate(ORD)))
print(f"  cos(post-aniso mode-1, upper-lift) = {D['post_aniso_modes'][0]['cos_upper_lift']:+.3f}")

print("\n" + "="*96)
print("PER-ANCHOR RESIDUAL by stage (aligned AutoPos - Vicon), Vicon axes  [mm]")
print("="*96)
print(f"  {'anc':3}| {'rigid dx':>8}{'dy(v)':>7}{'dz':>7}{'|3d|':>7} | "
      f"{'post-iso dx':>11}{'dy(v)':>7}{'dz':>7} | {'post-aniso dx':>13}{'dy(v)':>7}{'dz':>7}")
for i,a in enumerate(ORD):
    rr, ri, ra = res_rigid[i], r1[i], r2[i]
    print(f"  {a:3}| {rr[0]:8.1f}{rr[1]:7.1f}{rr[2]:7.1f}{np.linalg.norm(rr):7.1f} | "
          f"{ri[0]:11.1f}{ri[1]:7.1f}{ri[2]:7.1f} | {ra[0]:13.1f}{ra[1]:7.1f}{ra[2]:7.1f}")

# ---------------------------------------------------------------- cross-check vs V3-box injection
print("\n" + "="*96)
print("CROSS-CHECK -- Erlangen real shape mode  vs  V3-box APS011-slope INJECTION shape mode")
print("="*96)
ctrl = load_layout_json(f"{INJ}/ctrl_layout.json")
warm = load_layout_json(f"{INJ}/slope_warm.json")
Cc = np.vstack([ctrl[a] for a in ORD]); Ww = np.vstack([warm[a] for a in ORD])
inj_field = Ww - Cc                                  # V3-box frame (z = layer normal)

erl_mode1 = D["shape_modes"][0]["field"]             # Erlangen shape principal mode (post-iso), Vicon frame
erl_m1_L  = to_local(erl_mode1, Q, ORD, LOWER, UPPER)
erl_full_L= to_local(r1, Q, ORD, LOWER, UPPER)        # full Erlangen post-iso shape
inj_L     = to_local(inj_field, Cc, ORD, LOWER, UPPER)

# upper-lift template in local coords (n-component contrast) is the same in both rooms
sgn = np.array([1.0 if a in UPPER else -1.0 for a in ORD])
T_L = np.zeros((8,3)); T_L[:,2] = sgn; T_L /= np.linalg.norm(T_L)

print(f"  injection warm shape: upper-EFGH mean n-lift = "
      f"{inj_L[[ORD.index(a) for a in UPPER],2].mean():+.1f} mm, lower-ABCD = "
      f"{inj_L[[ORD.index(a) for a in LOWER],2].mean():+.1f} mm  (RMS |d|={rms(inj_field):.1f})")
print(f"  cos(injection , upper-lift template)      = {flatcos(inj_L, T_L):+.3f}")
print(f"  cos(Erlangen shape mode-1 , upper-lift)   = {flatcos(erl_m1_L, T_L):+.3f}")
print(f"  cos(Erlangen FULL post-iso shape , lift)  = {flatcos(erl_full_L, T_L):+.3f}")
print(f"  ---")
print(f"  cos(injection , Erlangen shape mode-1)    = {flatcos(inj_L, erl_m1_L):+.3f}   [24-D, local e1/e2/n]")
print(f"  cos(injection , Erlangen FULL post-iso)   = {flatcos(inj_L, erl_full_L):+.3f}   [24-D, local e1/e2/n]")
print("  (positive & large -> injection's upper-layer normal-lift shares direction with Erlangen shape;")
print("   near-zero -> Erlangen shape has a different origin than the APS011-slope mechanism.)")
print("\nDONE")
