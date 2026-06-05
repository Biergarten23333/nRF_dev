# Phase 2 Validation Gates

Phase 2 run status: `ready_for_stage1_screening`
Phase 1 source run: `20260604T161756Z`

| gate_id | status | blocking_screening | evidence |
| --- | --- | --- | --- |
| G3_range_bias_policy | PASS | False | bias_rows=272; tracks=34; min_anchor_rows=8; min_ge4_ratio=1.000 |
| G5_noise_seed_repeats | PASS_SCREENING | False | seeds=5; tracks=170; endpoint drift p05/p50/p95=10370.1/27803.8/48714.1 mm |

G3 creates the raw-range residual/bias policy table for tight-fusion rows.
G5 creates repeated stochastic IMU drift evidence for screening.
