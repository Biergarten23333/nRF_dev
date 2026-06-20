# Consistency Audit

| metric | source_1 | value_1 | source_2 | value_2 | discrepancy | status |
| --- | --- | --- | --- | --- | --- | --- |
| V5+C_V5+D_LOO median_3d | FULL_V5/static_summary_DLOO | 67.849 | FULL_transfer_matrix | 67.849 | 0.000 | OK |
| V5+C_V5+D_LOO median_3d | FULL_transfer_matrix | 67.849 | followup/f6 V5 baseline | 67.809 | 0.039 | OK |
| V4+C_V4+D_LOO median_3d | FULL_transfer_matrix | 57.921 | mechanism summary expected | 57.921 | 0.000 | OK |
| D_tag LOO | FULL_4way | 49.621 | FULL_V5/static_summary_DLOO | 49.621 | 0.000 | OK |
| Shapley D | GPU_tier1 | 1242.886 | GPU_discovery | 1242.886 | 0.000 | OK |
| Shapley F | GPU_tier1 | 1229.441 | GPU_discovery | 1229.441 | 0.000 | OK |
| NLOS PR-AUC best | GPU_tier1 | 0.952 | GPU_discovery | 0.949 | 0.004 | OK |
