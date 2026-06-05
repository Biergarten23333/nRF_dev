# Phase 2 Visual Audit

Generated: 2026-06-04T17:47:19.758164+00:00
Phase status: `stage2_visual_audit_complete`
Stage2 wall time: 35.93 s
Selected experiments: 13
PNG figures: 65

## Visual Gates

| gate_id | status | evidence | blocking_next_phase |
| --- | --- | --- | --- |
| G7_stage1_row_count | PASS | stage1 rows=705 expected=705 | False |
| G8_stage2_visual_assets | PASS | selected_experiments=13 figures=65 | False |
| G9_deployable_candidate_present | PASS | FUSION_HELPS_DEPLOYABLE rows=1 | False |
| G10_cpu_parallel_execution | PASS | stage1_workers=10 | False |

## Selected Experiments

| stage2_reason | experiment_id | kind | screening_score | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | legacy_deltaR_error_rms_mm | trackmedian_radius_error_abs_mm | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| control_baseline | B0_A0_U4_P0_T1 | baseline | 272.9 | 105.8 | 231.8 | 80.1 | 59.7 | BASELINE_UWB_ONLY |
| perfect_imu_oracle | X_A0_L0_I0_T11 | imu_only | 173.1 | 0.0 | 0.0 | 247.3 | 0.0 | IMU_ONLY_PERFECT_ORACLE |
| mpu6050_like_drift_control | X_A0_L2_I3_T11 | imu_only | 24826.2 | 3753.1 | 13891.6 | 16823.2 | 8869.6 | IMU_ONLY_DRIFTS_AS_EXPECTED |
| deployable_help_candidate | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | position_fusion | 249.0 | 97.3 | 197.5 | 77.0 | 57.3 | FUSION_HELPS_DEPLOYABLE |
| top_position_score | X_A0_U4_P2_L8_I1+I2+I3+I8_T3 | position_fusion | 245.9 | 96.3 | 183.3 | 86.8 | 49.4 | FUSION_HURTS_GEOMETRY |
| top_position_score | X_A0_U4_P0_L7_I4_T2 | position_fusion | 249.2 | 106.4 | 214.5 | 56.8 | 56.0 | FUSION_NEUTRAL |
| top_position_score | X_A0_U4_P2_L3_I1_T2 | position_fusion | 251.2 | 97.2 | 166.8 | 107.3 | 40.8 | FUSION_HURTS_GEOMETRY |
| top_position_score | X_A0_U4_P0_L3_I1_T2 | position_fusion | 254.4 | 99.4 | 183.5 | 93.6 | 50.7 | FUSION_HURTS_GEOMETRY |
| neutral_control | X_A0_U4_P0_L7_I3_T2 | position_fusion | 263.1 | 106.3 | 215.9 | 75.5 | 56.8 | FUSION_NEUTRAL |
| neutral_control | X_A0_U4_P0_L7_I4_T5 | position_fusion | 271.5 | 103.2 | 227.0 | 84.5 | 59.3 | FUSION_NEUTRAL |
| best_range_side_proto | X_A0_R4_L0_I1+I2+I3+I8_T6 | range_fusion | 769.9 | 323.0 | 530.3 | 282.0 | 127.8 | FUSION_HURTS_GEOMETRY |
| best_range_side_proto | X_A0_R4_L1_I1+I2+I3+I8_T6 | range_fusion | 774.5 | 340.8 | 551.0 | 251.0 | 130.3 | FUSION_HURTS_GEOMETRY |
| best_range_side_proto | X_A0_R4_L0_I1+I3+I7_T6 | range_fusion | 779.2 | 341.7 | 555.3 | 252.6 | 132.6 | FUSION_HURTS_GEOMETRY |

## Main Read

- Only one row currently satisfies deployable-help verdict; visual confirmation is mandatory before promoting it.
- Most high-score position rows improve central error but damage ROTO geometry, so P50/P95 alone is not sufficient.
- Best range-side prototypes are still much worse than B0 after bias correction, so T6/T8 remain prototype-only in this run.
- Pure IMU rows are retained as drift diagnostics, not candidates for deployable ROTO fusion.

## Figure Index

| figure_kind | experiment_id | capture_id | tag | figure_path |
| --- | --- | --- | --- | --- |
| contact_sheet_xz | B0_A0_U4_P0_T1 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/B0_A0_U4_P0_T1__contact_sheet.png |
| curated_worst_track_xz_error | B0_A0_U4_P0_T1 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/B0_A0_U4_P0_T1/B0_A0_U4_P0_T1__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | B0_A0_U4_P0_T1 | R02 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/B0_A0_U4_P0_T1/B0_A0_U4_P0_T1__R02__BS2DCE__curated.png |
| curated_worst_track_xz_error | B0_A0_U4_P0_T1 | R05 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/B0_A0_U4_P0_T1/B0_A0_U4_P0_T1__R05__BS2DCE__curated.png |
| curated_worst_track_xz_error | B0_A0_U4_P0_T1 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/B0_A0_U4_P0_T1/B0_A0_U4_P0_T1__R09__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_L0_I0_T11 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_L0_I0_T11__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_L0_I0_T11 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L0_I0_T11/X_A0_L0_I0_T11__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_L0_I0_T11 | R01 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L0_I0_T11/X_A0_L0_I0_T11__R01__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_L0_I0_T11 | R02 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L0_I0_T11/X_A0_L0_I0_T11__R02__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_L0_I0_T11 | R02 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L0_I0_T11/X_A0_L0_I0_T11__R02__BSDC91__curated.png |
| contact_sheet_xz | X_A0_L2_I3_T11 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_L2_I3_T11__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_L2_I3_T11 | R02 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L2_I3_T11/X_A0_L2_I3_T11__R02__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_L2_I3_T11 | R03 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L2_I3_T11/X_A0_L2_I3_T11__R03__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_L2_I3_T11 | R04 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L2_I3_T11/X_A0_L2_I3_T11__R04__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_L2_I3_T11 | R11 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_L2_I3_T11/X_A0_L2_I3_T11__R11__BSDC91__curated.png |
| contact_sheet_xz | X_A0_R4_L0_I1+I2+I3+I8_T6 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_R4_L0_I1+I2+I3+I8_T6__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I2+I3+I8_T6 | R07 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I2+I3+I8_T6/X_A0_R4_L0_I1+I2+I3+I8_T6__R07__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I2+I3+I8_T6 | R11 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I2+I3+I8_T6/X_A0_R4_L0_I1+I2+I3+I8_T6__R11__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I2+I3+I8_T6 | R11 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I2+I3+I8_T6/X_A0_R4_L0_I1+I2+I3+I8_T6__R11__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I2+I3+I8_T6 | R14 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I2+I3+I8_T6/X_A0_R4_L0_I1+I2+I3+I8_T6__R14__BSDC91__curated.png |
| contact_sheet_xz | X_A0_R4_L0_I1+I3+I7_T6 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_R4_L0_I1+I3+I7_T6__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I3+I7_T6 | R07 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I3+I7_T6/X_A0_R4_L0_I1+I3+I7_T6__R07__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I3+I7_T6 | R11 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I3+I7_T6/X_A0_R4_L0_I1+I3+I7_T6__R11__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I3+I7_T6 | R11 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I3+I7_T6/X_A0_R4_L0_I1+I3+I7_T6__R11__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L0_I1+I3+I7_T6 | R14 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L0_I1+I3+I7_T6/X_A0_R4_L0_I1+I3+I7_T6__R14__BSDC91__curated.png |
| contact_sheet_xz | X_A0_R4_L1_I1+I2+I3+I8_T6 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_R4_L1_I1+I2+I3+I8_T6__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_R4_L1_I1+I2+I3+I8_T6 | R07 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L1_I1+I2+I3+I8_T6/X_A0_R4_L1_I1+I2+I3+I8_T6__R07__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L1_I1+I2+I3+I8_T6 | R11 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L1_I1+I2+I3+I8_T6/X_A0_R4_L1_I1+I2+I3+I8_T6__R11__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L1_I1+I2+I3+I8_T6 | R11 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L1_I1+I2+I3+I8_T6/X_A0_R4_L1_I1+I2+I3+I8_T6__R11__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_R4_L1_I1+I2+I3+I8_T6 | R14 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_R4_L1_I1+I2+I3+I8_T6/X_A0_R4_L1_I1+I2+I3+I8_T6__R14__BSDC91__curated.png |
| contact_sheet_xz | X_A0_U4_P0_L3_I1_T2 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P0_L3_I1_T2__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L3_I1_T2 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L3_I1_T2/X_A0_U4_P0_L3_I1_T2__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L3_I1_T2 | R03 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L3_I1_T2/X_A0_U4_P0_L3_I1_T2__R03__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L3_I1_T2 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L3_I1_T2/X_A0_U4_P0_L3_I1_T2__R09__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L3_I1_T2 | R10 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L3_I1_T2/X_A0_U4_P0_L3_I1_T2__R10__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_U4_P0_L7_I3_T2 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P0_L7_I3_T2__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I3_T2 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I3_T2/X_A0_U4_P0_L7_I3_T2__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I3_T2 | R03 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I3_T2/X_A0_U4_P0_L7_I3_T2__R03__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I3_T2 | R08 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I3_T2/X_A0_U4_P0_L7_I3_T2__R08__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I3_T2 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I3_T2/X_A0_U4_P0_L7_I3_T2__R09__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_U4_P0_L7_I4_T2 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P0_L7_I4_T2__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T2 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T2/X_A0_U4_P0_L7_I4_T2__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T2 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T2/X_A0_U4_P0_L7_I4_T2__R09__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T2 | R16 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T2/X_A0_U4_P0_L7_I4_T2__R16__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T2 | R17 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T2/X_A0_U4_P0_L7_I4_T2__R17__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_U4_P0_L7_I4_T5 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P0_L7_I4_T5__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T5 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T5/X_A0_U4_P0_L7_I4_T5__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T5 | R02 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T5/X_A0_U4_P0_L7_I4_T5__R02__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T5 | R05 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T5/X_A0_U4_P0_L7_I4_T5__R05__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L7_I4_T5 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L7_I4_T5/X_A0_U4_P0_L7_I4_T5__R09__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P0_L8_I1+I2+I3+I8_T3__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | R01 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L8_I1+I2+I3+I8_T3/X_A0_U4_P0_L8_I1+I2+I3+I8_T3__R01__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | R05 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L8_I1+I2+I3+I8_T3/X_A0_U4_P0_L8_I1+I2+I3+I8_T3__R05__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L8_I1+I2+I3+I8_T3/X_A0_U4_P0_L8_I1+I2+I3+I8_T3__R09__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P0_L8_I1+I2+I3+I8_T3 | R17 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P0_L8_I1+I2+I3+I8_T3/X_A0_U4_P0_L8_I1+I2+I3+I8_T3__R17__BS2DCE__curated.png |
| contact_sheet_xz | X_A0_U4_P2_L3_I1_T2 | R01-R17 | both | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/contact_sheets/X_A0_U4_P2_L3_I1_T2__contact_sheet.png |
| curated_worst_track_xz_error | X_A0_U4_P2_L3_I1_T2 | R03 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P2_L3_I1_T2/X_A0_U4_P2_L3_I1_T2__R03__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P2_L3_I1_T2 | R03 | BSDC91 | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P2_L3_I1_T2/X_A0_U4_P2_L3_I1_T2__R03__BSDC91__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P2_L3_I1_T2 | R09 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P2_L3_I1_T2/X_A0_U4_P2_L3_I1_T2__R09__BS2DCE__curated.png |
| curated_worst_track_xz_error | X_A0_U4_P2_L3_I1_T2 | R10 | BS2DCE | runs/phase2_screening/20260604T163422Z/stage2_ranking_and_visual_audit/figs/curated/X_A0_U4_P2_L3_I1_T2/X_A0_U4_P2_L3_I1_T2__R10__BS2DCE__curated.png |
