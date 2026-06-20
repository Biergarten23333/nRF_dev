# Task G2 - Noise Model Contradiction Audit

Verdict: **Student-t is BIC winner; key-card had a parsing error**.

Direct BIC check gives `M2_student_t` with BIC 2327.788. The key card's `M0_global_gaussian` line is a parsing/reporting error, not the model-evidence winner.

Student-t 95% posterior coverage after N3 is 0.458, so the residual model improves evidence but does not fully calibrate uncertainty.

Recommended wording: Student-t best describes the residual distribution by BIC, but posterior coverage remains under-calibrated; robust losses are still an engineering choice, not a complete uncertainty model.

| model | likelihood | n_params | loglik | aic | bic | coverage_50 | coverage_90 | coverage_95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| M0_global_gaussian | Gaussian | 1 | -1170.554 | 2343.108 | 2346.365 | 0.083 | 0.292 | 0.333 |
| M1_per_anchor_gaussian | Gaussian | 8 | -1165.059 | 2346.118 | 2372.178 | 0.083 | 0.292 | 0.333 |
| M2_student_t | Student-t | 2 | -1158.636 | 2321.273 | 2327.788 | 0.167 | 0.333 | 0.458 |
| M3_gaussian_exponential_tail | Gaussian/exponential mixture | 3 | -1159.140 | 2324.280 | 2334.052 | 0.125 | 0.292 | 0.333 |
| M4_per_anchor_mixture | Gaussian/exponential mixture | 24 | -1147.108 | 2342.215 | 2420.395 | 0.125 | 0.292 | 0.333 |
| M5_elevation_tail_mixture | Gaussian/exponential mixture | 5 | -1159.140 | 2328.280 | 2344.567 | 0.125 | 0.292 | 0.333 |
| N3_gaussian | gaussian |  |  |  |  | 0.083 | 0.292 | 0.333 |
| N3_gaussian_exp_tail | gaussian_exp_tail |  |  |  |  | 0.125 | 0.292 | 0.333 |
| N3_student_t | student_t |  |  |  |  | 0.167 | 0.333 | 0.458 |
