# Nightly 8h Phase 4 Plan On 2x GTX 1080 Ti

Generated: 2026-06-05 00:50 CEST

## Goal

Tonight's 8 hour run is a Phase 4 bootstrap, not the final full claim.

The goal is to make tomorrow's RTX 5090D run productive:

```text
1. freeze the Phase 4 manifest and handoff package
2. validate CPU/GPU agreement on real raw-range rows
3. calibrate the 2x1080Ti chunk size and scheduler behavior
4. produce first partial Phase 4 rows/failure labels
5. leave a resumable run package for the 5090D machine
```

## Hardware Result From Tonight's Smoke Tests

Dense CUDA matmul tests on the local 2x GTX 1080 Ti:

```text
size 12288:
  GPU0 9.52 TFLOPS, GPU1 9.64 TFLOPS, about 2.0 GB/GPU

size 16384:
  GPU0 9.47 TFLOPS, GPU1 9.61 TFLOPS, about 3.3 GB/GPU

size 24576:
  GPU0 9.43 TFLOPS, GPU1 9.56 TFLOPS, about 7.1-7.4 GB/GPU
```

Decision:

```text
Use 12288-equivalent medium chunks as the first Phase 4 GPU target.
Do not chase maximum VRAM usage. The target is GPU SM 95-100%, low idle time,
and small enough chunks for good work stealing and resume granularity.
```

## Current Phase 4 Code Reality

Existing script:

```text
scripts/run_phase4_gpu_pilot.py
```

Current coverage:

```text
T6/T8 raw-range CPU/GPU agreement pilot only
single-device option: --device cuda:0 or --device cuda:1
not yet the official dual-GPU FULL launcher
```

Existing agreement evidence:

```text
R2:L0:I0:T6 float32 information-form GPU:
  CPU/GPU RMSE about 0.00020 mm

R4:L8:I1+I2+I3+I8:T8 float32 information-form GPU:
  CPU/GPU RMSE about 0.00020 mm
```

Therefore tonight must not pretend that Phase 4 FULL is already runnable. The
first part of tonight is implementation and launch-gate work.

## 8 Hour Schedule

Assumed window:

```text
start: 2026-06-05 00:50 CEST
stop:  2026-06-05 08:50 CEST
```

### 00:50-01:30: Handoff And Manifest Freeze

Deliverables:

```text
runs/phase4_algorithm_factory/<run_id>/manifest.json
runs/phase4_algorithm_factory/<run_id>/tables/phase4_algorithm_registry.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_matrix_manifest.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_exclusion_reasons.csv
```

Rules:

```text
FULL manifest first.
No T/I/P/R/L family is pruned by Phase 2/3 score.
Only physically/data-incompatible rows are excluded, with explicit reasons.
```

### 01:30-03:00: Dual-GPU Launcher Skeleton

Required gates before broad execution:

```text
G11_two_gpu_dynamic_balance:
  one worker per GPU, manifest-backed queue, work stealing

G12_cpu_parallel_execution:
  CPU-bound broad stages use process pool/vectorized batches

G13_thread_oversubscription_control:
  GPU feeder workers set torch/BLAS/OpenMP threads to one
```

Minimum viable implementation:

```text
medium chunk target based on 12288-equivalent smoke
chunk status = pending/running/done/failed/retry
atomic per-chunk CSV write
--resume-run <run_id>
--max-wall-time <seconds>
--stop-at-local-time HH:MM
```

### 03:00-04:00: CPU/GPU Agreement Expansion

Run deterministic agreement rows on both GPUs:

```text
R2:L0:I0:T6
R2:L2:I3:T6
R4:L8:I1+I2+I3+I8:T8
R4:L14:I1+I2+I3+I8:T8
```

Acceptance:

```text
CPU/GPU RMSE <= 0.01 mm on fixed subsets
same update/mask behavior
same range-bias policy
same finite-frame count
```

### 04:00-07:45: Partial Real Phase 4 Rows

Run a real partial matrix, not a fake benchmark:

```text
inputs: all R01-R17 captures and both tags where available
seed policy: at least S00-S01 for stochastic realistic rows
priority rows:
  T6/T8 raw-range rows that failed in Phase 3
  L0/L2/L8/L14/L15/L17 sensor representatives
  I0, I3, I1+I2+I3+I8 filter representatives
  R2/R4 range policies
```

Output:

```text
tables/phase4_partial_ranking.csv
tables/phase4_partial_timing.csv
tables/phase4_partial_gpu_balance.csv
tables/phase4_partial_failure_labels.csv
reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md
```

Do not produce heavy PNGs for every row tonight. Generate selected PNG/contact
sheets only for controls, clear failures, and best partial rows.

### 07:45-08:30: 5090D Handoff Bundle

Bundle should contain:

```text
IMU-Fusion-Simulation/
required official input/cache manifest
configs/
scripts/
runs/phase4_algorithm_factory/<run_id>/
README_5090D_RESUME.md
```

The 5090D machine should resume pending chunks, not recompute completed chunks.

### 08:30-08:50: Stop Cleanly

Actions:

```text
stop dispatching new chunks
let active chunks finish
flush manifest/tables/logs
write overnight summary
verify GPUs are idle
```

## Expected Output By Morning

At minimum:

```text
PASS/FAIL for G11/G12/G13
CPU/GPU agreement table for T6/T8 rows
measured chunk timing on 2x1080Ti
partial Phase 4 ranking/failure labels
resumable manifest for tomorrow's 5090D run
clear command for 5090D resume
```

## Morning Decision

If G11/G12/G13 pass:

```text
send handoff bundle to 5090D machine
resume FULL matrix there
```

If any gate fails:

```text
do not send a fake FULL launcher
send data/package plus failure report
fix the launcher before spending 5090D time
```
