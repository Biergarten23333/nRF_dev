# Phase 4 Algorithm Factory Plan

Generated: 2026-06-04

## Purpose

Phase 4 is the compute-heavy algorithm factory. Its job is to spend time and
hardware to find the best practical combination:

```text
IMU sensor L
+ IMU filter chain I
+ raw anchor-tag measurement policy R
+ final offline tag-trajectory solver T
```

This is not a hand-picked `T2/T3/T6` comparison. Phase 4 must cover the full
planned solver families `T1-T10`, plus `T11/T12` IMU-only diagnostics, and then
rank every compatible combination in the declared matrix.

Solved-position products `U/P` are kept as baselines and controls. The primary
Phase 4 winner should come from raw anchor-tag measurements unless the raw
measurement path is explicitly shown to be worse or invalid.

## Non-Negotiables

```text
1. filter != EKF.
2. EKF is only one T-family, not the definition of filtering.
3. I/P/R/T layers stay separate in naming, code, tables, and plots.
4. L10-L19 consumer/drone IMUs are not pruned for runtime.
5. CPU single-core broad sweeps are forbidden.
6. GPU backend must pass CPU golden agreement before full launch.
7. Final winner needs multiseed and stress evidence, not one lucky run.
8. FULL means non-selective: no solver/filter/sensor family may be pruned by
   Phase 2/3 score, runtime anxiety, or manual preference.
9. The only rows excluded from a FULL matrix are physically/data-incompatible
   rows, and each exclusion must be listed with a reason.
```

## Algorithm Library

### I Layer: IMU Preprocessing / Filter Chains

Minimum Phase 4 library:

```text
I0  no IMU filter
I1  low-pass/FIR
I2  notch vibration filter
I3  bias calibration + bias random walk
I4  Mahony/Madgwick attitude prefilter
I5  error-state preintegration
I6  ZUPT/ZARU low-motion constraint
I7  Hampel/median spike rejection
I8  adaptive IMU noise
```

Filter-chain grid:

```text
single filters:
  I0-I8

recommended stacks:
  I1+I3
  I1+I7
  I2+I3
  I3+I8
  I1+I3+I7
  I1+I2+I3+I8
  I1+I2+I3+I6+I8
```

Phase 4 may expand stack variants, but the manifest must record every filter
component and parameter. Do not hide filter choices inside a vague solver name.

### P Layer: UWB Solved-Position Filters

Minimum Phase 4 library:

```text
P0 no UWB position post-filter
P1 Hampel/median spike filter
P2 constant-velocity Kalman filter
P3 robust innovation gate
P4 session-window fixed-lag smoother
P5 full-session RTS upper-bound reference
```

### R Layer: Raw Anchor-Tag Measurement Filters / Corrections

Minimum Phase 4 library:

```text
R0 raw ranges passthrough
R1 raw ranges + sanity gate
R2 raw ranges + tag/anchor bias correction
R3 raw ranges + residual robust weighting
R4 raw ranges + NLOS/dropout mixture weighting
```

`R` is the raw per-link anchor-tag measurement stream. The Phase 4 loader should
preserve anchor ID, tag ID, timestamp, range, quality/residual fields, CIR/NLOS
metadata if present, and missing-link masks. Do not collapse `R` to solved
positions before T6-T10.

Range-bias rule:

```text
T6/T7/T8/T9/T10 raw-range rows must declare whether they consume R2/R3/R4.
Tight fusion with stable unmodeled range residual is expected to fail and must
be labelled as a failure-control, not as an IMU failure.
```

Phase 3 screening rule:

```text
Bad Phase 2/3 prototype results for T6/T8 do not exclude the raw-range family.
They identify what Phase 4 must fix and test: range-bias policy, NLOS/dropout
weighting, robust residuals, and the final T6/T7/T8/T9/T10 implementations.
```

### T Layer: Final Tag Fusion Solvers

Minimum Phase 4 solver coverage:

```text
T1  UWB-only baseline/control
T2  position-domain relative-motion prior / complementary style
T3  loose-coupled EKF
T4  loose-coupled UKF
T5  error-state EKF with IMU bias states
T6  tight raw-range EKF
T7  tight raw-range UKF
T8  robust tight EKF with NLOS/dropout mixture
T9  session-window fixed-lag factor graph
T10 full-session batch / RTS upper-bound solver
T11 IMU-only dead-reckoning diagnostic
T12 IMU-only with ZUPT/ZARU or pseudo-reset diagnostic
```

`T11/T12` diagnose inertial drift. They do not compete as final UWB+IMU fusion
rows because they do not consume UWB.

### Coupling Mode

Phase 4 must report not only the solver family, but also who corrects whom:

```text
uwb_only_control       UWB-only baseline/control
imu_only_diagnostic   IMU-only drift/reset diagnostic
uwb_corrects_imu      UWB position/range measurements correct IMU drift/bias
imu_corrects_uwb      IMU motion prior gates/smooths UWB positions/ranges
bidirectional_joint   UWB and IMU correct each other in one estimator
calibration_coestimate row also estimates range bias, delay, time offset,
                       IMU extrinsic, or NLOS state
```

Default `T` mapping:

```text
T1  uwb_only_control
T2  imu_corrects_uwb
T3  bidirectional_joint
T4  bidirectional_joint
T5  uwb_corrects_imu
T6  bidirectional_joint
T7  bidirectional_joint
T8  bidirectional_joint
T9  bidirectional_joint, optionally calibration_coestimate
T10 bidirectional_joint, optionally calibration_coestimate
T11 imu_only_diagnostic
T12 imu_only_diagnostic
```

The final report must include `coupling_mode` beside `T`, `information_use`,
and all metric columns. A low-error row is not interpretable unless the report
states whether the gain came from UWB correcting IMU drift, IMU correcting UWB
outliers, or joint/session calibration.

## Solver Information-Use Classes

This benchmark is a PC-side/offline study because the tag emits raw
measurements and the workstation solves the trajectory. Do not use
`online/offline` to describe algorithm causality; in this project `online`
means Tag-side firmware solving only. The class label below records how much
future/session information the PC-side solver used.

```text
causal-forward:
  T1-T8, plus causal I/P/R filters if implemented
  output at time t uses data available at or before t

session-window:
  P4/T9
  output at time t may use data up to t + lag

full-session:
  P5/T10
  output for the whole capture may use the whole capture
```

Therefore Phase 4 must report separate leaderboards:

```text
best full-session combo
best session-window combo with future-data window reported
best causal-forward combo, if useful as a reference
```

Do not mix these into one unlabeled ranking. A T10 winner is a valid final
session-solver result for this project.

## Matrix Shape

Use compatibility constraints instead of manual pruning:

```text
solved-position rows:
  A * U * P * L * I_chain * T1-T5 * seeds * stress
  baseline/control branch

raw-range rows:
  A * R * L * I_chain * T6-T10 * seeds * stress
  primary branch because the tag provides raw anchor-tag measurements

IMU-only diagnostics:
  A * L * I_chain * T11-T12 * seeds * stress
```

The point is not to implement one thousand unrelated algorithms. The point is
to implement the right algorithm families and then generate thousands of
meaningful parameterized combinations.

FULL matrix rule:

```text
Phase 4 FULL nominal = every compatible A/U/P/R/L/I/T/seed row in the declared
matrix.

Phase 4 FULL stress = every compatible nominal row crossed with every declared
stress profile, when a stress run is requested.

Phase 4 FULL expanded-I = every compatible row using the declared expanded IMU
filter-chain set, when the expanded-I run is requested.

No early Phase 2/3 result is allowed to remove T4/T7/T9/T10, T6/T8 raw-range
families, L10-L19 sensors, or any declared I/P/R filter component.
```

GPU memory is not allowed to change this definition. The launcher may stream the
FULL matrix in chunks, but the manifest must still contain the full compatible
row set before execution starts.

## GPU Backend Plan

### Why a GPU Backend Is Needed

The i7-8700K is fine for Phase 3 CPU confirmation, but Phase 4 matrix size is
large enough that broad CPU-only tight fusion would waste calendar time. Use the
two GTX 1080 Ti cards for batch numerical work.

Observed Phase 3 CPU timing from the first completed seed:

```text
per seed wall time:        about 46 min
IMU prior generation:      small
position-side fusion:      about 9 min/seed
raw measurement T6/T8:     about 36 min/seed
```

If only the raw measurement range/tight-fusion block is accelerated, expected
Phase 3 rerun runtime is roughly:

```text
2x range speedup:   about 2.4 h for 5 seeds
4x range speedup:   about 1.6 h for 5 seeds
8x range speedup:   about 1.3 h for 5 seeds
12x range speedup:  about 1.1 h for 5 seeds
```

This estimate excludes backend development and validation. A correct torch CUDA
backend is expected to take longer than the remaining current Phase 3 CPU run,
so do not stop a valid Phase 3 run just to retrofit GPU midway. Use the finished
CPU run as the golden reference for Phase 4.

Safe pilot policy while a CPU Phase 3 run is still active:

```text
do not stop or restart Phase 3
do not start a broad GPU sweep
use tiny subsets only, e.g. max_tracks <= 1-2 and max_frames <= 20-200
set torch CPU threads to 1
prefer GPU1 so the desktop GPU0 is less likely to stutter
record timing, agreement, GPU memory, and whether Phase 3 throughput changed
```

Initial smoke findings:

```text
run_phase4_gpu_pilot.py added as the first CPU/GPU agreement pilot.

tiny R2/L0/I0/T6, 1 track, 20 frames, cuda:1:
  padded 8x8 covariance update, float32:
    CPU/GPU RMSE about 9.3 mm -> not acceptable
  padded 8x8 covariance update, float64:
    CPU/GPU RMSE about 0.000034 mm -> algorithm mapping was correct
  3x3 information-form update, float32:
    CPU/GPU RMSE about 0.00020 mm -> acceptable

tiny R4/L8/I1+I2+I3+I8/T8, 1 track, 20 frames, cuda:1:
  3x3 information-form update, float32:
    CPU/GPU RMSE about 0.00020 mm -> acceptable
```

Phase 4 GPU backend should therefore use the information-form update for
batched raw-measurement solvers instead of padded 8x8 covariance matrices with
huge invalid-anchor variances.

### Tensor Layout

Represent batches like this:

```text
position samples:
  pos[B, T, 3]
  pos_mask[B, T]

IMU samples:
  imu_accel[B, T, 3]
  imu_gyro[B, T, 3]
  dt[B, T]
  imu_mask[B, T]

raw ranges:
  range[B, T, A]
  range_mask[B, T, A]
  anchor_xyz[B, A, 3]
  range_bias[B, A]

state:
  x[B, state_dim]
  P[B, state_dim, state_dim]
```

Batch over rows/tracks/seeds; loop over time only where the filter is truly
sequential.

### GPU-Suitable Families

```text
high priority:
  P filters, T2, T3, T5, T6, T7, T8 batch predict/update

medium priority:
  T9 residual/Jacobian scoring, session-window lag, robust weights

careful/limited:
  T10 full-session solver, because memory and long windows can dominate
```

### CPU Golden Agreement

Before any full GPU launch:

```text
1. Select fixed rows covering T1-T10 and T11/T12.
2. Run CPU golden.
3. Run GPU backend with the same seeds and inputs.
4. Compare P50/P95/RMSE/deltaR and per-sample trajectory tolerance.
5. Fail closed if masking, range-bias, dropout, or update acceptance differs.
```

Agreement table:

```text
runs/phase4_algorithm_factory/<run_id>/tables/phase4_cpu_gpu_agreement.csv
```

## Two-GPU Scheduler

Use cost-balanced scheduling, not row-count splitting.

Initial solver weights:

```text
T1:  1
T2:  1
T3:  2
T4:  4
T5:  4
T6:  8
T7: 12
T8: 14
T9: 20
T10: 30
T11: 2
T12: 3
```

Launcher behavior:

```text
1. Build full manifest.
2. Estimate row cost and memory.
3. Chunk compatible rows by solver family and tensor shape.
4. Greedy bin-pack chunks onto GPU0/GPU1.
5. Allow work stealing for not-yet-started chunks.
6. CPU writes CSV/PNG/report outputs and watches memory.
```

Resource-balance launch gates:

```text
G11_two_gpu_dynamic_balance:
  PASS only if the official launcher starts one worker per GPU, both workers pull
  chunks from a manifest-backed queue, and idle GPU workers can steal pending
  chunks from the heavier queue. Static GPU0/GPU1 half-splitting is not enough.

G12_cpu_parallel_execution:
  PASS only if CPU-bound Phase 4 stages use a process pool or vectorized batch
  backend with at least two active workers when enough jobs exist. A broad sweep
  with one saturated CPU thread and idle cores is a launch failure.

G13_thread_oversubscription_control:
  PASS only if GPU worker feeder processes set torch/BLAS/OpenMP CPU threads to
  one by default, while CPU-only fallback stages use multiple processes. This
  prevents one GPU feeder from silently stealing all CPU scheduling capacity.
```

The Phase 4 manifest must report `gpu_completed_cost`, `gpu_wall_time_s`,
`gpu_idle_time_s`, `gpu_steal_count`, `cpu_worker_count`, and per-stage worker
strategy. If GPU0 finishes early while GPU1 remains loaded for a long tail, the
run is valid only if work stealing had no pending compatible chunk left to move.

## Segmented / Nightly Execution

Phase 4 FULL may take longer than one night, so the official launcher must be
resumable. FULL still means every compatible declared row is evaluated; it does
not mean all rows must run in one uninterrupted wall-clock session.

Segmented-run rules:

```text
1. Build the full manifest once.
2. Mark every chunk as pending/running/done/failed/retry.
3. Write chunk outputs atomically before marking the chunk done.
4. Support --resume-run <run_id> without recomputing completed chunks.
5. Support --max-wall-time or --stop-at-local-time for overnight runs.
6. Near the cutoff, stop launching new chunks and let active chunks finish.
7. During daytime, do not run heavy GPU/CPU chunks unless explicitly requested.
8. Final ranking is produced only after all required chunks are done.
```

Recommended workstation schedule:

```text
night session:
  run both GPUs with medium chunks, target GPU SM 95-100%

morning cutoff:
  stop dispatching new chunks 20-30 minutes before the user needs the machine
  finish currently running chunks
  flush manifests/tables/logs

daytime:
  keep the machine free for interactive work
  optionally run only lightweight report aggregation

next night:
  resume the same run_id from pending chunks
```

This schedule is preferred over huge all-day chunks. Medium chunks keep GPU
utilization high while preserving resume granularity and work stealing.

## Phase 4 Stages

```text
4.0 registry freeze
  Write algorithm/filter registry and exact compatibility rules.

4.1 CPU golden implementation
  Implement missing T4/T7/T9/T10/T12 and planned I/P/R filters.

4.2 GPU backend
  Implement torch CUDA kernels/batched loops and CPU/GPU agreement tests.
  Start with the raw anchor-tag measurement T6/T7/T8/T9/T10 path, because
  Phase 3 timing shows it dominates wall-clock.
  Also implement G11/G12/G13 resource-balance gates before any official FULL
  matrix launch.

4.3 pilot algorithm sweep
  Run small rows across all T1-T10 families and every L group only to validate
  the backend. Pilot results do not prune the official FULL matrix.

4.4 full nominal sweep
  Run the non-selective full compatible matrix over all active L sensors,
  filter chains, T1-T10 families, diagnostics, and seeds.

4.5 stress sweep
  If stress is part of the official Phase 4 claim, run it as a full compatible
  stress matrix too. Smaller winner/failure-control stress views may be produced
  for quick reading, but they do not replace the FULL stress table.

4.6 ranking and visual audit
  Produce final ranking, per-family best, per-sensor best, failure labels, and
  selected/full PNG sheets.
```

## Ranking Verdict

Final ranking must include at least:

```text
P50
P95
RMSE
deltaR
radius error
turn-center error
divergence count
NIS/update accept sanity for fusion rows
seed mean/std/worst-case
stress pass/fail
future-data/session window for P4/T9/T10
```

Do not pick the winner from one metric. A low P50 row with bad P95, unstable
range residual, or visual trajectory distortion is not the final recommendation.

## Expected Final Answer

Phase 4 should produce:

```text
best full-session combo
best session-window combo with future-data window reported
best causal-forward combo, if useful as a reference
best low-cost IMU combo
best robust-under-stress combo
best UWB-only control
best per T-family row
best per L-sensor row
explicit excluded/failure rows with reasons
```
