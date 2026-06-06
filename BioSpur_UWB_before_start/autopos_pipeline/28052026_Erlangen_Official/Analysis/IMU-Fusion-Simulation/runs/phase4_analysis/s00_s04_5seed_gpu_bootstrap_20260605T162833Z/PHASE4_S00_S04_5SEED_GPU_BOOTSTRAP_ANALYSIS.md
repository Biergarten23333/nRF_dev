# Phase 4 S00-S04 5-Seed GPU Bootstrap Analysis

Generated: 2026-06-05T16:32:02.873199+00:00

## Scope

This report aggregates the five completed Phase 4 GPU/CPU bootstrap seeds:

- `S00_default`: `phase4_GPUCPU_FULLFACTORY_2x1080ti_20260605T113145Z`
- `S01`: `phase4_GPUCPU_FULLFACTORY_S01_2x1080ti_20260605T122945Z`
- `S02`: `phase4_GPUCPU_FULLFACTORY_S02_2x1080ti_20260605T132500Z`
- `S03`: `phase4_GPUCPU_FULLFACTORY_S03_2x1080ti_20260605T142014Z`
- `S04`: `phase4_GPUCPU_FULLFACTORY_S04_2x1080ti_20260605T151945Z`

This is the current raw-range GPU bootstrap/factory path, not the final Opti-truth quality ranking. It verifies throughput, two-GPU scheduling, and CPU/GPU numerical agreement for the current `R2/R4 x L x I x T6/T8` raw-range factory chunks.

## Executive Summary

- All five seeds completed: **290/290 chunks per seed**, **580/580 rows per seed**, **0 failed chunks**.
- Two-GPU scheduling is balanced: each seed uses roughly **144/144 partial chunks** across `cuda:0` and `cuda:1`.
- Runtime is stable: seed wall time is about **55-63 min** on `2x GTX 1080 Ti + i7-8700K`.
- Numerical agreement passes the P95 audit in all seeds: max P95 CPU/GPU xyz diff remains **8.586 mm**, below the **10 mm** threshold.
- The same repeated single-frame outlier remains: `R4:L0:I1+I2+I3+I8:T6 / R13 / BS2DCE`, max single-frame diff about **844 mm**. This is a final-claim audit item, not a run-completion failure.
- The run is still CPU-reference-limited: CPU golden averages roughly **24-28 s/row**, while the torch GPU stage is roughly **2.5-3.0 s/row**.

## Seed Run Summary

| seed | run_id | chunks_done | chunks_failed | chunks_running | chunks_pending | rows_done | rows_total | elapsed_wall_min | chunk_wall_median_s | chunk_wall_p95_s | partial_chunks_cuda0 | partial_chunks_cuda1 | agreement_rows | xyz_diff_p95_max_mm | xyz_diff_max_max_mm | accept_rate_delta_max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| S00_default | phase4_GPUCPU_FULLFACTORY_2x1080ti_20260605T113145Z | 290 | 0 | 0 | 0 | 580 | 580 | 57.14 | 93.07 | 110.61 | 144 | 144 | 19592 | 8.586 | 844.065 | 0 |
| S01 | phase4_GPUCPU_FULLFACTORY_S01_2x1080ti_20260605T122945Z | 290 | 0 | 0 | 0 | 580 | 580 | 55.13 | 90.85 | 102.59 | 144 | 144 | 19592 | 8.586 | 844.065 | 0 |
| S02 | phase4_GPUCPU_FULLFACTORY_S02_2x1080ti_20260605T132500Z | 290 | 0 | 0 | 0 | 580 | 580 | 55.13 | 90.25 | 101.19 | 146 | 142 | 19592 | 8.586 | 844.065 | 0 |
| S03 | phase4_GPUCPU_FULLFACTORY_S03_2x1080ti_20260605T142014Z | 290 | 0 | 0 | 0 | 580 | 580 | 59.4 | 98.19 | 112.55 | 144 | 144 | 19592 | 8.586 | 844.065 | 0 |
| S04 | phase4_GPUCPU_FULLFACTORY_S04_2x1080ti_20260605T151945Z | 290 | 0 | 0 | 0 | 580 | 580 | 62.68 | 102.55 | 118.99 | 144 | 144 | 19592 | 8.586 | 844.065 | 0 |

## Timing By Stage

Mean seconds per row/stage. These are worker-stage timings, not whole-seed wall time.

| seed | cpu_golden | simulate_imu_prior | torch_gpu |
| --- | --- | --- | --- |
| S00_default | 25.329 | 0.782 | 2.649 |
| S01 | 24.349 | 0.778 | 2.557 |
| S02 | 24.205 | 0.782 | 2.554 |
| S03 | 26.293 | 0.813 | 2.818 |
| S04 | 27.698 | 0.834 | 2.968 |

## Resource Summary

| seed | gpu_index | util_mean_pct | util_p95_pct | util_max_pct | mem_max_mb | temp_max_c | power_mean_w |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S00_default | 0 | 4.29 | 24 | 100 | 1046 | 56 | 61.15 |
| S00_default | 1 | 4.55 | 27.65 | 100 | 906 | 56 | 57.48 |
| S01 | 0 | 6.52 | 31 | 100 | 1046 | 56 | 62.99 |
| S01 | 1 | 5.02 | 28 | 100 | 906 | 56 | 57.56 |
| S02 | 0 | 5.22 | 29 | 100 | 1046 | 56 | 62.38 |
| S02 | 1 | 5.47 | 31.05 | 94 | 906 | 55 | 55.86 |
| S03 | 0 | 5.58 | 28.2 | 100 | 1046 | 56 | 59.87 |
| S03 | 1 | 4.88 | 26 | 100 | 906 | 55 | 56.29 |
| S04 | 0 | 5.4 | 29 | 100 | 1046 | 56 | 61.56 |
| S04 | 1 | 5.12 | 25.55 | 99 | 906 | 55 | 55.69 |

## Resource Gates

| seed | gate_id | status | evidence |
| --- | --- | --- | --- |
| S00_default | G11_two_gpu_dynamic_balance | PASS | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 144, 'cuda:1': 144}; gpu0 max_util=100%, gpu1 max_util=100%; failed=0 |
| S00_default | G12_cpu_parallel_execution | PASS | workers_per_device=4; total_workers=8; cpu_golden_mean=25.33s, simulate_imu_prior_mean=0.78s, torch_gpu_mean=2.65s |
| S00_default | G13_thread_oversubscription_control | PASS | checked_logs=20; torch_threads_1_in_sample=True; workers_per_device=4 |
| S01 | G11_two_gpu_dynamic_balance | PASS | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 144, 'cuda:1': 144}; gpu0 max_util=100%, gpu1 max_util=100%; failed=0 |
| S01 | G12_cpu_parallel_execution | PASS | workers_per_device=4; total_workers=8; cpu_golden_mean=24.35s, simulate_imu_prior_mean=0.78s, torch_gpu_mean=2.56s |
| S01 | G13_thread_oversubscription_control | PASS | checked_logs=20; torch_threads_1_in_sample=True; workers_per_device=4 |
| S02 | G11_two_gpu_dynamic_balance | PASS | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 146, 'cuda:1': 142}; gpu0 max_util=100%, gpu1 max_util=94%; failed=0 |
| S02 | G12_cpu_parallel_execution | PASS | workers_per_device=4; total_workers=8; cpu_golden_mean=24.20s, simulate_imu_prior_mean=0.78s, torch_gpu_mean=2.55s |
| S02 | G13_thread_oversubscription_control | PASS | checked_logs=20; torch_threads_1_in_sample=True; workers_per_device=4 |
| S03 | G11_two_gpu_dynamic_balance | PASS | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 144, 'cuda:1': 144}; gpu0 max_util=100%, gpu1 max_util=100%; failed=0 |
| S03 | G12_cpu_parallel_execution | PASS | workers_per_device=4; total_workers=8; cpu_golden_mean=26.29s, simulate_imu_prior_mean=0.81s, torch_gpu_mean=2.82s |
| S03 | G13_thread_oversubscription_control | PASS | checked_logs=20; torch_threads_1_in_sample=True; workers_per_device=4 |
| S04 | G11_two_gpu_dynamic_balance | PASS | devices=['cuda:0', 'cuda:1']; partial_chunks_by_gpu={'cuda:0': 144, 'cuda:1': 144}; gpu0 max_util=100%, gpu1 max_util=99%; failed=0 |
| S04 | G12_cpu_parallel_execution | PASS | workers_per_device=4; total_workers=8; cpu_golden_mean=27.70s, simulate_imu_prior_mean=0.83s, torch_gpu_mean=2.97s |
| S04 | G13_thread_oversubscription_control | PASS | checked_logs=20; torch_threads_1_in_sample=True; workers_per_device=4 |

## Numerical Agreement Audit

| seed | audit_id | status | evidence |
| --- | --- | --- | --- |
| S00_default | A1_accept_rate_match | PASS | max_abs_accept_rate_delta=0 |
| S00_default | A2_p95_cpu_gpu_xyz_agreement | PASS | max_p95_xyz_diff=8.586 mm; threshold=10 mm |
| S00_default | A3_single_frame_outlier_audit | REVIEW | max_single_frame_xyz_diff=844.065 mm at R4:L0:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |
| S01 | A1_accept_rate_match | PASS | max_abs_accept_rate_delta=0 |
| S01 | A2_p95_cpu_gpu_xyz_agreement | PASS | max_p95_xyz_diff=8.586 mm; threshold=10 mm |
| S01 | A3_single_frame_outlier_audit | REVIEW | max_single_frame_xyz_diff=844.065 mm at R4:L0:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |
| S02 | A1_accept_rate_match | PASS | max_abs_accept_rate_delta=0 |
| S02 | A2_p95_cpu_gpu_xyz_agreement | PASS | max_p95_xyz_diff=8.586 mm; threshold=10 mm |
| S02 | A3_single_frame_outlier_audit | REVIEW | max_single_frame_xyz_diff=844.065 mm at R4:L0:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |
| S03 | A1_accept_rate_match | PASS | max_abs_accept_rate_delta=0 |
| S03 | A2_p95_cpu_gpu_xyz_agreement | PASS | max_p95_xyz_diff=8.586 mm; threshold=10 mm |
| S03 | A3_single_frame_outlier_audit | REVIEW | max_single_frame_xyz_diff=844.065 mm at R4:L0:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |
| S04 | A1_accept_rate_match | PASS | max_abs_accept_rate_delta=0 |
| S04 | A2_p95_cpu_gpu_xyz_agreement | PASS | max_p95_xyz_diff=8.586 mm; threshold=10 mm |
| S04 | A3_single_frame_outlier_audit | REVIEW | max_single_frame_xyz_diff=844.065 mm at R4:L0:I1+I2+I3+I8:T6/R13/BS2DCE; resource gate can pass, but final numerical claim must inspect this outlier |

## Cross-Seed Agreement Outliers

Top rows by maximum single-frame CPU/GPU difference across S00-S04.

| row_spec | seed_count | R | L | I | T | p95_max_across_seeds_mm | max_diff_across_seeds_mm | accept_delta_max_across_seeds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R4:L0:I1+I2+I3+I8:T6 | 5 | R4 | L0 | I1+I2+I3+I8 | T6 | 8.586 | 844.065 | 0 |
| R4:L1:I1+I2+I3+I8:T6 | 5 | R4 | L1 | I1+I2+I3+I8 | T6 | 6.698 | 838.371 | 0 |
| R4:L0:I1+I3+I7:T6 | 5 | R4 | L0 | I1+I3+I7 | T6 | 6.11 | 836.201 | 0 |
| R4:L1:I1+I3+I7:T6 | 5 | R4 | L1 | I1+I3+I7 | T6 | 4.45 | 828.346 | 0 |
| R4:L19:I1+I2+I3+I8:T6 | 5 | R4 | L19 | I1+I2+I3+I8 | T6 | 4.293 | 827.416 | 0 |
| R4:L5:I1+I2+I3+I8:T6 | 5 | R4 | L5 | I1+I2+I3+I8 | T6 | 4.205 | 826.86 | 0 |
| R4:L16:I1+I2+I3+I8:T6 | 5 | R4 | L16 | I1+I2+I3+I8 | T6 | 4.155 | 826.581 | 0 |
| R4:L15:I1+I2+I3+I8:T6 | 5 | R4 | L15 | I1+I2+I3+I8 | T6 | 3.816 | 824.384 | 0 |
| R4:L14:I1+I2+I3+I8:T6 | 5 | R4 | L14 | I1+I2+I3+I8 | T6 | 3.301 | 820.552 | 0 |
| R4:L13:I1+I2+I3+I8:T6 | 5 | R4 | L13 | I1+I2+I3+I8 | T6 | 3.059 | 818.476 | 0 |
| R4:L19:I1+I3+I7:T6 | 5 | R4 | L19 | I1+I3+I7 | T6 | 2.575 | 813.75 | 0 |
| R4:L5:I1+I3+I7:T6 | 5 | R4 | L5 | I1+I3+I7 | T6 | 2.506 | 812.964 | 0 |
| R4:L16:I1+I3+I7:T6 | 5 | R4 | L16 | I1+I3+I7 | T6 | 2.47 | 812.596 | 0 |
| R4:L0:I0:T6 | 5 | R4 | L0 | I0 | T6 | 2.334 | 811.008 | 0 |
| R4:L0:I0:T8 | 5 | R4 | L0 | I0 | T8 | 2.334 | 811.008 | 0 |
| R4:L0:I1+I2+I3+I8:T8 | 5 | R4 | L0 | I1+I2+I3+I8 | T8 | 2.334 | 811.008 | 0 |
| R4:L0:I1+I3+I7:T8 | 5 | R4 | L0 | I1+I3+I7 | T8 | 2.334 | 811.008 | 0 |
| R4:L0:I1:T6 | 5 | R4 | L0 | I1 | T6 | 2.334 | 811.008 | 0 |
| R4:L0:I1:T8 | 5 | R4 | L0 | I1 | T8 | 2.334 | 811.008 | 0 |
| R4:L0:I3:T6 | 5 | R4 | L0 | I3 | T6 | 2.334 | 811.008 | 0 |

## Interpretation

What this five-seed analysis tells us:

- The background training/scan completed cleanly through S04.
- The 2x1080Ti worker scheduler is now behaving correctly: balanced chunk assignment, no failed chunks, and both GPUs reached high instantaneous utilization during work.
- CPU/GPU numerical agreement is globally stable at P95 level across all five seeds.
- The repeated `A3` outlier should be inspected before using this GPU implementation as an unquestioned final numerical reference. It is localized and repeatable, which is good for debugging.

What it does not tell us yet:

- It does not by itself decide the best UWB+IMU algorithm against Opti. For that, use the Opti-truth same-P evaluation rule: pure UWB and fusion must both be compared against Opti, and fusion must be compared only within the same `P` stream.
- It does not replace the trajectory-quality plots. This report is the execution and numerical-equivalence audit for the five bootstrap seeds.

## Next Training Recommendation

Given your acceptable 4-5 hour window, the next useful run should not repeat S00-S04. The best next step is a **truth-quality production pass** over the promising L2/JY61P-like scope:

```text
A0/U4, L2 only, S00-S04 seeds, same-P Opti-truth scoring,
forward + session solver families,
metrics split into 3D, XY, Z, spike, roughness, and ROTO XY shape.
```

Goal: produce one ranking table that answers: **which UWB+IMU combination actually beats the same-P UWB baseline against Opti, across five seeds?**

Recommended compute budget on this workstation: **4-5 hours**, with both GPUs enabled and CPU worker count kept high but memory-safe.

## Plots

- [01_seed_wall_time.png](figs/01_seed_wall_time.png)
- [02_stage_timing_mean.png](figs/02_stage_timing_mean.png)
- [03_cpu_gpu_agreement_distribution.png](figs/03_cpu_gpu_agreement_distribution.png)
- [04_top_cross_seed_outliers.png](figs/04_top_cross_seed_outliers.png)
- [05_gpu_chunk_balance.png](figs/05_gpu_chunk_balance.png)
- [06_gpu_utilization_timeline.png](figs/06_gpu_utilization_timeline.png)
- [07_agreement_heatmap_L_T.png](figs/07_agreement_heatmap_L_T.png)

## Tables

- `tables/seed_run_summary.csv`
- `tables/timing_by_seed_stage.csv`
- `tables/resource_samples_summary.csv`
- `tables/resource_gates_s00_s04.csv`
- `tables/numerical_audit_s00_s04.csv`
- `tables/row_agreement_cross_seed.csv`
- `tables/top30_cross_seed_outliers.csv`
- `tables/matrix_manifest_s00_s04.csv`
