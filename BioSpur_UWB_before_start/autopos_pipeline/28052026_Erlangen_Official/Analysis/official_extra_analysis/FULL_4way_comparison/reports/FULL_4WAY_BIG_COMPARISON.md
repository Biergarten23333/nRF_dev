# FULL 4-Way Big Comparison

Generated 2026-06-03T16:15:55.902754+00:00.

This is the combined comparison for the corrected FULL OptiTrack export and the three derived analysis paths: known Vicon anchors, full similarity scale-to-Vicon, and one-baseline scale correction. Static rows summarize 24 fixed positions; ROTO rows summarize 34 tag-tracks over 17 rotating captures using fixed capture-level time offsets from original FULL v4-io/T4.

## One-Screen Headline

- Original FULL static production `v4-io`: **74.0 mm median / 282.1 mm P95**.
- Original FULL static raw replay `v4-io/T4`: **69.7 / 173.9 mm**; `v4-io/T3`: **69.2 / 173.0 mm**.
- Static Vicon anchors + delaycal lower bound: **64.1 / 128.4 mm** for T4.
- Static one-baseline correction: best row **55.2 / 141.0 mm** (`v1-old`, F-H, T4, delaycal); useful v4-io E-H/T4 row **58.1 / 130.2 mm**.
- Original FULL ROTO `v4-io/T4`: **105.8 / 231.8 mm** track-median 3D P50/P95.
- Legacy no-groundtruth ROTO self-consistency for original FULL `v4-io/T4`: **25.9 mm dR RMS**, **13.7 mm turn-center repeatability median**, and **37.6 mm inner/outer center-separation median**.
- New OptiTrack/Vicon ROTO turn-center absolute 3D RMS is **72.1 mm** for original FULL, **72.7 mm** for Vicon anchors + delaycal, **76.7 mm** for full similarity scale + delaycal, and **77.1 mm** for one-baseline E-H + delaycal.
- Apples-to-apples comparison is static raw replay `v4-io/T4` versus ROTO `v4-io/T4`: ROTO is worse (**69.7 -> 105.8 mm** median, **173.9 -> 231.8 mm** P95). Do not compare the ROTO track-level P95 directly against static production P95.
- ROTO Vicon anchors + delaycal: **105.6 / 200.4 mm**, so dynamic ROTO median does **not** collapse like static.
- ROTO best overall row: **100.1 / 220.5 mm** (`v4-io`, B-C one-baseline with solver delay, T4), but its P95 is worse than Vicon+delaycal.

## Headline Table

| case | type | layout_or_anchor | delay_mode | tag_solver | static_3d_p50_mm | static_3d_p95_mm | static_xy_p95_mm | static_z_p95_mm | roto_3d_p50_mm | roto_3d_p95_mm | roto_xy_p95_mm | roto_z_p95_mm | opti_turn_center_abs_error_p50_mm | opti_turn_center_abs_error_rms_mm | legacy_deltaR_rms_mm | legacy_turn_center_repeatability_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FULL original production v4-io | static production | AutoPos v4-io rigid no-scale | solver_delay | production | 74.0 | 282.1 |  |  |  |  |  |  |  |  |  |  |
| FULL original raw replay v4-io/T4 | static + ROTO | AutoPos v4-io rigid no-scale | solver_delay | T4 | 69.7 | 173.9 |  |  | 105.8 | 231.8 | 167.5 | 187.4 | 69.1 | 72.1 | 25.9 | 13.7 |
| FULL original raw replay v4-io/T3 | static replay best-ish | AutoPos v4-io rigid no-scale | solver_delay | T3 | 69.2 | 173.0 |  |  |  |  |  |  |  |  |  |  |
| Vicon anchors + delaycal / T4 | static control/ablation | vicon_truth | vicon_inter_anchor_delaycal | T4 | 64.1 | 128.4 | 81.3 | 112.8 |  |  |  |  |  |  |  |  |
| Full similarity scale + delaycal / v4-io/T4 | static control/ablation | solver_similarity_scale_to_vicon | scaled_layout_inter_anchor_delaycal | T4 | 67.1 | 132.6 | 80.5 | 117.4 |  |  |  |  |  |  |  |  |
| One-baseline E-H + delaycal / v4-io/T4 | static control/ablation | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | 58.1 | 130.2 | 72.9 | 106.8 |  |  |  |  |  |  |  |  |
| One-baseline best static row | static control/ablation | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | 55.2 | 141.0 | 69.2 | 121.4 |  |  |  |  |  |  |  |  |
| ROTO Vicon anchors + delaycal / T4 | ROTO control/ablation | vicon_truth | vicon_inter_anchor_delaycal | T4 |  |  |  |  | 105.6 | 200.4 | 137.2 | 172.0 | 69.8 | 72.7 | 18.0 | 13.3 |
| ROTO full similarity scale + delaycal / v4-io/T4 | ROTO control/ablation | solver_similarity_scale_to_vicon | scaled_layout_inter_anchor_delaycal | T4 |  |  |  |  | 110.5 | 200.7 | 139.5 | 176.5 | 71.3 | 76.7 | 15.6 | 13.3 |
| ROTO one-baseline E-H + delaycal / v4-io/T4 | ROTO control/ablation | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 |  |  |  |  | 106.2 | 200.4 | 138.2 | 170.4 | 75.2 | 77.1 | 13.4 | 13.7 |
| ROTO best overall row | ROTO control/ablation | one_baseline_scale | solver_delay | T4 |  |  |  |  | 100.1 | 220.5 | 159.3 | 176.2 | 69.4 | 71.4 |  |  |

## Layout Absolute Accuracy, Original FULL

| version | reflection_allowed_rms_3d_mm | reflection_allowed_horizontal_rms_mm | reflection_allowed_vertical_rms_mm | shape_rms_mm | similarity_scale | similarity_rms_3d_mm |
| --- | --- | --- | --- | --- | --- | --- |
| v1-old | 101.3 | 93.8 | 38.1 | 140.6 | 1.0 | 50.1 |
| v4-io | 105.4 | 86.8 | 59.8 | 136.3 | 1.0 | 67.1 |
| v2 | 136.5 | 109.0 | 82.1 | 192.0 | 0.9 | 60.3 |
| v3-lite | 136.6 | 108.8 | 82.6 | 192.0 | 0.9 | 60.9 |
| v3-full | 143.4 | 127.5 | 65.8 | 151.6 | 1.0 | 125.3 |

Interpretation: corrected FULL still has the same scale story. `v4-io` is the production solver, but `v1-old` remains very close in rigid layout RMS; similarity scale is diagnostic only.

## Static: Original FULL

| source | layout_solver | tag_method | err_3d_median_mm | err_3d_p95_mm | err_3d_rms_mm | err_horizontal_median_mm | err_vertical_median_mm | d3_std_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| raw replay | v4-io | T3 | 69.2 | 173.0 | 106.6 | 45.5 | 47.8 | 58.7 |
| raw replay | v4-io | T4 | 69.7 | 173.9 | 108.9 | 37.5 | 60.0 | 67.4 |
| raw replay | v4-io | T1 | 70.8 | 283.7 | 139.3 | 42.7 | 64.0 | 58.6 |
| raw replay | v4-io | T2 | 71.1 | 282.9 | 139.0 | 42.8 | 63.9 | 58.9 |
| production | v4-io | production | 74.0 | 282.1 | 139.6 | 42.3 | 65.3 |  |
| raw replay | v2 | T3 | 74.0 | 205.3 | 106.2 | 49.0 | 60.1 | 63.3 |
| raw replay | v3-lite | T3 | 74.7 | 205.3 | 106.2 | 49.2 | 64.1 | 63.1 |
| raw replay | v2 | T4 | 76.5 | 168.2 | 104.9 | 42.6 | 66.0 | 62.7 |
| raw replay | v3-lite | T4 | 77.1 | 168.1 | 104.9 | 43.2 | 66.5 | 62.7 |
| production | v2 | production | 81.1 | 248.0 | 135.7 | 44.3 | 74.0 |  |
| raw replay | v2 | T2 | 81.2 | 246.4 | 134.3 | 44.4 | 73.3 | 61.8 |
| raw replay | v2 | T1 | 81.6 | 247.5 | 134.7 | 43.5 | 74.0 | 61.9 |
| production | v3-lite | production | 81.8 | 248.5 | 135.9 | 44.6 | 74.6 |  |
| raw replay | v3-lite | T2 | 82.0 | 247.0 | 134.5 | 44.7 | 74.1 | 62.0 |
| raw replay | v3-lite | T1 | 82.3 | 247.9 | 134.9 | 43.8 | 74.6 | 62.1 |
| raw replay | v3-full | T3 | 89.8 | 250.3 | 129.4 | 46.2 | 70.4 | 56.2 |
| raw replay | v3-full | T4 | 98.2 | 237.0 | 132.9 | 40.8 | 89.4 | 61.5 |
| raw replay | v3-full | T1 | 116.2 | 291.6 | 160.2 | 48.8 | 109.7 | 64.5 |
| raw replay | v3-full | T2 | 116.8 | 291.6 | 159.8 | 47.5 | 110.0 | 65.3 |
| production | v3-full | production | 120.6 | 293.8 | 160.7 | 48.8 | 113.7 |  |

## Static: Derived 4-Way Ablations, Top Rows

| experiment | layout_solver | layout_variant | delay_mode | tag_method | scale_source | baseline_pair | err_3d_median_mm | err_3d_p95_mm | err_horizontal_xz_p95_mm | err_vertical_y_p95_mm | d3_std_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-H | F-H | 55.2 | 141.0 | 69.2 | 121.4 | 63.8 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | C-F | C-F | 56.3 | 138.4 | 70.7 | 119.6 | 64.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-C | B-C | 56.4 | 132.2 | 74.1 | 114.6 | 64.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | D-F | D-F | 56.5 | 132.3 | 73.9 | 114.6 | 64.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-F | B-F | 56.7 | 137.0 | 71.3 | 118.6 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | C-G | C-G | 56.9 | 130.7 | 75.0 | 112.8 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-H | E-H | 57.3 | 129.7 | 75.7 | 111.2 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | C-G | C-G | 57.3 | 126.6 | 69.3 | 112.0 | 61.9 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-G | F-G | 57.4 | 133.8 | 72.8 | 116.2 | 64.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-G | B-G | 57.4 | 129.1 | 76.1 | 110.4 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-E | B-E | 57.5 | 129.3 | 77.0 | 108.9 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-F | E-F | 57.6 | 134.4 | 72.6 | 116.7 | 64.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-H | E-H | 57.6 | 127.4 | 70.0 | 110.5 | 61.8 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | C-G | C-G | 57.6 | 127.3 | 71.7 | 112.3 | 61.9 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | B-E | B-E | 57.7 | 129.4 | 70.9 | 109.5 | 60.5 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | B-E | B-E | 57.8 | 129.1 | 71.7 | 109.2 | 60.5 |
| one_baseline | v3-lite | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | B-E | B-E | 57.8 | 128.8 | 70.5 | 110.1 | 60.5 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-G | E-G | 57.8 | 129.3 | 70.9 | 110.0 | 60.5 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | B-G | B-G | 57.8 | 127.7 | 70.3 | 109.8 | 61.8 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | D-E | D-E | 57.8 | 129.5 | 77.3 | 108.0 | 64.2 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | E-H | E-H | 57.9 | 128.1 | 72.1 | 110.6 | 61.8 |
| one_baseline | v3-lite | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-G | E-G | 57.9 | 128.9 | 70.6 | 110.9 | 60.5 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | E-G | E-G | 58.0 | 128.9 | 71.8 | 109.5 | 60.5 |
| one_baseline | v3-lite | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | B-E | B-E | 58.0 | 128.6 | 71.3 | 109.8 | 60.6 |
| one_baseline | v2 | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-H | E-H | 58.0 | 127.9 | 69.8 | 100.7 | 63.0 |

Static interpretation:

- Vicon anchors without delaycal, or Vicon anchors with the AutoPos delay vector, are bad. AutoPos delay is layout-coupled, not portable physical antenna-delay calibration.
- Scale correction alone is not enough; it becomes useful only when paired with re-estimated endpoint delay on the corrected layout.
- One independent baseline is a strong engineering path because it attacks scale/delay coupling without requiring full Vicon layout.

## ROTO: Original FULL Solver Matrix

| layout | tag_method | err3d_p50_track_median_mm | err3d_p95_track_median_mm | err_horizontal_xz_p95_track_median_mm | err_vertical_y_p95_track_median_mm | turn_center_abs_error_3d_track_median_mm | radius_error_abs_track_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v4-io | T4 | 105.8 | 231.8 | 167.5 | 187.4 | 69.1 | 51.3 |
| v4-io | T2 | 106.5 | 232.5 | 168.1 | 181.6 | 69.0 | 61.7 |
| v4-io | T1 | 106.7 | 233.6 | 168.4 | 182.1 | 69.1 | 61.6 |
| v4-io | T3 | 110.0 | 236.0 | 171.5 | 189.1 | 71.4 | 58.2 |
| v3-lite | T4 | 110.4 | 237.1 | 165.2 | 190.1 | 78.0 | 47.0 |
| v2 | T4 | 110.4 | 237.0 | 164.5 | 190.3 | 77.7 | 47.0 |
| v2 | T1 | 110.8 | 231.6 | 169.4 | 186.1 | 77.1 | 54.3 |
| v2 | T2 | 110.9 | 231.9 | 170.1 | 186.1 | 77.1 | 54.2 |
| v3-lite | T1 | 111.0 | 231.5 | 170.4 | 186.5 | 76.8 | 54.3 |
| v3-lite | T2 | 111.2 | 232.2 | 170.7 | 187.5 | 76.8 | 54.3 |
| v3-lite | T3 | 113.4 | 235.2 | 168.6 | 186.0 | 78.9 | 52.0 |
| v2 | T3 | 113.5 | 234.7 | 167.7 | 185.4 | 78.8 | 51.9 |

## ROTO: Derived 4-Way Ablations, Top Rows

| experiment | layout_solver | layout_variant | delay_mode | tag_method | scale_source | baseline_pair | err3d_p50_track_median_mm | err3d_p95_track_median_mm | err_horizontal_xz_p95_track_median_mm | err_vertical_y_p95_track_median_mm | turn_center_abs_error_3d_track_median_mm | radius_error_abs_track_median_mm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T4 | B-C | B-C | 100.1 | 220.5 | 159.3 | 176.2 | 69.4 | 44.6 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T2 | B-C | B-C | 101.8 | 216.4 | 160.7 | 173.4 | 68.9 | 52.4 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T1 | B-C | B-C | 101.9 | 216.3 | 160.5 | 172.7 | 69.1 | 52.3 |
| one_baseline | v4-io | one_baseline_scale | solver_delay | T3 | B-C | B-C | 102.2 | 221.3 | 163.5 | 180.7 | 70.9 | 48.8 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 102.7 | 203.3 | 146.4 | 169.7 | 78.9 | 26.5 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | E-F | E-F | 102.7 | 202.7 | 145.5 | 170.1 | 78.9 | 24.7 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | B-C | B-C | 102.8 | 211.3 | 147.9 | 170.9 | 78.4 | 36.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-H | F-H | 103.0 | 204.9 | 135.7 | 173.4 | 73.6 | 20.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | E-F | E-F | 103.1 | 205.5 | 148.3 | 171.2 | 75.9 | 28.1 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | E-F | E-F | 103.2 | 205.2 | 148.6 | 170.8 | 76.0 | 27.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | B-C | B-C | 103.3 | 212.7 | 152.2 | 176.9 | 77.5 | 38.6 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 103.3 | 205.7 | 149.5 | 172.2 | 76.4 | 29.1 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | B-C | B-C | 103.3 | 211.8 | 152.0 | 176.5 | 77.5 | 38.8 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T3 | F-H | F-H | 103.3 | 201.3 | 135.7 | 173.9 | 75.9 | 21.9 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | F-H | F-H | 103.4 | 205.6 | 149.5 | 171.6 | 76.3 | 29.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | C-F | C-F | 103.4 | 203.3 | 136.5 | 173.9 | 73.4 | 20.7 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-G | F-G | 103.4 | 201.6 | 135.2 | 173.8 | 73.1 | 19.6 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | D-F | D-F | 103.5 | 201.6 | 135.5 | 173.4 | 73.0 | 19.9 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T2 | F-H | F-H | 103.5 | 203.8 | 137.9 | 170.6 | 72.9 | 22.5 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-F | B-F | 103.5 | 203.2 | 136.4 | 173.6 | 73.3 | 20.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-C | B-C | 103.5 | 201.6 | 135.5 | 173.3 | 73.0 | 20.0 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T1 | F-H | F-H | 103.5 | 205.1 | 138.8 | 171.7 | 72.9 | 22.3 |
| one_baseline | v1-old | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | E-F | E-F | 103.6 | 202.3 | 135.2 | 173.7 | 73.2 | 19.5 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | B-C | B-C | 103.7 | 216.7 | 147.9 | 173.8 | 78.3 | 30.7 |
| one_baseline | v4-io | one_baseline_scale | one_baseline_layout_inter_anchor_delaycal | T4 | F-H | F-H | 103.9 | 206.5 | 147.1 | 171.8 | 77.0 | 23.5 |

ROTO interpretation:

- In the same `v4-io/T4` solver line, ROTO is worse than static raw replay. The apparently lower ROTO P95 versus static production is not an accuracy improvement; it mixes a different static output path with a track-level ROTO summary.
- Static and ROTO tell different stories. Static lower bound drops to about 6 cm median; ROTO stays around 10 cm median even with Vicon anchors + delaycal.
- The old no-groundtruth ROTO metrics are still useful as self-consistency checks: original FULL `v4-io/T4` has 25.9 mm dR RMS and 13.7 mm turn-center repeatability median.
- The new OptiTrack/Vicon turn-center absolute error is a different metric: it stays around 7--8 cm across original FULL and the derived FULL controls.
- Therefore ROTO error is not explained by layout scale alone. It includes motion, time alignment residuals, rotating-wand ranging behavior, and possibly tag/antenna geometry effects.
- One-baseline can slightly improve ROTO median in some rows, but the clean Vicon+delaycal control has better P95. For deployment claims, quote both median and P95.

## ROTO Dynamic Diagnostics

| case | speed_best_bin | speed_best_p50 | speed_worst_bin | speed_worst_p50 | phase_best_bin | phase_best_p50 | phase_worst_bin | phase_worst_p50 | radius_p50_median | radius_p95_median | rel_p50_median | rel_p95_median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| FULL original v4-io/T4 | mid 68.0-84.1 deg/s | 101.9 | fast > 84.1 deg/s | 103.2 | 90-180 | 100.4 | 180-270 | 107.5 | 59.2 | 171.3 | 104.6 | 254.8 |
| Vicon anchors + delaycal | mid 68.0-84.1 deg/s | 101.3 | slow <= 68.0 deg/s | 108.4 | 270-360 | 97.2 | 90-180 | 106.9 | 50.5 | 143.5 | 59.6 | 174.5 |
| Full similarity scale + delaycal | mid 68.0-84.1 deg/s | 104.6 | slow <= 68.0 deg/s | 110.5 | 270-360 | 100.1 | 180-270 | 110.3 | 50.6 | 142.6 | 61.3 | 180.3 |
| One-baseline E-H + delaycal | mid 68.0-84.1 deg/s | 102.1 | slow <= 68.0 deg/s | 107.1 | 270-360 | 98.1 | 180-270 | 109.0 | 51.9 | 140.6 | 64.4 | 174.3 |

Dynamic interpretation: speed/phase bins move the median only modestly. Two-wand relative-distance consistency improves strongly when using Vicon anchors/delaycal versus original FULL, but absolute trajectory error still remains about 10 cm median.

## Recommended Claims

1. Keep the unfiltered original FULL result as the calibration-level validation number: about **7 cm median** static replay and **7.4 cm production median**, with a wide vertical/tail component.
2. Vicon-anchor and scale/baseline ablations are diagnostic controls, not field claims unless the corresponding field measurement exists.
3. The strongest engineering recommendation is independent baseline calibration plus delay re-estimation.
4. ROTO absolute validation should be stated conservatively: about **10 cm median dynamic absolute trajectory error**, with P95 around **20--23 cm** depending on the control row.
5. Always split horizontal XZ and vertical Y in the detailed report.

## Output Files

- `tables/full_4way_headline_comparison.csv`
- `reports/FULL_4WAY_BIG_COMPARISON.md`
- `reports/STATIC_4WAY_COMPARISON.md`
- `reports/ROTO_4WAY_COMPARISON.md`
- Derived dynamic reports under each `*/roto_absolute/dynamic_diagnostics/reports/ROTO_DYNAMIC_DIAGNOSTICS.md`
