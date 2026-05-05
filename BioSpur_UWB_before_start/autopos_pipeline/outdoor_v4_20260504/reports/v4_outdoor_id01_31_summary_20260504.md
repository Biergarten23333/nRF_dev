# Outdoor AutoPos V4 Summary - ID01-ID31

Date: 2026-05-04  
Workspace: `autopos_pipeline/outdoor_v4_20260504`

## Executive Summary

An outdoor AutoPos/V4 calibration data set was collected using one 500-set
inter-anchor sweep and 31 TR-only tag-anchor captures. The data set includes
static placements at multiple heights, regions, and antenna orientations
(ID01-ID27), plus four small-tilt RotoTag orientation captures (ID28-ID31).

The final selected V4 solve is:

`solves/anchor_layout_v4_joint_id01_31_outdoor_sub100_t150_20260504.json`

It uses the complete ID01-ID31 capture set as the data source, with a
representative per-sweep subsampling for the nonlinear joint solve. The full
raw capture inventory contains 205,536 TR rows, of which 192,522 are valid
range rows. The selected joint solve used 1,932 tag-anchor range observations
over 257 tag-position nuisance frames, plus all 28 inter-anchor median range
constraints.

Final robust/inlier residual quality:

| Residual set | Inlier definition | Count | RMS |
|---|---:|---:|---:|
| Inter-anchor | abs error <= 30/50 mm | 22 / 28 pairs | 15.5 mm |
| Inter-anchor | abs error <= 100 mm | 27 / 28 pairs | 32.6 mm |
| Tag-anchor | abs error <= 50 mm | 840 rows | 28.4 mm |
| Tag-anchor | abs error <= 100 mm | 1428 rows | 51.7 mm |
| Tag-anchor | abs error <= 150 mm | 1735 rows | 69.6 mm |

All-residual RMS, including outliers:

| Residual set | RMS |
|---|---:|
| Inter-anchor all 28 pairs | 45.1 mm |
| Tag-anchor all selected rows | 102.6 mm |

Interpretation: the useful inlier tag-anchor residual is approximately
50 mm RMS. The all-row RMS is higher because a minority of tag-anchor
observations remain large outliers, especially involving anchor A/H and some
RotoTag/anchor combinations. This is expected in the current TR-only outdoor
data set and should be handled by robust loss/downweighting in downstream
solvers.

## Data Used

### Inter-anchor sweep

- Source: `sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv`
- Rows: 28,000 raw pair measurements
- Aggregation for V4: median per anchor pair
- Constraints used in V4: 28 inter-anchor pair ranges
- Result of sweep: all A-H pairs completed 500/500 with final responder
  ready=8/8.

### Tag-anchor TR captures

Valid capture directories:

- Static: ID01-ID27
- Roto: ID28-ID31
- Excluded: ID32 and later were not used. ID32 was rain-aborted and archived
  under `notes/`.

TR inventory:

| Group | Valid TR rows | Total TR rows | Valid rate |
|---|---:|---:|---:|
| All ID01-ID31 | 192,522 | 205,536 | 93.7% |
| BSF66F static | 119,624 | 128,680 | 93.0% |
| BS2DCE RotoTag | 37,860 | 38,408 | 98.6% |
| BSDC91 RotoTag | 35,038 | 38,448 | 91.1% |

The V4 input file generated from these captures is:

`v4_data/v4_data_id01_31_tr_20260504.json`

It contains:

- `inter_anchor_ranges`: 28
- `tag_anchor_ranges`: 192,522
- `tag_position_initializers`: 0

## Solver Formulation

The V4 solve estimates all relevant variables in one nonlinear least-squares
problem:

- Anchor positions: `A0..A7`
- Per-anchor delay/bias terms: `d_anchor[0..7]`
- Per-tag delay/bias terms: `d_tag`
- Per-frame tag positions as nuisance variables

Inter-anchor residual:

```text
||A_i - A_j|| + d_anchor_i + d_anchor_j - measured_ij
```

Tag-anchor residual:

```text
||T_k - A_i|| + d_anchor_i + d_tag_j - measured_tag_i
```

Gauge constraints:

- Anchor A fixed at origin
- Anchor B constrained to the x-axis
- Anchor C constrained to the lower reference plane for the remaining rotation

Robust optimization:

- SciPy `least_squares`
- Huber loss
- Inter-anchor sigma: 15 mm
- Tag-anchor sigma: 150 mm
- Huber transition: 30 mm
- Delay soft prior sigma: 20 mm
- Delay bounds: +/-60 mm

The selected run uses `tag_subsample=100`. This keeps all capture IDs and all
tags represented while avoiding a dense, unstable full-frame nonlinear problem.
The failed higher-density stress solve (`tag_subsample=10`) is recorded but not
used as the final result because it did not converge and produced unrealistic
residuals/delay values.

## Final Anchor Layout

Selected output:

`solves/anchor_layout_v4_joint_id01_31_outdoor_sub100_t150_20260504.json`

Coordinates are in millimeters in the solver gauge frame.

| Anchor | x_mm | y_mm | z_mm | d_anchor_mm |
|---|---:|---:|---:|---:|
| A | -0.0 | -0.0 | 0.0 | 0.0 |
| B | 4520.1 | 0.0 | -0.0 | 20.3 |
| C | 4547.1 | 2905.6 | 0.0 | 8.0 |
| D | 107.8 | 2989.0 | -64.5 | 10.6 |
| E | 37.6 | -55.5 | 1514.6 | -17.2 |
| F | 4528.9 | 4.8 | 1816.2 | 9.7 |
| G | 4475.5 | 2977.0 | 1483.3 | 19.6 |
| H | -16.8 | 2910.8 | 1451.4 | 10.0 |

Estimated tag delay/bias terms:

| Tag | d_tag_mm |
|---|---:|
| BS2DCE | 0.0 |
| BSDC91 | 4.7 |
| BSF66F | 15.8 |

The delay values are within plausible small-board/antenna bias range and did
not hit the configured +/-60 mm bounds in the selected solve.

## Baselines and Comparison

### Inter-anchor-only baseline

Source:

`solves/anchor_layout_interonly_linear_outdoor_500set_20260504.json`

This uses only the 28 median inter-anchor pair ranges.

| Metric | Value |
|---|---:|
| All-pair RMS | 42.5 mm |
| Median absolute error | 25.7 mm |
| Maximum absolute pair error | 106.5 mm |

The inter-anchor-only result is a strong outdoor geometric baseline. The joint
V4 result keeps 27/28 inter-anchor pairs within 100 mm and 22/28 pairs within
50 mm while adding tag-anchor consistency constraints.

### Selected V4 joint solve

| Metric | Value |
|---|---:|
| Inter-anchor all-pair RMS | 45.1 mm |
| Inter-anchor inlier RMS, abs <= 50 mm | 15.5 mm |
| Inter-anchor inlier RMS, abs <= 100 mm | 32.6 mm |
| Tag-anchor all-row RMS on selected frames | 102.6 mm |
| Tag-anchor inlier RMS, abs <= 100 mm | 51.7 mm |
| Tag-anchor inlier RMS, abs <= 150 mm | 69.6 mm |

### Failed high-density stress solve

The `tag_subsample=10` run is:

`solves/anchor_layout_v4_joint_id01_31_outdoor_sub10_t150_20260504.json`

It is not selected as final because:

- It hit `max_nfev=300` without convergence.
- Inter-anchor RMS degraded to 107.3 mm.
- Tag-anchor all-row RMS degraded to 310.0 mm.
- BSF66F `d_tag` hit the +60 mm bound.

This does not invalidate the data set. It shows that the current dense
joint formulation needs better initialization, staged frame admission, or an
analytic/sparser Jacobian before using a much higher frame density.

## Observations

1. The outdoor inter-anchor data is much cleaner than the earlier indoor
   obstructed data. The selected V4 result keeps most inter-anchor residuals
   in the 15-50 mm range.

2. Tag-anchor residuals show a strong inlier core near 50 mm RMS, which is in
   line with the original concept target.

3. A minority of large tag-anchor outliers remain. In the selected solve, the
   largest outliers are concentrated around anchor A/H and some RotoTag
   observations. These should be handled by robust downweighting, residual
   heatmap inspection, or per-condition filtering rather than by trusting
   unfiltered all-row RMS.

4. The RotoTag captures ID28-ID31 completed the four small-tilt antenna-facing
   directions. This gives useful directionality coverage, but mid/high tilt
   captures were not collected due to rain.

5. The final selected layout is suitable as a first outdoor V4 calibrated
   layout candidate. Before pushing it as a production baseline, it should be
   validated by APOS push and a fresh TR-only motion capture, then compared
   against offline-solved positions.

## Files Produced

- V4 data:
  - `v4_data/v4_data_id01_31_tr_20260504.json`
- Inter-anchor-only solves:
  - `solves/inter_anchor_free_outdoor_500set_20260504.json`
  - `solves/inter_anchor_free_outdoor_500set_f50_20260504.json`
  - `solves/anchor_layout_interonly_linear_outdoor_500set_20260504.json`
- Selected V4 joint solve:
  - `solves/anchor_layout_v4_joint_id01_31_outdoor_sub100_t150_20260504.json`
- Non-selected stress solve:
  - `solves/anchor_layout_v4_joint_id01_31_outdoor_sub10_t150_20260504.json`

## Recommended Next Step

Use the selected layout as the candidate outdoor APOS layout:

1. Push `anchor_layout_v4_joint_id01_31_outdoor_sub100_t150_20260504.json`
   to the Tags via APOS.
2. Run a fresh outdoor TR-only validation capture.
3. Solve positions offline from TR and report:
   - per-tag RMS/trajectory consistency
   - 4-anchor vs 8-anchor delta
   - per-anchor residual heatmap
   - outlier fraction per anchor/tag/orientation

This keeps the firmware path simple: Tags stream TR, and the host/offline
solver owns the final position solution.
