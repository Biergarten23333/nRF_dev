# Phase 4 Nightly 1080Ti Bootstrap

Generated: 2026-06-06T21:04:47.780788+00:00
Elapsed: 10.0 s

## Status

- Chunks done/failed/running/pending: 5/0/0/0
- Rows done/total: 65/65

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
| G11_two_gpu_dynamic_balance | FAIL | False | devices=['cuda:0']; partial_chunks_by_gpu={'cuda:0': 4}; gpu0 max_util=0%; failed=0 |
| G12_cpu_parallel_execution | PASS | True | workers_per_device=4; total_workers=4; build_track_tensors_mean=0.07s, cpu_golden_mean=0.07s, load_imu_prior_cache_mean=0.00s, load_track_tensors_cache_mean=0.00s, simulate_imu_prior_mean=0.30s, torch_gpu_mean=0.28s |
| G13_thread_oversubscription_control | PASS | True | checked_logs=5; torch_threads_1_in_sample=True; workers_per_device=4 |

## Numerical Agreement Audit

| audit_id | status | blocking_final_claim | evidence |
|---|---:|---:|---|
| A1_accept_rate_match | PASS | False | max_abs_accept_rate_delta=0 |
| A2_p95_cpu_gpu_xyz_agreement | PASS | False | max_p95_xyz_diff=0.001 mm; threshold=10 mm |
| A3_single_frame_outlier_audit | PASS | False | max_single_frame_xyz_diff=0.001 mm at R2:L2:I3:T6/R01/BSDC91; resource gate can pass, but final numerical claim must inspect this outlier |

This is a bootstrap run for tomorrow's 5090D handoff, not the final Phase 4 FULL claim.