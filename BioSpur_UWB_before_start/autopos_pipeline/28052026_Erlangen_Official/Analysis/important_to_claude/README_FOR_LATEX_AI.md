# Context Package for Writing the AutoPos Vicon Ground-Truth Report

This folder is a compact context package for another AI or human writer. Its goal is to make the report reproducible without forcing the writer to scan the entire `Analysis/` tree.

The target report is not an "Erlangen official" report. The correct title meaning is:

> Vicon Motion Capture System ground-truth measurement of the AutoPos system performed in Erlangen on 28 May 2026.

Important terminology:

- Use `Vicon Motion Capture System`, not `OptiTrack`, in the final report text.
- Use `RotoArm UWB-Tag`, not `ROTO`, in the final report text.
- Avoid `production` wording. The preferred wording is `AutoPos Solver and UWB-Tag solver combination`, `main configuration`, or `measured system`.
- Some copied source markdown/CSV files still contain legacy names such as `OptiTrack`, `ROTO`, or `production`. Treat those as historical analysis labels, not final wording.

## Package Layout

```text
important_to_claude/
├── README_FOR_LATEX_AI.md
└── files/
    ├── figures/
    │   ├── report_figures/      # Directly usable key figures
    │   └── wall_nlos/           # Extra wall/NLOS simulation figures
    ├── csv/
    │   ├── official_extra_analysis/
    │   └── other_analysis/
    ├── md/
    │   ├── official_extra_analysis/
    │   └── other_analysis/
    └── json/                    # reserved for selected manifest/meta files if needed
```

Current compact zip:

```text
../important_to_claude_context.zip
```

It is kept below 30 MB by excluding large raw/sample tables and duplicate figures.

## Recommended Report Spine

The report should be detailed, not a short paper. A defensible structure is:

1. Introduction and dataset definition.
2. Coordinate systems and evaluation objects.
3. AutoPos anchor self-localization versus Vicon Motion Capture System ground truth.
4. Delay-layout coupling.
5. Static UWB-Tag localization using AutoPos Solver and UWB-Tag solver combinations.
6. Static filtering and why it improves repeatability but is not calibration.
7. Dynamic RotoArm UWB-Tag evaluation.
8. Monte Carlo keep-k robustness and anchor dropout.
9. Wall/NLOS simulation as propagation-risk discussion.
10. Phase 4 UWB+IMU as dynamic-extension evidence.
11. CIR evidence: clear LOS can still contain multipath and link-quality differences.
12. Limitations and next work.
13. Conclusion.

The central claim should be:

> AutoPos UWB self-localization recovers a usable anchor geometry and supports centimeter-to-decimeter static UWB-Tag localization in a real measured field setup. However, final UWB accuracy is governed jointly by layout, delay calibration, solver choice, motion state, and propagation environment.

## Headline Results to Use

### Static UWB-Tag main result

Use this as the main static result:

```text
AutoPos Solver layout: v4-io
UWB-Tag solver: T4
Median 3D: 72.7 mm
P95 3D: 171.5 mm
RMSE 3D: 109.8 mm
```

Interpretation:

- This is the best wording for the report headline: `AutoPos Solver and UWB-Tag solver combination v4-io/T4`.
- Do not call it `production`.
- Do not replace it with the lower median from estimator ablation unless explicitly discussing ablation.

Related ablation:

```text
v4-io/T4 median-estimator or raw replay ablation:
Median 3D around 69.7-69.8 mm
P95 3D around 173.8-173.9 mm
RMSE 3D around 108.9 mm
```

Use this only to show estimator sensitivity, not as the main claim.

Useful packaged files:

```text
files/csv/official_extra_analysis/FULL/tables/tag_accuracy_summary.csv
files/csv/official_extra_analysis/FULL/tables/tag_raw_replay_accuracy_summary.csv
files/csv/official_extra_analysis/FULL/tables/tag_raw_replay_abs_errors_per_session.csv
files/csv/official_extra_analysis/FULL_4way_comparison/production_method_probe/production_static_method_real_run_eval/tables/tag_accuracy_summary.csv
files/md/official_extra_analysis/FULL/tables/tag_accuracy_summary.md
files/md/official_extra_analysis/FULL_US/tables/tag_raw_replay_accuracy_summary.md
```

Direct figures:

```text
files/figures/report_figures/tag_error_cdf.png
files/figures/report_figures/static_tag_error_by_position.png
files/figures/report_figures/static_tag_error_vs_distance.png
files/figures/report_figures/tag_error_by_position.png
files/figures/report_figures/tag_error_vs_distance.png
```

### Anchor layout

Use these results:

```text
v4-io layout vs Vicon Motion Capture System anchors:
SE(3) / rigid no-scale RMSE: 105.4 mm
Sim(3) / similarity residual: 67.1 mm
Estimated scale bias: about -4.17%
```

Interpretation:

- SE(3) shows absolute anchor-layout error without allowing global scale.
- Sim(3) allows one global scale factor; the lower residual shows that a strong part of the error is scale/delay related.
- Do not claim AutoPos equals Vicon. Do claim AutoPos recovers usable topology and approximate geometry.

Useful packaged files:

```text
files/csv/official_extra_analysis/FULL/tables/layout_alignment_summary.csv
files/md/official_extra_analysis/FULL/tables/layout_alignment_summary.md
files/csv/official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv
files/csv/official_extra_analysis/FULL/tables/anchor_source_comparison.csv
```

Direct figure:

```text
files/figures/report_figures/layout_opti_vs_autopos_3d.png
```

The filename still says `opti`; final caption should say Vicon Motion Capture System.

### Delay-layout coupling

This is one of the most important mechanism results:

```text
Vicon anchors, no delay correction: 311.3 mm RMSE
Vicon anchors, transplanted AutoPos delay: 252.2 mm RMSE
Vicon anchors, re-estimated delay: 77.7 mm RMSE
AutoPos v4-io, co-fitted delay: 108.9 mm RMSE
```

Interpretation:

- Anchor geometry and UWB delay calibration cannot be separated naively.
- Vicon anchors alone do not automatically improve tag localization.
- Delay must be matched to the anchor layout.

Useful packaged files:

```text
files/csv/official_extra_analysis/FULL_4way_comparison_US/reporting_checklist/tables/delay_layout_coupling.csv
files/csv/official_extra_analysis/FULL_AutoPos_align_to_Vicon/tables/vicon_delaycal_diagnostics.csv
files/csv/official_extra_analysis/FULL/tables/delay_common_differential.csv
files/csv/official_extra_analysis/FULL/tables/delay_method_agreement.csv
```

Direct figure:

```text
files/figures/report_figures/delay_layout_coupling.png
```

### Dynamic RotoArm UWB-Tag

Main result for original UWB-only `v4-io/T4`:

```text
Track-median 3D P50: 105.8 mm
Track-median 3D P95: 231.8 mm
Sample RMSE: 141.3 mm
Sample-weighted 3D P50/P95: 102.6 / 256.9 mm
Horizontal XZ sample P50/P95: 66.1 / 179.0 mm
Vertical sample P50/P95: 61.6 / 205.9 mm
```

Negative result to state clearly:

```text
Even with Vicon anchors and re-estimated delay:
Dynamic RotoArm UWB-Tag remains about 105.6 mm median and 200.4 mm P95.
```

Interpretation:

- Dynamic error is not solved by swapping in Vicon anchor geometry.
- Time skew and the 0.8 ms protocol window are too small to explain the 100-200 mm tail.
- More plausible causes: dynamic range bias, antenna orientation, body obstruction, multipath/NLOS, and solver dynamic assumptions.

Useful packaged files:

```text
files/md/official_extra_analysis/FULL/roto_absolute/reports/ROTO_ABSOLUTE_ANALYSIS.md
files/md/official_extra_analysis/FULL/roto_absolute/reports/ROTO_SEGMENT_ALIGNMENT_SEARCH.md
files/md/official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/reports/ROTO_DYNAMIC_DIAGNOSTICS.md
files/csv/official_extra_analysis/FULL/roto_absolute/tables/roto_abs_summary_by_solver.csv
files/csv/official_extra_analysis/FULL/roto_absolute/tables/roto_abs_per_track.csv
files/csv/official_extra_analysis/FULL/roto_absolute/tables/roto_time_offsets_v4io_T4.csv
files/csv/official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/tables/roto_error_by_angular_speed.csv
files/csv/official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/tables/roto_error_by_phase.csv
files/csv/official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/tables/roto_radius_error_by_track.csv
files/csv/official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/tables/roto_two_wand_relative_distance_summary.csv
files/csv/official_extra_analysis/FULL_4way_comparison/tables/roto_4way_accuracy_summary.csv
```

Direct figures:

```text
files/figures/report_figures/roto_abs_cdf_v4io_T4.png
files/figures/report_figures/roto_solver_matrix_median3d.png
files/figures/report_figures/roto_error_by_angular_speed.png
files/figures/report_figures/roto_error_by_phase.png
```

Large raw tables intentionally not copied:

```text
official_extra_analysis/FULL/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
official_extra_analysis/FULL/roto_absolute/dynamic_diagnostics/tables/roto_dynamic_samples_v4io_T4.csv
```

### Static filtering

Use this as a supporting result:

```text
F5 repeatability spread: 67.4 mm -> 18.7 mm
T4+F5: about 64.9 mm median, 175.7 mm P95, 109.5 mm RMSE
```

Interpretation:

- Filtering improves repeatability and visual stability.
- It is not anchor calibration and does not remove systematic bias.

Useful packaged files:

```text
files/md/official_extra_analysis/FULL/filtered_deployment/reports/filtered_static_results.md
files/csv/official_extra_analysis/FULL/filtered_deployment/tables/filtered_static_metrics_full.csv
files/csv/official_extra_analysis/FULL/filtered_deployment/tables/filtered_static_radial_decomposition.csv
files/csv/official_extra_analysis/FULL/filtered_deployment/tables/filtered_static_per_axis_bias.csv
```

Direct figure:

```text
files/figures/report_figures/filtered_static_cdf_v4io_all8.png
```

### Monte Carlo keep-k robustness

Use this as robustness discussion:

```text
Static repeatability d3 std median for v4-io/T4:
keep8: 61.7 mm
keep7: 83.2 mm
keep6: 107.3 mm
keep5: 149.1 mm
keep4: 196.4 mm

RotoArm turn-center RMS median:
keep8: 12.1 mm
keep4: 63.9 mm

Stratified keep-k:
keep5 lower-heavy drop: 96.0 mm
keep5 upper-heavy drop: 65.1 mm
keep4 lower-heavy drop: 117.3 mm
keep4 upper-heavy drop: 57.2 mm
```

Interpretation:

- Anchor count matters, but vertical/spatial coverage matters at least as much.
- Do not use Monte Carlo as the main accuracy headline.

Useful packaged files:

```text
files/csv/official_extra_analysis/FULL/tables/mc_keepk_combined_summary.csv
files/csv/official_extra_analysis/FULL/tables/stratified_keepk_by_drop_set.csv
files/csv/official_extra_analysis/FULL/tables/stratified_keepk_composition_summary.csv
files/csv/official_extra_analysis/FULL/tables/single_anchor_criticality.csv
```

Direct figures:

```text
files/figures/report_figures/mc_keepk_static_curves.png
files/figures/report_figures/mc_keepk_roto_curves.png
files/figures/report_figures/stratified_keepk_upper_vs_lower.png
```

Large per-repeat Monte Carlo CSVs from `Analysis/Monte-Carlo-Simulation` were not copied after zip-size trimming. The official combined summaries above are sufficient for the report.

### Wall/NLOS simulation

Use this as discussion, not as direct validation:

```text
Clear LOS P95: about 0.062 m
4-wall default P95:
  0 cm: about 0.764 m
  40 cm: about 0.287 m
  100 cm: about 0.088 m
4-wall + metal P95:
  0 cm: about 0.892 m
  40 cm: about 0.405 m
  100 cm: about 0.123 m
Convergence to LOS+25%:
  default wall: about 115 cm
  wall + metal: about 145 cm
```

Interpretation:

- This supports the physical explanation that walls, metal, and obstruction can strongly inflate the error tail.
- It does not replace the Vicon Motion Capture System-UWB evidence chain.

Useful packaged files:

```text
files/md/other_analysis/AutoPos_simulation/wall_nlos_study/README.md
files/md/other_analysis/AutoPos_simulation/wall_nlos_study/analysis/phase123_comparison_report.md
files/md/other_analysis/AutoPos_simulation/wall_nlos_study/analysis/extended_4wall_convergence_report.md
files/csv/other_analysis/AutoPos_simulation/wall_nlos_study/analysis/phase123_4wall_key_comparison.csv
files/csv/other_analysis/AutoPos_simulation/wall_nlos_study/analysis/extended_4wall_convergence_to_los.csv
files/csv/other_analysis/AutoPos_simulation/wall_nlos_study/analysis/extended_4wall_series.csv
```

Direct figures:

```text
files/figures/report_figures/wall_nlos_p95_comparison.png
files/figures/report_figures/wall_nlos_convergence.png
files/figures/wall_nlos/AutoPos_simulation/wall_nlos_study/figures/key_plots/key_01_phase123_4wall_curves.png
files/figures/wall_nlos/AutoPos_simulation/wall_nlos_study/figures/extended_4wall_0to300cm/extended_4wall_convergence_linear.png
```

### Phase 4 UWB+IMU

Use the Phase 4 L2/L16/L20 TRUEFULL 5-seed Vicon-truth analysis generated on 5 June 2026. Do not use the earlier real 6-axis vertical-slice smoke run as the main IMU result.

Main matched-best rows:

```text
L20: X_A0_U4_P4_L20_I5_T2
  P50/P95/RMSE: 68.4 / 112.1 / 75.7 mm
  same-P P95 improvement: 26.8 mm
  B0 P0 P95 improvement: 119.7 mm
  RotoArm radius abs / band P95: 16.6 / 105.1 mm

L16: X_A0_U4_P4_L16_I5_T2
  P50/P95/RMSE: 69.0 / 114.9 / 76.1 mm
  same-P P95 improvement: 24.0 mm

L2: X_A0_U4_P4_L2_I5_T3
  P50/P95/RMSE: 83.9 / 146.2 / 94.2 mm
  same-P P95 improvement: -7.3 mm
```

Spiky track result:

```text
Track: R01 / BS2DCE
B0 pure UWB:
  X-Z jumps >200 mm: 178
  jump p99: 425.8 mm
  max jump: 506.5 mm
  3D P95: 595.5 mm
  vertical P95: 545.1 mm

L2/L16/L20:
  jumps >200 mm: 0
  3D P95: 188.4 / 185.1 / 169.4 mm
```

Interpretation:

- IMU is no longer only future work. It has Phase 4 replay/simulation evidence.
- Its strongest value is dynamic robustness: reducing P95, suppressing jump spikes, and improving RotoArm shape metrics.
- It should not replace the main Vicon Motion Capture System-UWB validation because it is still a replay/simulation sweep, not a fully embedded closed-loop Ghost IMU system measurement.

Useful packaged files:

```text
files/md/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/reports/PHASE4_L2_L16_L20_TRUEFULL_5SEED_ANALYSIS.md
files/md/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/reports/PHASE4_PROFESSOR_L2_L16_L20_SPIKY_TRACK.md
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/matched_best_combo_L2_L16_L20.csv
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/top5_by_sensor.csv
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/production_A0_position_ranking_by_accuracy.csv
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/production_A0_position_ranking_by_sameP_rescue.csv
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/production_A0_raw_range_ranking.csv
files/csv/other_analysis/IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/professor_l2_l16_l20_spiky_track/R01_BS2DCE_L2_L16_L20_professor_metrics.csv
```

Direct figures:

```text
files/figures/report_figures/phase4_l2_l16_l20_sensor_comparison.png
files/figures/report_figures/phase4_t2_pi_heatmap_by_sensor.png
files/figures/report_figures/phase4_spiky_track_xz_l2_l16_l20.png
files/figures/report_figures/phase4_spiky_track_err3d_l2_l16_l20.png
files/figures/report_figures/phase4_spiky_track_vertical_l2_l16_l20.png
files/figures/report_figures/imu_stress_best_per_sensor_p95.png
files/figures/report_figures/phase4_stress_top12_worstcase_p95.png
```

Large raw IMU table intentionally not copied:

```text
IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/all_seed_track_metrics.csv
IMU-Fusion-Simulation/runs/phase4_analysis/l2_l16_l20_truefull_5seed_opti_truth_20260605T221806Z/tables/all_seed_summary_with_deltas.csv
```

### CIR

CIR should be written as propagation evidence, not as a final CIR-assisted localization result.

Use these facts:

```text
FULL CIR Sweep10:
  Source: CIRRAW_AUTOPOS_SWEEP10_20260602_225118
  Complete full-CIR frames: 958 / about 1120
  Coverage: 85.5%
  Accumulator: 4064 bytes = 1016 complex samples
  Covers all 8 masters and 10/10 sweep lines

High-tail / suspicious links:
  C<-G, G<-C, F<-G, E<-H, G<-F, H<-E
  tail/main proxy around 0.2

F/H 8-hour FULL CIR baseline:
  Source: CIRRAW_BSF66F_20260602_020301
  Frames: 31,514
  Duration: 8.00 h
  F tail/main median/P95: 0.137 / 0.215
  H tail/main median/P95: 0.132 / 0.186
  Peak index median: 750, IQR 4

COMPACT CIR:
  952 compact ACRX samples
  56 directed links
  suspicious links include A<-C, C<-A, C<-G, G<-C, A<-B, E<-H, H<-E

CIR-weighted layout smoke test:
  baseline RMS edges: 79.437 mm
  CIR-weighted RMS edges: 82.467 mm
```

Interpretation:

- Clear LOS does not imply ideal single-path UWB.
- CIR can explain multipath/tail energy/link-quality variation.
- The current CIR-weighted solver is not a final improvement; it only proves an interface for future weighting/filtering.

Direct figures:

```text
files/figures/report_figures/cir_full_frame_count_heatmap.png
files/figures/report_figures/cir_full_snr_proxy_heatmap.png
files/figures/report_figures/cir_full_tail_ratio_heatmap.png
files/figures/report_figures/cir_full_receiver_envelope_overview.png
files/figures/report_figures/cir_fh_mean_waveforms.png
files/figures/report_figures/cir_fh_tail_ratio_timeseries.png
files/figures/report_figures/cir_compact_suspicion_score_heatmap.png
files/figures/report_figures/cir_compact_suspicious_links.png
```

## Additional Official Extra Diagnostics Worth Mining

These are useful for a detailed report or appendix:

```text
files/md/official_extra_analysis/FULL/tables/additional_diagnostics_summary.md
files/csv/official_extra_analysis/FULL/tables/tag_error_vs_center_distance.csv
files/csv/official_extra_analysis/FULL/tables/tag_error_vector_decomposition.csv
files/csv/official_extra_analysis/FULL/tables/worstpoint_range_residuals.csv
files/csv/official_extra_analysis/FULL/tables/anchor_health_scorecard.csv
files/csv/official_extra_analysis/FULL/tables/tag_error_by_height.csv
files/csv/official_extra_analysis/FULL/tables/tag_error_by_facing.csv
files/csv/official_extra_analysis/FULL/tables/tag_error_edge_vs_center.csv
files/csv/official_extra_analysis/FULL/tables/single_anchor_criticality.csv
files/csv/official_extra_analysis/FULL/tables/temporal_drift_anchor_summary.csv
files/csv/official_extra_analysis/FULL/tables/metric_confidence_intervals.csv
```

Key additional interpretations:

- Error increases with distance from array center; all8 3D-error slope is about 166.8 mm/m.
- Error vectors are often radially outward; 79% of points are radially outward in the additional diagnostic summary.
- G, D, H appear as low-trust anchors in the heuristic health score, but geometric criticality must be evaluated separately.
- Single-anchor criticality ranks E, D, A as important to keep for combined degradation.

## What Not to Use as Main Evidence

Do not use:

- `official_extra_analysis/old-G_DO_NOT_ANALYSE_ANYMORE/`
- old Anchor-G corrupted ground-truth outputs
- early `phase4_real_6axis_vertical_slice` as the main IMU result
- CIR-weighted layout smoke test as a final accuracy improvement
- filtering results as calibration results
- tag-truth-fitted frame locks as accuracy claims
- Sim(3) residual as the no-scale anchor-layout accuracy claim

## Compression Policy Used for This Package

To keep the zip below 30 MB, this package includes:

- Current report figures.
- Official summary/report markdown.
- Official CSV tables below the package threshold.
- Phase 4 summary/ranking CSVs and spiky-track CSVs.
- Wall/NLOS summary CSVs and figures.

It excludes:

- Large raw/sample CSV tables.
- Duplicate US/no-US sample-level tables where summary tables are present.
- Duplicate copies of the same PNGs from source result directories.
- Thousands of low-value JSON run metadata files.

The original full workspace still contains everything; use the original paths listed above if raw samples are needed.
