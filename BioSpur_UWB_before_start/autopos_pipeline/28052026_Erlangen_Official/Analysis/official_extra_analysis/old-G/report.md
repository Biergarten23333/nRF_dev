# 2026-05-28 Erlangen Official Analysis Draft

## 1. Dataset / Hardware / Solver Versions

Dataset root:

`autopos_pipeline/28052026_Erlangen_Official`

Analysis root:

`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis`

Anchor layout solvers included:

`v1-old`, `v2`, `v3-lite`, `v3-full`, `v4-io`

Tag solver family:

`T1`, `T2`, `T3`, `T4`

Roto OptiTrack absolute validation is pending; current roto results are UWB-only consistency diagnostics.

## 2. Anchor Layout Absolute Accuracy

Source: `tables/layout_alignment_summary.md`

Headline convention: reflection-allowed rigid alignment, no scale fitted to OptiTrack truth.

Current v4-io headline:

- all8 rigid RMS: 104.9 mm
- all8 horizontal RMS: 86.1 mm
- all8 vertical RMS: 59.9 mm
- all8 shape RMS: 130.5 mm
- similarity scale: 0.960, diagnostic only
- noG rigid RMS: 104.4 mm

G marker warning: `Gshort/Glong` marker fingerprint is suspect, but the layout RMS changes only from 104.9 mm to 104.4 mm when G is removed. Keep both all8 and noG for rigor, not because G changes the layout conclusion.

Important boundary: OptiTrack-derived delay/scale diagnostics are not independent anchor-layout validation. The anchor headline remains rigid, no-scale RMS after reflection-allowed alignment.

## 3. Static Tag Absolute Accuracy

Source:

- `tables/tag_accuracy_summary.md`
- `tables/tag_abs_errors_per_session.csv`
- `tables/tag_alignment_method_comparison.csv`
- `tables/tag_scale_propagation_summary.csv`
- `tables/tag_ground_truth_correction_summary.csv`
- `tables/anchor_source_comparison.md`
- `tables/surveyed_anchor_baseline_per_position.csv`
- `tables/additional_diagnostics_summary.md`

This pass includes both production solver outputs and a raw replay matrix. The official transform is fitted from anchors only; no tag truth is used for fitting. Static tag truth now uses the ID01/ID05 I-ball relabel correction and consensus ball-local `Iantenna` rebuild.

Frame-locking rule:

- A tag-cloud fit is circular and is only reported as a failure-mode comparison.
- Centroid-only alignment is underdetermined; it is reported as an error range over swept rotations/reflections.
- C anchor-locked alignment is the only official value.

Current v4-io production-output headline:

- all8 median 3D error: 77.4 mm
- all8 p95 3D error: 270.3 mm
- all8 RMS 3D error: 138.3 mm
- noG median 3D error: 81.3 mm
- noG p95 3D error: 278.6 mm
- noG RMS 3D error: 141.1 mm
- all8 median scale-bias diagnostic contribution: 30.0 mm
- noG median scale-bias diagnostic contribution: 31.2 mm

Raw replay matrix:

`5 anchor-layout solvers x 4 tag solvers x all8/noG`

Raw replay v4-io highlights:

- best-case solver combination: T3/all8 = 62.3 mm median 3D, p95 = 158.2 mm
- T4/all8 median 3D: 69.1 mm, p95 = 182.3 mm
- T4/all8 median horizontal / vertical: 41.3 / 55.0 mm
- T4/all8 median repeatability D3: 67.4 mm
- noG does not improve absolute tag error for v4-io; T3/T4 noG median 3D = 83.9 mm, p95 = 291.1 mm

Interpretation: internal repeatability near the 50-70 mm level is not inconsistent with absolute error near 60-80 mm median, because absolute error also contains anchor-frame scale/shape error and residual range model bias. T3 gives the best-case raw replay median in this dataset; the production-output headline remains 77.4 mm. T4 is close but more conservative under dropout. The ID01/ID05 marker-label correction fixes the tag truth path but does not explain the 270 mm-class production tail by itself.

## 4. Surveyed-Anchor Baseline

Source: `tables/anchor_source_comparison.md`

This control solves the tag directly in the OptiTrack frame using OptiTrack-truth anchor coordinates. No Kabsch, reflection, or scale fitting is applied.

Headline all8 comparison:

- OptiTrack anchors + raw zero delay: 296.0 mm median, p95 443.1 mm
- OptiTrack anchors + AutoPos v4-io delay vector: 241.9 mm median, p95 376.3 mm
- OptiTrack anchors + inter-anchor delaycal: 58.4 mm median, p95 134.8 mm, RMS 74.9 mm
- AutoPos v4-io production output: 77.4 mm median, p95 270.3 mm, RMS 138.3 mm

The AutoPos-delay hybrid is a useful negative control: the V4-io delay vector was generated from AutoPos data, but it does not transfer cleanly to the OptiTrack layout because it is jointly estimated with the AutoPos geometry. The delaycal row is partly circular because OptiTrack supplies both anchor coordinates and the inter-anchor delay fit; it is an optimistic lower bound. Against that lower bound, AutoPos is +18.9 mm in median but +135.4 mm in p95. ID03/ID04/ID06 collapse to 78.2/89.5/34.6 mm with surveyed anchors and delaycal, so the 270 mm-class production tail is mainly a self-calibration/layout/frame-lock tail rather than intrinsic UWB failure at those points.

## 5. Additional Diagnostics

Source: `tables/additional_diagnostics_summary.md`

Nine extra diagnostics were added for the corrected production-output `v4-io / all8` line:

- Delay decomposition: AutoPos common effective delay is 34.4 mm versus 90.6 mm for the OptiTrack inter-anchor delay fit; differential agreement is weak (Pearson r=-0.03), so the AutoPos delay vector is layout/self-calibration coupled.
- Distance/radial structure: 3D error grows with centroid distance at 166.5 mm/m (R^2=0.28, p=0.007), and signed radial error grows at 229.9 mm/m (p=0.000).
- Error vector field: 83% of points are radially outward; the common mean vector is small compared with scatter (|mean|/RMS-scatter=0.19).
- Worst-point residual fingerprints: ID01/ID03/ID04/ID06 show anchor-specific residual structure, not one shared common offset.
- Anchor triage: lowest heuristic trust anchors are G, D, H; most critical anchors to keep in drop-one keep7 are E, D, A.
- Stratifications: low/high and edge positions are worse than mid/center; facing-group effects remain exploratory because each group has small n.

Interpretation: the remaining p95 tail is coupled layout/self-calibration/frame-lock error with scale/radial and anchor-specific components. It is not explained by corrected tag truth, pure VDOP, or a transferable AutoPos delay vector alone.

## 6. VDOP Geometry Explanation

Source:

- `tables/dop_summary_grid100.md`
- `tables/dop_summary_grid50.md`
- `tables/dop_summary_grid25.md`

Default geometry model: range-only Jacobian `[ux, uy, uz]`.

Current grid50 summary:

- all8 VDOP median 0.806, p95 0.950
- noG VDOP median 0.859, p95 1.185
- dropH VDOP median 0.864, p95 1.206

The 25 mm grid is intended for final report figures.

## 7. MC Keep-k Robustness

Source:

- `tables/mc_integrity_summary.md`
- `tables/mc_keepk_combined_summary.csv`
- `figs/mc_keepk_static_curves.png`
- `figs/mc_keepk_roto_curves.png`
- MC repeat percentile CIs in `tables/metric_confidence_intervals.csv`

Complete matrix:

`5 Vx x 4 Tx x static/roto x keep 8/7/6/5/4 x MC5000`

Integrity: `40/40 PASS`, no issues detected.

Current v4-io / T4 keep-k snapshot:

| kind | keep8 | keep7 | keep6 | keep5 | keep4 |
| --- | --- | --- | --- | --- | --- |
| static D3 std median mm | 61.7 | 83.2 | 107.3 | 149.1 | 196.4 |
| roto turn-center RMS median mm | 12.1 | 21.6 | 30.5 | 41.4 | 63.9 |

Interpretation: the degradation is monotonic with forced anchor dropout. The MC5000 repeat intervals are now included as `mc_*` rows in `metric_confidence_intervals.csv`.

## 8. Roto UWB-only Consistency

Source:

- `tables/metric_confidence_intervals.md`
- existing solver tables under `solver/outputs/v1_to_v4_io_field_check/tables/`

Current v4-io bootstrap headline:

- roto abs deltaR error median: 33.33 mm, 95% CI 22.52-40.15 mm
- roto turn-center RMS median: 14.31 mm, 95% CI 13.16-17.36 mm

Roto OptiTrack absolute validation remains pending.

## 9. Pair Residual Diagnostics

Source:

- `tables/pair_residual_diagnostics.md`
- `figs/pair_raw_asymmetry_heatmap.png`
- per-version residual heatmaps in `figs/`

Current v4-io worst all1000 pairs include B-C, B-G, D-E, D-F, and F-H. G-involving pairs are explicitly flagged.

## 10. Bootstrap CI

Source:

- `tables/metric_confidence_intervals.csv`
- `tables/metric_confidence_intervals.md`
- `figs/bootstrap_confidence_intervals.png`

Current CIs include layout, static repeatability, production static tag absolute, raw replay static tag absolute, roto UWB-only, and MC keep-k metrics. MC intervals use direct MC5000 repeat percentiles rather than a second bootstrap over Monte Carlo repeats.

## 11. Temporal / Thermal Drift

Source:

- `tables/temporal_drift_summary.md`
- `tables/temporal_drift_per_anchor_session.csv`
- `figs/temporal_drift_slope_heatmap.png`

Static raw-link drift headline:

- static sessions analyzed: 24
- anchor-session links analyzed: 192
- median absolute drift slope: 1.54 mm/min
- p95 absolute drift slope: 16.21 mm/min
- median absolute drift over capture: 3.07 mm
- p95 absolute drift over capture: 32.42 mm

Per-anchor pattern:

- A-E are mostly small drift over 120 s.
- F/G/H are worse, with G and F the most visible outliers.
- Worst links include ID01-G, ID15-G, ID01-H, ID08-F, ID03-F.

Interpretation: temporal drift is not the dominant reason for the 60-80 mm median absolute tag error, but F/G/H drift tails are report-relevant and should be treated as possible hardware/link instability under certain placements.

## 12. Stratified Keep-k

Source:

- `tables/stratified_keepk_by_drop_set.csv`
- `tables/stratified_keepk_category_summary.csv`
- `tables/stratified_keepk_summary.md`
- `figs/stratified_keepk_upper_vs_lower.png`

Method: exhaustive fixed dropped-set replay, separate from random MC5000. Completed matrix:

`5 Vx x 4 Tx x static/roto x keep 7/6/5/4`

Integrity:

- detail rows: 6480
- blocks: 40/40
- fixed keep-sets per block: 162

V4-io / T4 headline:

| kind | keep_k | lower-heavy metric | upper-heavy metric | interpretation |
| --- | ---: | ---: | ---: | --- |
| static D3 std mm | 7 | 65.2 | 61.0 | lower drop slightly worse |
| static D3 std mm | 6 | 84.5 | 63.1 | lower drop worse |
| static D3 std mm | 5 | 96.0 | 65.1 | lower drop worse |
| static D3 std mm | 4 | 117.3 | 57.2 | lower drop much worse |
| roto turn-center RMS mm | 7 | 18.3 | 16.0 | lower drop slightly worse |
| roto turn-center RMS mm | 6 | 25.2 | 18.1 | lower drop worse |
| roto turn-center RMS mm | 5 | 30.9 | 24.1 | lower drop worse |
| roto turn-center RMS mm | 4 | 48.5 | 34.0 | lower drop worse |

Interpretation: the measured fixed-drop pattern does not support the simple hypothesis that dropping upper anchors is always worse for Z. In this field geometry, losing lower-layer anchors hurts both static repeatability and roto stability more strongly. The worst individual V4-io/T4 drop set is roto keep4 with dropped set `ACEG`, which is a balanced drop but geometrically destructive.

## 13. Limitations / Pending

- G OptiTrack marker labeling likely has a short/long fingerprint issue.
- Roto OptiTrack absolute data is not yet available.
- Roto absolute comparison remains pending until OptiTrack roto processing lands.
