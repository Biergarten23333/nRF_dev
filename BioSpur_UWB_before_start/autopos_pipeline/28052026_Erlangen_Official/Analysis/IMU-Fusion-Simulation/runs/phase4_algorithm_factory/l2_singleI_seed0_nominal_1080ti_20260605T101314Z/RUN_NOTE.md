# Phase 4 L2 Single-I Seed0 Nominal 1080Ti Run Note

Started: 2026-06-05T10:13:14Z
Actually launched locally: 2026-06-05T12:15:24+02:00
Driver PID: 641467
Completed: 2026-06-05T12:19:26+02:00
Completion status: 17/17 chunks done, 0 failed, 0 pending

## Purpose

Run the current implemented Phase 4 GPU bootstrap with the IMU sensor dimension
restricted to:

```text
L2 = MPU6050-like / JY61P-like 6-axis IMU
```

This is the local 2x GTX 1080 Ti / i7-8700K nominal seed0 run requested before
moving the heavier Phase 4 work to the RTX 5090D workstation.

## Exact Scope

Important: this run uses the current bootstrap launcher, which implements the
raw-range `R2/R4 x T6/T8` path only. It is not the final 95,256-row Phase 4 FULL
production runner.

Current matrix:

```text
L = L2 only
R = R2, R4
I = I0, I1, I3, I4, I7, I8, I1+I3+I7, I1+I2+I3+I8
T = T6, T8
tracks = all 34 ROTO tracks
frames = all raw frames
seed = seed0 nominal, encoded through this fixed run_id
```

The actual row count is:

```text
agreement rows = 1
partial rows = 2 R * 1 L * 8 I * 2 T = 32
total rows = 33
chunks = 1 agreement chunk + 16 partial chunks = 17
```

This is why the run ended in about 4 minutes. It finished the declared bootstrap
scope; it did not crash.

## Launch Command

```bash
python3 autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation/scripts/run_phase4_nightly_bootstrap.py \
  --run-id l2_singleI_seed0_nominal_1080ti_20260605T101314Z \
  --l-ids L2 \
  --devices cuda:0 cuda:1 \
  --workers-per-device 3 \
  --chunk-size 2 \
  --partial-max-tracks 0 \
  --partial-max-frames 0 \
  --chunk-timeout-s 7200 \
  --monitor-interval 30
```

## Where To Look

```text
Run dir:
runs/phase4_algorithm_factory/l2_singleI_seed0_nominal_1080ti_20260605T101314Z

Main log:
runs/phase4_algorithm_factory/l2_singleI_seed0_nominal_1080ti_20260605T101314Z/logs/driver.log

Progress:
runs/phase4_algorithm_factory/l2_singleI_seed0_nominal_1080ti_20260605T101314Z/tables/phase4_nightly_chunks.csv

Resource monitor:
runs/phase4_algorithm_factory/l2_singleI_seed0_nominal_1080ti_20260605T101314Z/tables/phase4_nightly_resource_samples.csv

Report:
runs/phase4_algorithm_factory/l2_singleI_seed0_nominal_1080ti_20260605T101314Z/reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md
```

## Initial Live Check

At about 45 seconds after launch:

```text
driver PID = 641467 running
completed chunks = 1 agreement chunk
running chunks = 5 partial_raw chunks
pending chunks = 11
CPU workers = 6 child python processes, roughly one full core each
RAM = about 14 GiB used, about 17 GiB available
GPU = both GPUs have active python compute processes, but utilization is
      bursty/feeding-limited in the current bootstrap backend
```

## Final Check

```text
elapsed = 240.2 s
chunks done/failed/pending = 17/0/0
rows done/total = 33/33
partial chunks by GPU = cuda:0 -> 8, cuda:1 -> 8
cpu_golden mean = 19.86 s per row
torch_gpu mean = 2.35 s per row
```

Agreement gates:

```text
A1 accept-rate match = PASS
A2 CPU/GPU p95 XYZ agreement = PASS, max p95 diff 1.628 mm
A3 single-frame outlier audit = REVIEW
  max single-frame diff 800.467 mm at R4:L2:I1+I2+I3+I8:T6 / R13 / BS2DCE
```

Resource gate note:

```text
G11 reports FAIL because the monitor sampled GPU util every 30 s and missed the
short CUDA bursts. Child processes did use both cuda:0 and cuda:1, but this
bootstrap is CPU-golden/feed limited and is not a sustained full-GPU workload.
```

This run is a backend/agreement bootstrap. It does not produce the final
accuracy leaderboard against OptiTrack. The production `single-I FULL` runner
still needs to be implemented separately.

## Reminder For 5090D

Do not call this the final Phase 4 FULL result. The 5090D production run still
needs the real `scripts/run_phase4_full_factory.py` style launcher described in
`ENV.md` and `CODEX_HANDOFF.md`.
