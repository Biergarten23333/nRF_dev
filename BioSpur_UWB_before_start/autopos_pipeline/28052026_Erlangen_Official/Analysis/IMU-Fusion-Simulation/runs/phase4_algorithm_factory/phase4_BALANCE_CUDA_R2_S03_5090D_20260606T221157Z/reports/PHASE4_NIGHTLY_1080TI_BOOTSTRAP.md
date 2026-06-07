# Phase 4 Nightly 1080Ti Bootstrap

Generated: 2026-06-07T00:10:32.084950+00:00
Elapsed: 60.1 s

## Status

- Chunks done/failed/running/pending: 2/72/0/0
- Rows done/total: 4/580

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
| G11_two_gpu_dynamic_balance | FAIL | False | devices=['cuda:0']; partial_chunks_by_gpu={}; gpu0 max_util=36%; failed=72 |
| G12_cpu_parallel_execution | PASS | True | workers_per_device=24; total_workers=24; build_track_tensors_mean=0.37s, cpu_golden_mean=0.40s, simulate_imu_prior_mean=0.85s, torch_gpu_mean=0.57s |
| G13_thread_oversubscription_control | REVIEW | False | checked_logs=2; torch_threads_1_in_sample=True; workers_per_device=24 |

## Numerical Agreement Audit

| audit_id | status | blocking_final_claim | evidence |
|---|---:|---:|---|
| A1_accept_rate_match | PASS | False | max_abs_accept_rate_delta=0 |
| A2_p95_cpu_gpu_xyz_agreement | PASS | False | max_p95_xyz_diff=0.001 mm; threshold=10 mm |
| A3_single_frame_outlier_audit | PASS | False | max_single_frame_xyz_diff=0.001 mm at R2:L2:I3:T6/R01/BSDC91; resource gate can pass, but final numerical claim must inspect this outlier |

This is a bootstrap run for tomorrow's 5090D handoff, not the final Phase 4 FULL claim.