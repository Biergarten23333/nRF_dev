# Task N3 - Bayesian Student-t Fix

Generated: 2026-06-18T01:08:36

Pyro is not installed, and the self-contained HMC path was interrupted after it proved too slow for an unattended batch turn. This task therefore reports a bounded Laplace posterior approximation around the V5 solved positions. The likelihood comparison is still apples-to-apples across Gaussian, Student-t (nu=3), and Gaussian+positive-tail mixture, but it is not a full HMC posterior.

| likelihood | nominal_coverage | actual_coverage | n_positions |
| --- | --- | --- | --- |
| gaussian | 0.500 | 0.083 | 24 |
| gaussian | 0.900 | 0.292 | 24 |
| gaussian | 0.950 | 0.333 | 24 |
| student_t | 0.500 | 0.167 | 24 |
| student_t | 0.900 | 0.333 | 24 |
| student_t | 0.950 | 0.458 | 24 |
| gaussian_exp_tail | 0.500 | 0.125 | 24 |
| gaussian_exp_tail | 0.900 | 0.292 | 24 |
| gaussian_exp_tail | 0.950 | 0.333 | 24 |
