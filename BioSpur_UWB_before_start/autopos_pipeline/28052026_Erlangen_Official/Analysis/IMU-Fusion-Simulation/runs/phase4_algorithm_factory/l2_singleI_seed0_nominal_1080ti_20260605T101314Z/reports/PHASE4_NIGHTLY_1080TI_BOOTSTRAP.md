# Phase 4 Nightly 1080Ti Bootstrap

Generated: 2026-06-05T10:19:26.022556+00:00
Elapsed: 240.2 s

## Status

- Chunks done/failed/running/pending: 17/0/0/0
- Rows done/total: 33/33

## Outputs

- `tables/phase4_nightly_chunks.csv`
- `tables/phase4_nightly_agreement.csv`
- `tables/phase4_nightly_timing.csv`
- `tables/phase4_nightly_resource_samples.csv`
- `tables/phase4_resource_gates.csv`
- `tables/phase4_numerical_agreement_audit.csv`

## Resource Gates

| gate_id | status | blocking_next_phase | evidence |
|---|---:|---:|---|
| G11_two_gpu_dynamic_balance | FAIL | False | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 8, 'cuda:1': 8}; gpu0 max_util=0%, gpu1 max_util=0%; failed=0 |
| G12_cpu_parallel_execution | PASS | True | workers_per_device=3; total_workers=6; cpu_golden_mean=19.86s, simulate_imu_prior_mean=0.62s, torch_gpu_mean=2.35s |
| G13_thread_oversubscription_control | PASS | True | checked_logs=17; torch_threads_1_in_sample=True; workers_per_device=3 |

## Numerical Agreement Audit

| audit_id | status | blocking_final_claim | evidence |
|---|---:|---:|---|
| A1_accept_rate_match | PASS | False | max_abs_accept_rate_delta=0 |
| A2_p95_cpu_gpu_xyz_agreement | PASS | False | max_p95_xyz_diff=1.628 mm; threshold=10 mm |
| A3_single_frame_outlier_audit | REVIEW | True | max_single_frame_xyz_diff=800.467 mm at R4:L2:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |

This is a bootstrap run for tomorrow's 5090D handoff, not the final Phase 4 FULL claim.