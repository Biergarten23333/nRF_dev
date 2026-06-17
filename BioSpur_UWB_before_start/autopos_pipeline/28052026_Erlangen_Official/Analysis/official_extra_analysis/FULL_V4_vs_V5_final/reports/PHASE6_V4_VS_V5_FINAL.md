# PHASE 6 - V4 vs V5 Final

## Executive Summary

V5 deployable LOO static is 67.8 mm median / 153.6 mm P95, slightly better than the V4 production headline while keeping the result deployable. The V5 scale problem is effectively fixed: Sim3 scale is 1.010, and rigid anchor RMSE is 63.0 mm. ROTO remains a best-fit-aligned dynamic floor, so it is useful for relative comparison but not a hardware-synchronized truth claim.

## Anchor-Side

V5 removes the V4 scale compression: V4 Sim3 scale is about 0.958, while V5 is near unity. Rigid alignment also improves substantially, so V5 should be treated as the better self-calibrated anchor geometry baseline.

| layout | sim3_scale | sim3_anchor_rmse_mm | rigid_anchor_rmse_mm | rigid_anchor_median_mm | rigid_anchor_p95_mm |
| --- | --- | --- | --- | --- | --- |
| v4-io | 0.958 | 67.121 | 105.420 | 92.771 | 156.886 |
| v5-commonmode | 1.010 | 60.340 | 62.992 | 59.452 | 93.317 |

V5 anchor delay uses common-mode plus differential terms; the table below is the direct V4-to-V5 delay transfer comparison.

| anchor_label | v4_d_anchor_mm | v5_d_anchor_mm | v5_minus_v4_d_anchor_mm | v5_common_mode_mm | v5_differential_e_i_mm |
| --- | --- | --- | --- | --- | --- |
| A | 0.000 | 99.634 | 99.634 | 111.985 | -12.351 |
| B | 37.127 | 113.688 | 76.561 | 111.985 | 1.703 |
| C | 60.000 | 127.338 | 67.338 | 111.985 | 15.353 |
| D | 60.000 | 124.511 | 64.511 | 111.985 | 12.526 |
| E | 31.143 | 109.625 | 78.482 | 111.985 | -2.359 |
| F | 27.043 | 111.362 | 84.319 | 111.985 | -0.623 |
| G | 27.559 | 100.042 | 72.483 | 111.985 | -11.943 |
| H | 32.419 | 109.679 | 77.260 | 111.985 | -2.306 |

## Tag-Side

D0 is clearly not enough for V5 static evaluation: median error is 109.5 mm. The LOO tag delay 49.621 mm gives the deployable result; the sweep optimum is 56.6 mm median but is in-sample and diagnostic only.

| case | median_3d_mm | p95_3d_mm | rmse_3d_mm | source |
| --- | --- | --- | --- | --- |
| V4 production static v4-io/T4 | 72.700 | 171.500 | 109.800 | FULL_4way_comparison report |
| V5 D0 | 109.515 | 223.859 | 140.520 | Phase2 |
| V5 D_LOO_CV | 67.849 | 153.635 | 82.799 | Phase2 |
| V5 D_sweep_opt | 56.619 | 128.054 | 74.484 | Phase2 |

## Oracle Ceiling

The known-anchor oracle remains close to the deployed V5 static result. That means most remaining static error is not simply V5 anchor scale; it is tag delay, residual ranging bias, NLOS, and solver/data effects.

| case | median_3d_mm | p95_3d_mm | rmse_3d_mm | source |
| --- | --- | --- | --- | --- |
| old Vicon anchor control | 64.100 | 128.400 |  | FULL_AutoPos_align_to_Vicon |
| updated V5-era Vicon cm oracle | 63.392 | 159.818 | 82.004 | Phase3 C_Vicon_refit_cm + D_LOO_CV |

## Transfer Matrix

The diagonal cells separate the three intended operating modes. The best off-diagonal cells show what can be achieved only when correction sources are mixed or D_tag is optimized in-sample.

| layout_source | correction_source | tag_delay_mode | tag_delay_value_mm | median_3d_mm | p95_3d_mm | rmse_3d_mm |
| --- | --- | --- | --- | --- | --- | --- |
| L_Vicon | C_Vicon_cm | D_LOO_CV | 49.621 | 63.392 | 159.818 | 82.004 |
| L_V4 | C_V4 | D0 | 0.000 | 72.688 | 171.498 | 109.843 |
| L_V5 | C_V5 | D_LOO_CV | 49.621 | 67.849 | 153.635 | 82.799 |

Best transfer-matrix cells by median 3D error:

| layout_source | correction_source | tag_delay_mode | tag_delay_value_mm | median_3d_mm | p95_3d_mm | rmse_3d_mm |
| --- | --- | --- | --- | --- | --- | --- |
| L_V4 | C_none | D_sweep_opt | 88.000 | 51.119 | 112.681 | 68.718 |
| L_Vicon | C_Vicon_cm | D_sweep_opt | 68.000 | 52.772 | 139.594 | 74.598 |
| L_V4 | C_none | D_oracle_91 | 91.153 | 53.198 | 117.458 | 68.629 |
| L_V5 | C_V5 | D_sweep_opt | 76.000 | 56.619 | 128.054 | 74.484 |
| L_V4 | C_V4 | D_LOO_CV | 49.621 | 57.921 | 110.585 | 74.372 |
| L_V4 | C_V4 | D_sweep_opt | 50.000 | 57.951 | 110.338 | 74.319 |
| L_V5 | C_V5 | D_oracle_91 | 91.153 | 58.591 | 143.198 | 77.880 |
| L_V4 | C_V5 | D0 | 0.000 | 59.833 | 117.660 | 75.281 |

## Single-Baseline

V5 gets only a limited gain from one external baseline because its scale is already near unity. V4 still shows its known F-H baseline improvement, which is why the single-baseline result is mostly a V4 scale-fix story rather than a V5 requirement.

| layout | best_pair | best_median_3d_mm | best_p95_3d_mm | best_scale_factor |
| --- | --- | --- | --- | --- |
| V5 | B-D | 58.364 | 128.311 | 1.009 |
| V4 | F-H | 55.189 | 140.992 | 0.978 |

## ROTO Dynamic

ROTO with V5 LOO improves over V5 D0 and is close to the V4 dynamic floor, but all values remain best-fit aligned with no hardware time sync. Treat this as a motion-floor diagnostic, not absolute synchronized tracking accuracy.

| case | median_3d_mm | p95_3d_mm | rmse_3d_mm | source |
| --- | --- | --- | --- | --- |
| V4 production ROTO v4-io/T4 | 105.800 | 231.800 |  | FULL/roto_absolute |
| V5 ROTO D0 | 126.394 | 276.249 | 167.274 | Phase2 |
| V5 ROTO D_LOO_CV | 101.485 | 214.369 | 126.226 | Phase2 |

## NLOS Fingerprint

Static and dynamic rho fingerprints identify which anchors generate positive range spikes. These anchors should be inspected before attributing the remaining dynamic floor to geometry or tag delay alone.

| anchor_label | static_rho_rms_mm | dynamic_rho_rms_mm | static_positive_spike_rate_gt100 | dynamic_positive_spike_rate_gt100 | static_positive_spike_rate_gt150 | dynamic_positive_spike_rate_gt150 |
| --- | --- | --- | --- | --- | --- | --- |
| D | 126.005 | 133.261 | 0.233 | 0.263 | 0.173 | 0.168 |
| F | 117.921 | 138.021 | 0.210 | 0.212 | 0.144 | 0.123 |
| A | 124.257 | 128.836 | 0.105 | 0.166 | 0.090 | 0.106 |
| H | 87.143 | 116.104 | 0.041 | 0.157 | 0.023 | 0.097 |
| B | 96.124 | 94.931 | 0.118 | 0.118 | 0.100 | 0.073 |
| E | 90.652 | 121.873 | 0.135 | 0.110 | 0.078 | 0.061 |
| G | 118.120 | 103.746 | 0.096 | 0.103 | 0.084 | 0.057 |
| C | 115.978 | 94.452 | 0.080 | 0.092 | 0.047 | 0.060 |

## Runtime Summary

| phase | elapsed_s | mean_cpu_percent | max_cpu_percent | physical_cores | logical_cores | workers |
| --- | --- | --- | --- | --- | --- | --- |
| Phase 1 | 0.002 | 10.100 | 10.100 | 6 | 12 | 6 |
| Phase 2 | 84.157 | 50.453 | 99.700 | 6 | 12 | 6 |
| Phase 3 | 27.500 | 30.177 | 91.600 | 6 | 12 | 6 |
| Phase 4 | 26.305 | 67.868 | 80.500 | 6 | 12 | 6 |
| Phase 5 | 675.867 | 57.650 | 80.100 | 6 | 12 | 6 |
| Phase 6 | 0.018 | 13.200 | 13.200 | 6 | 12 | 6 |

## Remaining Gaps / Future Work

- Add hardware time synchronization before promoting ROTO from best-fit-aligned diagnostic to absolute dynamic accuracy.
- Keep D_LOO_CV as the deployable tag-delay result; label D_sweep_opt and D_oracle rows as diagnostics only.
- Use the rho/NLOS tables to inspect high-spike anchors and links before claiming the dynamic error floor is solved.
- If more external references are allowed, validate V5 on an independent holdout rather than only the 24-position LOO/sweep setup.
