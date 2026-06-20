# Convention Unification Completion

## 1. Summary

- Rows in locked headline table: 18.
- Rows changed versus `locked_headline_v2.csv`: 7.
- Rows A-E were regenerated under a scalar static convention with one aggregated range per `(position, anchor)` link and one solve per position.
- ROTO rows L-N are retained as `per_frame_dynamic`; they are not static scalar rows by design.
- Rows O-R were verified as scalar lower-trim/oracle/bootstrap rows from the rawframe V3 pipeline.

## 2. V5 Baseline

- Old headline C: median 67.8 mm, P95 160.5 mm, RMSE 86.4 mm.
- New scalar C: median 63.462 mm, P95 175.137 mm, RMSE 91.750 mm.
- D_tag LOO: mean 49.519 mm, median 49.621 mm.

## 3. V4 Baseline

- Old headline B: median 57.9 mm, P95 110.6 mm, RMSE 74.4 mm.
- New scalar B: median 61.471 mm, P95 157.006 mm, RMSE 87.506 mm.
- D_tag LOO: mean 33.141 mm, median 33.081 mm.

## 4. Convention Verification

| Row | Variant | New convention | Action | Notes |
|---|---|---|---|---|
| A | V4 production | scalar_p50_t4_fixed_d0 | regenerated | same scalar convention, regenerated because f6 is stale |
| B | V4 + D_LOO | scalar_p50_t4_dtag_loo_per_fold | regenerated | transfer-matrix static cell uses 28,818 frame mean-position evaluator |
| C | V5 baseline | scalar_p50_t4_dtag_loo_per_fold | regenerated | new row uses per-fold D_tag LOO and valid=True p50 links |
| D | V5 apparent best | scalar_p30_inverse_rms_t4_dtag_loo_per_fold | regenerated | post-selected scalar row retained but regenerated |
| E | V4 apparent best | scalar_p30_inverse_rms_t4_dtag_loo_per_fold | regenerated | post-selected scalar row retained but regenerated |
| F | V5 corrected | scalar_optimism_corrected_median | recomputed | existing optimism gap added to regenerated D median |
| G | V4 corrected | scalar_optimism_corrected_median | recomputed | existing optimism gap added to regenerated E median |
| H | V5 bootstrap CI | scalar_bootstrap_ci_existing | verified | existing scalar bootstrap retained |
| I | Nested CV (height) | scalar_nested_cv_existing | verified | batch3 uses scalar percentile matrices and train/eval splits |
| J | Nested CV (quadrant) | scalar_nested_cv_existing | verified | batch3 uses scalar percentile matrices and train/eval splits |
| K | Nested CV (spatial6) | scalar_nested_cv_existing | verified | batch3 uses scalar percentile matrices and train/eval splits |
| L | ROTO V5 per-frame | per_frame_dynamic | kept | ROTO is dynamic/per-frame by nature |
| M | ROTO SE(3) aligned | per_frame_dynamic | kept | ROTO is dynamic/per-frame by nature |
| N | ROTO Sim3 aligned | per_frame_dynamic | kept | ROTO is dynamic/per-frame by nature |
| O | lower_trim_20 + Huber30 + V5 | scalar_lower_trim_20_huber30_loo | verified | rawframe v3 Stage 3 builds scalar estimator matrix then 24-fold LOO |
| P | lower_trim_20 + Huber30 + V5(e_i=0 anchor refit) | scalar_p50_anchor_ezero_huber30_loo | verified | anchor lower trim l3 row is scalar per-position LOO |
| Q | Oracle lower bound | scalar_oracle_lower_bound | verified | rawframe v3 scalar oracle ceiling |
| R | Bootstrap CI (lower_trim_20) | scalar_lower_trim_20_bootstrap_ci | verified | bootstrap over scalar lower_trim_20 held-out/OOB medians |

## 5. Final Locked Headline Table

| Row | Variant | Convention | Median 3D mm | P95 mm | RMSE mm | Evaluation |
|---|---|---|---:|---:|---:|---|
| A | V4 production | scalar_p50_t4_fixed_d0 | 81.564 | 189.361 | 114.307 | in-sample, all 24 |
| B | V4 + D_LOO | scalar_p50_t4_dtag_loo_per_fold | 61.471 | 157.006 | 87.506 | LOO-CV |
| C | V5 baseline | scalar_p50_t4_dtag_loo_per_fold | 63.462 | 175.137 | 91.750 | LOO-CV |
| D | V5 apparent best | scalar_p30_inverse_rms_t4_dtag_loo_per_fold | 65.522 | 131.995 | 84.887 | in-sample post-selected |
| E | V4 apparent best | scalar_p30_inverse_rms_t4_dtag_loo_per_fold | 57.120 | 221.614 | 94.387 | in-sample post-selected |
| F | V5 corrected | scalar_optimism_corrected_median | 75.090 |  |  | OOB-bootstrap correction |
| G | V4 corrected | scalar_optimism_corrected_median | 66.688 |  |  | OOB-bootstrap correction |
| H | V5 bootstrap CI | scalar_bootstrap_ci_existing | [54.3, 63.7] |  |  | bootstrap 95% CI |
| I | Nested CV (height) | scalar_nested_cv_existing | 82.925 |  |  | held-out test |
| J | Nested CV (quadrant) | scalar_nested_cv_existing | 88.042 |  |  | held-out test |
| K | Nested CV (spatial6) | scalar_nested_cv_existing | 94.250 |  |  | held-out test |
| L | ROTO V5 per-frame | per_frame_dynamic | 101.485 | 214.369 | 126.226 | BEST-FIT-ALIGNED |
| M | ROTO SE(3) aligned | per_frame_dynamic | 82.516 | 185.207 | 103.746 | diagnostic |
| N | ROTO Sim3 aligned | per_frame_dynamic | 74.264 | 160.793 | 94.811 | diagnostic only |
| O | lower_trim_20 + Huber30 + V5 | scalar_lower_trim_20_huber30_loo | 44.485 | 164.135 | 81.537 | LOO-CV |
| P | lower_trim_20 + Huber30 + V5(e_i=0 anchor refit) | scalar_p50_anchor_ezero_huber30_loo | 43.172 | 163.093 | 81.790 | LOO-CV; anchor refit diagnostic |
| Q | Oracle lower bound | scalar_oracle_lower_bound | 44.596 |  |  | oracle |
| R | Bootstrap CI (lower_trim_20) | scalar_lower_trim_20_bootstrap_ci | [33.4, 82.8] |  |  | bootstrap 95% CI |

## 6. Action Items For V3 Report Update

- Replace the old headline table with `FULL_V5_convention_unification/tables/unified_headline_table.csv`.
- Update prose that cites V4/V5 p50 baselines to use the regenerated scalar rows B and C.
- Keep ROTO rows labeled as dynamic/per-frame, separate from static scalar rows.
- Do not reuse `FULL_V5_followup_validation/tables/f6_final_comparison.csv` as a headline source; it is a stale/generated table relative to the current scalar regeneration.
- Use `headline_diff.csv` for exact row-by-row changes.

## Verification

- [x] V5 p50 scalar baseline regenerated.
- [x] V4 p50 scalar baseline regenerated.
- [x] lower_trim_20 confirmed as scalar.
- [x] All 18 headline rows have verified convention.
- [x] `unified_headline_table.csv` written with convention column.
- [x] `headline_diff.csv` written.
- [x] No static row mixes mean-position and scalar conventions.
- [x] ROTO rows excluded from static scalar requirement as dynamic per-frame rows.

## Runtime Context

- Worker count used: 1 CPU worker.
- CPU utilization snapshot: 8.5%.
- GPU utilization snapshot: 0, NVIDIA GeForce GTX 1080 Ti, 7, 192, 11264; 1, NVIDIA GeForce GTX 1080 Ti, 0, 10, 11264.
