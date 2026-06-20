# Task N2 - Solver Search Fixed with D_tag LOO

Generated: 2026-06-18T01:10:55

Best fixed-LOO variant: Cauchy50/inverse_rho_rms/scalar/V5_common_mode = 82.6 mm. V4 baseline LOO in this tensor solver = 86.9 mm.

| variant_id | loss | weighting | dtag_model | delay_source | loo_median_3d | loo_rmse | loo_p95 | loo_dtag_mean | loo_dtag_median |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | Cauchy50 | inverse_rho_rms | scalar | V5_common_mode | 82.604 | 109.667 | 193.713 | 49.519 | 49.621 |
| 1 | Cauchy50 | inverse_rho_rms | elevation_2params | V5_common_mode | 84.558 | 110.513 | 195.046 | 49.260 | 49.504 |
| 3 | Cauchy50 | NLOS_probability_based | scalar | V5_common_mode | 84.736 | 109.999 | 188.238 | 49.519 | 49.621 |
| 2 | Cauchy50 | inverse_rho_rms | scalar | V5_no_regularization | 85.208 | 112.560 | 202.305 | 50.207 | 50.158 |
| 5 | Cauchy50 | inverse_rho_rms | elevation_2params | V5_no_regularization | 86.555 | 113.527 | 204.556 | 49.987 | 50.166 |
| 7 | StudentT3 | inverse_rho_rms | scalar | V5_common_mode | 86.772 | 110.697 | 193.794 | 49.519 | 49.621 |
| 30 | baseline | uniform | scalar | V4_CV4 | 86.852 | 115.926 | 202.576 | 33.141 | 33.081 |
| 6 | Cauchy50 | NLOS_probability_based | elevation_2params | V5_common_mode | 86.998 | 110.842 | 189.675 | 49.260 | 49.504 |
| 4 | Cauchy50 | NLOS_probability_based | scalar | V5_no_regularization | 87.422 | 112.681 | 198.312 | 50.207 | 50.158 |
| 12 | StudentT3 | inverse_rho_rms | scalar | V5_no_regularization | 87.807 | 113.271 | 202.934 | 50.207 | 50.158 |
| 9 | StudentT3 | NLOS_probability_based | scalar | V5_common_mode | 88.082 | 113.271 | 200.341 | 49.519 | 49.621 |
| 14 | Cauchy50 | uniform | scalar | V5_common_mode | 88.525 | 111.299 | 194.258 | 49.519 | 49.621 |
| 11 | StudentT3 | inverse_rho_rms | elevation_2params | V5_common_mode | 89.028 | 111.570 | 195.191 | 49.260 | 49.504 |
| 15 | Huber50 | inverse_rho_rms | scalar | V5_common_mode | 89.201 | 111.251 | 195.457 | 49.519 | 49.621 |
| 22 | Cauchy50 | inverse_range_std | scalar | V5_no_regularization | 89.214 | 114.004 | 194.721 | 50.207 | 50.158 |
| 17 | Huber100 | inverse_rho_rms | scalar | V5_common_mode | 89.314 | 111.813 | 195.648 | 49.519 | 49.621 |
| 8 | Cauchy50 | NLOS_probability_based | elevation_2params | V5_no_regularization | 89.366 | 113.656 | 200.739 | 49.987 | 50.166 |
| 19 | L1 | inverse_rho_rms | scalar | V5_common_mode | 89.550 | 113.082 | 196.408 | 49.519 | 49.621 |
| 20 | L2 | inverse_rho_rms | scalar | V5_common_mode | 89.550 | 113.082 | 196.408 | 49.519 | 49.621 |
| 21 | Cauchy50 | inverse_range_std | scalar | V5_common_mode | 89.680 | 112.123 | 194.677 | 49.519 | 49.621 |
