# Phase 1 Vertical Slice

Generated: 2026-06-04T16:15:15.147648+00:00

## Summary

| experiment_id | track_count | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | legacy_deltaR_error_rms_mm | trackmedian_radius_error_abs_mm | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| B0_A0_U4_P0_T1 | 34 | 105.8 | 231.8 | 80.1 | 59.7 | BASELINE_UWB_ONLY |
| X_A0_L0_I0_T11 | 34 | 0.0 | 0.0 | 247.3 | 0.0 | IMU_ONLY_PERFECT_ORACLE |
| X_A0_L2_I3_T11 | 34 | 5748.4 | 22721.4 | 27912.5 | 16674.8 | IMU_ONLY_DRIFTS_AS_EXPECTED |
| X_A0_U4_P0_L0_I0_T2 | 34 | 66.2 | 97.3 | 596.8 | 17.5 | FUSION_HELPS_DEPLOYABLE |
| X_A0_U4_P0_L2_I3_T3 | 34 | 98.3 | 194.8 | 179.8 | 55.4 | FUSION_HELPS_DEPLOYABLE |
| X_A0_R2_L0_I0_T6 | 34 | 89.3 | 176.5 | 233.2 | 53.4 | FUSION_HELPS_DIAGNOSTIC_ONLY |
| X_A0_R2_L2_I3_T6 | 34 | 97.4 | 185.1 | 210.2 | 53.6 | FUSION_HELPS_DIAGNOSTIC_ONLY |

## Validation Gates

| gate_id | status | blocking_next_phase | evidence |
| --- | --- | --- | --- |
| G1_frame_gravity | PASS | False | frame_conventions.md exists=True; L0/T11 P95=0.000 mm |
| G2_drift_from_L_properties | PASS | False | sensors.yaml exists=True; L2/T11 endpoint drift median=25417.7 mm |
| G3_range_bias_policy | PASS_OR_LIMITED_PROTO | True | range_bias_policy.md exists; T6 raw availability audited but solved-position proxy is used |
| G4_fixed_time_alignment | PASS | False | all Phase 1 rows use official aligned sample grid; no beta_s refit |
| G5_noise_seed_repeats | PASS_DEBUG_SINGLE_SEED | True | recorded stochastic seeds=34; final claims still require multiseed |
| G6_multimetric_verdict | PASS | False | summary metric columns present=True; figure_count=245 |

## Notes

T6 rows are limited prototypes in this run: raw-range availability is audited, but solved-position proxy updates are used until the Phase 2 range-bias policy is implemented.
