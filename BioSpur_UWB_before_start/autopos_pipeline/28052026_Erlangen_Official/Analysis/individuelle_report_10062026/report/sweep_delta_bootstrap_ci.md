# Sweep Delta Bootstrap Confidence Intervals

- Generated: `2026-06-10T12:52:24`
- Scope: supporting table for the individual report; no solver files were modified.

Confidence intervals use a fixed-design residual bootstrap over the 28 unordered anchor-pair residuals from the Phase 1.2 full model. This keeps the Vicon pair geometry fixed and estimates model-parameter sensitivity to the remaining pair-level residual structure.

| anchor | delta_full_mm | delta_full_ci95_low_mm | delta_full_ci95_high_mm | delta_additive_only_mm | delta_additive_ci95_low_mm | delta_additive_ci95_high_mm |
| --- | --- | --- | --- | --- | --- | --- |
| A | 294.249 | 206.180 | 381.496 | 297.458 | 232.254 | 364.477 |
| B | 189.791 | 98.175 | 276.825 | 193.025 | 125.965 | 261.548 |
| C | 251.423 | 160.182 | 342.865 | 254.648 | 188.302 | 322.028 |
| D | 226.355 | 138.086 | 317.367 | 229.576 | 161.350 | 296.896 |
| E | 95.170 | 3.907 | 185.741 | 98.436 | 29.880 | 164.860 |
| F | 97.885 | 6.014 | 188.342 | 101.184 | 31.818 | 168.644 |
| G | 168.438 | 74.732 | 258.960 | 171.752 | 102.562 | 237.594 |
| H | 167.874 | 78.225 | 260.630 | 171.173 | 104.466 | 236.539 |


| parameter | estimate | ci95_low | ci95_high | bootstrap_n | bootstrap_method |
| --- | --- | --- | --- | --- | --- |
| rho_percent | 0.119 | -2.090 | 2.326 | 5000 | fixed_design_residual_bootstrap |
| full_model_rms_mm | 43.775 |  |  | 5000 | fixed_design_residual_bootstrap |


STOP: bootstrap support table complete.
