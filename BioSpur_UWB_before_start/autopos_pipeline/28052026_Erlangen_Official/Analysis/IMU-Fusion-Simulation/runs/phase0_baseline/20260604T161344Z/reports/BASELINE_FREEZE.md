# Phase 0 Baseline Freeze

Generated: 2026-06-04T16:14:04.906298+00:00

## Summary

| experiment_id | track_count | trackmedian_err3d_p50_mm | trackmedian_err3d_p95_mm | legacy_deltaR_error_rms_mm | trackmedian_radius_error_abs_mm | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| B0_A0_U4_P0_T1 | 34 | 105.8 | 231.8 | 80.1 | 59.7 | BASELINE_UWB_ONLY |
| B1_A1_U4_P0_T1 | 34 | 106.2 | 200.4 | 33.3 | 33.6 | FUSION_NEUTRAL |
| B2_A2_U4_P0_T1 | 34 | 105.6 | 200.4 | 23.6 | 22.6 | FUSION_NEUTRAL |

## Validation Gates

| gate_id | status | blocking_next_phase | evidence |
| --- | --- | --- | --- |
| G4_fixed_time_alignment | PASS | False | R01-R17 pairing manifest and official beta_s alignment table |
| G6_multimetric_verdict | PASS | False | baseline summary emits P50/P95/deltaR/radius metrics and PNG figure index |

## Notes

B0/B1/B2 are recomputed from the existing official aligned sample tables. official_extra_analysis is read-only.
