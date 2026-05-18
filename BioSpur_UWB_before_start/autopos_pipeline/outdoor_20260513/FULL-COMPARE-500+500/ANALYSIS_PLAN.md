# FULL-COMPARE-500+500 Analysis Plan

## Purpose

This directory tests split-consensus layout generation.

The first 500 sweep sets and last 500 sweep sets are solved separately, then aligned and fused into one final consensus layout. That final consensus layout is used for all downstream validation, just like the full-data analysis.

## Layout Data Policy

- Solve data A: first 500 sweep sets per directed pair
- Solve data B: last 500 sweep sets per directed pair
- Final layout: align B to A, then generate consensus layout from A and B
- Anchor-layout evaluation data: first 500, last 500, and all 1000 sweep sets
- Static / roto / wand validation: all collected 2026-05-13 captures, using the final consensus layout
- Source data must not be modified

## Solver Versions

| Folder | Paper name | Layout input | Delay-aware | Extra layout constraint | Meaning |
|---|---|---|---:|---|---|
| `v1-old` | `V1` | simple bidirectional mean | No | None | earliest fragile baseline |
| `v2` | `V2` | weighted / IVW pair fusion | No | None | better pair fusion, still no delay |
| `v3-lite` | `V3-lite` | MAD/MVUE robust pair fusion | No | None | robust fusion and asymmetry handling |
| `v3-full` | `V3-full` | MAD/MVUE robust pair fusion | Yes | None | first antenna-delay-aware solver |
| `v4-io` | `V4-io` | MAD/MVUE robust pair fusion | Yes | None | current production inter-anchor solver |
| `v4-io-td` | `V4-io-td` | V4-io fixed layout + static common Tag-delay scan | Yes | static type-level Tag delay | tests whether one deploy-realistic common Tag delay improves downstream validation |
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

`V4-io-td` must keep the V4-io anchor layout and per-anchor delay fixed, then scan one common type-level Tag delay using static captures only. This is a downstream compensation experiment, not a factory calibration and not a new AutoPos anchor-layout constraint. The scan should report whether the static objective has a clear minimum; if the curve is flat, the estimated Tag delay must be treated as weakly observable.

## Evaluation Dataset Requirements

Every solver/layout version must be evaluated on the same downstream captures. Do not evaluate only a subset unless the source folder is missing or the capture has insufficient valid frames; in that case record `status=missing` or `status=insufficient`.

## Validation / Information Isolation Policy

All downstream validation must use the full collected capture set, not randomly selected sessions and not cherry-picked subsets:

- Static validation: all available `Static_Test/ID*` captures
- Roto validation: all available `Roto_Test/ID*` captures
- Wand validation: all available `Wand_Test/W*` captures

Every solver version must be evaluated on static, roto, and wand validation. The difference between solver versions is the information used during layout generation or downstream compensation, not the validation data selection.

Information injection sources must remain isolated:

- `V1`, `V2`, `V3-lite`, `V3-full`, and `V4-io` use no roto or wand layout constraints; roto/wand are validation only.
- `V4-io-td` uses only V4-io plus a common static Tag-delay compensation scan; it must not inject roto or wand layout constraints.
- `V4-io-roto` uses RotoArm constraints on top of V4-io; it must not inject wand constraints or Tag-delay scan into the same variant.
- `V4-io-wand` uses W01-W04 calibration-wand constraints on top of V4-io; it must not inject roto constraints or Tag-delay scan into the same variant.
- `V5` is a diagnostics / uncertainty layer based on V4-io unless explicitly stated otherwise.

When a solver uses a validation source as a layout constraint, report that clearly. For example, `V4-io-roto` is not a fully independent roto holdout because a small amount of roto information is injected into the layout; however, the full roto capture set is still evaluated for consistency.

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

For `FULL-COMPARE-500+500`, report both cross directions and also evaluate the final consensus layout on first500, last500, and all1000.

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

Important interpretation rule:

- Do **not** report raw dynamic circle-thickness diagnostics as dynamic positioning accuracy.
- The Roto target is continuously moving, so circle residual mixes localization noise, mechanical wobble, time/TDMA effects, Z observability, and outlier frames.
- Report Roto primarily as **kinematic consistency**, not absolute dynamic accuracy.

For each version, each roto ID, and each roto peer (`BS2DCE`, `BSDC91`), output diagnostic circle-fit quantities:

- `N_frames`
- fitted circle radius
- radial std
- plane/off-axis std
- plane tilt angle
- fitted center and normal if available

Optional circle-thickness diagnostics may be emitted only under explicit diagnostic names such as `circle_thickness_*_diagnostic`. They must not be included in main progression tables, main accuracy summaries, or headline figures.

For each roto ID where both peers exist, output the primary physical consistency metrics:

- `deltaR_mm = R_outer - R_inner`
- `deltaR_error_mm = (R_outer - R_inner) - 120mm`
- `deltaR_error_rms_mm`, the signed RMS of `deltaR_error_mm` over all roto captures
- `abs_deltaR_error_mm`
- `inner_outer_center_sep_mm`

Also compute per-revolution center repeatability:

- unwrap fitted circle angle over time
- split the trajectory into approximately one-turn segments (`2π`)
- fit one circle center per turn
- report `turn_center_rms_3d_mm`, `turn_center_p95_3d_mm`, and per-axis center std

These metrics should be summarized by version, tilt level, and facing direction when the ID mapping is known. In the main report, prefer `abs_deltaR_error` and `turn_center_rms` over raw circle thickness.

## Split-Consensus Outputs

Each version should produce:

- `layout_first500.json`
- `layout_last500_aligned.json`
- `layout_consensus.json`
- `tables/autopos_quality_summary.csv`
- `tables/holdout_generalization.csv`
- `tables/delay_sanity.csv` for delay-aware versions
- `v4-io-td/tag_delay_scan_first500.csv` and `v4-io-td/tag_delay_scan_last500.csv` for the static common Tag-delay scans
- `tables/split_layout_disagreement.csv`
- `tables/layout_residuals_first500.csv`
- `tables/layout_residuals_last500.csv`
- `tables/layout_residuals_all1000.csv`

The downstream validation should use `layout_consensus.json`.

## Required Downstream Outputs

Each version should also produce:

- `tables/static_all_captures.csv`
- `tables/static_group_summary.csv`
- `tables/static_orientation_effect.csv`
- `tables/roto_all_captures.csv`
- `tables/roto_radius_consistency.csv`
- `tables/roto_physical_consistency_all.csv`
- `tables/roto_physical_consistency_summary.csv`
- `figures/roto_deltaR_distribution.png`
- `figures/roto_turn_center_rms_distribution.png`
- `tables/wand_static_summary.csv` for W01-W04 if usable
- `reports/repeatability_xyz_report.md`
- `reports/autopos_layout_report.md`
- `figures/` with progression, distribution, split-stability, and diagnostic figures

## Main Questions

1. Are first-500 and last-500 layouts geometrically consistent after alignment?
2. Which solver has the smallest split disagreement?
3. Does split-consensus improve robustness compared with first-500 alone?
4. Does split-consensus approach the full 1000-set result?
5. Does V4-io-td estimate similar common Tag delay on first500 and last500, and does the consensus delay improve downstream validation?
6. Does V4-io-roto reduce split disagreement in Z or improve roto validation?
7. Does V4-io-wand reduce split disagreement or improve static Wand consistency?
