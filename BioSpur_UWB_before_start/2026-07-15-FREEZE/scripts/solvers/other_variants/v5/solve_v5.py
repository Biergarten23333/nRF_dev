#!/usr/bin/env python3
"""V5 layout solver -- common-mode reparameterized anchor auto-positioning.

Standalone + importable. This module does NOT modify any V4-IO production code.
It *imports* the existing, battle-tested utility functions (gauge_align_local,
mds_init, pos_param_map, pack_pos, unpack_pos, physical_layout_prior_residuals,
layout_physical_diagnostics, local_pairs, solve_autopos_v1) from the production
evaluation module, and reuses run_clean_full_compare.py for data loading/fusion.

--------------------------------------------------------------------------------
WHY V5 (the delay <-> scale coupling)
--------------------------------------------------------------------------------
V4-IO estimates 18 gauge-fixed position params + 7 per-anchor ABSOLUTE delays
d_i (anchor A fixed d_A = 0), with a HARD +/-60 mm box on each d_i. In the
deployed 20260710 calibration that box BINDS on C and H (both pinned at
59.99999 mm). The box exists because the common-mode anchor delay is degenerate
with isotropic layout scale (Fisher-information rho = -0.977, variance inflation
22x): a wider box lets the per-anchor delay block soak up geometry error it
cannot actually resolve. So when the box binds, the solver *cannot represent the
true per-anchor delay* for those devices -- it just clips.

--------------------------------------------------------------------------------
WHAT V5 CHANGES (reparameterize, do not just widen the box)
--------------------------------------------------------------------------------
V5 splits every non-gauge anchor delay into a shared common mode plus a
per-anchor differential:

    d_A = 0                      (gauge, IDENTICAL to V4-IO -> delays stay
                                  directly comparable to the V4-IO output)
    d_i = c + e_i   for i in B..H

  * c   : ONE shared common-mode delay. This is the scale-coupled component, so
          it still needs a bound -- but it can be LOOSER (+/-150 mm) than v4's
          +/-60 because it is a single parameter, not 7, and the coupling is now
          isolated in exactly this one knob.
  * e_i : per-anchor DIFFERENTIAL delay. Identifiable from inter-anchor range
          ASYMMETRY (not scale-coupled), so bounded looser (+/-200 mm) but pulled
          toward 0 by a soft Tikhonov prior e_i / sigma_e (sigma_e = 30 mm).
  * gauge on the split: mean(e over B..H) tied to 0. This breaks the residual
          c <-> mean(e) confound so c carries the shared level and e_i carry only
          anchor-to-anchor differences.

Parameter budget (n = 8): 18 position + 1 common (c) + 7 differential (e_B..e_H)
- 1 mean-tie = 25 effective DOF -- the SAME count as V4-IO's 18 + 7. The
difference is *where the DOF live*: v4 forces the common mode to be expressed
through the 7 individually-boxed d_i (which then clip at 60 mm); V5 gives the
common mode its own explicit, loosely-bounded knob so C and H can take their
true differential delay without hitting a wall.

--------------------------------------------------------------------------------
STABILITY CLASSIFICATION
--------------------------------------------------------------------------------
The overnight static drift study shows per-anchor bias is STABLE for A/C/D/F/G
(sigma1 ~ 25 mm, < 40 mm drift over 10.6 h) but B/E/H exhibit events (a 300 mm
step, bistable multipath, noise bursts). V5 therefore also emits a per-anchor
`stability_class` in {STABLE, NOISY, BURSTY, STEP}, computed from the temporal
stability of each inter-anchor pair's range series (see classify_stability).
STABLE differentials are trustworthy for days/weeks; NOISY/BURSTY/STEP anchors
are flagged so downstream solvers (and humans) know their delay is provisional.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

# ---------------------------------------------------------------------------
# Module loading -- import (never copy) the production utilities.
# ---------------------------------------------------------------------------
REPO = Path(__file__).resolve().parents[2]
EVAL_PATH = REPO / "autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py"
FC_PATH = REPO / "autopos_pipeline/outdoor_20260513/run_clean_full_compare.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required so @dataclass in the module resolves (py3.12)
    spec.loader.exec_module(mod)
    return mod


# The production evaluation module holds the solve utilities we reuse verbatim.
EVAL = _load_module(EVAL_PATH, "v5_eval")

# ---------------------------------------------------------------------------
# V5 defaults (all in range-equivalent millimetres).
# ---------------------------------------------------------------------------
V5_C_BOUND_MM = 150.0          # common-mode delay box (scale-coupled -> bounded)
V5_E_BOUND_MM = 200.0          # differential delay box (not scale-coupled -> loose)
V5_SIGMA_E_MM = 30.0           # Tikhonov prior scale pulling differentials -> 0
V5_SIGMA_C_MM = 25.0           # Tikhonov prior on the common mode c. CRITICAL: c is
                               # scale-degenerate (rho=-0.977), and the DW1000 hardware
                               # ANT_DLY=16436 already absorbs the shared antenna delay,
                               # so the RESIDUAL common mode should be ~0. Without this
                               # prior, c saturates its box and shrinks the layout ~100mm
                               # (scale leakage). See analysis/solver_v2_validation.
V5_RESIDUAL_SIGMA_MM = 15.0    # inter-anchor pair residual sigma (same as V4-IO)
V5_F_SCALE_MM = 30.0           # Huber transition (range-mm); NLOS-positive outliers

# Stability-classification thresholds (range-mm), applied to per-pair series.
STAB_NOISE_SIGMA_MM = 40.0     # robust within-segment scatter above this -> NOISY
STAB_STEP_MM = 50.0            # sustained median shift above this -> STEP
STAB_STEP_K = 4.0             # ...and shift must exceed this x within-segment scatter
STAB_BURST_K = 5.0             # excursion above this x robust sigma counts as a burst
STAB_BURST_ABS_MM = 80.0       # ...and must be at least this many mm in absolute terms
STAB_BURST_FRAC = 0.01         # burst fraction above this (but baseline calm) -> BURSTY
STAB_ANCHOR_FRAC = 0.35        # fraction of an anchor's incident pairs that must
                               # show an event before the anchor inherits that class
_CLASS_PRIORITY = {"STEP": 3, "BURSTY": 2, "NOISY": 1, "STABLE": 0}


# ===========================================================================
# Core V5 solve
# ===========================================================================
def solve_v5(
    pair_dists,
    anchor_ids,
    x_init=None,
    *,
    c_bound_mm: float = V5_C_BOUND_MM,
    e_bound_mm: float = V5_E_BOUND_MM,
    sigma_e_mm: float = V5_SIGMA_E_MM,
    sigma_c_mm: float = V5_SIGMA_C_MM,
    fix_common_mode: float | None = None,
    residual_sigma_mm: float = V5_RESIDUAL_SIGMA_MM,
    f_scale_mm: float = V5_F_SCALE_MM,
    loss: str = "huber",
    max_nfev: int = 5000,
):
    """Solve the 8-anchor layout with common-mode delay reparameterization.

    Args:
        pair_dists: dict {(gi, gj): distance_mm} of fused inter-anchor ranges.
        anchor_ids: list of global anchor ids (e.g. [0..7]).
        x_init: optional (n,3) seed; MDS init if None.

    Returns:
        (x, dly, result) where
          x    : (n,3) gauge-aligned anchor positions (mm),
          dly  : (n,) absolute per-anchor delays d_i = c + e_i (d_A = 0),
          result: scipy OptimizeResult with V5 diagnostics attached
                  (.common_mode_mm, .differential_delay_mm, .absolute_delay_mm,
                   .pair_rmse_mm, .pair_residuals_mm, .physical_diagnostics, ...).
    """
    if not np.isfinite(residual_sigma_mm) or residual_sigma_mm <= 0.0:
        raise ValueError(f"residual_sigma_mm must be positive, got {residual_sigma_mm!r}")
    if not np.isfinite(sigma_e_mm) or sigma_e_mm <= 0.0:
        raise ValueError(f"sigma_e_mm must be positive, got {sigma_e_mm!r}")

    lp, _g2l, _l2g = EVAL.local_pairs(pair_dists, anchor_ids)
    n = len(anchor_ids)
    if x_init is None:
        x_init = EVAL.mds_init(lp, n)
    pmap = EVAL.pos_param_map(n)
    # differentials for anchors 1..n-1 (anchor 0 = A is the d_A = 0 gauge).
    n_e = max(0, n - 1)

    # Common-mode prior: pull c toward c_target. Normal operation targets 0 with
    # sigma_c_mm; fix_common_mode pins c to a chosen value with a tiny sigma (the
    # c=0 / c=fixed ablations used in validation).
    c_target = 0.0 if fix_common_mode is None else float(fix_common_mode)
    eff_sigma_c = float(sigma_c_mm) if fix_common_mode is None else 1e-3
    if not np.isfinite(eff_sigma_c) or eff_sigma_c <= 0.0:
        eff_sigma_c = None  # no prior on the common mode (leakage ablation)

    def unpack(v):
        x = EVAL.unpack_pos(v[:len(pmap)], n)
        c = float(v[len(pmap)]) if n > 1 else 0.0
        e = np.zeros(n, dtype=float)
        if n_e > 0:
            e[1:] = np.asarray(v[len(pmap) + 1:len(pmap) + 1 + n_e], dtype=float)
        # e_A = 0 gauge (anchor A's DIFFERENTIAL is the gauge). Every anchor
        # carries the shared common mode c, so d_A = c + e_A = c and
        # d_i = c + e_i. Applying c to ALL anchors (incl. A) makes it a clean
        # SYMMETRIC scale DOF: pair predictions inflate uniformly by 2c, so the
        # data is near-indifferent to c (pure scale<->c degeneracy) and the
        # sigma_c prior can actually pin it -- unlike a d_A=0 gauge, which makes
        # c asymmetric and creates a spurious strong data pull to the bound.
        dly = c + e
        return x, c, e, dly

    def residual(v):
        x, c, e, dly = unpack(v)
        out = [
            (np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist) / residual_sigma_mm
            for (i, j), dist in lp.items()
        ]
        if n_e > 0:
            # Tikhonov prior: pull each A-referenced differential toward zero.
            out.extend((e[1:] / sigma_e_mm).tolist())
        # Common-mode prior: resolve the c<->scale degeneracy toward c_target.
        # This is the term that stops c eating layout scale (see module docstring).
        if eff_sigma_c is not None:
            out.append((c - c_target) / eff_sigma_c)
        out.extend(EVAL.physical_layout_prior_residuals(x, anchor_ids))
        return np.asarray(out, dtype=float)

    x0 = np.r_[EVAL.pack_pos(x_init), [c_target], np.zeros(n_e)]
    lo = np.r_[np.full(len(pmap), -np.inf), [-c_bound_mm], np.full(n_e, -e_bound_mm)]
    hi = np.r_[np.full(len(pmap), np.inf), [c_bound_mm], np.full(n_e, e_bound_mm)]
    # scipy's f_scale is in the (normalized) residual units used inside `fun`.
    normalized_f_scale = max(float(f_scale_mm) / float(residual_sigma_mm), 1e-9)
    result = least_squares(
        residual, x0, loss=loss, f_scale=normalized_f_scale,
        bounds=(lo, hi), max_nfev=max_nfev,
    )

    x, c, e, dly = unpack(result.x)
    x = EVAL.gauge_align_local(x)
    pair_resid = [
        float(np.linalg.norm(x[i] - x[j]) + dly[i] + dly[j] - dist)
        for (i, j), dist in lp.items()
    ]
    result.physical_diagnostics = EVAL.layout_physical_diagnostics(x, anchor_ids)
    result.common_mode_mm = float(c)
    result.differential_delay_mm = np.asarray(e, dtype=float)   # e[0]=0 (A)
    result.absolute_delay_mm = np.asarray(dly, dtype=float)
    result.pair_residuals_mm = pair_resid
    result.pair_rmse_mm = float(np.sqrt(np.mean(np.square(pair_resid)))) if pair_resid else float("nan")
    result.c_bound_mm = float(c_bound_mm)
    result.e_bound_mm = float(e_bound_mm)
    result.sigma_e_mm = float(sigma_e_mm)
    result.sigma_c_mm = (float(sigma_c_mm) if eff_sigma_c is not None else float("inf"))
    result.common_mode_fixed = (None if fix_common_mode is None else float(fix_common_mode))
    result.n_e_at_bound = int(np.sum(np.abs(e[1:]) >= e_bound_mm - 0.01)) if n_e > 0 else 0
    result.c_at_bound = bool(abs(c) >= c_bound_mm - 0.01)
    return x, dly, result


# ===========================================================================
# Stability classification
# ===========================================================================
def _classify_pair_series(series) -> dict:
    """Classify one inter-anchor pair's range time-series.

    Returns a dict with the assigned class + the diagnostics that drove it.
    Robust throughout (median / MAD) so a few outliers do not masquerade as
    persistent noise.
    """
    x = np.asarray([v for v in series if np.isfinite(v)], dtype=float)
    out = {
        "class": "STABLE", "n": int(x.size),
        "robust_sigma_mm": float("nan"), "max_step_mm": 0.0, "burst_frac": 0.0,
    }
    if x.size < 12:
        # Too few samples to say anything -> treat as STABLE (unknown), not an event.
        if x.size:
            med = float(np.median(x))
            out["robust_sigma_mm"] = float(1.4826 * np.median(np.abs(x - med)))
        return out

    med = float(np.median(x))
    sigma = float(1.4826 * np.median(np.abs(x - med)))
    out["robust_sigma_mm"] = sigma

    # --- STEP: best two-segment split by |median(left) - median(right)| ---
    w = max(6, x.size // 10)              # minimum segment length
    best_step = 0.0
    best_within = sigma
    for k in range(w, x.size - w):
        left, right = x[:k], x[k:]
        ml, mr = np.median(left), np.median(right)
        step = abs(float(mr - ml))
        if step > best_step:
            sl = 1.4826 * np.median(np.abs(left - ml))
            sr = 1.4826 * np.median(np.abs(right - mr))
            best_step = step
            best_within = max(1.0, 0.5 * (sl + sr))
    out["max_step_mm"] = best_step
    is_step = best_step >= STAB_STEP_MM and best_step >= STAB_STEP_K * best_within

    # --- BURST: occasional large excursions on an otherwise calm baseline ---
    scale = max(sigma, 1.0)
    excursion = np.abs(x - med)
    burst_mask = (excursion > STAB_BURST_K * scale) & (excursion > STAB_BURST_ABS_MM)
    burst_frac = float(np.mean(burst_mask))
    out["burst_frac"] = burst_frac
    is_burst = burst_frac >= STAB_BURST_FRAC and sigma < STAB_NOISE_SIGMA_MM

    # --- NOISE: persistently high robust scatter ---
    is_noisy = sigma >= STAB_NOISE_SIGMA_MM

    if is_step:
        out["class"] = "STEP"
    elif is_burst:
        out["class"] = "BURSTY"
    elif is_noisy:
        out["class"] = "NOISY"
    return out


def classify_stability(directed_series, anchor_ids) -> dict:
    """Per-anchor stability class from directed inter-anchor pair series.

    Args:
        directed_series: dict {(gi, gj): [range_mm, ...]} time-ordered samples
            (e.g. the output of run_clean_full_compare.load_sweep_grouped()).
        anchor_ids: list of global anchor ids.

    Returns:
        dict {anchor_id: {"class": ..., diagnostics...}}. An anchor inherits an
        event class (STEP/BURSTY/NOISY) only if at least STAB_ANCHOR_FRAC of its
        incident directed pairs exhibit it -- a real per-anchor fault shows in
        most of that anchor's pairs, whereas one flaky link shows in only one,
        so a single bad partner cannot smear the whole anchor.
    """
    per_anchor_pairs: dict[int, list[dict]] = {a: [] for a in anchor_ids}
    for (gi, gj), series in directed_series.items():
        if gi not in per_anchor_pairs or gj not in per_anchor_pairs:
            continue
        info = _classify_pair_series(series)
        per_anchor_pairs[gi].append(info)
        per_anchor_pairs[gj].append(info)

    result: dict[int, dict] = {}
    for a in anchor_ids:
        infos = per_anchor_pairs[a]
        if not infos:
            result[a] = {"class": "STABLE", "n_pairs": 0, "median_sigma_mm": float("nan"),
                         "max_step_mm": 0.0, "max_burst_frac": 0.0, "class_counts": {}}
            continue
        counts = {"STEP": 0, "BURSTY": 0, "NOISY": 0, "STABLE": 0}
        for info in infos:
            counts[info["class"]] += 1
        n_pairs = len(infos)
        sigmas = [i["robust_sigma_mm"] for i in infos if np.isfinite(i["robust_sigma_mm"])]
        cls = "STABLE"
        # Priority: STEP > BURSTY > NOISY. Require a plurality of incident pairs.
        for candidate in ("STEP", "BURSTY", "NOISY"):
            if counts[candidate] >= max(1, int(np.ceil(STAB_ANCHOR_FRAC * n_pairs))):
                cls = candidate
                break
        result[a] = {
            "class": cls,
            "n_pairs": n_pairs,
            "median_sigma_mm": float(np.median(sigmas)) if sigmas else float("nan"),
            "max_step_mm": float(max((i["max_step_mm"] for i in infos), default=0.0)),
            "max_burst_frac": float(max((i["burst_frac"] for i in infos), default=0.0)),
            "class_counts": counts,
        }
    return result


# ===========================================================================
# Data loading + end-to-end run (mirrors logs/.../run_v4io_solve.py)
# ===========================================================================
def load_pairs_and_series(pairs_csv: Path, anchor_ids):
    """Return (fused_v3_pair_dists, directed_series) from a pairs_all.csv.

    Uses the production run_clean_full_compare loader/fuser -- byte-for-byte the
    same fusion (directed medians -> v3 robust/MAD) V4-IO uses.
    """
    fc = _load_module(FC_PATH, "v5_fc")
    fc.SWEEP_CSV = Path(pairs_csv).resolve()
    mod = fc.load_eval_module()
    directed = fc.load_sweep_grouped()          # {(gi,gj): [dist_mm,...]}
    fused = fc.fuse_all(mod, directed, anchor_ids)
    return fused["v3"], directed, fc


def _v4io_divergence(x, anchor_ids, v4_ref):
    """max/rms position deviation (mm) of V5 layout from a V4-IO reference dict."""
    if not v4_ref:
        return None, None
    ref = {a["id"]: (a["x_mm"], a["y_mm"], a["z_mm"]) for a in v4_ref.get("anchors", [])}
    devs = []
    for li, gi in enumerate(anchor_ids):
        if gi in ref:
            devs.append(float(np.linalg.norm(np.asarray(x[li]) - np.asarray(ref[gi]))))
    if not devs:
        return None, None
    return float(np.max(devs)), float(np.sqrt(np.mean(np.square(devs))))


def build_layout_dict(x, dly, result, stability, anchor_ids, pairs_csv, fc,
                      v4_ref=None, scale_locked=False) -> dict:
    ANCHORS = fc.ANCHORS
    diff = result.differential_delay_mm      # A-referenced differential e_i (e_A=0)
    present = sorted({a for a in anchor_ids})
    maxdev, rmsdev = _v4io_divergence(x, anchor_ids, v4_ref)

    # Honest scale-identifiability verdict. The inter-anchor common mode is
    # degenerate with layout scale (rho=-0.977); if c saturated its box the
    # absolute geometry is NOT trustworthy without an external metric constraint.
    warnings = []
    scale_identifiable = True
    if result.c_at_bound and not scale_locked:
        scale_identifiable = False
        warnings.append(
            f"common_mode c saturated its +/-{result.c_bound_mm:.0f} mm box "
            f"(c={result.common_mode_mm:+.1f} mm): the inter-anchor data wants a large "
            "shared bias that is degenerate with layout scale -> absolute geometry is "
            "UNIDENTIFIABLE from inter-anchor ranges alone.")
    if maxdev is not None and maxdev > 50.0 and not scale_locked:
        warnings.append(
            f"layout diverges {maxdev:.0f} mm (max) / {rmsdev:.0f} mm (rms) from the "
            "deployed V4-IO geometry: V4-IO was pinned by its +/-60 mm delay box, not "
            "by the data. Do NOT deploy this absolute geometry without a metric-scale "
            "constraint (corner reflector / measured baseline / fixed-XYZ reference tag).")
    if result.n_e_at_bound > 0:
        warnings.append(f"{result.n_e_at_bound} differential(s) at the +/-{result.e_bound_mm:.0f} mm box.")

    anchors = []
    for li, gi in enumerate(anchor_ids):
        st = stability.get(gi, {})
        anchors.append({
            "id": int(gi),
            "label": ANCHORS[gi],
            "x_mm": float(x[li, 0]),
            "y_mm": float(x[li, 1]),
            "z_mm": float(x[li, 2]),
            # d_anchor_mm = absolute delay d_i = c + e_i, self-consistent with the
            # positions above. The A-referenced differential is reported separately.
            "d_anchor_mm": float(dly[li]),
            "differential_delay_mm": float(diff[li]),
            "stability_class": st.get("class", "STABLE"),
            "stability_median_sigma_mm": st.get("median_sigma_mm", float("nan")),
            "stability_max_step_mm": st.get("max_step_mm", 0.0),
        })
    return {
        "version": "v5",
        "label": "V5 common-mode" + (" (scale-locked)" if scale_locked else ""),
        "solver": "v5",
        "anchor_ids": list(anchor_ids),
        "anchors": anchors,
        "tag_delay_mm": 0.0,
        "common_mode_delay_mm": float(result.common_mode_mm),
        "differential_delay_mm": [float(v) for v in diff],
        "stability_class": [a["stability_class"] for a in anchors],
        "scale_identifiable": bool(scale_identifiable),
        "warnings": warnings,
        "stats": {
            "inter_anchor_pair_rms_mm": round(float(result.pair_rmse_mm), 2),
            "n_pairs": len(result.pair_residuals_mm),
            "anchors_present": "".join(ANCHORS[i] for i in present),
            "solver": "v5 (common-mode reparameterization; standalone solve_v5.py)",
            "source_pairs_csv": str(pairs_csv),
            "success": bool(getattr(result, "success", False)),
            "scale_locked": bool(scale_locked),
            "common_mode_delay_mm": float(result.common_mode_mm),
            "c_bound_mm": float(result.c_bound_mm),
            "e_bound_mm": float(result.e_bound_mm),
            "sigma_e_mm": float(result.sigma_e_mm),
            "sigma_c_mm": float(result.sigma_c_mm),
            "n_differential_at_bound": int(result.n_e_at_bound),
            "common_mode_at_bound": bool(result.c_at_bound),
            "layout_maxdev_from_v4io_mm": maxdev,
            "layout_rmsdev_from_v4io_mm": rmsdev,
        },
        "extra": dict(result.physical_diagnostics or {}),
    }


V4IO_DEPLOYED = REPO / "logs/system_calibration_20260710_233443/anchor_layout.json"


def run(pairs_csv: Path, out_json: Path, anchor_ids=None, *,
        scale_lock: bool = False, v4_ref_path: Path | None = V4IO_DEPLOYED) -> dict:
    """Solve + classify + write a V5 layout JSON.

    scale_lock=False (default): spec config (c +/-150, e +/-200) -- the honest
        best-fit that also reveals the scale unidentifiability. Absolute geometry
        is flagged untrustworthy if the common mode saturates.
    scale_lock=True: fix c=0 and re-impose a +/-60 mm differential box -> a
        V4-IO-compatible, deployment-safe geometry (differentials only).
    """
    anchor_ids = list(range(8)) if anchor_ids is None else list(anchor_ids)
    pairs_csv = Path(pairs_csv).resolve()
    out_json = Path(out_json).resolve()
    v4_ref = None
    if v4_ref_path and Path(v4_ref_path).exists():
        v4_ref = json.loads(Path(v4_ref_path).read_text(encoding="utf-8"))

    pair_dists, directed, fc = load_pairs_and_series(pairs_csv, anchor_ids)
    init, _ = EVAL.solve_autopos_v1(pair_dists, anchor_ids)   # same seed as V4-IO
    if scale_lock:
        x, dly, result = solve_v5(pair_dists, anchor_ids, init,
                                  fix_common_mode=0.0, e_bound_mm=60.0)
    else:
        x, dly, result = solve_v5(pair_dists, anchor_ids, init)
    stability = classify_stability(directed, anchor_ids)

    layout = build_layout_dict(x, dly, result, stability, anchor_ids, pairs_csv, fc,
                               v4_ref=v4_ref, scale_locked=scale_lock)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(layout, indent=2), encoding="utf-8")

    print(f"[v5] solved 8-anchor layout -> {out_json}  (scale_lock={scale_lock})")
    print(f"[v5] inter-anchor pair RMS = {result.pair_rmse_mm:.2f} mm over "
          f"{len(result.pair_residuals_mm)} pairs; common-mode c = {result.common_mode_mm:+.2f} mm")
    print(f"[v5] scale_identifiable={layout['scale_identifiable']}; "
          f"maxdev_from_v4io={layout['stats']['layout_maxdev_from_v4io_mm']}")
    for w in layout["warnings"]:
        print(f"    !! {w}")
    for li, gi in enumerate(anchor_ids):
        st = stability.get(gi, {})
        print(f"    {fc.ANCHORS[gi]}: x={x[li,0]:8.1f} y={x[li,1]:8.1f} z={x[li,2]:8.1f}  "
              f"d={dly[li]:+6.1f}  e={result.differential_delay_mm[li]:+6.1f}  "
              f"[{st.get('class','?'):6s} sig={st.get('median_sigma_mm', float('nan')):5.1f}]")
    return layout


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V5 common-mode layout solver")
    ap.add_argument("pairs_csv", type=Path, help="inter-anchor pairs_all.csv")
    ap.add_argument("out_json", type=Path, help="output anchor_layout.json (V5)")
    ap.add_argument("--scale-lock", action="store_true",
                    help="fix c=0 + re-impose +/-60 mm differential box (V4-IO-safe)")
    args = ap.parse_args(argv)
    run(args.pairs_csv, args.out_json, scale_lock=args.scale_lock)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
