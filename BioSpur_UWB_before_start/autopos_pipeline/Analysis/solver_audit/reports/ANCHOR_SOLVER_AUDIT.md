# Anchor Solver Audit: V1-V5

Generated: 2026-06-19T11:14:33

This is a read-only audit. Solver source files were not modified. Audit outputs were written only under `autopos_pipeline/Analysis/solver_audit/`.

## Files Inspected

### Anchor solver implementation sources
- `autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`
- `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v1_to_v4_io.py`
- `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py`
- `autopos_pipeline/scripts/prepare_v4_data.py`

### Anchor solver artifacts / metadata
- `autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/`
- `autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/tables/version_summary.csv`
- `autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/v*/layout.json`

## Version Chain Summary

The anchor solver chain is implemented mostly as a single historical evaluation module plus wrapper scripts. `run_v1_to_v4_io.py` selects the five field-check variants, while `run_clean_full_compare.py` adds V4 common-mode and downstream diagnostic branches. V5 is not a cleanly isolated standalone production script; in current code it is the common-mode extension of V4-io (`solve_v4_common_mode`) plus selected/generated layout artifacts such as `v5-commonmode/layout.json`.

## V1 / v1-old

**Files:** `run_clean_full_compare.py`, `run_full_evaluation_same_pipeline_20260513.py`  
**Core functions:** `solve_v1_old`, `mds_init`, `solve_autopos_v1`, `solve_mds_nls`

Inter-anchor ranges come from `sweep1000/pairs_all.csv`. For the field replay `v1-old`, all directed samples for a pair are concatenated and fused by arithmetic mean. The helper module also contains an `AutoPos V1` name that performs MDS followed by nonlinear least-squares refinement; the field replay `v1-old` is the stricter early MDS baseline.

Unknowns are only anchor coordinates. The coordinate gauge fixes anchor A at the origin, B on the x-axis, and C in the xy-plane. There are no anchor delays.

Historical field `v1-old` is classical MDS on the fused pair-distance matrix. The helper `AutoPos V1` path refines:

```text
min_A sum_{i<j} ( ||A_i - A_j|| - r_ij )^2
```

Classical MDS initializes the geometry. The helper path uses `scipy.optimize.least_squares` with linear loss. Output layout delay fields are zero.

## V2

**File:** `run_full_evaluation_same_pipeline_20260513.py`  
**Core functions:** `fuse_from_directed(..., method="v2")`, `solve_autopos_v2`

For each directed pair, V2 computes a mean and sample variance. The two directions are fused by inverse variance weighting:

```text
r_ij = (var_ba * mean_ab + var_ab * mean_ba) / (var_ab + var_ba)
```

Anchor coordinates are the only unknowns. V2 minimizes pair residuals plus a weak z/coplanarity soft regularizer whose `lam` starts at `0.01` and is halved over three refinement passes. It uses classical MDS initialization followed by `least_squares` with linear loss.

Difference from V1: better bidirectional range fusion and weak geometric regularization.

## V3-lite

**File:** `run_full_evaluation_same_pipeline_20260513.py`  
**Core functions:** `fuse_from_directed(..., method="v3")`, `solve_autopos_v1`

V3-lite computes each direction's median, estimates spread via MAD, then fuses the two directional medians using MAD-sigma squared weights. This is the first robust pair aggregation stage.

Unknowns are only anchor coordinates; no delay terms. The cost is the same no-delay least-squares pair-distance cost as the V1 helper.

Difference from V2: robust median/MAD pair fusion replaces mean/variance fusion.

## V3-full

**File:** `run_full_evaluation_same_pipeline_20260513.py`  
**Core function:** `solve_v3_full`

V3-full uses V3 median/MAD fused inter-anchor pair distances. It estimates anchor coordinates and independent anchor delays. Anchor A is implicitly fixed at `d_A=0` because delay updates iterate over anchors `1..N-1`.

The solver alternates between robust position solving and median delay updates:

```text
r_ij = ||A_i - A_j|| + d_i + d_j - r_ij_measured
sigma = max(MAD(r), 5 mm)
w_ij = Tukey(r_ij / (4.685 * sigma))
min_A sum_{i<j} w_ij * r_ij^2
```

Then each delay `d_i` is updated from the median incident-link estimate:

```text
d_i = median_j( r_ij_measured - ||A_i-A_j|| - d_j )
```

It runs up to 50 IRLS outer iterations. The inner solve is `scipy.optimize.least_squares` with linear loss on preweighted residuals. There are no explicit delay bounds and no `e_i` regularizer.

Difference from V3-lite: per-anchor delay estimation and Tukey IRLS.

## V4 / V4-io

**File:** `run_full_evaluation_same_pipeline_20260513.py`  
**Core function:** `solve_v4`

V4 uses V3 median/MAD fused inter-anchor pair distances. No tag-position data is used in the V4-io layout solve.

Unknowns are anchor coordinates plus independent anchor delays for anchors 1..N-1. Anchor A delay is gauge-fixed zero.

```text
min_{A,d} Huber_f=2([
  (||A_i-A_j|| + d_i + d_j - r_ij) / 15,
  d_i / 20,
  physical_layout_priors(A)
])
```

`physical_layout_priors` softly keeps D near the lower layer, E/F/G/H near the upper layer, and the layer gap within a plausible range. The optimizer is `scipy.optimize.least_squares(loss="huber", f_scale=2.0, max_nfev=5000)`. Delay bounds are `[-60,+60] mm` for non-A anchors.

Official field summary reports V4-io AutoPos RMS `48.17 mm`, static median `58.60 mm`, and delay max at the `60 mm` bound.

Difference from V3-full: joint bounded delay/position optimization with Huber loss and physical priors.

## V5 / V5-commonmode

**Files:** `run_full_evaluation_same_pipeline_20260513.py`, `run_clean_full_compare.py`, generated `v5-commonmode/layout.json`  
**Core function:** `solve_v4_common_mode`

V5 uses the same inter-anchor graph as V4, normally V3/p50-style fused ranges. The latest lower-trim blind experiment indicates lower-trim anchor ranges were worse, so anchor-side recommendation remains p50-like aggregation.

Unknowns are anchor coordinates, a global common-mode delay `c`, and optional per-anchor residual terms `e_i`:

```text
d_i = c + e_i
```

Unlike V4, there is no `d_A=0` delay gauge. The gauge for `e_i` is controlled by a mean(e) residual when `e_i` is enabled.

```text
min_{A,c,e} rho([
  (||A_i-A_j|| + c+e_i + c+e_j - r_ij) / sigma_range,
  e_i / e_reg,
  mean(e),
  physical_layout_priors(A)
])
```

The existing official V5 layout artifact records `c = 111.985 mm`, `e_reg = 20 mm`, `max |e_i| = 15.353 mm`, and pair RMSE `38.291 mm`.

Current code defaults to Huber with `f_scale_mm=30`, `residual_sigma_mm=15`, `c in [-150,+150]`, and `e_i in [-100,+100]` when enabled. The current source now defaults to `use_per_anchor_ei=False` unless `e_init` or positive `e_reg_scale_mm` is explicitly supplied. Existing `v5-commonmode/layout.json` was generated with `e_reg=20`, so it documents the historical selected V5 artifact, not necessarily the behavior of a fresh default call after the recent solver modification.

Difference from V4: V5 moves bulk antenna/cable/range delay into a single common-mode variable instead of squeezing it through bounded per-anchor independent delays. Prior analyses report V4 Sim3 scale around `0.958` and V5 around `1.010`.

## Code Quality / Audit Findings

1. `V1` naming is overloaded: `v1-old` in `run_clean_full_compare.py` is classical MDS only, while `AutoPos V1` in `run_full_evaluation_same_pipeline_20260513.py` is MDS+NLS.
2. V5 is not a clean standalone production version in one runner. It is represented by `solve_v4_common_mode` plus generated layout artifacts and analysis scripts.
3. Existing V5 artifacts were generated with `e_reg=20`, but current source defaults to `e_i=0` unless explicitly enabled. New experiments should record `use_per_anchor_ei` and `e_reg_scale_mm` in every layout.
4. Anchor aggregation conventions are spread across historical fusion code and `prepare_v4_data.py`. The latest `lower_trim_*` options are configurable, but historical V1-V4 runners use their embedded fusion logic.
5. V4 delay bounds can saturate at `+60 mm`; the field `delay_sanity.csv` shows V4 max delay at the bound.
6. V3-full alternating delay updates are weakly constrained and produced large residual tails in field artifacts.

## Recommendations From Erlangen Campaign

- Keep p50/V3-style inter-anchor aggregation for anchor self-calibration; the lower-trim anchor blind experiment was worse (`46.375 mm` vs `44.485 mm` control).
- Use common-mode V5-style delay parameterization for physical scale correctness.
- For the latest best static results, evaluate `e_i=0` or low `e_reg` explicitly rather than assuming the old `e_reg=20` artifact remains optimal.
- Record the exact anchor aggregation, tag aggregation, robust loss, `f_scale_mm`, `e_reg`, and `D_tag` source in every generated layout/report.
