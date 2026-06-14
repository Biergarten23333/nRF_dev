# AutoPos Vicon Report Audit Findings

Date: 2026-06-11

Scope: Phase 1 revised audit plus Phase 1c/1d follow-up. No paper text was edited.
The Phase 1 script is
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/scripts/audit_phase1_revised.py`.
The Phase 1c script is
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/scripts/audit_phase1c_common_mode.py`.
The Phase 1d script is
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/scripts/audit_phase1d_tag_delay_cancellation.py`.
Outputs are under
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/audit_phase1/`
and
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/audit_phase1c/`
and
`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL/audit_phase1d/`.

Machine-readable summary:
`official_extra_analysis/FULL/audit_phase1/tables/audit_phase1_revised_summary.json`.
Phase 1c machine-readable summary:
`official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json`.
Phase 1d machine-readable summary:
`official_extra_analysis/FULL/audit_phase1d/tables/audit_phase1d_summary.json`.

The earlier Phase-1 entry that compared a constant model R2 against a
through-origin proportional slope is superseded. A constant model with an
intercept has centered R2=0 by definition, and the through-origin slope is not
the correct test for proportionality.

## Item 1 - Corrected constant-vs-proportional regression

Source table:
`official_extra_analysis/FULL/audit_phase1/tables/item1_pairwise_delta.csv`.

Corrected OLS over 28 anchor pairs:

`Delta_ij = b0 + b1 * d_ij`, where `Delta_ij` is AutoPos minus Vicon
inter-anchor distance in mm and `d_ij` is the Vicon distance in m.

Results:

| quantity | value |
| --- | ---: |
| n pairs | 28 |
| mean Delta | 120.53 mm |
| median Delta | 116.77 mm |
| b0 | 63.46 mm |
| b1 | 20.92 mm/m |
| 95% CI for b1 | -13.26 to 55.09 mm/m |
| OLS RMSE | 61.80 mm |
| centered R2 for OLS slope | 0.057 |

Interpretation corrected after Phase 1c review: the regression is not
discriminating. The slope CI includes zero, but it also includes the pure-scale
prediction from the current Sim(3) scale, `(1/0.958267 - 1) * 1000 = 43.55
mm/m`. Therefore the OLS fit alone neither supports nor rejects a proportional
scale explanation. The OLS CI is also optimistic because the 28 pairs share
per-anchor error terms and are not independent. The discriminating evidence is
the additive endpoint decomposition in Item 2, not this regression.

Outputs:

- `tables/item1_ols_with_intercept.csv`
- `tables/item1_ols_with_intercept.json`
- `figs/item1_revised_ols_delta_vs_distance.png`

## Item 2 - Per-anchor additive decomposition

Source tables:

- `tables/item2_model_comparison.csv`
- `tables/item2_per_anchor_additive_deltas.csv`
- `tables/item2_top8_fig4_pairwise_scale_errors.csv`

Model comparison:

| model | RMSE | centered R2 | signal SS explained vs zero |
| --- | ---: | ---: | ---: |
| constant offset | 63.66 mm | 0.000 | 0.782 |
| distance OLS with intercept | 61.80 mm | 0.057 | 0.794 |
| per-anchor additive | 42.78 mm | 0.548 | 0.902 |

The per-anchor additive model explains substantially more residual structure
than distance alone. It is therefore the useful diagnostic model, while still
not a calibrated physical delay measurement.

Unconstrained endpoint deltas from `Delta_ij = delta_i + delta_j`:

| anchor | delta_i | current v4-io d_i |
| --- | ---: | ---: |
| A | 145.61 mm | 0.00 mm |
| B | 52.39 mm | 37.13 mm |
| C | 43.99 mm | 60.00 mm |
| D | 72.08 mm | 60.00 mm |
| E | 26.44 mm | 31.14 mm |
| F | 25.73 mm | 27.04 mm |
| G | 69.63 mm | 27.56 mm |
| H | 46.25 mm | 32.42 mm |

Gauge-fixed reporting:

- Zero-mean gauge: pair intercept 120.53 mm; differential deltas are A +85.34,
  B -7.87, C -16.27, D +11.82, E -33.82, F -34.54, G +9.37, H -14.02 mm.
- A-gauge: pair intercept 291.21 mm; B -93.21, C -101.61, D -73.53, E -119.17,
  F -119.88, G -75.97, H -99.36 mm relative to A.

Hypothesis check: the narrow hypothesis that both `delta_C` and `delta_D`
exceed the +60 mm v4-io delay bound is not supported. D exceeds 60 mm
(`72.08 mm`), but C does not (`43.99 mm`). G also exceeds 60 mm
(`69.63 mm`) in the unconstrained endpoint model. C and D still matter:
4 of the top 8 Fig. 4 pairwise scale-error pairs involve C or D:

| rank | pair | scale error | Delta |
| ---: | --- | ---: | ---: |
| 1 | D-H | 10.26% | 143.14 mm |
| 3 | A-D | 8.07% | 174.86 mm |
| 7 | C-G | 6.87% | 96.60 mm |
| 8 | A-C | 6.75% | 230.54 mm |

Item verdict: per-anchor/additive structure is real, but it is not uniquely a
C/D story. Treat the delta values as diagnostic endpoint terms with uncertainty,
not as exact antenna-delay estimates.

## Item 3 - Relaxed delay-bound rerun

Source table:
`tables/item3_relaxed_delay_bound_summary.csv`.

Current baseline:

| metric | current v4-io |
| --- | ---: |
| Sim(3) scale, AutoPos to Vicon | 0.958267 |
| rigid anchor median / RMSE | 92.77 / 105.42 mm |
| static tag T4 mean median / P95 / RMSE | 72.69 / 171.49 / 109.84 mm |
| saturated anchors at +60 mm | C, D |

Relaxed-bound results:

| case | Sim(3) scale | rigid median / RMSE | static T4 mean median / P95 / RMSE | saturated anchors |
| --- | ---: | ---: | ---: | --- |
| `abs(d_i) <= 150 mm` | 0.966518 | 81.85 / 103.91 mm | 95.04 / 196.28 / 124.26 mm | D |
| `abs(d_i) <= 200 mm` | 0.966236 | 104.94 / 110.83 mm | 101.63 / 189.84 / 126.67 mm | D |

Fitted delays:

| case | A | B | C | D | E | F | G | H |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 150 mm | 0.0 | 28.2 | 139.2 | 150.0 | 25.5 | 25.5 | 17.5 | 19.4 |
| 200 mm | 0.0 | 29.3 | 91.0 | 200.0 | 27.6 | 19.4 | 19.8 | 19.0 |

Phase 1c supersession note: this relaxed-bound rerun kept the original
`d_A=0` delay gauge and the 20 mm delay regularizer, so it does not test the
common-mode delay hypothesis. Its static T4 numbers are not a `tag_delay_mm`
artifact, but its mechanism verdict is superseded by Phase 1c below. Treat
these layouts as diagnostic only, and do not replace the current production
layout from this run.

## Item 4 - Vicon anchor-registration sensitivity

Source tables:

- `tables/item4_registration_sensitivity_summary.csv`
- `tables/item4_registration_sensitivity_trials.csv`

Implementation note: the official Vicon table uses `truth_y_vertical_mm` as the
vertical axis. The "vertical common-mode" perturbation was therefore applied to
that axis. All Item 4 calculations keep the current v4-io layout and current T4
static mean positions frozen.

Baseline:

| metric | value |
| --- | ---: |
| rigid anchor median / RMSE | 92.77 / 105.42 mm |
| Sim(3) scale | 0.958267 |
| mean pair Delta | 120.53 mm |
| OLS b0 | 63.46 mm |
| delta_C / delta_D | 43.99 / 72.08 mm |
| static T4 mean median | 72.69 mm |

Monte Carlo and deterministic perturbation results:

| perturbation | key result |
| --- | --- |
| M1 isotropic Gaussian, sigma=5 mm per axis per anchor, N=1000 | mean Delta P50 120.45 mm, P5-P95 116.36-124.92 mm; Sim(3) scale P50 0.958302, P5-P95 0.956747-0.959705; static median P50 72.73 mm, P5-P95 70.58-75.12 mm. |
| M2 radial +5 mm outward deterministic | mean Delta 114.02 mm, shift -6.51 mm; Sim(3) scale 0.960633; delta_C/D 40.75/68.82 mm. |
| M2 radial -5 mm inward deterministic | mean Delta 127.04 mm, shift +6.51 mm; Sim(3) scale 0.955902; delta_C/D 47.23/75.34 mm. |
| M2 radial common U(0,5) mm outward, N=1000 | mean Delta P50 117.40 mm, P5-P95 114.48-120.16 mm; delta_C/D P50 42.44/70.52 mm. |
| M3 vertical +5 mm deterministic | pair metrics unchanged; static median 70.30 mm. |
| M3 vertical -5 mm deterministic | pair metrics unchanged; static median 75.30 mm. |

Uncertainty band on endpoint deltas under 5 mm registration perturbations:

- Under isotropic M1, `delta_C` P5-P95 is 37.71-50.51 mm and `delta_D`
  P5-P95 is 65.54-78.58 mm.
- Under deterministic radial +/-5 mm, `delta_C` spans 40.75-47.23 mm and
  `delta_D` spans 68.82-75.34 mm.

Item verdict: 5 mm marker-to-phase-center registration error cannot create the
120.53 mm all-pair mean excess. The only structured perturbation that moves the
constant pair-distance signal is radial common-mode error, and a +/-5 mm radial
case shifts the mean Delta by only +/-6.51 mm, about 5.4% of the observed
signal. To fake the all-pair mean would require a common radial registration
error on the order of many centimetres, not 5 mm. Vertical common-mode does not
change pair distances and only shifts the static tag median by about +/-2.6 mm.

## Implications for main_EN.tex

These are recommendations only. No edits were applied.

Section 3.3:

- Replace any through-origin proportional-slope or constant-model R2 language.
- State that the v4-io layout has a mean pairwise AutoPos-minus-Vicon distance
  excess of 120.53 mm, while the corrected OLS slope is 20.92 mm/m with a 95%
  CI of -13.26 to 55.09 mm/m. Note that the CI is optimistic because pair errors
  are correlated through shared anchors.

Section 13.2:

- Present the per-anchor additive decomposition as a diagnostic. It reduces RMSE
  from 63.66 mm for a constant offset to 42.78 mm, but its endpoint deltas are
  not exact physical delays.
- If reporting delta values, include the registration sensitivity band. D remains
  above 60 mm under the 5 mm perturbation checks; C does not.

Section 13.3, item 2:

- Supersede the relaxed-bound mechanism verdict with the Phase 1c common-mode
  delay result below. The relaxed-bound rerun is still useful as a warning that
  increasing C/D bounds alone does not produce a usable production layout, but
  it should not be cited as evidence that the apparent scale term is structural.

Conclusion:

- Retain the current static headline unless a later review chooses a different
  layout. Add a defensive registration-bounding sentence: 5 mm Vicon
  marker-to-phase-center error changes the constant pairwise excess by only a
  few millimetres and cannot explain the 120.53 mm signal.
- Avoid claiming a purely structural scale ambiguity. The supported wording is
  residual per-link range bias and layout-delay coupling that appears as an
  apparent Sim(3) scale component.

## Phase 1c - Common-mode delay hypothesis test

Script:
`official_extra_analysis/FULL/scripts/audit_phase1c_common_mode.py`.

Outputs:
`official_extra_analysis/FULL/audit_phase1c/`.

Machine-readable summary:
`official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json`.

### Phase 1c item 0 - tag_delay parity

Production v4-io `layout.json` has `tag_delay_mm = 0.0`. The Phase-1 relaxed
layouts were also saved with `tag_delay_mm = 0.0`, so the Item 3 static T4
comparison is not apples-to-oranges through tag delay. The previous relaxed
static numbers are not superseded by a tag-delay bug; their mechanism
interpretation is superseded because the experiment kept `d_A=0` and the delay
regularizer.

Output: `tables/item0_tag_delay_parity.csv`.

### Phase 1c item 1 - oracle per-anchor delay regression

Regression:

`dhat_ij - d_Vicon,ij = d_i + d_j`

using the same fused symmetric inter-anchor distances that feed the layout
solver.

Results:

| quantity | value |
| --- | ---: |
| n pairs | 28 |
| residual RMSE | 43.99 mm |
| residual P95 absolute | 81.40 mm |
| mean fitted `d_i` | 94.62 mm |
| median fitted `d_i` | 91.24 mm |
| sum fitted `d_i` | 756.98 mm |
| mean pair excess, `sum(d_i)/4` | 189.24 mm |
| mean fitted `d_i`, one-way DTU equivalent | 20.18 DTU |
| DTU convention | 1 DTU ~= 4.69 mm one-way range equivalent |

Per-anchor oracle range-bias fit:

| anchor | oracle `d_i` | Phase-1 endpoint `delta_i` | oracle minus endpoint | oracle DTU |
| --- | ---: | ---: | ---: | ---: |
| A | 148.22 mm | 145.61 mm | 2.61 mm | 31.60 |
| B | 96.34 mm | 52.39 mm | 43.95 mm | 20.54 |
| C | 127.42 mm | 43.99 mm | 83.42 mm | 27.17 |
| D | 114.66 mm | 72.08 mm | 42.58 mm | 24.45 |
| E | 48.94 mm | 26.44 mm | 22.50 mm | 10.44 |
| F | 50.02 mm | 25.73 mm | 24.29 mm | 10.66 |
| G | 86.13 mm | 69.63 mm | 16.50 mm | 18.36 |
| H | 85.25 mm | 46.25 mm | 39.00 mm | 18.18 |

Verdict: all eight oracle `d_i` are positive, and A is the largest at 148.22 mm,
matching the important Phase-1 observation that A is the delay-gauge anchor but
carries the largest endpoint bias. The mean is not the hypothesized ~60 mm; it
is 94.62 mm/device in the raw fused range-minus-Vicon frame. That is still a
plausible antenna-delay miscalibration class signal relative to the hardcoded
16436 DTU setting: the mean is about 20 DTU and A is about 32 DTU in the stated
one-way range convention.

Outputs:

- `tables/item1_oracle_pair_excess.csv`
- `tables/item1_oracle_per_anchor_delay.csv`
- `tables/item1_oracle_summary.csv`

### Phase 1c item 2 - common-mode plus differential v4-io solve

Re-parameterization:

`d_i = c + e_i`

with `|c| <= 150 mm`, no regularizer on `c`, `|e_i| <= 100 mm`, the original
20 mm regularizer on `e_i`, and a strong zero-mean regularization on `e_i` to
keep `c` identifiable. The pair residual scaling, Huber loss, vertical priors,
TRF solver, and v3-lite initialization were kept consistent with v4-io.

Main fit:

| quantity | current v4-io | common-mode fit |
| --- | ---: | ---: |
| Sim(3) scale, AutoPos to Vicon | 0.958267 | 1.009782 |
| rigid anchor median | 92.77 mm | 59.45 mm |
| rigid anchor RMSE | 105.42 mm | 62.99 mm |
| rigid anchor P95 | n/a | 93.32 mm |
| pair residual RMSE | n/a | 38.29 mm |
| static T4 mean median | 72.69 mm | 109.52 mm |
| static T4 mean P95 | 171.49 mm | 223.86 mm |
| static T4 mean RMSE | 109.84 mm | 140.53 mm |

Fitted common-mode and differential delays:

| anchor | `c` | `e_i` | `c + e_i` | oracle `d_i` |
| --- | ---: | ---: | ---: | ---: |
| A | 111.98 mm | -12.35 mm | 99.63 mm | 148.22 mm |
| B | 111.98 mm | 1.70 mm | 113.69 mm | 96.34 mm |
| C | 111.98 mm | 15.35 mm | 127.34 mm | 127.42 mm |
| D | 111.98 mm | 12.53 mm | 124.51 mm | 114.66 mm |
| E | 111.98 mm | -2.36 mm | 109.63 mm | 48.94 mm |
| F | 111.98 mm | -0.62 mm | 111.36 mm | 50.02 mm |
| G | 111.98 mm | -11.94 mm | 100.04 mm | 86.13 mm |
| H | 111.98 mm | -2.31 mm | 109.68 mm | 85.25 mm |

Predictions checked:

- `c ≈ +60 mm`: not confirmed; the fitted common mode is 111.98 mm, while the
  direct oracle mean is 94.62 mm.
- `e_A ≈ +85 mm`: not confirmed in the re-solved geometry. The solver mostly
  absorbs the range bias into `c` and keeps `e_i` within +/-15.35 mm.
- Sim(3) scale within ~1% of 1.0: confirmed; scale moves from 0.958267 to
  1.009782.

Verdict: the diagnostic layout strongly supports a common-mode range-bias
mechanism and collapses the apparent scale term to within about 1% of unity,
while improving the Vicon-referenced anchor metric. It should not replace the
production layout from this audit because the frozen-registration static T4
headline worsens.

Outputs:

- `tables/item2_common_mode_summary.csv`
- `tables/item2_common_mode_anchor_delays.csv`
- `tables/item2_static_t4_mean_sessions_common_mode.csv`
- `layouts/v4io_common_mode/layout.json`

### Phase 1c item 3 - identifiability and valley flatness

Clamp comparison:

| case | cost | cost delta vs free | pair RMSE | Sim(3) scale | rigid median / RMSE |
| --- | ---: | ---: | ---: | ---: | ---: |
| free `c=111.98 mm` | 81.62 | 0.00 | 38.29 mm | 1.009782 | 59.45 / 62.99 mm |
| `c=0` | 138.09 | 56.47 | 63.84 mm | 0.937144 | 130.84 / 142.68 mm |
| `c=111.98 mm` fixed | 81.62 | ~0.00 | 38.29 mm | 1.009782 | 59.45 / 62.99 mm |

Perturbed-initialization check, 5 runs:

| quantity | spread |
| --- | ---: |
| fitted `c` min-max | 111.9847 to 111.9851 mm |
| fitted `c` SD | 0.00015 mm |
| Sim(3) scale min-max | 1.00978223 to 1.00978250 |
| Sim(3) scale SD | 0.00000010 |

Verdict: under this residual form and priors, the common-mode/scale valley is
not flat. Clamping `c=0` materially increases the robust least-squares cost and
degrades both pair residuals and Vicon-referenced anchor metrics. The five
perturbed starts all converge to the same solution at numerical precision.

Outputs:

- `tables/item3_c_clamp_comparison.csv`
- `tables/item3_perturbed_init_spread.csv`
- `tables/item3_identifiability_summary.csv`

### Phase 1c item 4 - verdict synthesis

Phase-1 regression correction:

- Pure-scale slope implied by the current Sim(3) scale is 43.55 mm/m.
- The corrected OLS slope is 20.92 mm/m with 95% CI -13.26 to 55.09 mm/m.
- The pure-scale slope lies inside that CI, so the regression alone is
  underpowered and discriminates nothing.
- The additive endpoint decomposition is the discriminating evidence:
  additive vs constant `F(7,20)=3.47, p=0.0134`; additive vs OLS
  `F(6,20)=3.62, p=0.0135`.

Supported verdict: (a), with caveats. The +4.36% apparent scale term is
predominantly a common-mode per-device range-bias / antenna-delay
miscalibration-class effect coupled to the original solver gauge and
regularizer. It is recoverable by re-parameterizing the solver, so it should not
be described as a proven structural scale ambiguity. The honest caveats are
that the fitted common mode is closer to 95-112 mm/device than the hypothesized
60 mm/device, the oracle fit still has 43.99 mm residual RMSE, and the static
T4 headline does not improve under the diagnostic layout.

### Phase 1c implications for main_EN.tex

These are recommendations only. No edits were applied.

Section 3.3:

- Say the OLS pair-distance regression is non-discriminating. Its CI includes
  both zero and the pure-scale prediction of 43.55 mm/m. Do not use it as the
  basis for choosing constant versus proportional wording.
- Use the additive endpoint model as the statistical discriminator, with the
  F-tests above, and keep the pair-correlation caveat.

Section 13.2:

- Add the oracle fused-range-minus-Vicon delay regression as the clean physical
  diagnostic. Report that all eight anchors have positive range bias, A is
  largest at 148.22 mm, and the mean is 94.62 mm/device, about 20 DTU in the
  one-way range convention.
- Keep the Phase-1 endpoint `delta_i` table, but label it as geometry-conflated.
  It is useful because it exposed the A-gauge inconsistency; it is not the final
  physical delay estimate.

Section 13.3, item 2:

- Replace the structural-scale wording with a common-mode range-bias mechanism:
  the original `d_A=0` gauge and 20 mm delay regularizer force a positive
  per-device range bias into layout scale/geometry.
- State that the common-mode reparameterization moves the Sim(3) scale from
  0.958267 to 1.009782 and improves rigid anchor RMSE from 105.42 to 62.99 mm,
  but worsens frozen-registration static T4 median from 72.69 to 109.52 mm.
  Therefore this is a mechanism/layout-metric diagnostic, not a new production
  headline.

Conclusion:

- Do not claim that the final static tag headline improved; keep the production
  72.7 mm static headline unless a later reviewed production layout replaces it.
- Avoid calling the +4.36% term structural. The supported conclusion is
  residual common-mode per-device range bias, plausibly antenna-delay
  miscalibration class, interacting with the original delay gauge and
  regularizer to appear as Sim(3) scale.

## Phase 1d - Tag-delay cancellation test

Script:
`official_extra_analysis/FULL/scripts/audit_phase1d_tag_delay_cancellation.py`.

Outputs:
`official_extra_analysis/FULL/audit_phase1d/`.

Machine-readable summary:
`official_extra_analysis/FULL/audit_phase1d/tables/audit_phase1d_summary.json`.

### Phase 1d item 1 - per-frame joint tag-delay estimation

Implementation: per-frame Gauss-Newton/Huber solve of `[x, y, z, d_tag]`, with
the same residual sign convention as T4 (`range_pred = distance + d_anchor +
d_tag`), anchor sigma weighting, `d_tag` initialized at 0, and `|d_tag| <= 300
mm`. Static sessions use the same frozen anchor-locked 3D rigid registration
and mean session point convention as the 72.69 mm headline.

Static results:

| layout / solver | 3D median / P95 / RMSE | horiz / vert median | fitted `d_tag` session median, P50 [P25, P75] |
| --- | ---: | ---: | ---: |
| production v4-io, baseline fixed 0 | 72.69 / 171.50 / 109.85 mm | 37.42 / 61.87 mm | 0 |
| production v4-io, per-frame `d_tag` | 72.77 / 207.35 / 106.41 mm | 34.87 / 59.47 mm | 39.25 [18.83, 52.84] mm |
| Phase-1c common-mode, fixed 0 | 109.52 / 223.86 / 140.53 mm | 45.22 / 89.42 mm | 0 |
| Phase-1c common-mode, per-frame `d_tag` | 72.86 / 317.47 / 146.84 mm | 34.39 / 61.19 mm | 46.72 [9.52, 63.12] mm |

Verdict: the per-frame four-unknown solve partially restores the common-mode
layout median from 109.52 mm to 72.86 mm, close to the current 72.69 mm
headline, so the cancellation hypothesis is directionally supported. However,
the fitted `d_tag` values do not cluster near the oracle 94.62 mm mean, and the
common-mode P95/RMSE become much worse. This is evidence that unconstrained
per-frame `d_tag` is weakly identified/noisy in these static captures. It is a
diagnostic, not yet a production replacement.

Output:
`tables/item1_item2_static_per_session.csv`,
`tables/item1_item2_static_summary.csv`,
`tables/item1_joint_dtag_frame_diagnostics_sampled.csv`.

### Phase 1d item 2 - fixed tag-delay sweep

Fixed `tag_delay_mm` sweep using the standard C-core T4 replay:

| layout | best fixed `d_tag` in grid | 3D median / P95 / RMSE | horiz / vert median |
| --- | ---: | ---: | ---: |
| production v4-io | 60 mm | 59.03 / 110.33 / 74.12 mm | 34.31 / 39.29 mm |
| Phase-1c common-mode | 80 mm | 60.07 / 131.98 / 74.87 mm | 32.15 / 45.80 mm |
| Phase-1c common-mode, 95 mm check | 95 mm | 60.74 / 145.78 / 79.53 mm | 35.49 / 47.81 mm |

Verdict: this is the cleaner cancellation test. On the common-mode layout, the
optimum moves far away from 0 and sits in the 80-95 mm neighborhood, consistent
with a positive tag-device range bias and with the Phase-1c oracle scale. The
prediction that the production layout optimum would sit near 0 is not supported:
production also improves substantially at `d_tag=60 mm` relative to fixed 0.
Therefore production's 72.7 mm headline is partly helped by layout/range-bias
cancellation, but not fully; there is still a residual tag-delay correction that
the production static replay can exploit.

Plot:
`figs/item2_fixed_tag_delay_sweep.png`.

### Phase 1d item 3 - consolidated delay table and closure arithmetic

Per-anchor table:
`tables/item3_consolidated_delay_table.csv`.

| anchor | oracle `d_i` | Phase-1 `delta_i` | Phase-1c `c+e_i` | production v4-io delay |
| --- | ---: | ---: | ---: | ---: |
| A | 148.22 | 145.61 | 99.63 | 0.00 |
| B | 96.34 | 52.39 | 113.69 | 37.13 |
| C | 127.42 | 43.99 | 127.34 | 60.00 |
| D | 114.66 | 72.08 | 124.51 | 60.00 |
| E | 48.94 | 26.44 | 109.63 | 31.14 |
| F | 50.02 | 25.73 | 111.36 | 27.04 |
| G | 86.13 | 69.63 | 100.04 | 27.56 |
| H | 85.25 | 46.25 | 109.68 | 32.42 |

Closure:

- Raw oracle per-link bias: `2 * 94.62 = 189.24 mm`.
- Production delay sum: `275.29 mm`.
- Complete-graph per-link delay absorption: `275.29 / 4 = 68.82 mm`.
- Residual after production delay absorption: `189.24 - 68.82 = 120.42 mm`.
- Measured Phase-1 mean pairwise geometric excess: `120.53 mm`.
- Closure difference: `-0.11 mm`.

Verdict: the error-budget closure is exact at the reported precision. The raw
fused ranges contain about 189 mm/link of positive bias; production v4-io's
delay terms absorb about 69 mm/link; the remaining about 120 mm/link appears as
geometric pair-distance excess and Sim(3) scale. Also, `oracle d_A=148.22 mm`
and `Phase-1 delta_A=145.61 mm` agree within 2.61 mm, cross-validating the
oracle regression and the endpoint decomposition.

Output:
`tables/item3_closure_arithmetic.csv`.

### Phase 1d item 4 - RotoArm spot check

Dynamic RotoArm absolute track-level results with per-frame `d_tag`:

| layout / solver | track P50 / P95 3D | track RMSE median | horiz / vert P50 | fitted `d_tag` track median P50 |
| --- | ---: | ---: | ---: | ---: |
| production v4-io/T4 baseline | 105.84 / 231.80 mm | 132.83 mm | 65.51 / 63.85 mm | n/a |
| production v4-io, per-frame `d_tag` | 102.92 / 202.88 mm | 122.95 mm | 64.05 / 63.79 mm | 56.62 mm |
| Phase-1c common-mode, per-frame `d_tag` | 106.66 / 209.98 mm | 125.58 mm | 64.79 / 69.72 mm | 74.02 mm |

Verdict: the dynamic median floor remains about 100 mm. Per-frame `d_tag`
reduces the production P95 tail by about 29 mm and the common-mode layout also
lands near the same class, but neither result changes the conclusion that ROTO
absolute dynamic error is not primarily a layout/tag-delay calibration problem.

Outputs:
`tables/item4_roto_joint_dtag_summary.csv`,
`tables/item4_roto_joint_dtag_per_track.csv`,
`tables/item4_roto_joint_dtag_offsets_and_delay.csv`.

### Phase 1d synthesis

The closure arithmetic is now the strongest quantitative mechanism evidence:
raw range bias, production delay absorption, and remaining geometric excess
match to 0.11 mm. The fixed tag-delay sweep also confirms the cancellation
story qualitatively: the metric common-mode layout needs a positive tag delay
to restore static accuracy. The exact cancellation claim needs softer wording,
because production is not optimized at `d_tag=0`; the grid optimum is 60 mm.
The safer statement is that production accuracy benefits from partial
layout/range-bias cancellation while still leaving a real tag-delay correction
available.

### Phase 2 readiness

Fully supported for rewrite:

- Section 3.3: corrected OLS limitation, additive decomposition, and closure
  arithmetic.
- Section 4: methods description for frozen anchor-locked registration and
  explicit delay/gauge diagnostics.
- Section 5.1 vertical discussion: vertical static error is materially reduced
  by fixed tag-delay correction, e.g. production fixed 0 to 60 mm changes
  vertical median 61.87 -> 39.29 mm; common-mode fixed 0 to 80 mm changes
  89.42 -> 45.80 mm.
- Section 13.2 item 1: oracle per-anchor range-bias regression and consolidated
  delay table.
- Section 13.3 item 2: common-mode range-bias mechanism replacing structural
  scale wording, with the exact 189.24 / 68.82 / 120.42 / 120.53 mm closure.
- Conclusion: residual common-mode per-device range bias and solver
  gauge/regularizer coupling, not a proven structural scale ambiguity.

Claims still open or requiring caution:

- Do not claim per-frame `d_tag` is production-ready from this audit. It is
  noisy and worsens static tails without additional temporal/session-level
  regularization.
- Do not claim production static accuracy is already fully delay-cancelled at
  `d_tag=0`; the fixed sweep shows a better grid point at 60 mm.
- Do not claim dynamic ROTO calibration is solved by tag-delay estimation; the
  track median remains near 100 mm.
- A deployable tag-delay branch should be treated as a separate solver roadmap
  item, not folded into this paper audit.

## Stop status

Hard stop honored. Phase 1d outputs were written, `AUDIT_FINDINGS.md` was
updated, and `main_EN.tex` / production layouts were not edited. Await review
before any Phase 2 paper-text changes.

## Phase 2 applied edits

Date: 2026-06-12.

Paper text edited: `reports/EN/main_EN.tex`.
New figure copied into the report tree:
`reports/EN/fig/tag_delay_sweep_phase1d.png`.
Headline production numbers and the v4-io/T4/F0 headline pipeline were left
unchanged.

Section-by-section summary:

- Section 3.3 (`Scale Bias Interpretation`): replaced the old scale-ambiguity
  interpretation with the corrected OLS-with-intercept result, the optimistic
  CI caveat, the mean pairwise excess, and the additive endpoint decomposition.
  The text now states that the Sim(3) fit expresses a coherent positive
  per-link range excess as apparent scale, not a true geometric scale
  distortion.
- Section 4: added `Per-Anchor Range-Bias Decomposition and Error-Budget
  Closure` (`sec:delay_decomposition`). This includes the oracle per-anchor
  regression, the 189.2 / 68.8 / 120.4 / 120.5 mm closure arithmetic, the
  common-mode `d_i=c+e_i` diagnostic, the tag-delay cancellation diagnostics,
  the 5 mm registration bound, the fixed tag-delay sweep figure, and a
  consolidated per-anchor delay table.
- Section 5.1: extended the vertical-bias paragraph to point to the concrete
  sources identified by the decomposition: anchor-side per-device range bias
  and uncorrected tag-side device delay.
- Section 13.2 item 1: rewrote the identified error source as coherent
  inter-anchor range excess under-absorbed by the production delay gauge,
  bound, and regularizer.
- Section 13.3 item 2: replaced the old structural scale item with
  `Resolving the apparent scale bias / per-device delay calibration`, including
  per-device antenna-delay calibration, tag-side delay handling, and production
  validation of the solver re-parameterization.
- Conclusion: replaced the structural scale wording with the audited
  per-device range-bias mechanism, kept the dynamic-floor conclusion, and added
  the Phase 1d result that tag-delay estimation leaves the dynamic median near
  103--107 mm.
- Appendix A.1: added a V4-io note that the `d_A=0` gauge, +/-60 mm bound, and
  `(d_i/20)^2` regularizer limit delay absorption, with a cross-reference to
  `sec:delay_decomposition`.

Verification:

- `pdflatex -interaction=nonstopmode -halt-on-error main_EN.tex` completed
  successfully twice from `reports/EN/`.
- New `cleveref` references and the PNG figure resolved after the second pass.
- `rg "structural|ambiguity" main_EN.tex` shows only the pre-existing mirror
  and time-alignment ambiguity references; no structural scale-ambiguity claim
  remains.

Phase 2.1 follow-up:
- Removed the rounded high-precision convergence range from prose and replaced it with a numerical-precision statement.
- Replaced internal Phase 1d naming in the Conclusion with a cross-reference to `sec:delay_decomposition`.
- Restored the 16436 DTU plausibility anchor and added the anchor-A/common-mode compression explanation; `pdflatex` passed twice.

Phase 2.2 follow-up:
- Removed seven rendered wall-simulation phase wordings: the local-reflector
  body sentence, the scenario-introduction sentence, the three item labels, the
  trial-count sentence, and the wall-material table caption.
- Chosen scenario names: `baseline wall scenario`, `wall-plus-metal scenario`,
  and `material-sensitivity sweep`.
- Also removed the three rendered IMU `Phase 4` references from the fusion
  table caption, the limitations item heading, and the next-step sentence.
- Verification passed: `rg -n "Phase~|Phase [0-9]|phases" main_EN.tex`,
  `rg -n "Phase" main_EN.tex`, two `pdflatex` passes, and `git diff --check`.

Stop status: Phase 2 paper-text edits are applied and compiled. Await review
before any further paper changes.

## Solver-line backlog

Date: 2026-06-14.

### Two-layer vertical prior: helps nominal, distorts off-nominal

Finding: synthetic-layout evidence from `AutoPos_simulation` over 1000 layouts
shows that v4-io's soft two-layer vertical prior reconstructs
off-nominal/irregular anchor geometries markedly worse than the prior-free
v3-lite. In the synthetic solver sweep, v4-io has coordinate RMS median
7.58 mm, while v3-lite has coordinate RMS median 0.98 mm. This is not
real-accuracy validation; it is solver-line synthetic-layout evidence. The
prior bakes in two-layer structure, so unusual geometries get pulled toward it.

Mechanism: `Phi_prior` in
`outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`
uses lower-layer z sigma 180 mm, upper-layer z sigma 220 mm, and a layer-gap
bound of 450--2600 mm through `physical_layout_prior_residuals(...)`. This
helps when the real layout is two-layer, as in Erlangen and most clinical-room
deployments, but it penalizes legitimate off-nominal placements.

Trigger / priority: this is not a problem for nominal two-layer deployments;
there the prior helps and no immediate action is required. It only becomes
relevant if a deployment uses non-two-layer or irregular geometry. It sits
behind the common-mode plus tag-delay work.

Candidate future fix: make the prior adaptive or conditional. Detect layer
structure, relax the sigma values or drop the layer-gap bound when the layout is
off-nominal, or fall back to prior-free reconstruction in the v3-lite style for
unusual layouts and recover metric scale separately.

To confirm before implementation: `solve_v4_common_mode(...)` currently carries
the same `physical_layout_prior_residuals(...)` call, so this caveat likely
applies to it too. That caveat is orthogonal to the delay re-parameterization.
