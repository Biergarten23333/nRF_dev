# Tag Solver Audit: T1-T4 and T4_V6_IMU_GATE

Generated: 2026-06-19T11:14:33

This is a read-only audit. Solver source files were not modified.

## Files Inspected

- `biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/models.py`
- `biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/c_solver.py`
- `biospur_tag_positioning_offline_solver/biospur_tag_positioning_offline_solver/trajectory.py`
- `biospur_tag_positioning_offline_solver/c_core/include/biospur_tagpos/tagpos_solver.h`
- `biospur_tag_positioning_offline_solver/c_core/src/tagpos_solver.c`
- `biospur_tag_positioning_offline_solver/docs/t_series_design.md`
- `biospur_tag_positioning_offline_solver/docs/c_api.md`
- `biospur_tag_positioning_offline_solver/docs/version_chain.md`

No production `T5` solver was found. The implemented set is `T1`, `T2`, `T3`, `T4`, and Python policy variant `T4_V6_IMU_GATE`.

## Shared C Core

The C core solves one frame at a time. It estimates only the tag position `p`. File parsing, static aggregation, layout loading, per-tag delay selection, and trajectory iteration stay in Python.

Shared model:

```text
pred_i = ||p - A_i|| + d_anchor_i + D_tag
residual_i = pred_i - range_i
min_p sum_i rho(residual_i / sigma_eff_i)
```

The solver uses custom Gauss-Newton normal equations, `max_iters=8`, `max_step=500 mm`, convergence threshold `0.02 mm`, and centroid or previous-position initialization. Outputs include position, used mask, residual RMS, p95 absolute residual, max absolute residual, and residuals by anchor.

## T1: Robust WLS Multilateration

**Method ID:** `1`  
**Purpose:** behavior-compatible baseline for the official Python solver.

Inputs are per-frame tag-anchor ranges, anchor positions, anchor delays from `layout.json`, anchor sigma values, optional previous position as initial guess, and `D_tag` from layout or per-tag override. The only unknown is tag position `p = [x,y,z]`.

T1 uses anchor sigma weighting and robust residual weighting. Current source defaults to Huber with `huber_delta_mm=30`. If `huber_delta_mm <= 0`, the legacy normalized `huber_k=2` path is used.

## T2: Quality-Aware Robust WLS

**Method ID:** `2`

T2 uses the same unknowns, Gauss-Newton core, and robust loss as T1, but changes effective sigma based on current quality and quality EMA:

```text
bad = max(0, (100 - quality) / 50)
penalty = clamp(1 + quality_penalty_scale * bad^2, 1, quality_penalty_cap)
sigma_eff = sigma_anchor * penalty
```

Default `quality_penalty_scale=1.5`, `quality_penalty_cap=4.0`.

## T3: Dynamic-Stable Robust WLS

**Method ID:** `3`

T3 adds persistent residual memory and a weak previous-position prior. If a link's residual EMA exceeds `120 mm`, effective sigma is inflated:

```text
excess = (residual_ema - 120) / 80
penalty = clamp(1 + 0.50 * excess, 1, 2.5)
```

When previous position `x0` is valid, it adds `(p - x0) / temporal_prior_sigma_mm` with default `temporal_prior_sigma_mm=180`.

Difference from T2: T3 prioritizes dynamic continuity and low-redundancy stability. It does not hard-reject anchors.

## T4: Adaptive Redundancy Policy

**Method ID:** `4` in C, implemented as a Python wrapper policy.

T4 is the current dynamic candidate. In Python `TagPositionSolver.solve_frame`:

```text
if method is T4/T4_V6_IMU_GATE and n >= 8:
    call C solver with a memory-free T1 config and no previous-position prior
else:
    use the T4/T3-style path with quality/residual EMA and temporal prior
```

This means full 8-anchor frames avoid carrying temporal/NLOS memory, while low-redundancy frames keep T3-style stabilization.

## T4_V6_IMU_GATE

This is a Python-side policy variant over C method 4, not a new C enum. For low-redundancy frames with valid IMU summaries, it weakens the temporal prior according to acceleration norm variation:

```text
sigma_acc_mps2 = acc_norm_std_mg * 0.00980665
prior_scale = exp(-ln(2) * sigma_acc_mps2 / 0.5)
sigma_prior_used = sigma_prior_base / sqrt(prior_scale)
```

If IMU data is missing/invalid, it falls back to T4 behavior. With all 8 anchors, it uses the same memory-free T1 path as T4.

## Code Quality / Audit Findings

1. `SolverLossName` exposes `linear`, `huber`, and `tukey`, and `CConfig` includes `BIOSPUR_TAGPOS_LOSS_LINEAR=0`, but `biospur_tagpos_solve_frame` currently validates only Huber/Tukey and resets other values to Huber. Thus `solver_loss="linear"` is not actually honored by the C core.
2. `reject_abs_threshold_mm`, `reject_min_improvement_mm`, and `reject_improvement_ratio` are present in the C/Python config, but the current C solve path does not perform a leave-one-out hard rejection search; `rejected_index` remains `-1` unless a future path sets it.
3. T4 naming has internal history (`T4 v1` ... `T4 v5` in validation reports), but production code exposes only `T4`. Reports should say `T4` means current T4 v5 behavior.
4. `T4_V6_IMU_GATE` is mapped to C method 4; any downstream analysis should preserve the Python method string to avoid losing the IMU-gating distinction.
5. Range aggregation is intentionally outside the tag C solver. Static p50/lower-trim choices must be documented by the caller, not inferred from T1-T4.

## Recommendations From Erlangen Campaign

- Use T4 only where its dynamic policy is wanted. For full-anchor static frames, T4's current path is effectively T1.
- For static analysis, explicitly log range aggregation (`p50`, `p30`, `lower_trim_20`) and `D_tag` source.
- Keep Huber(`delta=30 mm`) as the default robust loss for NLOS-positive tails.
- Fix or document the `linear` loss mismatch if linear mode is intended for reproducibility/audits.
