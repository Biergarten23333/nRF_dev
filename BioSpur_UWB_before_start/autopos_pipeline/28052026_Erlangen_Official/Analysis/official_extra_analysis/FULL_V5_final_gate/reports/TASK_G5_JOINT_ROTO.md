# Task G5 - Range-Level Two-Tag ROTO Joint Solver

Verdict: **rigid body does not improve ROTO**.

Independent baseline median: 101.084 mm. Best joint median: 261.835 mm (`joint_fixed_49p621`).

The joint solver enforces the 120 mm baseline exactly in the state model. The D-tag heatmap is a coarse subset diagnostic used to avoid a new broad hyperparameter search in this closing gate.

| method | overall_median | overall_p95 | overall_rmse | baseline_error_median | convergence_rate | n_frames |
| --- | --- | --- | --- | --- | --- | --- |
| independent_baseline_current | 101.084 | 227.705 | 132.059 | 552.327 | 1.000 | 15717 |
| joint_fixed_49p621 | 261.835 | 498.140 | 315.468 | 0.000 | 0.928 | 15717 |
| joint_static_estimated_dtag | 262.202 | 495.908 | 314.098 | 0.000 | 0.932 | 15717 |
| joint_coarse_cost_min_dtag | 264.159 | 491.663 | 312.281 | 0.000 | 0.919 | 15717 |
