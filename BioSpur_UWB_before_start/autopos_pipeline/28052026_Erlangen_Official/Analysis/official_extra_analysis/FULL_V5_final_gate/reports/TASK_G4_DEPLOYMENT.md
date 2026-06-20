# Task G4 - Deployment Recipe Validation

P3 Pareto recipes were replayed through the same follow-up validation C solver path (`solve_ranges`/`loo_eval`). For P3 rows with `n_cal=0`, D=0 was kept. For calibrated P3 rows, this gate uses the prompt-required 23-position LOO D-tag validation.

Verdict: **proxy-solver max gap 5.7 mm**.

| recipe_id | layout | percentile | d_tag_method | proxy_median | full_solver_loo_median | discrepancy_mm | solve_time_per_position_s | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P3_pareto_1 | V5_CV5 | p30 | fixed_0 | 83.777 | 89.474 | 5.697 | 0.000 | P3 proxy row; n_cal=0; anchor_labels=HDGEBCAF |
| P3_pareto_2 | V5_CV5 | p30 | range_residual_LOO_23 | 61.308 | 59.842 | -1.466 | 0.001 | P3 proxy row; n_cal=1; anchor_labels=HDGEBCAF |
| mandatory_V5_p50_uniform_DLOO | V5_CV5 | p50 | range_residual_LOO_23 |  | 67.809 |  | 0.001 | standard V5 baseline |
| mandatory_V4_p50_uniform_DLOO | V4_CV4 | p50 | range_residual_LOO_23 |  | 62.177 |  | 0.001 | standard V4 baseline |
| mandatory_V5_p50_uniform_D0 | V5_CV5 | p50 | fixed_0 |  | 115.977 |  | 0.000 | no tag-delay calibration |
