# Codex Handoff: UWB+IMU Fusion Simulation

Generated: 2026-06-05

This file is the black-box handoff for continuing the UWB+IMU simulation on a
new RTX 5090D workstation. Read this before running or rewriting Phase 4.

## Mission

Find the best practical PC-side tag trajectory solver combination for the ROTO
captures:

```text
anchor/layout A
+ pure UWB control U/P or raw anchor-tag stream R
+ simulated IMU sensor L
+ IMU preprocessing/filter chain I
+ final PC-side tag trajectory solver T
+ seeds/stress
```

The desired final answer is not a hand-picked `T3` or `T6` comparison. It is a
FULL compatible matrix result with leaderboards for:

```text
best full-session combo
best session-window combo
best causal-forward combo
best low-cost IMU combo
best robust-under-stress combo
best UWB-only control
best per T-family row
best per L-sensor row
explicit failed/excluded rows with reasons
```

## Transfer Rule

Prefer transferring the whole directory:

```text
autopos_pipeline/28052026_Erlangen_Official
```

Do not transfer only `Analysis/IMU-Fusion-Simulation` unless you have already
verified that every loader input it needs is inside that folder. The simulation
line uses caches, reports, official UWB/Vicon/ROTO outputs, and alignment data
from the surrounding official analysis tree.

Minimum useful project root on the new machine:

```text
<repo>/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation
```

Recommended command style from the old machine:

```bash
rsync -aH --info=progress2 \
  /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/ \
  <new-host>:~/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/
```

If storage is tight, preserve all `configs/`, `scripts/`, `manifests/`,
`cache/`, Phase 1-4 summary reports/tables, and the upstream official input
data. Heavy old PNG folders can be regenerated later, but losing input/cache
files will waste more time.

## Terms That Must Not Be Changed

The naming source of truth is:

```text
NAMING_RULES.md
configs/fusion.yaml
PHASE4_ALGORITHM_FACTORY_PLAN.md
```

Critical meanings:

```text
U-series = pure UWB solver family, old T1-T4 renamed U1-U4
T-series = final PC-side tag trajectory solver family
B0       = A0 + U4 + P0 + T1
```

`B0` is the current production/dynamic UWB-only baseline:

```text
B0 = A0/U4/P0/T1
ROTO track-median 3D P50/P95 = 105.8 / 231.8 mm
deltaR = 80.1 mm
```

Do not call `U4` fusion. It is old pure-UWB T4 renamed.

Do not use `online/offline` to mean causality. In this project:

```text
online solver  = Tag-side/firmware-side real-time solver
offline solver = PC-side solver fed by recorded or streamed measurements
```

Inside PC-side offline solving, use:

```text
causal-forward solver = output at time t uses data up to t
session-window solver = output at time t may use a declared future window
full-session solver   = output may use the whole recorded capture/session
```

Every fusion row must report `coupling_mode`:

```text
uwb_only_control
imu_only_diagnostic
uwb_corrects_imu
imu_corrects_uwb
bidirectional_joint
calibration_coestimate
```

## Phase 0-3 Results Already Done

### Phase 0/1 Vertical Slice

Current important run:

```text
runs/phase1_vertical_slice/20260604T161344Z
```

Important files:

```text
runs/phase1_vertical_slice/20260604T161344Z/reports/PHASE1_VERTICAL_SLICE.md
runs/phase1_vertical_slice/20260604T161344Z/reports/VALIDATION_GATES.md
runs/phase1_vertical_slice/20260604T161344Z/tables/phase1_summary.csv
runs/phase1_vertical_slice/20260604T161344Z/figs/contact_sheets/
```

Phase 1 established:

```text
B0_A0_U4_P0_T1:
  P50/P95/deltaR = 105.8 / 231.8 / 80.1 mm

X_A0_R2_L0_I0_T6:
  P50/P95/deltaR = 89.3 / 176.5 / 53.4 mm
```

Important warning: Phase 1 `T6` was a limited prototype / solved-position proxy
update. It visually shows that IMU-like correction can make trajectories look
better, but it is not the final raw-range tight fusion proof.

Useful visual reference:

```text
runs/phase1_vertical_slice/20260604T161344Z/figs/contact_sheets/X_A0_R2_L0_I0_T6__contact_sheet.png
```

### Phase 2 Screening

Current important run:

```text
runs/phase2_screening/20260604T163422Z
```

Important files:

```text
runs/phase2_screening/20260604T163422Z/reports/VALIDATION_GATES.md
runs/phase2_screening/20260604T163422Z/reports/PHASE2_SCREENING.md
runs/phase2_screening/20260604T163422Z/reports/PHASE2_VISUAL_AUDIT.md
runs/phase2_screening/20260604T163422Z/tables/phase2_ranked_top50.csv
runs/phase2_screening/20260604T163422Z/stage0_gate_fulfillment/tables/g3_range_bias_policy_R2.csv
```

Validation gates:

```text
G3_range_bias_policy  PASS
G5_noise_seed_repeats PASS_SCREENING
```

Phase 2 top screening rows included:

```text
X_A0_U4_P2_L8_I1+I2+I3+I8_T3
  P50/P95/deltaR = 96.3 / 183.3 / 86.8 mm
  verdict = FUSION_HURTS_GEOMETRY

X_A0_U4_P0_L8_I1+I2+I3+I8_T3
  P50/P95/deltaR = 97.3 / 197.5 / 77.0 mm
  verdict = FUSION_HELPS_DEPLOYABLE
```

Interpretation: Phase 2 was useful for finding candidate behavior and visual
failure modes. It is not allowed to prune Phase 4 FULL solver/sensor/filter
families.

### Phase 3 Full Confirmation Over Implemented Families

Current important run:

```text
runs/phase3_full_confirmation/20260604T180859Z
```

Important files:

```text
runs/phase3_full_confirmation/20260604T180859Z/reports/PHASE3_FULL_CONFIRMATION.md
runs/phase3_full_confirmation/20260604T180859Z/reports/PHASE3_INTERPRETED_RESULTS.md
runs/phase3_full_confirmation/20260604T180859Z/tables/phase3_final_ranking.csv
runs/phase3_full_confirmation/20260604T180859Z/stage1_full_nominal_multiseed/tables/
```

Scope:

```text
7925 seed rows
1585 aggregated rows
5 seeds
269450 track metric rows
workers = 10
elapsed = 13893.71 s = 3.86 h
```

Implemented T families in Phase 3:

```text
T2, T3, T5, T6, T8, T11
```

Not yet algorithm-complete at that time:

```text
T4, T7, T9, T10, T12
P1, P3, P4, P5
R0, R1, R3
I5, I6
```

Best non-diagnostic implemented fusion row:

```text
X_A0_U4_P0_L8_I0_T5
P50/P95/deltaR = 104.8 / 229.4 / 82.5 mm
vs B0 = -1.1 / -2.4 / +2.5 mm
```

Best raw measurement prototype row:

```text
X_A0_R4_L14_I1+I2+I3+I8_T6
P50/P95/deltaR = 381.5 / 604.7 / 118.2 mm
```

Interpretation:

```text
No strong Phase 3 winner over B0.
T11 rows are IMU-only diagnostics, not fusion winners.
Current T6/T8 prototype failure is evidence that raw-range handling needs work.
It is not a reason to prune T6-T10 from Phase 4.
```

## Phase 4 Bootstrap Already Done On 2x1080Ti

Current important run:

```text
runs/phase4_algorithm_factory/nightly_1080ti_20260604T231033Z
```

Important report:

```text
runs/phase4_algorithm_factory/nightly_1080ti_20260604T231033Z/reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md
```

Result:

```text
146/146 chunks done
580/580 rows done
failed = 0
elapsed = 3344.6 s
```

Resource gates:

```text
G11_two_gpu_dynamic_balance PASS
G12_cpu_parallel_execution  PASS
G13_thread_oversubscription PASS
```

Measured current-path timing:

```text
cpu_golden_mean          = 20.58 s
simulate_imu_prior_mean  = 0.58 s
torch_gpu_mean           = 2.24 s
```

Important interpretation: current bootstrap is CPU-golden/reference limited.
The GPU math is fast, but the current runner still computes too much CPU golden
work per row. A real 5090D Phase 4 must not repeat CPU golden for every row.

Numerical agreement:

```text
A1_accept_rate_match         PASS
A2_p95_cpu_gpu_xyz_agreement PASS, max p95 diff = 8.586 mm
A3_single_frame_outlier      REVIEW, blocking final numerical claim
```

Outlier requiring inspection before final claim:

```text
max_single_frame_xyz_diff = 844.065 mm
row/track = R4:L0:I1+I2+I3+I8:T6 / R13 / BS2DCE
```

This bootstrap is not the final Phase 4 FULL result.

## Existing Scripts

Run from:

```bash
cd <repo>/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation
```

Phase 1:

```bash
python3 scripts/run_phase0_phase1_vertical_slice.py --help
```

Phase 2:

```bash
python3 scripts/run_phase2_screening.py --help
python3 scripts/run_phase2_stage1_screening.py --help
python3 scripts/run_phase2_stage2_visual_audit.py --help
```

Phase 3:

```bash
python3 scripts/run_phase3_full_confirmation.py --help
```

Phase 4 current GPU pilot:

```bash
python3 scripts/run_phase4_gpu_pilot.py --help
```

Phase 4 current nightly/bootstrap runner:

```bash
python3 scripts/run_phase4_nightly_bootstrap.py --help
```

## First Actions On The 5090D Machine

1. Verify the environment with `ENV.md`.
2. Verify that the transferred project can compile and read configs:

```bash
cd <repo>/autopos_pipeline/28052026_Erlangen_Official/Analysis/IMU-Fusion-Simulation
python3 -m py_compile scripts/*.py
python3 - <<'PY'
import pathlib, yaml
root = pathlib.Path.cwd()
print("root", root)
for rel in [
    "NAMING_RULES.md",
    "configs/fusion.yaml",
    "configs/sensors.yaml",
    "runs/phase2_screening/20260604T163422Z/reports/VALIDATION_GATES.md",
    "runs/phase3_full_confirmation/20260604T180859Z/reports/PHASE3_INTERPRETED_RESULTS.md",
    "runs/phase4_algorithm_factory/nightly_1080ti_20260604T231033Z/reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md",
]:
    p = root / rel
    print(("OK " if p.exists() else "MISS "), rel)
yaml.safe_load((root / "configs/fusion.yaml").read_text())
PY
```

3. Verify CUDA/PyTorch:

```bash
python3 - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda available", torch.cuda.is_available())
print("device count", torch.cuda.device_count())
if torch.cuda.is_available():
    for i in range(torch.cuda.device_count()):
        print(i, torch.cuda.get_device_name(i), torch.cuda.get_device_properties(i).total_memory / 1024**3, "GiB")
PY
```

4. Run a tiny CUDA pilot before anything broad:

```bash
python3 scripts/run_phase4_gpu_pilot.py \
  --phase2-run 20260604T163422Z \
  --run-id smoke_5090d_cuda0 \
  --device cuda:0 \
  --dtype float32 \
  --torch-threads 1 \
  --max-tracks 1 \
  --max-frames 20 \
  --gpu-repeat 3 \
  --rows R2:L0:I0:T6 R4:L8:I1+I2+I3+I8:T8
```

5. Run a 15-30 minute bootstrap on the 5090D:

```bash
python3 scripts/run_phase4_nightly_bootstrap.py \
  --run-id bootstrap_5090d_$(date -u +%Y%m%dT%H%M%SZ) \
  --phase2-run 20260604T163422Z \
  --devices cuda:0 \
  --workers-per-device 4 \
  --chunk-size 8 \
  --partial-max-tracks 0 \
  --partial-max-frames 0 \
  --max-wall-time 1800 \
  --chunk-timeout-s 7200 \
  --monitor-interval 10
```

If GPU utilization is intermittent and CPU/RAM are comfortable on the 9950X,
try `--workers-per-device 6` and then `8`. Stop increasing once GPU utilization
does not improve or CPU reference work becomes the limit.

Resume an existing bootstrap run:

```bash
python3 scripts/run_phase4_nightly_bootstrap.py \
  --resume-run <run_id> \
  --phase2-run 20260604T163422Z \
  --devices cuda:0 \
  --workers-per-device 6 \
  --max-wall-time 3600
```

## What Phase 4 Still Needs

The 5090D should be used for the real algorithm factory, not only for the
current T6/T8 bootstrap. Implement or verify these before final FULL claim:

```text
4.0 registry freeze
  Build manifest from configs/fusion.yaml and NAMING_RULES.md.
  Add coupling_mode, information_use, row_class, seed_id, stress_id.
  Exclude only physically/data-incompatible rows, with explicit reasons.

4.1 CPU golden implementation
  Implement missing T4, T7, T9, T10, T12.
  Implement planned P1/P3/P4/P5, R0/R1/R3, I5/I6 if they remain in the declared matrix.

4.2 GPU backend
  Tensorize rows/tracks/seeds over torch.
  Batch raw ranges as range[B,T,A], mask[B,T,A], anchor_xyz[B,A,3], range_bias[B,A].
  Keep CPU golden only for sampled agreement gates, not every production row.
  Use information-form updates for float32 raw-range filters.

4.3 pilot algorithm sweep
  Small fixed rows across every T1-T10 family and L group.
  Pilot is for backend validation only. It must not prune FULL matrix.

4.4 full nominal sweep
  Run every compatible A/U/P/R/L/I/T/seed row.
  Segment/resume is allowed; selection/pruning is not.

4.5 stress sweep
  If stress is included in the claim, cross the compatible matrix with declared stress profiles.

4.6 ranking and visual audit
  Produce final ranking, per-family best, per-sensor best, failure labels,
  selected/full PNG sheets, and final PASS/FAIL gates.
```

## Current Runtime Reality

On the old 2x1080Ti + i7-8700K:

```text
current bootstrap architecture, FULL extrapolation:
  too slow, because CPU golden dominates

proper tensorized GPU production backend:
  expected to be much faster, but must be implemented
```

On a 9950X + 96GB RAM + RTX 5090D:

```text
current bootstrap code without rewrite:
  likely several times faster than the 8700K/2x1080Ti, mainly because CPU is stronger

proper Phase 4 GPU backend:
  likely order-of-magnitude calendar improvement over the current CPU-golden-heavy path
```

Do not report a final runtime estimate without measuring a 15-30 minute 5090D
bootstrap and a small true tensorized production batch.

## Final Ranking Rules

Do not pick winners from P50 alone. Every ranking table must include:

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
coupling_mode
information_use
future-data/session window for P4/T9/T10
```

A low P50 with bad P95, distorted circle geometry, high deltaR, unstable range
residuals, or bad visual sheets is not the final winner.

## Hard Warnings For The Next Codex

```text
Do not call Phase 1 T6 the final raw tight fusion result.
Do not call Phase 3 the final algorithm search.
Do not prune T4/T7/T9/T10 because Phase 3 did not implement them.
Do not prune T6/T8/T9/T10 because current T6/T8 prototypes failed.
Do not mix IMU-only T11/T12 into fusion winner rows.
Do not call U4 a fusion solver.
Do not use "online/offline" to mean causal/full-session.
Do not hide I/P/R filters inside T names.
Do not run a broad CPU-only single-core sweep.
Do not chase VRAM usage; chase GPU utilization and correct batching.
Do not run CPU golden for every production Phase 4 row on 5090D.
```

## Useful Files To Open First

```text
NAMING_RULES.md
SIMULATION_PLAN.md
PHASE4_ALGORITHM_FACTORY_PLAN.md
NIGHTLY_8H_PHASE4_1080TI_PLAN.md
ENV.md
configs/fusion.yaml
configs/sensors.yaml
configs/range_bias_policy.md
runs/phase3_full_confirmation/20260604T180859Z/reports/PHASE3_INTERPRETED_RESULTS.md
runs/phase4_algorithm_factory/nightly_1080ti_20260604T231033Z/reports/PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md
```
