# IMU Fusion Simulation Naming Rules

Generated: 2026-06-04

This document fixes the naming scheme for the IMU fusion simulation line.
The main rule is simple:

```text
U-series = pure UWB solver family, renamed from the old T-series.
T-series = final offline tag-trajectory solver family, including UWB-only
           controls and UWB+IMU fusion.
```

Do not introduce a new `T5` as a pure extension of the old `T1-T4`.
Once IMU enters the solver, the old pure-UWB meaning of `T` is no longer valid.

## Symbols

```text
A = anchor/layout source
U = pure UWB solver output/control, old T1-T4 renamed U1-U4
R = raw anchor-tag measurement stream and range preprocessing
P = UWB solved-position post-filter
L = IMU hardware or sensor model
I = IMU preprocessing/filter
T = final offline tag-trajectory solver, not firmware tag-side code
B = named baseline row
X = full experiment combination ID
```

## A: Anchor/Layout Source

```text
A0 = AutoPos v4-io rigid no-scale, production baseline
A1 = one-baseline scale correction, best ablation/control
A2 = Vicon/OptiTrack truth anchors + delaycal, oracle control
A3 = full similarity scale-to-Vicon + delaycal, diagnostic control
```

Primary Step 0 baseline:

```text
A0 = AutoPos v4-io rigid no-scale
```

## U: Pure UWB Solver

The existing official analysis used `T1-T4` for pure UWB tag solvers.
Inside this IMU fusion simulation line, rename them to `U1-U4`.

```text
U1 = old T1 pure UWB solver
U2 = old T2 pure UWB solver
U3 = old T3 pure UWB solver
U4 = old T4 pure UWB solver
```

Old notation:

```text
v4-io/T4
```

New notation:

```text
A0/U4
```

## R: Raw UWB Range Stream

Use `R` when the fusion solver consumes the raw anchor-tag measurements directly
instead of solved UWB positions. This is the primary final pipeline for the
offline simulation.

`R` should be treated as the per-link measurement packet stream, not only a
pre-solved position. When fields are available, keep anchor ID, tag ID,
timestamp, measured range, quality/residual metadata, CIR/NLOS indicators, and
missing-link representation.

```text
R0 = raw ranges with selected A-layout anchors
R1 = raw ranges + sanity gate
R2 = raw ranges + tag/anchor bias correction
R3 = raw ranges + residual robust weighting
R4 = raw ranges + NLOS/dropout mixture weighting
```

## P: UWB Position Post-Filter

Use `P` only after a pure UWB solver has already produced solved positions.

```text
P0 = no UWB position post-filter
P1 = Hampel/median spike filter
P2 = constant-velocity Kalman filter
P3 = robust innovation gate
P4 = session-window fixed-lag smoother
P5 = full-session RTS upper-bound reference
```

## L: IMU Hardware/Sensor Model

```text
L0 = perfect Vicon IMU
L1 = perfect sampled IMU, ODR/timestamp only
L2 = MPU6050-like 6-axis IMU
L3 = MPU9250/ICM20948-like 9-axis IMU
L4 = LIS2DH12 accel-only model
L5 = BMI270/LSM6DSO-like modern 6-axis IMU
L6 = industrial high-grade IMU
L7 = bad cheap IMU, high bias/noise/vibration sensitivity
L8 = mis-mounted IMU/extrinsic error stress
L9 = future real tag IMU replay
L10 = InvenSense MPU-6050 legacy cheap 6-axis IMU
L11 = InvenSense ICM-20948 9-axis IMU, simulated as accel+gyro only
L12 = InvenSense ICM-20602 FPV/drone 6-axis IMU
L13 = InvenSense ICM-42605 modern 6-axis IMU
L14 = InvenSense ICM-42670-P drone/robotics 6-axis IMU
L15 = InvenSense ICM-42688-P high-precision 6-axis IMU
L16 = InvenSense ICM-45686 premium 6-axis IMU
L17 = Bosch BMI270 low-power consumer 6-axis IMU
L18 = Bosch BMI088 drone/robotics vibration-robust 6-axis IMU
L19 = ST LSM6DSV16X high-end consumer 6-axis IMU
L20 = Xsens/Movella MTi-3-5A-T industrial AHRS module, simulated as accel+gyro
```

The exact noise, bias, vibration, drift, sampling, and extrinsic parameters live
in `configs/sensors.yaml`.

`L10-L19` are datasheet-backed consumer/drone-grade IMU candidates with a hard
price gate of <= 100 EUR per chip. The simulation must use their recorded
datasheet noise/offset/temperature fields to derive drift-producing residual
parameters; do not tune their drift manually after looking at fusion results.

`L20+` may be calibrated industrial IMU/AHRS modules used as lab/reference
candidates outside the <=100 EUR chip-only price gate. They must still be backed
by vendor/distributor specifications and must not be tuned after looking at
fusion results.

## I: IMU Preprocessing/Filter

`I` is the IMU-side preprocessing/filter family. It is not the final fusion
solver, and it is not synonymous with EKF.

```text
I0 = no IMU filter
I1 = low-pass/FIR filter
I2 = notch vibration filter
I3 = bias calibration + bias random-walk model
I4 = Mahony/Madgwick attitude filter
I5 = error-state preintegration
I6 = ZUPT/ZARU low-motion constraint
I7 = Hampel/median spike rejection
I8 = adaptive IMU noise filter
```

Multiple IMU filters may be combined with `+` when needed:

```text
I1+I3+I7 = low-pass + bias model + spike rejection
```

Filter layers must stay separate:

```text
I = IMU preprocessing/filter
P/R = UWB position/range filtering and correction
T = final tag fusion estimator / solver
```

EKF is only one possible `T` estimator family. Do not use "filter" to mean only
EKF; IMU and UWB filters include low-pass, notch, Hampel/median, bias
calibration, adaptive noise, NLOS/range-bias correction, and smoothing.

## T: Final PC-Side Tag-Trajectory Solver

`T` is reserved for the final PC-side tag-trajectory solver family in this
simulation line. It can be UWB-only or UWB+IMU, but it is not tag firmware and
it is not the old pure-UWB `T1-T4`.

Do not use `online/offline` to describe how much future information an algorithm
uses. In this project:

```text
online solver  = Tag-side/firmware-side real-time solver
offline solver = PC-side solver fed by recorded or streamed raw measurements
```

Within PC-side offline solving, use these information-use labels instead:

```text
causal-forward solver = output at time t uses data up to t
session-window solver = output at time t may use a declared future window
full-session solver   = output may use the whole recorded capture/session
```

```text
T0  = no final solver / passthrough diagnostic
T1  = UWB-only baseline using U4 positions
T2  = position-domain IMU relative-motion prior, PI1-style
T3  = loose-coupled EKF, IMU prediction + U4 position update
T4  = loose-coupled UKF
T5  = error-state EKF with IMU bias states
T6  = tightly-coupled raw-range EKF
T7  = tightly-coupled raw-range UKF
T8  = robust tight EKF with NLOS/dropout mixture
T9  = session-window fixed-lag factor graph
T10 = full-session batch/RTS upper-bound solver
T11 = IMU-only strapdown/dead-reckoning diagnostic
T12 = IMU-only with ZUPT/ZARU or pseudo-reset diagnostic
```

`T11` and `T12` are not fusion rows because they do not consume UWB. They are
required drift diagnostics for interpreting whether UWB+IMU fusion is actually
controlling inertial drift.

## Coupling Mode: Who Corrects Whom

Every fusion row must report `coupling_mode`. This is separate from the solver
family (`EKF`, `UKF`, factor graph, etc.) and separate from the information-use
class (`causal-forward`, `session-window`, `full-session`).

```text
uwb_only_control:
  UWB-only baseline/control. No IMU correction path.

imu_only_diagnostic:
  IMU-only drift diagnostic. No UWB correction path.

uwb_corrects_imu:
  IMU predicts motion; UWB position/range measurements correct IMU drift,
  velocity, trajectory, and optionally IMU bias states.

imu_corrects_uwb:
  IMU motion prior corrects, gates, smooths, or regularizes UWB solved
  positions/ranges. This is useful for UWB jump/NLOS suppression.

bidirectional_joint:
  UWB residuals correct IMU drift/bias while IMU motion priors correct/gate UWB
  measurements in the same estimator.

calibration_coestimate:
  The solver also estimates measurement-model parameters such as range bias,
  anchor/tag delay, time offset, IMU extrinsic, or NLOS state.
```

Default coupling labels for the declared `T` families:

```text
T1  = uwb_only_control
T2  = imu_corrects_uwb
T3  = bidirectional_joint
T4  = bidirectional_joint
T5  = uwb_corrects_imu
T6  = bidirectional_joint
T7  = bidirectional_joint
T8  = bidirectional_joint
T9  = bidirectional_joint or calibration_coestimate, depending on enabled states
T10 = bidirectional_joint or calibration_coestimate, depending on enabled states
T11 = imu_only_diagnostic
T12 = imu_only_diagnostic
```

If a `T9/T10` row estimates range bias, time offset, or extrinsic parameters, it
must include `calibration_coestimate` in the report, even if it is also a
bidirectional UWB/IMU fusion row.

## Baselines

```text
B0 = A0 + U4 + P0 + T1
   = AutoPos v4-io + old T4 pure UWB output + no UWB post-filter
   = current UWB-only production/dynamic baseline

B1 = A1 + U4 + P0 + T1
   = one-baseline scale correction + old T4 pure UWB output
   = best ablation/control baseline

B2 = A2 + U4 + P0 + T1
   = Vicon/OptiTrack truth anchors + delaycal + old T4 pure UWB output
   = oracle anchor-control baseline
```

Important:

```text
B0 is a baseline row, not a fusion solver.
U4 is the renamed old T4 pure UWB solver.
T1 is the new final Tag-solver label for UWB-only output.
```

The current Step 0 primary dynamic baseline is:

```text
B0 = A0/U4/P0/T1
ROTO track-median 3D P50/P95 = 105.8 / 231.8 mm
```

## Full Experiment ID

Loose-coupled experiments consume solved UWB positions:

```text
X_<A>_<U>_<P>_<L>_<I>_<T>
```

Tightly-coupled experiments consume raw UWB ranges:

```text
X_<A>_<R>_<L>_<I>_<T>
```

IMU-only diagnostics consume only the simulated IMU stream and an explicit
initialization policy:

```text
X_<A>_<L>_<I>_<T>
```

Examples:

```text
X_A0_U4_P0_L0_I0_T2
= v4-io + old T4 solved UWB positions + perfect Vicon IMU
  + no IMU filter + PI1-style position-domain fusion

X_A0_U4_P0_L2_I3_T3
= v4-io + old T4 solved UWB positions + MPU6050-like IMU
  + bias model + loose-coupled EKF

X_A0_U4_P2_L2_I3_T5
= v4-io + old T4 solved UWB positions + UWB CV post-filter
  + MPU6050-like IMU + bias model + error-state EKF

X_A0_R2_L2_I3_T6
= v4-io anchors + bias-corrected raw UWB ranges
  + MPU6050-like IMU + bias model + tight raw-range EKF

X_A0_R4_L2_I8_T8
= v4-io anchors + robust/NLOS-weighted raw UWB ranges
  + MPU6050-like IMU + adaptive IMU noise + robust tight EKF

X_A0_L2_I3_T11
= v4-io evaluation frame + MPU6050-like IMU + bias model
  + IMU-only strapdown/dead-reckoning diagnostic
```

## Step 0 Freeze

Before any IMU fusion experiment, freeze the UWB-only reference:

```text
A0 = AutoPos v4-io rigid no-scale
U4 = old T4 pure UWB solver
P0 = no UWB position post-filter
T1 = new UWB-only final offline tag-trajectory solver label
B0 = A0/U4/P0/T1
```

All IMU fusion rows must report improvement against `B0`, and selected controls
may also compare against `B1` and `B2`.

## Guardrails

- Do not use `T5` to mean "old T4 plus IMU" without defining the full pipeline.
- Do not call `U4` a fusion solver. It is pure UWB.
- Do not call `P` filters fusion algorithms. They only post-filter UWB positions.
- Do not call `L` sensor models algorithms. They only define simulated IMU realism.
- A row is true fusion only if its final `T` solver consumes both UWB and IMU.
- Raw-range tight fusion must use `R*`, not `U*`, because it bypasses solved UWB positions.
- Keep old reports unchanged when discussing historical results; use the new names only inside `IMU-Fusion-Simulation`.
