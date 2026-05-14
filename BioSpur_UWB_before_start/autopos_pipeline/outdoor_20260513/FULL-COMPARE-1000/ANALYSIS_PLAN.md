# FULL-COMPARE-1000 Analysis Plan

## Purpose

This directory is the clean full-data comparison run.

Layout generation uses all 1000 inter-anchor sweep sets. Every solver version is then evaluated with the same downstream validation data: static tag captures, roto captures, and wand captures.

## Layout Data Policy

- Solve data: all 1000 sweep sets
- Anchor-layout evaluation data: all 1000 sweep sets
- Static / roto / wand validation: all collected 2026-05-13 captures
- Source data must not be modified

## Solver Versions

| Folder | Paper name | Layout input | Delay-aware | Extra layout constraint | Meaning |
|---|---|---|---:|---|---|
| `v1-old` | `V1` | simple bidirectional mean | No | None | earliest fragile baseline |
| `v2` | `V2` | weighted / IVW pair fusion | No | None | better pair fusion, still no delay |
| `v3-lite` | `V3-lite` | MAD/MVUE robust pair fusion | No | None | robust fusion and asymmetry handling |
| `v3-full` | `V3-full` | MAD/MVUE robust pair fusion | Yes | None | first antenna-delay-aware solver |
| `v4-io` | `V4-io` | MAD/MVUE robust pair fusion | Yes | None | current production inter-anchor solver |
| `v4-io-roto` | `V4-io-roto` | V4-io + RotoArm constraints | Yes | RotoArm | tests whether RotoArm Z information improves layout |
| `v4-io-wand` | `V4-io-wand` | V4-io + static calibration-wand constraints | Yes | W01-W04 rigid body | tests whether Wand rigid-body constraints improve layout |
| `v5` | `V5` | V4 diagnostics layer | Uses V4 | No new layout by default | FIM / uncertainty / usable-area diagnosis |

## V4 Definitions

`V4-io` is the current `V4-interonly` implementation used in `FULL-COMPARE/v4`:

- MAD/MVUE fused inter-anchor distances
- V3 no-delay layout initialization
- joint anchor position + per-anchor delay solve
- Huber loss
- bounded delay, currently `[-60, +60] mm`
- delay regularization

`V4-io-roto` must be built on top of `V4-io`, not as a separate unrelated solver. It adds RotoArm geometric constraints to inject vertical/Z information. RotoArm is not used for V1, V2, V3-lite, or V3-full layout generation.

`V4-io-wand` must also be built on top of `V4-io`. It adds calibration-wand rigid-body constraints from W01-W04 only, using the measured Wand tag distances as soft constraints. W05 is dynamic under TDMA and should not be used as a synchronized rigid-body constraint; it can still be used for coverage and residual diagnostics.

## Evaluation Dataset Requirements

Every solver/layout version must be evaluated on the same downstream captures. Do not evaluate only a subset unless the source folder is missing or the capture has insufficient valid frames; in that case record `status=missing` or `status=insufficient`.

## AutoPos Layout Quality Metrics

These metrics judge the AutoPos anchor layout itself and must be reported before Tag-position repeatability. They do not use static/roto Tag RMS as the primary score.

### Inter-Anchor Self-Consistency

For each solver version, evaluate the solved layout against the fused inter-anchor measurements:

- global inter-anchor RMS
- p50 / p75 / p95 / max absolute residual
- per-pair residual table: measured distance, predicted distance, signed residual, absolute residual
- per-anchor residual table: RMS / p95 of all pairs touching each anchor
- worst pairs and worst anchors

This is necessary but not sufficient: a flexible solver can fit noisy data well while still producing a biased geometry.

### Holdout / Generalization

When a split is available, evaluate the layout on measurements not used for solving:

- solve on first 500, evaluate on last 500
- solve on last 500, evaluate on first 500
- report train vs holdout RMS, p50, p95, max
- report whether a version overfits the solve half

For `FULL-COMPARE-1000`, use all-1000 self-consistency as the main metric and use `FULL-COMPARE-500+500` for holdout/generalization.

### Split Layout Stability

For first-500 vs last-500 layouts:

- align the two layouts by rigid Procrustes transform
- report per-anchor position difference after alignment
- report delay/bias difference per anchor
- report global split RMS difference
- report layer/Z disagreement separately from XY disagreement

This is one of the best no-OptiTrack indicators: a good AutoPos method should produce nearly the same layout from two independent halves of the same sweep session.

### Delay / Bias Sanity

For delay-aware versions:

- delay min / max / L2 norm
- number of anchors at or near delay bounds
- correlation between large delay and large residual
- compare V3-full delay pattern vs V4-io delay pattern

If delay variables become large only to absorb geometry error, the layout should be flagged even if inter-anchor RMS improves.

### Robustness To Bad Anchors / Bad Pairs

Report how each version handles bad or asymmetric pairs:

- pair asymmetry before fusion
- pair MAD before fusion
- residuals involving D/H or other historically weak anchors
- sensitivity of V1/V2/V3/V4 to removing or downweighting worst pairs

This metric supports the robustness story: bad raw sweep data should hurt early baselines more than robust versions.

### Geometry / Observability Diagnostics

For V5 or diagnostic output:

- FIM / covariance per anchor: `sigma_x`, `sigma_y`, `sigma_z`, `sigma_3d`, `sigma_delay`
- condition number
- usable-area heatmap based on anchor geometry and residual/noise model
- upper-layer vs lower-layer contribution
- weak Z direction diagnosis

These diagnostics explain where the layout is reliable, not just what the average fit error is.

### Static Tag Evaluation

Use all collected BSF66F static captures under `autopos_pipeline/outdoor_20260513/Static_Test/`.

Expected IDs:

| Group | IDs | Meaning |
|---|---|---|
| edge low | `ID01`, `ID04`, `ID07`, `ID10` | near four side faces, low height |
| edge mid | `ID02`, `ID05`, `ID08`, `ID11` | near four side faces, mid height |
| edge high | `ID03`, `ID06`, `ID09`, `ID12` | near four side faces, high height |
| center mid | `ID13`, `ID14`, `ID15`, `ID16` | center, tag faces ABEF / BCGF / CDHG / ADHE |
| center low | `ID17`, `ID18`, `ID19`, `ID20` | center low, four tag orientations |
| center high | `ID21`, `ID22`, `ID23`, `ID24` | center high, four tag orientations |

For each version and each static ID, output:

- `N_frames`
- mean position `mean_x`, `mean_y`, `mean_z`
- repeatability `X_std`, `Y_std`, `Z_std`, `D3_std`
- radial distance-to-mean distribution: `p50`, `p75`, `p95`, `max`
- valid sweep coverage: median anchors per frame, percent frames with `>=7` and `>=8` anchors
- per-anchor residual summary if available

Also output grouped summaries for edge/center, low/mid/high, and facing direction.

### Roto Tag Evaluation

Use all collected roto captures under `autopos_pipeline/outdoor_20260513/Roto_Test/`.

Expected IDs include `ID25` through `ID41` and any extra collected roto IDs such as `ID38`-`ID41`. Do not hard-code only the original plan; discover all existing `ID*` capture folders.

For each version, each roto ID, and each roto peer (`BS2DCE`, `BSDC91`), output:

- `N_frames`
- fitted circle radius
- radial std
- plane/off-axis std
- 3D circle std and RMS
- plane tilt angle
- fitted center and normal if available
- radius difference between inner/outer tags when both peers exist

Also output grouped summaries by tilt level and facing direction when the ID mapping is known.

## Required Outputs

Each version should produce:

- `layout.json`
- `tables/layout_residuals_per_pair.csv`
- `tables/layout_residuals_per_anchor.csv`
- `tables/autopos_quality_summary.csv`
- `tables/holdout_generalization.csv` if a split exists
- `tables/split_layout_stability.csv` if a split exists
- `tables/delay_sanity.csv` for delay-aware versions
- `tables/static_all_captures.csv`
- `tables/static_group_summary.csv`
- `tables/static_orientation_effect.csv`
- `tables/roto_all_captures.csv`
- `tables/roto_group_summary.csv`
- `tables/roto_radius_consistency.csv`
- `tables/wand_static_summary.csv` for W01-W04 if usable
- `reports/repeatability_xyz_report.md`
- `reports/autopos_layout_report.md`
- `figures/` with progression, distribution, and diagnostic figures

## Main Questions

1. Does the full 1000-set AutoPos layout self-consistency improve from V1 to V4-io?
2. Does V4-io-roto improve Z stability or roto residuals compared with V4-io?
3. Does V4-io-wand help at all, or does the calibration Wand data prove too weak/noisy to improve layout?
4. Are static tag repeatability and roto dynamic residuals consistent with the AutoPos layout metrics?
5. Is V5 useful as a reliability/usable-area diagnostic even when it does not improve the layout itself?
