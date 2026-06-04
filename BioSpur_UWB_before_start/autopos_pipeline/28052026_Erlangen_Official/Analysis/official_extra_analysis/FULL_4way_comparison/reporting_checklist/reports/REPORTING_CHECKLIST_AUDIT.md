# Reporting Checklist Audit

Generated 2026-06-04T10:58:36.070100+00:00.

This report maps the requested anchor/tag positioning reporting checklist onto the existing FULL and 4-way analysis outputs. It aggregates existing tables, incorporates the resilience-gap audit outputs, and marks remaining missing evidence explicitly.

## Core Tables

### Anchor Layout Absolute
| method | se3_rmse_mm | se3_median_mm | se3_p95_mm | x_rmse_mm | y_rmse_mm | z_rmse_mm | scale_bias_pct | sim3_rmse_mm | pairwise_distance_rmse_mm | worst_anchor |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AutoPos v1-old | 101.27 | 84.57 | 154.09 | 48.54 | 38.14 | 80.28 | -4.50 | 50.07 | 140.64 | A |
| AutoPos v2 | 136.45 | 127.79 | 183.98 | 64.78 | 82.07 | 87.68 | -6.15 | 60.32 | 192.03 | A |
| AutoPos v3-full | 143.45 | 118.91 | 211.73 | 91.31 | 65.82 | 88.92 | -3.61 | 125.28 | 151.62 | D |
| AutoPos v3-lite | 136.60 | 127.95 | 184.18 | 64.66 | 82.58 | 87.51 | -6.15 | 60.86 | 191.97 | A |
| AutoPos v4-io | 105.42 | 92.77 | 156.89 | 52.89 | 59.77 | 68.88 | -4.17 | 67.12 | 136.31 | A |

### Anchor Repeatability
| method_or_split | coordinate_sd_median_mm | pairwise_distance_sd_median_mm | delay_sd_mm | worst_anchor_sd_mm | status |
| --- | --- | --- | --- | --- | --- |
| OptiTrack anchor truth repeated static files | 0.28 | 0.24 |  | 1.45 | measured |
| AutoPos solver anchor sigma prior | 24.46 |  |  | 25.20 | proxy_only |
| Raw-pair bootstrap original_selfcal | 1.02 | 0.76 | 0.56 | 1.13 | bootstrap_diagnostic |
| Raw-pair bootstrap vicon_truth_delaycal | 0.00 | 0.00 | 0.45 | 0.00 | bootstrap_diagnostic |
| Raw-pair bootstrap scale_to_vicon_delaycal | 0.97 | 0.70 | 0.53 | 1.08 | bootstrap_diagnostic |
| Raw-pair bootstrap one_baseline_EH_delaycal | 1.20 | 1.24 | 0.54 | 1.29 | bootstrap_diagnostic |
| AutoPos v4-io residual delay structure |  |  | 19.34 |  | structure_not_repeatability |
| Independent repeated AutoPos layout runs |  |  |  |  | not_measured |

### Tag Static
| layout_delay_config | repeatability_3d_sd_median_mm | absolute_3d_rmse_mm | x_rmse_mm | y_rmse_mm | z_rmse_mm | absolute_3d_p95_mm |
| --- | --- | --- | --- | --- | --- | --- |
| Original FULL production v4-io |  | 139.55 | 38.48 | 130.54 | 30.88 | 282.13 |
| Original FULL raw replay v4-io/T4 | 67.44 | 108.91 | 37.53 | 97.97 | 29.25 | 173.93 |
| Original FULL filtered static v4-io/T4+F5 | 18.67 | 109.53 | 36.99 | 98.93 | 29.00 | 175.73 |
| Original FULL filtered static v4-io/T3+F5 | 20.98 | 105.36 | 43.57 | 89.77 | 33.81 | 169.93 |
| Vicon truth anchors + delaycal/T4 | 59.78 | 77.67 | 40.24 | 60.98 | 26.37 | 128.38 |
| Full Sim(3) scale-to-Vicon + delaycal/T4 | 63.30 | 81.61 | 41.30 | 65.19 | 26.55 | 132.61 |
| One-baseline E-H + delaycal/T4 | 63.53 | 78.41 | 37.62 | 64.63 | 23.56 | 130.22 |

### Tag Dynamic
| layout_delay_config | ate_rmse_mm | rpe_rmse_mm | ate_p95_mm | drift_abs_slope_median_mm_per_min | effective_packet_loss_median_pct | effective_update_rate_median_hz |
| --- | --- | --- | --- | --- | --- | --- |
| Original FULL raw replay v4-io/T4 | 141.27 | 134.45 | 256.93 | 1.20 | 0.00 | 9.73 |
| Vicon truth anchors + delaycal/T4 | 125.40 | 112.79 | 206.19 | 1.22 | 0.00 | 9.73 |
| Full Sim(3) scale-to-Vicon + delaycal/T4 | 127.76 | 111.71 | 207.96 | 1.18 | 0.00 | 9.73 |
| One-baseline E-H + delaycal/T4 | 126.69 | 113.36 | 207.44 | 1.40 | 0.00 | 9.73 |

### Ablation
| layout | delay_or_bias | tag_rmse_mm | tag_p95_mm | anchor_or_pair_residual_rms_mm | interpretation |
| --- | --- | --- | --- | --- | --- |
| AutoPos v4-io rigid | solver residual corrections | 108.93 | 174.07 | 48.17 | current self-cal joint solution; geometry and layout-level residual delay corrections are coupled |
| AutoPos v4-io Sim(3)-scaled | solver residual corrections | 227.61 | 384.94 | 48.17 | shows AutoPos residual corrections are not transferable after changing layout scale |
| AutoPos v4-io Sim(3)-scaled | re-estimated inter-anchor residual corrections | 81.61 | 132.61 | 59.94 | separates global scale correction from residual delay re-estimation |
| Vicon/OptiTrack truth anchors | none | 311.32 | 453.45 | 221.08 | optical geometry alone is not enough; endpoint delays dominate |
| Vicon/OptiTrack truth anchors | AutoPos solver residual corrections | 252.17 | 394.57 | 221.08 | tests delay transferability; poor result means solver residual corrections are layout-conditioned |
| Vicon/OptiTrack truth anchors | re-estimated inter-anchor residual corrections | 77.67 | 128.38 | 59.94 | known-anchor lower-bound control |
| One-baseline E-H v4-io | re-estimated inter-anchor residual corrections | 78.41 | 130.22 |  | practical field-measurable one-baseline scale/delay correction |
| PANS/manual | corresponding delay |  |  |  | not found in current FULL dataset; add if a practical manual/PANS baseline exists |

## Coverage
| domain | check | status | evidence | note |
| --- | --- | --- | --- | --- |
| Anchor | Inter-anchor range residual | covered | checklist_anchor_layout_absolute.csv + checklist_ablation.csv | AutoPos internal residual and Vicon delaycal residual are reported. |
| Anchor | Inter-anchor distance error | covered | checklist_anchor_layout_absolute.csv | Pairwise distance MAE/RMSE and relative MAE are included. |
| Anchor | SE(3)-aligned anchor error | covered | checklist_anchor_layout_absolute.csv | Frame-normalized rigid/reflection alignment is used; note explains handedness. |
| Anchor | Axis-wise SE(3) anchor error | covered | checklist_anchor_layout_absolute.csv | X/Y/Z RMSE columns included. |
| Anchor | Sim(3)-aligned residual and scale bias | covered | checklist_anchor_layout_absolute.csv | Sim(3) scale factor, scale bias %, and Sim(3) RMSE included. |
| Anchor | Per-anchor error ranking | partial | layout_abs_errors_all8.csv + checklist_anchor_layout_absolute.csv | Worst anchor is summarized; full per-anchor table already exists. |
| Anchor | Per-axis scale / anisotropy | partial | checklist_anchor_layout_absolute.csv | Horizontal vs vertical-sensitive pairwise RMSE proxy included, not a full anisotropic scale fit. |
| Anchor | Repeatability of layout | covered_bootstrap | resilience_gap_audit/tables/bootstrap_layout_repeatability.csv + checklist_anchor_repeatability.csv | Raw-pair bootstrap repeatability is now reported for 4xFULL; independent repeated AutoPos deployments are still not measured. |
| Anchor | Delay/bias repeatability | covered_bootstrap | resilience_gap_audit/tables/bootstrap_delay_sd.csv + checklist_anchor_repeatability.csv | Anchor residual-delay correction differences rel_A now have bootstrap SD; absolute/common-mode delay remains gauge-coupled. |
| Anchor | Delay-layout coupling | covered | checklist_ablation.csv | Vicon solver-delay vs Vicon delaycal and scale-to-Vicon solver-delay rows show non-transferability. |
| Anchor | Baseline comparison | partial | checklist_ablation.csv | AutoPos/Vicon/one-baseline covered; PANS/manual missing. |
| Tag | Static tag repeatability | covered | checklist_tag_static.csv | Per-position d3_std median included. |
| Tag | Static tag absolute error | covered | checklist_tag_static.csv | RMSE/median/P95 included. |
| Tag | Axis-wise static error | covered | checklist_tag_static.csv | X/Y/Z RMSE included. |
| Tag | Moving tag trajectory error | covered | checklist_tag_dynamic.csv | ATE RMSE/median/P95 included from ROTO OptiTrack truth. |
| Tag | Relative trajectory error | covered | checklist_tag_dynamic.csv | Frame-to-frame RPE RMSE included. |
| Tag | Rigid wand / RotoArm consistency | covered | FULL_4WAY_BIG_COMPARISON.md + reviewer audit WHY #2/#10/#11 | Radius, dR, turn-center repeatability already reported. |
| Tag | Layout-to-tag propagation | covered | checklist_ablation.csv + static/dynamic tables | Same tag data compared across AutoPos/Vicon/scale/one-baseline layouts. |
| Tag | Anchor dropout robustness | covered | mc_keepk_combined_summary.csv + stratified_keepk_category_summary.csv | Monte Carlo keep-k/dropout tables exist; not expanded into checklist core tables. |
| Tag | LOS/NLOS or quality robustness | partial | tag_error_by_facing.csv + pair_raw_scatter.csv + worstpoint_range_residuals.csv | Quality/facing/range diagnostics exist; explicit CIR/NLOS labels not found. |
| Tag | Update-rate / packet-loss robustness | covered_synthetic | resilience_gap_audit/tables/static_dropout_stress_summary.csv + resilience_gap_audit/tables/roto_sample_dropout_stress_summary.csv | Static raw frames are re-solved under synthetic packet/dropout stress; ROTO coverage is solved-sample thinning, not raw range re-solving. |
| Tag | Long-term stability | partial | temporal_drift_anchor_summary.csv | Anchor range drift exists; long moving/tag drift stress table not found. |

## Output Tables
- `../tables/checklist_anchor_layout_absolute.csv`
- `../tables/checklist_anchor_repeatability.csv`
- `../tables/checklist_tag_static.csv`
- `../tables/checklist_tag_dynamic.csv`
- `../tables/checklist_ablation.csv`
- `../tables/checklist_coverage.csv`
- `../../resilience_gap_audit/reports/RESILIENCE_GAP_AUDIT.md`
