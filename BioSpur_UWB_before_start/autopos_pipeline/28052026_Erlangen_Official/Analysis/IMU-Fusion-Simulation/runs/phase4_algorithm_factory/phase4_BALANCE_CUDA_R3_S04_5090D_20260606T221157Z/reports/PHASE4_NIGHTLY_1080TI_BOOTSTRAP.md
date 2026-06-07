# Phase 4 Nightly 1080Ti Bootstrap

Generated: 2026-06-07T00:13:31.310701+00:00
Elapsed: 29.1 s

## Status

- Chunks done/failed/running/pending: 0/74/0/0
- Rows done/total: 0/580

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
| G11_two_gpu_dynamic_balance | FAIL | False | devices=['cuda:0']; partial_chunks_by_gpu={}; gpu0 max_util=0%; failed=74 |
| G12_cpu_parallel_execution | PASS | True | workers_per_device=24; total_workers=24; timing unavailable |
| G13_thread_oversubscription_control | REVIEW | False | checked_logs=0; torch_threads_1_in_sample=False; workers_per_device=24 |

## Numerical Agreement Audit

| audit_id | status | blocking_final_claim | evidence |
|---|---:|---:|---|
| A0_agreement_table_present | FAIL | True | tables/phase4_nightly_agreement.csv is missing or empty |

This is a bootstrap run for tomorrow's 5090D handoff, not the final Phase 4 FULL claim.