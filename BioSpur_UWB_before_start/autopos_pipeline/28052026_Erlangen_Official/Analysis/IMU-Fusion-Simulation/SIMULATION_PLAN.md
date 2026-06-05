# IMU Fusion Simulation Plan

Generated: 2026-06-04

This plan defines how to run the Vicon-derived synthetic IMU and UWB+IMU fusion
study without turning the workspace into a pile of unrelated CSVs.

Read this together with `NAMING_RULES.md`.

For the algorithm-complete GPU sweep, also read
`PHASE4_ALGORITHM_FACTORY_PLAN.md`.

## Goal

Build a controlled benchmark for offline tag-trajectory solvers. The primary
measurement source is the raw anchor-tag measurement stream; solved UWB
positions are baseline/control products, not the final limitation of the study.

```text
raw anchor-tag measurements
  -> UWB-only baseline/control
  -> Vicon-perfect IMU diagnostic
  -> realistic IMU models
  -> offline UWB+IMU trajectory fusion
```

The central question is not only whether an IMU helps. The real question is:

```text
Which offline trajectory solver T, IMU L, IMU filter I, and raw-measurement
policy R gives the best trajectory under realistic IMU bias/noise/vibration and
realistic UWB outliers/dropout?
```

Terminology:

```text
filter != EKF
```

This project has three different filtering/estimation layers:

```text
I layer:
  IMU preprocessing/filtering, e.g. low-pass, FIR, notch, Hampel/median,
  bias calibration, random-walk bias modeling, Mahony/Madgwick attitude
  prefilter, ZUPT/ZARU, adaptive IMU noise, and stacked IMU filters.

P/R layer:
  UWB position/range filtering, e.g. raw passthrough, EMA/median smoothing,
  Hampel outlier rejection, range-bias correction, NLOS/residual gating,
  dropout policy, robust range weighting, and offline smoothing controls.

T layer:
  final tag fusion estimator/solver, e.g. complementary/alpha-beta,
  loose EKF, ESKF, tight range EKF, UKF, particle/RBPF, session-window factor
  graph, full-session RTS/batch solver, and robust M-estimator variants.
```

EKF is only one `T` family. A result is not an algorithm-complete filter
comparison unless all three layers declare which families are implemented,
excluded, or pending.

## Phase Overview

The operational compute phases are:

```text
Phase 0 = freeze and verify UWB-only baselines, pairing, and time alignment
Phase 1 = vertical slice, timed end-to-end with a few representative rows
Phase 2 = broad screening matrix, timed and ranked
Phase 3 = full nominal multiseed confirmation sweep plus stress confirmation
Phase 4 = algorithm-complete GPU factory for thousands of filter/fusion combos
```

Phase 5 is the final paper/reporting matrix assembled from the winners and
required controls.

```text
Phase 5 = final headline/report matrix
```

## Fixed Naming

Use this naming scheme throughout this folder:

```text
A = anchor/layout source
U = pure UWB solver output/control, old T1-T4 renamed U1-U4
R = raw anchor-tag measurement stream and range preprocessing
P = UWB solved-position post-filter
L = IMU hardware/sensor model
I = IMU preprocessing/filter
T = final offline tag-trajectory solver, either UWB-only or UWB+IMU fusion
B = named baseline row
X = full experiment combination ID
```

Important:

```text
Old official T4 = new U4.
New T-series = final offline tag-trajectory solver family.
Do not create a new old-style T5.
```

## Workspace Layout

All new generated artifacts stay inside:

```text
Analysis/IMU-Fusion-Simulation/
```

Planned structure:

```text
IMU-Fusion-Simulation/
  NAMING_RULES.md
  SIMULATION_PLAN.md
  configs/
    sensors.yaml
    fusion.yaml
    experiment_matrix.yaml
  scripts/
    00_extract_step0_baselines.py
    01_generate_vicon_perfect_imu.py
    02_simulate_sensor_imu.py
    03_run_position_fusion.py
    04_run_range_fusion.py
    05_compare_results.py
    launch_dual_gpu_phase.py
  fusion/
    vicon_rigid_body.py
    imu_models.py
    imu_filters.py
    uwb_filters.py
    tag_solvers.py
    metrics.py
  cache/
    vicon_pose/
    perfect_imu/
    sensor_imu/
    uwb_streams/
  runs/
    phase0_baseline/
    phase1_vertical_slice/
    phase2_screening/
    phase3_tight_fusion/
    phase4_final_matrix/
  tables/
  figs/
  reports/
  logs/
  manifests/
```

Rules:

- Cache expensive reusable streams once.
- Numerically evaluate every row in the declared FULL matrix.
- Store full per-sample trajectories for selected heavy-output rows when storage
  is a concern; this is an output policy only, never a compute-pruning policy.
- Store summary-only output for broad sweeps, with enough metrics to rank every
  compatible row.
- Every run writes a `manifest.json` with input hashes, config IDs, git status,
  command line, host, GPU, and output paths.
- Never write new IMU simulation outputs into `official_extra_analysis`.
- Never overwrite a previous run directory; create a new run ID.
- Do not create `pre-Phase`, `Phase 1.5`, or `Phase 2A` directories. If a
  Phase 2 requirement must be satisfied before broad screening, it runs as an
  internal stage of the official `phase2_screening/<run_id>` run.

## Visualization Policy

PNG outputs are required. Numerical tables are not enough for this project
because the main question is whether fusion changes the ROTO trajectory shape,
tail behavior, and per-turn stability in a visually credible way.

Use two visualization levels:

```text
full = broad, automatically generated, lightweight diagnostic PNGs
curated = selected high-value visual comparisons for human inspection/reporting
```

Directory layout:

```text
runs/<phase>/<run_id>/figs/full/
runs/<phase>/<run_id>/figs/curated/
runs/<phase>/<run_id>/figs/contact_sheets/
runs/<phase>/<run_id>/tables/figure_index.csv
```

Every PNG must have one row in `figure_index.csv`:

```text
figure_path
figure_kind
phase
experiment_id
capture_id
tag
metric_context
baseline_id
comparison_id
notes
```

Full visual set:

```text
trajectory_xz_overlay.png
imu_only_drift_xz_overlay.png
trajectory_y_time.png
err3d_time.png
err3d_hist.png
radius_phase_error.png
uwb_vs_fused_vs_opti_contact.png
```

Curated visual set:

```text
before_after_3d_or_xz_overlay.png
before_after_time_series.png
imu_drift_growth_panel.png
per_turn_repeatability_overlay.png
failure_case_overlay.png
best_case_overlay.png
worst_tail_overlay.png
```

Phase-specific policy:

```text
Phase 0:
  Generate full B0/B1/B2 trajectory/contact PNGs for all R01-R17 tracks.

Phase 1:
  Generate full PNGs for every row and every R01-R17 track.
  Generate curated before/after PNGs for B0 vs each Phase 1 fusion row.

Phase 2:
  Generate summary/contact sheets for all rows.
  Generate per-track full PNGs only for:
    top 50 rows,
    bottom/failure rows,
    B0/B1/B2 controls,
    one representative row per L/I/T family.

Phase 3:
  Generate full PNGs for all declared stress-audit rows.
  Generate curated stress-sweep panels for dropout, NLOS, vibration, and
  extrinsic-error sweeps.

Phase 4:
  Numerically evaluate every compatible row in the declared FULL matrix.
  Generate broad summary/contact sheets over the full ranking, full per-family
  and per-sensor rankings, and failure exemplars.
  Generate selected heavy PNGs for CPU/GPU agreement rows, per-family best rows,
  per-L best rows, stress rows, and rows entering the final recommendation.
  PNG selection is an output-size policy only; it is never an algorithm/row
  pruning policy.

Phase 5:
  Generate publication/report PNGs only from curated rows.
```

Recommended visual comparisons:

```text
B0 vs X_A0_L0_I0_T11
B0 vs X_A0_L2_I3_T11
B0 vs X_A0_U4_P0_L0_I0_T2
B0 vs X_A0_U4_P0_L2_I3_T3
B0 vs X_A0_R2_L0_I0_T6
B0 vs X_A0_R2_L2_I3_T6
B0 vs best Phase 2 loose row
B0 vs best Phase 2 tight row
B0 vs best robust Phase 3 row
```

Rendering rules:

- Use fixed axis limits per capture when comparing before/after rows.
- Always plot OptiTrack truth, UWB-only baseline, and fused trajectory together.
- Use identical colors and labels across all phases.
- Put `capture_id`, `tag`, experiment ID, P50/P95, and row verdict in the title.
- Avoid 3D-only plots; include XZ/top view and time-series error so deviations
  are visible without rotating a figure.
- Save contact sheets for fast scanning before opening individual PNGs.

## Evaluation Protocol

Evaluate UWB-only, IMU-only, and UWB+IMU rows with the same metric stack where
applicable. Do not rank rows by one scalar RMSE only.

Ground truth:

```text
OptiTrack/Vicon antenna-point trajectory after fixed R01-R17 capture-level
time alignment and the selected A-layout spatial alignment policy.
```

Primary dynamic accuracy metrics:

```text
sample_err3d_p50_mm
sample_err3d_p95_mm
sample_err3d_rmse_mm
trackmedian_err3d_p50_mm
trackmedian_err3d_p95_mm
trackmedian_horizontal_xz_p95_mm
trackmedian_vertical_y_p95_mm
```

ROTO geometry metrics:

```text
turn_center_abs_error_3d_mm
turn_center_abs_error_xz_mm
turn_center_abs_error_y_mm
radius_error_abs_mm
circle_thickness_rms_mm
circle_thickness_p95_mm
turn_center_repeatability_rms_mm
turn_center_repeatability_p95_mm
```

Two-wand relative metrics:

```text
legacy_deltaR_error_rms_mm
legacy_abs_deltaR_error_median_mm
legacy_abs_deltaR_error_p95_mm
inter_wand_distance_error_p50_mm
inter_wand_distance_error_p95_mm
```

Temporal/trajectory-quality metrics:

```text
rpe_1step_rmse_mm
rpe_5step_rmse_mm
velocity_error_rmse_mm_s
acceleration_outlier_rate
jitter_score_mm
max_gap_s
effective_update_rate_hz
availability_fraction
```

Pure IMU drift metrics:

```text
imu_only_endpoint_drift_3d_mm
imu_only_endpoint_drift_xz_mm
imu_only_endpoint_drift_y_mm
imu_only_drift_rate_3d_mm_s
imu_only_drift_p50_mm
imu_only_drift_p95_mm
imu_only_time_to_100mm_s
imu_only_time_to_250mm_s
imu_only_time_to_500mm_s
imu_only_orientation_error_final_deg
imu_only_orientation_error_p95_deg
imu_only_velocity_error_final_mm_s
```

These metrics are mandatory for `T11/T12` and optional diagnostics for fusion
rows. They answer whether UWB updates are actually controlling inertial drift.

Fusion health metrics:

```text
uwb_update_accept_rate
uwb_innovation_nis_median
uwb_innovation_nis_p95
imu_residual_norm_median
imu_residual_norm_p95
bias_estimate_final_norm
bias_estimate_drift_norm
filter_divergence_count
covariance_condition_warning_count
```

Robustness metrics:

```text
dropout_condition
nlos_condition
imu_vibration_condition
imu_extrinsic_error_condition
degradation_vs_clean_p50_mm
degradation_vs_clean_p95_mm
failure_rate
```

Row-class labels:

```text
imu_only_diagnostic
offline_oracle
offline_diagnostic
fixed_lag_windowed
causal_control
raw_measurement_primary
```

Ranking rule:

```text
1. A row must be valid for its claimed class.
2. It must improve trackmedian_err3d_p50_mm vs B0 by at least 5 mm.
3. It must not worsen trackmedian_err3d_p95_mm vs B0 by more than 5 mm.
4. It must not worsen legacy_deltaR_error_rms_mm by more than 5 mm unless
   explicitly marked as an absolute-trajectory-only diagnostic.
5. It must have acceptable fusion health: no divergence and no persistent
   innovation blow-up.
```

Verdict labels:

```text
BASELINE_UWB_ONLY
FUSION_HELPS_PRIMARY_OFFLINE
FUSION_HELPS_DIAGNOSTIC_ONLY
FUSION_NEUTRAL
FUSION_HURTS
FUSION_DIVERGED
FUSION_INVALID_INPUT
IMU_ONLY_DRIFTS_AS_EXPECTED
IMU_ONLY_INVALID
```

Core comparison groups:

```text
pure IMU T11/T12 vs B0, to expose drift without UWB correction
B0 vs same A/U/P with T2-T5 position-side fusion
B0 vs same A/R with T6-T8 raw-range fusion
B1 and B2 controls vs corresponding fusion rows
perfect L0 vs realistic L2/L3/L5/L7
no-filter I0 vs filtered I1/I3/I7/I8 variants
clean vs dropout/NLOS/vibration/extrinsic stress
```

Every solver row must report `coupling_mode`:

```text
uwb_only_control
imu_only_diagnostic
uwb_corrects_imu
imu_corrects_uwb
bidirectional_joint
calibration_coestimate
```

This is required because "fusion" can mean different correction directions:
UWB can pull IMU drift back, IMU can gate or smooth UWB outliers, and
session-window/full-session solvers can jointly estimate trajectory plus
range-bias/time-offset/extrinsic parameters. Final ranking tables must show
`coupling_mode` beside `T` so the winning mechanism is interpretable.

Report every headline row as:

```text
row_id
row_class
coupling_mode
trackmedian 3D P50/P95
sample 3D RMSE
horizontal/vertical P95 split
turn-center RMS
deltaR RMS
effective update rate
fusion verdict
```

Interpretation guardrails:

- `T11/T12` pure IMU rows are drift diagnostics, not UWB+IMU fusion claims.
- `T2` with Vicon-perfect motion prior is an oracle/diagnostic unless replaced
  by simulated or real IMU signals.
- `T9/T10` session-solver rows are primary PC-side candidates; report their
  future-data/session window, but do not penalize them for not being Tag-side
  real-time.
- A lower median with a worse P95 tail is not automatically better.
- A smoother-looking trajectory that distorts ROTO radius or inter-wand
  spacing is not automatically better.
- A tight raw-range row that only works with Vicon truth anchors is an oracle
  control, not a production AutoPos-layout result.

## IMU Drift Model And Bidirectional Coupling

Different IMU models must have different drift behavior. Drift is not a single
number added after the fact; it emerges from the sensor properties in `L` and
the preprocessing/filter choices in `I`.

Each `L` sensor model must define at least:

```text
accelerometer_white_noise_density
gyro_white_noise_density
accelerometer_bias_initial_distribution
gyro_bias_initial_distribution
accelerometer_bias_random_walk
gyro_bias_random_walk
scale_factor_error
axis_misalignment
timestamp_jitter
quantization
ODR
vibration_sensitivity
```

Those properties apply to every block that uses that `L`:

```text
T11/T12 pure IMU drift diagnostics
T2/T3/T4/T5 solved-position fusion
T6/T7/T8 raw-range tight fusion
T9/T10 session-window or full-session fusion
```

So a row such as:

```text
X_A0_U4_P0_L2_I3_T3
```

uses the same `L2` MPU6050-like noise/bias/drift model as:

```text
X_A0_L2_I3_T11
X_A0_R2_L2_I3_T6
```

The difference is the final `T` solver, not a hidden change in IMU physics.

Pure IMU rows answer:

```text
How fast does this IMU drift without UWB?
```

Fusion rows answer:

```text
How much of that drift can UWB constrain, and how much can IMU constrain UWB
between UWB updates or under poor UWB geometry/outliers?
```

The coupling is bidirectional in state-estimator rows:

```text
IMU -> UWB:
  IMU prediction constrains short-term motion, velocity, orientation, and
  expected range/position evolution between UWB updates.

UWB -> IMU:
  UWB range/position innovations correct position, velocity, orientation
  consistency, and IMU bias estimates when those states are observable.
```

For loose coupling (`T3-T5`), UWB updates the shared navigation state using
solved UWB positions. For tight coupling (`T6-T8`), raw range residuals directly
update the shared state and can correct IMU bias estimates through the EKF/UKF
cross-covariance. This is why fusion health metrics must include both UWB
innovation statistics and IMU/bias residual statistics.

## Non-Negotiable Validation Gates

These checks are not optional. If any gate fails, the run can still be kept as a
debug artifact, but it cannot be used for Phase progression or headline claims.

```text
G1_frame_gravity:
  IMU frame, body frame, antenna frame, world frame, gravity sign, and
  specific-force convention must be explicit and tested. If this is wrong, every
  fusion solver can look falsely good or falsely bad.

G2_drift_from_L_properties:
  IMU drift must emerge from the selected L sensor properties and I filtering
  choices. It must not be hand-tuned per result row.

G3_range_bias_policy:
  UWB range bias must be declared and handled for tight fusion. Otherwise a
  stable range offset can dominate T6-T8 and make IMU/fusion conclusions false.

G4_fixed_time_alignment:
  R01-R17 time alignment must be loaded from the frozen official alignment
  artifact. Fusion rows must not re-estimate beta_s.

G5_noise_seed_repeats:
  Single-seed rows are allowed for debugging and early screening only. Final
  claims about a sensor model or fusion solver need repeated noise seeds.

G6_multimetric_verdict:
  Ranking must include P50, P95, deltaR, turn radius/repeatability, availability,
  and divergence/fusion-health metrics. P50/RMSE alone is not enough.
```

Phase ownership:

```text
Before Phase 1:
  G1 must pass for L0 and L2.
  G2 must pass for L0 and L2, including T11 pure-IMU drift diagnostics.
  G3 must have at least a written policy for the T6 prototype.
  G4 must pass for all R01-R17.
  G6 metric emission must exist for B0 and all Phase 1 rows.

Before Phase 2:
  G2 must pass for every L model included in the screening matrix.
  G3 must be implemented for every R variant used by T6-T8.
  G5 must record deterministic seeds for all stochastic rows.
  G6 ranking/verdict generation must be automatic, not manual.

Before Phase 3:
  G3 must include robust range-bias/outlier stress behavior.
  G5 must run multiple seeds for every realistic row in the declared Phase 3
  confirmation matrix.
  G6 must include degradation and failure-rate reporting under stress.

Before Phase 4:
  T1-T10 algorithm families must be represented as implemented, validated CPU
  golden, or explicitly excluded with a technical reason.
  GPU batching must be profiled on a fixed CPU/GPU agreement subset before any
  large matrix is trusted.

Before Phase 5:
  G5 must be repeated for every headline realistic IMU/fusion row.
  G6 must be reported in the final tables and PNG titles.
  Any row that fails G1-G4 is excluded from headline comparison.
```

Required gate artifacts:

```text
runs/<phase>/<run_id>/tables/validation_gates.csv
runs/<phase>/<run_id>/reports/VALIDATION_GATES.md
configs/frame_conventions.md
configs/range_bias_policy.md
configs/sensors.yaml
configs/imu_datasheet_parameter_notes.md
manifests/noise_seeds_<run_id>.json
```

## ROTO Pairing And Time Alignment Contract

This is a hard constraint for the whole IMU fusion simulation.

The dynamic dataset is not an arbitrary pool of UWB and OptiTrack files. It is
the 17 official ROTO captures:

```text
R01, R02, R03, R04, R05, R06, R07, R08, R09,
R10, R11, R12, R13, R14, R15, R16, R17
```

Each `Rxx` has a one-to-one UWB capture and OptiTrack/Vicon capture:

```text
UWB captures:
  captures/erlangen_20260528_optitrack/roto_Rxx_BS2DCE_BSDC91_120s_*/

OptiTrack exports:
  opti_captures/full/Rxx.trc
  opti_captures/full/Rxx.csv
```

The `roto_R01-Static-middle-test_*` folder is not part of the official dynamic
R01-R17 set unless explicitly added as a separate diagnostic.

Time alignment already exists and must be reused. Do not re-fit one time offset
per fusion row. The official ROTO analysis solved one capture-level offset per
`Rxx` from the primary `v4-io/T4` trajectory and reused it for all layout/solver
variants.

Primary alignment artifact:

```text
official_extra_analysis/FULL_US/roto_absolute/tables/roto_time_offsets_v4io_T4.csv
```

Aligned sample artifacts already expose both time axes:

```text
official_extra_analysis/FULL_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
official_extra_analysis/FULL_AutoPos_one_baseline_scale_correction_US/roto_absolute/tables/roto_abs_samples_v4io_T4.csv
```

These sample tables contain:

```text
capture_id
tag
uwb_time_s
opti_time_s
uwb_x_mm, uwb_y_vertical_mm, uwb_z_mm
opti_x_mm, opti_y_vertical_mm, opti_z_mm
```

For synthetic IMU generation:

```text
1. Use Rxx.trc to generate Vicon body pose and perfect IMU.
2. Use the existing capture-level `beta_s`/aligned `opti_time_s` relation.
3. Query the synthetic IMU on the UWB/fusion time grid through this fixed
   alignment.
4. Keep the same alignment for all L/I/T rows in a run.
```

Forbidden in broad sweeps:

```text
re-estimating beta_s per L/I/T row
using IMU or fused trajectory to improve the UWB/OptiTrack time offset
mixing Rxx UWB with a different Ryy OptiTrack file
silently including R01-Static-middle-test in the R01-R17 matrix
```

Allowed diagnostics:

```text
time-offset sensitivity sweep around the fixed beta_s
clock-skew stress as a named robustness diagnostic
R01-Static-middle-test as a separately named non-headline diagnostic
```

Every manifest must record the exact alignment source:

```text
alignment_source = FULL_US/roto_absolute/tables/roto_time_offsets_v4io_T4.csv
alignment_policy = fixed_capture_level_beta_from_primary_v4io_U4
capture_set = R01-R17
```

## Phase 0: Freeze Baselines

Purpose: establish the UWB-only references before any IMU enters.

Primary baseline:

```text
B0 = A0/U4/P0/T1
A0 = AutoPos v4-io rigid no-scale
U4 = old T4 pure UWB solver
P0 = no UWB solved-position post-filter
T1 = new final Tag-solver label for UWB-only output
```

Known current dynamic baseline:

```text
B0 ROTO track-median 3D P50/P95 = 105.8 / 231.8 mm
```

Controls:

```text
B1 = A1/U4/P0/T1
   = one-baseline scale correction control

B2 = A2/U4/P0/T1
   = Vicon/OptiTrack truth anchors + delaycal oracle control
```

Outputs:

```text
runs/phase0_baseline/<run_id>/tables/baseline_summary.csv
runs/phase0_baseline/<run_id>/tables/validation_gates.csv
runs/phase0_baseline/<run_id>/reports/BASELINE_FREEZE.md
runs/phase0_baseline/<run_id>/reports/VALIDATION_GATES.md
runs/phase0_baseline/<run_id>/figs/full/
runs/phase0_baseline/<run_id>/figs/contact_sheets/
cache/uwb_streams/A0_U4_P0/
cache/alignment/R01_R17_fixed_beta_v4io_U4/
```

Do not continue to Phase 1 until `B0`, `B1`, and `B2` can be reproduced from
the existing official tables and the R01-R17 pairing/alignment manifest has
been written.

## Phase 1: Vertical Slice

Purpose: prove the whole pipeline works end-to-end with very few combinations.

Generate:

```text
L0 = perfect Vicon IMU
L2 = MPU6050-like 6-axis IMU
I0 = no IMU filter
I3 = bias calibration + bias random-walk model
```

Run:

```text
B0
X_A0_L0_I0_T11
X_A0_L2_I3_T11
X_A0_U4_P0_L0_I0_T2
X_A0_U4_P0_L2_I3_T3
X_A0_R2_L0_I0_T6
X_A0_R2_L2_I3_T6
```

Why these rows:

- `T11` exposes pure IMU drift without UWB correction.
- `T2` checks compatibility with the existing PI1-style pseudo-IMU result.
- `T3` checks loose-coupled EKF with realistic IMU noise.
- `T6` checks raw-range tight EKF on perfect and realistic IMU.

Outputs:

```text
runs/phase1_vertical_slice/<run_id>/tables/phase1_summary.csv
runs/phase1_vertical_slice/<run_id>/tables/validation_gates.csv
runs/phase1_vertical_slice/<run_id>/figs/
runs/phase1_vertical_slice/<run_id>/figs/full/
runs/phase1_vertical_slice/<run_id>/figs/curated/
runs/phase1_vertical_slice/<run_id>/figs/contact_sheets/
runs/phase1_vertical_slice/<run_id>/reports/PHASE1_VERTICAL_SLICE.md
runs/phase1_vertical_slice/<run_id>/reports/VALIDATION_GATES.md
```

Success criteria:

- `L0/I0/T11` should reconstruct Vicon closely enough to validate frame
  conventions and IMU generation.
- `L2/I3/T11` should drift; if it does not drift, the IMU realism model is
  probably too optimistic or accidentally corrected by truth.
- `L0/I0/T2` should be close to the old PI1 oracle directionally.
- `L0/I0/T6` should not be worse than B0 unless the tight model has a bug.
- `L2/I3/T3` should show plausible degradation versus perfect IMU, not random
  improvement from a broken filter.

## Gated Execution And Timing Protocol

The first real run should be gated:

```text
Phase 0 -> analyze Phase 0 -> automatically start Phase 1 -> analyze Phase 1
```

Phase 1 must not start unless Phase 0 verifies:

```text
B0/B1/B2 baseline rows reproduced
R01-R17 UWB/OptiTrack pairing manifest written
fixed beta_s alignment loaded from the official alignment table
no missing Rxx.trc files
no accidental inclusion of R01-Static-middle-test
G4_fixed_time_alignment = PASS
```

If those checks pass, the runner may automatically start Phase 1.

The official Phase 2 runner may start after Phase 1. Broad screening inside
Phase 2 must not start until Phase 2's own gate-fulfillment stage verifies:

```text
G1_frame_gravity = PASS for L0 and L2
G2_drift_from_L_properties = PASS for L0 and L2
G3_range_bias_policy = PASS for all enabled tight-fusion rows, or T6/T8 are
  disabled with explicit exclusion reasons
G4_fixed_time_alignment = PASS for all Phase 1 rows
G5_noise_seed_repeats = PASS_SCREENING with repeated recorded seeds
G6_multimetric_verdict = PASS for all Phase 1 rows
no Phase 1 row silently reused truth after IMU simulation
no T11 realistic-IMU row has impossible zero drift
```

Correct Phase 2 workspace shape:

```text
runs/phase2_screening/<run_id>/stage0_gate_fulfillment/
runs/phase2_screening/<run_id>/stage1_screening/
runs/phase2_screening/<run_id>/stage2_ranking_and_visual_audit/
```

Forbidden workspace shape:

```text
runs/pre_phase2/
runs/phase1_5/
runs/phase2A/
```

If `stage0_gate_fulfillment` cannot satisfy G3/G5, the official Phase 2 run
stops with:

```text
phase_status = blocked_before_screening
```

It does not spawn a separate pseudo-phase.

Phase 3 must not start unless Phase 2 verifies:

```text
G2_drift_from_L_properties = PASS for every L in the Phase 2 matrix
G3_range_bias_policy = PASS for all tight-fusion R/T rows
G5_noise_seed_repeats = PASS_SCREENING for the screened rows
G6_multimetric_verdict = PASS_AUTOMATIC_RANKING
top rows, failure rows, and controls have PNG/contact-sheet evidence
```

Phase 4 algorithm-factory launch must not start unless Phase 3 verifies:

```text
G3_range_bias_policy = PASS_UNDER_STRESS for every tight-fusion family entering
the Phase 4 FULL matrix
G5_noise_seed_repeats = PASS_PHASE3_MULTISEED for every realistic row entering
the Phase 4 FULL matrix
G6_multimetric_verdict = PASS_PHASE3_RANKING_AND_FAILURE_LABELS
the CPU runner has enough timing data to build the GPU cost model
the missing algorithm families T4/T7/T9/T10/T12 have implementation tickets
or explicit exclusion reasons
G11_two_gpu_dynamic_balance = PASS for the official Phase 4 launcher
G12_cpu_parallel_execution = PASS for CPU-bound broad-sweep stages
G13_thread_oversubscription_control = PASS for GPU feeder and CPU fallback modes
```

Phase 5 headline reporting must not start unless Phase 4 verifies:

```text
G5_noise_seed_repeats = PASS_FINAL_MULTISEED for every realistic headline row
G6_multimetric_verdict = PASS_FINAL_TABLES_AND_FIGURES
GPU and CPU golden subsets agree within tolerance
all excluded rows have explicit exclusion reasons
```

Required timing artifacts:

```text
runs/phase0_baseline/<run_id>/manifest.json
runs/phase0_baseline/<run_id>/tables/timing_summary.csv
runs/phase1_vertical_slice/<run_id>/manifest.json
runs/phase1_vertical_slice/<run_id>/tables/per_row_timing.csv
runs/phase1_vertical_slice/<run_id>/tables/gpu_memory_summary.csv
runs/phase1_vertical_slice/<run_id>/reports/RUNTIME_EXTRAPOLATION.md
```

Every row must record:

```text
experiment_id
A/U/R/P/L/I/T labels
solver_family
n_tracks
n_uwb_samples
n_opti_frames
summary_only_or_full_trace
wall_time_s
gpu_id
peak_gpu_mem_mb
cpu_peak_rss_mb
status
retry_count
```

The first extrapolation should be simple and explicit:

```text
measured_seconds_per_cost_unit =
  sum(wall_time_s for successful Phase 1 rows)
  / sum(estimated_cost for successful Phase 1 rows)

estimated_full_matrix_time =
  measured_seconds_per_cost_unit
  * total_estimated_cost_for_target_matrix
  / effective_parallel_gpu_factor
```

Use a conservative effective parallel factor at first:

```text
effective_parallel_gpu_factor = 1.6
```

After Phase 2, replace it with the measured value from both GPUs.

For the 1,505,280-row I-subset matrix, estimate runtime only. Do not launch it
unless a later report proves it is scientifically useful and computationally
reasonable.

## Phase 2: Screening Matrix

Purpose: find promising sensor/filter/solver families without brute-forcing
everything.

Phase 2 is one official run with internal stages:

```text
stage0_gate_fulfillment:
  upgrade G5 from debug single-seed to screening multiseed.
  upgrade G3 from limited T6 prototype to implemented range-bias policy, or
  explicitly disable tight-fusion rows from the screening manifest.

stage1_screening:
  run the broad controlled matrix.

stage2_ranking_and_visual_audit:
  rank by automatic multimetric verdicts and generate PNG/contact-sheet evidence.
```

Recommended screening rows:

```text
A: A0 only
U/P: U4/P0, U4/P2
R: R2, R4
L: L0, L1, L2, L3, L4, L5, L7, L8
I: I0, I1, I3, I4, I7, I8, I1+I3+I7, I1+I2+I3+I8
T: T2, T3, T5, T6, T8 plus T11 drift diagnostics
```

Approximate row count:

```text
position-side rows: 1 A * 2 U/P * 8 L * 8 I * 3 T = 384
range-side rows:    1 A * 2 R   * 8 L * 8 I * 2 T = 256
imu-only drift rows: 1 A * 8 L * 8 I * 1 T = 64
total screening rows = 704
```

This is the first useful broad benchmark. It is small enough to run repeatedly.

Outputs:

```text
runs/phase2_screening/<run_id>/stage0_gate_fulfillment/
runs/phase2_screening/<run_id>/stage1_screening/
runs/phase2_screening/<run_id>/stage2_ranking_and_visual_audit/
runs/phase2_screening/<run_id>/tables/phase2_summary.csv
runs/phase2_screening/<run_id>/tables/phase2_ranked_top50.csv
runs/phase2_screening/<run_id>/tables/validation_gates.csv
runs/phase2_screening/<run_id>/reports/PHASE2_SCREENING.md
runs/phase2_screening/<run_id>/reports/VALIDATION_GATES.md
```

Only the top 50 and selected failure rows get per-sample trajectories.

## Phase 3: Full Confirmation Sweep

Purpose: spend computation time to determine the best currently implemented
offline trajectory combination, not merely refine the current favorite. Phase 2
is allowed to be a screening pass; Phase 3 must answer:

```text
Which anchor layout / UWB processing / IMU model / IMU filter / tag fusion
solver combination wins among currently implemented families, and under which
failure modes does it stop winning?
```

This is not a Phase 2 `stage3`. Phase 2 ends at visual audit. Phase 3 is its
own official run:

```text
runs/phase3_full_confirmation/<run_id>/
```

Phase 3 uses time for certainty. It must not collapse to a single-candidate
study just because Phase 2 found one promising row.

Official internal stages:

```text
stage0_readiness_and_matrix_manifest:
  verify Phase 2 gates and freeze the exact runnable matrix.
  rows missing source artifacts are listed as NOT_RUN_SOURCE_MISSING, not silently
  dropped.

stage1_full_nominal_multiseed:
  run the full nominal matrix over all runnable combinations and multiple noise
  seeds.

stage2_contender_stress_matrix:
  promote all rows that can plausibly beat B0 after uncertainty intervals, plus
  family-best failure controls from every solver family, into stress sweeps.

stage3_tight_range_diagnosis:
  separately diagnose T6/T8/T9 raw-range fusion failures with residual,
  anchor-wise, and geometry metrics. This stage is for explaining failure, not
  hiding it.

stage4_final_visual_audit:
  generate contact sheets and curated worst-track PNGs for top rows, near-top
  rows, stress failures, and excluded controls.
```

Nominal matrix policy:

```text
A: all runnable anchor layouts from the official AutoPos outputs
U/P: all runnable UWB solver/filter pairs from the official outputs
R: all runnable raw anchor-tag measurement policies implemented in this project
L: all Phase 2 IMU sensor models plus L10-L19 datasheet-backed consumer/drone
   IMUs from `configs/sensors.yaml`
I: all IMU filters from Phase 2
T: all implemented offline trajectory solvers, including UWB-only controls,
   position-side controls, and raw-measurement fusion
noise seeds: at least 5 for stochastic IMU rows
captures: all 17 ROTO captures, both tags
```

Current minimum runnable nominal matrix, using the implemented Phase 2 families:

```text
baseline/control rows:
  B0 plus any runnable B1/B2 controls

IMU-only drift diagnostics:
  18 L * 8 I * 5 seeds = 720 rows

position-side fusion:
  2 U/P * 18 L * 8 I * 3 T * 5 seeds = 4320 rows per anchor-layout set

raw-range fusion:
  2 R * 18 L * 8 I * 2 T * 5 seeds = 2880 rows per anchor-layout set

minimum A0-only total:
  about 7920 rows plus controls
```

If A1/A2 or additional U/P/R/T artifacts are runnable, they are included and the
manifest records the expanded count before stage1 starts.

The L dimension is deliberately not pruned for time. The purpose of Phase 3 is
to spend computation to find the best real IMU / filter / solver combination.

Current runner scope:

```text
implemented prototype T rows: T2, T3, T5, T6, T8
diagnostic T rows: T11
not yet implemented in the runner: T4, T7, T9, T10, T12
```

The current Phase 3 run is therefore a full sweep over the implemented solver
families, not the final algorithm-complete comparison. Before final claims about
"best fusion algorithm", add and validate the missing UKF, fixed-lag factor
graph, offline RTS/batch, and pseudo-reset diagnostic rows.

Stress dimensions:

```text
range dropout: 0%, 10%, 25%, 50%
range outliers/NLOS-like bias: none, mild, harsh
IMU timestamp jitter: none, mild, harsh
IMU vibration: none, platform-like, harsh
IMU extrinsic error: 0 deg, 2 deg, 5 deg
```

Stress policy:

```text
Do not use stress sweeps to discover the winner from scratch.
First run the full nominal multiseed matrix.
Then stress every row whose confidence interval overlaps B0 or the current best,
plus every solver-family best row and every important failure-control row.
```

Phase 3 winner policy:

```text
A row may be called best only if it beats B0 on the multi-metric verdict:
  P50
  P95
  RMSE
  deltaR
  radius error
  turn-center error
  divergence count
  update accept rate / NIS sanity for fusion rows
and it remains acceptable across seed repeats and selected stress sweeps.
```

Outputs:

```text
runs/phase3_full_confirmation/<run_id>/manifest.json
runs/phase3_full_confirmation/<run_id>/tables/phase3_nominal_summary.csv
runs/phase3_full_confirmation/<run_id>/tables/phase3_stress_summary.csv
runs/phase3_full_confirmation/<run_id>/tables/phase3_final_ranking.csv
runs/phase3_full_confirmation/<run_id>/tables/phase3_exclusion_reasons.csv
runs/phase3_full_confirmation/<run_id>/reports/PHASE3_FULL_CONFIRMATION.md
runs/phase3_full_confirmation/<run_id>/reports/PHASE3_VISUAL_AUDIT.md
```

## Phase 4: Algorithm-Complete GPU Factory

Purpose: spend computation to find the best practical combination, not to
manually choose a few comfortable rows. Phase 4 FULL means non-selective full
matrix execution: every compatible declared combination is run, with T1-T10
represented and with enough seed and stress evidence to justify the final
recommendation.

The Phase 4 output should answer one sentence:

```text
Which IMU L, IMU filter chain I, UWB filter/range policy P/R, and final tag
solver T should be used for the real tag?
```

Mandatory solver coverage:

```text
T1  UWB-only control
T2  position-domain IMU relative-motion prior
T3  loose-coupled EKF
T4  loose-coupled UKF
T5  error-state EKF with IMU bias states
T6  tight raw-range EKF
T7  tight raw-range UKF
T8  robust tight EKF with NLOS/dropout mixture
T9  session-window fixed-lag factor graph
T10 full-session batch/RTS upper-bound solver
```

`T11/T12` remain IMU-only diagnostics. They explain drift and reset behavior,
but they are not competing fusion solvers because they do not consume UWB.

Phase 4 tiers:

```text
Tier A: algorithm completion and CPU golden validation
  implement/pin T4, T7, T9, T10, T12
  add planned I/P/R filters or mark them excluded with reasons
  validate every new family against small deterministic CPU golden rows

Tier B: torch CUDA batch backend
  tensorize row/track/seed batches where the math is compatible
  run CPU/GPU agreement tests on fixed subsets
  profile per-family wall time and GPU memory on both 1080 Ti cards
  write a calibrated cost model before the large launch

Tier C: full algorithm factory sweep
  run the non-selective compatibility-constrained matrix over all active L
  sensors, all declared I/P/R filters, all T1-T10 solver families, and all
  required seeds
  run full stress matrices when stress is part of the official claim
  produce ranked tables, failure labels, and visual evidence
```

Phase 4 matrix policy:

```text
Do not prune L10-L19 for time.
Do not prune a T family just because Phase 2/3 did not implement it yet.
Do not prune a T/I/P/R/L family because Phase 2/3 scored badly.
Do not replace FULL with winners/near-winners/family-best selected-subset logic.
Do not call a result final if a missing T family could plausibly beat it.
Do use compatibility constraints so impossible rows are never generated.
Do separate compute completeness from output volume: every compatible row must
be numerically evaluated even if only selected rows get heavy PNG/detail output.
```

GPU execution policy:

```text
Use CPU only as the golden/reference and as the data-loading/reporting host.
Use both GTX 1080 Ti cards for batch scoring/predict-update work where possible.
Balance by estimated compute cost, not by row count.
Keep a manifest-backed queue so an idle GPU can steal pending chunks.
If a solver cannot be safely tensorized, isolate it and report why.
GPU memory cannot shrink the matrix. It only controls chunk size.
```

Phase 4 primary outputs:

```text
runs/phase4_algorithm_factory/<run_id>/manifest.json
runs/phase4_algorithm_factory/<run_id>/tables/phase4_algorithm_registry.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_cpu_gpu_agreement.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_full_ranking.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_best_by_family.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_best_by_sensor.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_stress_summary.csv
runs/phase4_algorithm_factory/<run_id>/tables/phase4_exclusion_reasons.csv
runs/phase4_algorithm_factory/<run_id>/figs/full/
runs/phase4_algorithm_factory/<run_id>/figs/selected/
runs/phase4_algorithm_factory/<run_id>/reports/PHASE4_ALGORITHM_FACTORY.md
```

## Phase 5: Final Paper Matrix

Purpose: produce the clean final table after Phase 4 has already spent the
compute budget.

Candidate rows come from:

```text
B0/B1/B2 controls
best T1 UWB-only row
best raw-measurement session-solver row
best T9 session-window row with future-data window reported
best T10 full-session batch/RTS row
best L sensor family winner
best cheap IMU winner
best robust-under-stress winner
key failure exemplars, especially tight fusion dragged by range residual bias
```

Final report:

```text
reports/FINAL_UWB_IMU_FUSION_BENCHMARK.md
tables/final_fusion_headline.csv
tables/final_fusion_algorithm_ranking.csv
figs/final_fusion_matrix.png
figs/final_selected_contact_sheets/
```

## Combination Count Estimate

If we only allow single `I0-I8` filters and keep compatibility constraints:

```text
A count = 4
U count = 4
P count = 6
R count = 5
L count = 18 active Phase 3 sensor models
I count = 9
position-control-compatible T count = 5  (T1-T5)
raw-measurement-compatible T count = 5 (T6-T10)
imu-only diagnostic T count = 2 (T11-T12)
```

The active Phase 3 `L` count is:

```text
Phase 2 L set: L0, L1, L2, L3, L4, L5, L7, L8 = 8
New datasheet-backed consumer/drone IMUs: L10-L19 = 10
Active Phase 3 L total = 18
```

`L6` industrial high-grade and `L9` future real tag IMU replay stay excluded
from the consumer/drone full confirmation matrix unless real source artifacts
are added.

Then:

```text
position-control-compatible combinations = 4 * 4 * 6 * 18 * 9 * 5 = 77,760
raw-measurement-compatible combinations = 4 * 5 * 18 * 9 * 5 = 16,200
imu-only diagnostic combinations = 4 * 18 * 9 * 2 = 1,296
total compatible single-I combinations = 95,256
```

If every non-empty subset of the eight nontrivial IMU filters were allowed,
plus `I0`, the IMU-filter count becomes:

```text
1 + (2^8 - 1) = 256
```

Then:

```text
total combinations = 3,170,304
```

That is not a sensible first CPU run, but it is a valid Phase 4 FULL-expanded
target if declared. If we choose that target, it must be chunked and scheduled
instead of selectively pruned.

## Runtime Estimate

These are planning estimates after the first measured Phase 2 run unless marked
as future GPU-vectorized estimates.

Data scale:

```text
ROTO tracks = 17 captures * 2 tags = 34 tracks
UWB solved samples = about 40,661 total samples
OptiTrack frames per track = about 20k-23k for most tracks
```

Expected cost by component:

```text
Vicon rigid-body pose cache:
  one-time CPU job, minutes, not per combination

Perfect IMU generation:
  one-time CPU/vectorized job, minutes

Sensor IMU generation:
  cheap if cached by L/I/noise seed; seconds to minutes per L/I batch

Position-domain fusion T2/T3/T5:
  cheap to moderate; best batched on CPU/GPU across rows

Raw-range tight fusion T6/T7/T8:
  moderate to expensive; benefits from GPU batching across combinations/tracks

Session-window / full-session T9/T10:
  expensive; must still run in the official FULL matrix if declared, but should
  use chunking, caching, and cost-balanced GPU/CPU scheduling
```

Rough wall-clock estimates using 2x GTX 1080 Ti and batched/vectorized code:

```text
Phase 0:
  minutes

Phase 1, 7 rows:
  10-60 minutes including debugging/reporting

Phase 2 measured run:
  705 rows, 10 CPU workers, 1209 s = about 20.2 minutes

Phase 3 A0-only minimum nominal multiseed:
  about 7920 rows plus controls
  measured-CPU-rate projection: about 3.8-6 hours

Phase 3 A0/A1/A2 nominal multiseed, if A1/A2 runnable:
  about 23,760 rows plus controls
  measured-CPU-rate projection: about 11-18 hours

Full 95,256 single-I compatible matrix:
  batched GPU/vectorized summary-only target: about 12-48 hours
  mixed CPU/GPU with tight solvers: about 2-5 days
  pure Python tight/batch solvers: not worth running

Full 3,170,304 I-subset matrix:
  valid only if declared as FULL-expanded-I
  likely very expensive; requires GPU/vectorized implementation, cache reuse,
  and chunked execution rather than selective pruning
```

Practical rule:

```text
Run the full Phase 3 nominal matrix. Do not prune the IMU sensor dimension for
time. If wall-clock becomes unacceptable, optimize the runner/backend instead of
dropping L10-L19.

For Phase 4, apply the same rule to the whole declared matrix: do not drop
solver/filter/sensor combinations to save time. Reduce chunk size, improve the
GPU backend, or extend wall-clock.
```

## 2x GTX 1080 Ti Execution Strategy

Use the GPUs for batched numerical work, not for one tiny EKF at a time.

Naive even/odd sharding is only acceptable for very uniform summary-only rows.
It is not acceptable once the matrix mixes cheap position-domain solvers with
expensive raw-range tight solvers, fixed-lag smoothers, and stress sweeps. The
launcher should balance estimated cost, not row count.

Scheduling principle:

```text
GPU0 and GPU1 should receive approximately equal total estimated work,
approximately equal peak memory pressure, and the same mix of cheap/expensive
solver families whenever practical.
```

GPU backend decision rule:

```text
A0-only Phase 3 nominal multiseed:
  keep the CPU multiprocessing backend if it is already running and projected to
  finish in a few hours. Do not stop a valid run only to begin a risky CUDA
  rewrite unless CPU progress collapses.

Expanded Phase 3 / stress / full 95,256-row single-I matrix:
  implement and validate a torch CUDA backend before launching the full run, or
  explicitly accept multi-day CPU runtime.
```

The CUDA backend must be validated against the CPU backend on a small fixed
subset before it is trusted. A fast GPU result that changes the EKF math,
masking, range-bias correction, or missing-anchor behavior is invalid.

Cost model:

```text
estimated_cost =
  n_tracks
  * n_samples_per_track
  * solver_family_weight
  * stress_multiplier
  * output_multiplier
```

Initial solver-family weights before profiling:

```text
T2 position-prior fusion:        1
T3 loose EKF:                    2
T5 error-state EKF:              4
T6 tight raw-range EKF:          8
T7 tight raw-range UKF:         12
T8 robust tight EKF:            14
T9 session-window factor graph: 20
T10 full-session batch/RTS:     30
T11 IMU-only strapdown:          2
T12 IMU-only with pseudo-reset:  3
```

Initial output multipliers:

```text
summary-only row:                1
top-row per-sample trajectory:   2
debug/failure trace row:         3
```

After Phase 1, replace these guessed weights with measured median seconds per
`T` family and measured peak GPU memory. Store the calibrated model in:

```text
manifests/gpu_cost_model_<run_id>.json
```

Recommended launcher behavior:

```text
1. Build the phase manifest.
2. Estimate cost and memory for every row.
3. Group compatible rows by solver family and data shape.
4. Split groups with greedy bin packing by estimated total cost.
5. Start one worker per GPU.
6. Workers pull work chunks from a manifest-backed queue.
7. CPU handles data loading, CSV writing, and report aggregation.
```

Do not start the official Phase 4 FULL launch if the implementation is still a
single-device pilot such as `--device cuda:0` or `--device cuda:1`. A pilot can
validate math, but it cannot satisfy G11.

Segmented execution policy:

```text
Phase 4 may run across multiple nights.
FULL means every compatible declared row is eventually evaluated, not that the
whole matrix runs in one uninterrupted session.

The official launcher must support:
  --resume-run <run_id>
  --max-wall-time <seconds>
  --stop-at-local-time HH:MM
  chunk status: pending/running/done/failed/retry
  atomic per-chunk output writes
  final ranking only after all required chunks are done
```

For zekaixiao's workstation, prefer overnight heavy runs. In the morning, stop
dispatching new chunks before the cutoff, let active chunks finish, flush the
manifest, and leave the GPUs/CPU free for daytime interactive work.

The concrete 2026-06-05 8 hour 2x1080Ti bootstrap plan is recorded in:

```text
NIGHTLY_8H_PHASE4_1080TI_PLAN.md
```

Chunking:

```text
cheap rows: combine many rows per chunk to amortize launch overhead
expensive rows: small chunks so one GPU is not stuck for hours
T9/T10 rows: isolate or pair only with cheap rows
```

Memory policy:

```text
1080 Ti memory = 11 GB nominal

Phase 1 initial target:       <= 75%  (~8.25 GB)
Phase 1 hard threshold:       <= 90%  (~9.90 GB)

Phase 2 profiled target:      <= 85%  (~9.35 GB)
Phase 2 hard threshold:       <= 92%  (~10.1 GB)

Phase 3 stable solver target: <= 88%  (~9.7 GB)
Phase 3 hard threshold:       <= 95%  (~10.45 GB)

T9/T10 session solvers:
  profile separately; do not assume they can use the aggressive target safely

fallback:
  reduce batch size, then reduce chunk size, then move row to CPU/slow queue
```

Implementation rules:

- Preload/copy track arrays to GPU in batches.
- Batch rows with the same solver family and similar time grid.
- Keep an explicit per-GPU pending-cost counter in the manifest.
- Avoid writing per-sample CSVs during broad sweeps.
- Write one summary row per experiment row during broad sweeps.
- Save per-sample trajectories only for top rows, baseline rows, and failure audits.
- Use deterministic seeds for IMU noise and dropout.
- Record `CUDA_VISIBLE_DEVICES`, torch/CUDA versions, and GPU name in manifest.
- Record per-row runtime, peak memory, status, and retry count.
- Let an idle GPU steal a not-yet-started chunk from the heavier queue.
- Record `gpu_completed_cost`, `gpu_wall_time_s`, `gpu_idle_time_s`,
  `gpu_steal_count`, `cpu_worker_count`, and the CPU thread policy for each
  stage.
- Set torch/BLAS/OpenMP threads to one inside GPU feeder workers by default.
- Use multiple CPU processes for CPU-only broad stages when enough jobs exist;
  one hot CPU thread with idle cores is a failed resource gate.

The old Monte Carlo workflow already used two-GPU sharding successfully. Reuse
that pattern for `launch_dual_gpu_phase.py`, but make output phase-aware and
manifest-driven. Improve it with cost-balanced scheduling so one GPU does not
carry all tight fusion rows while the other idles on cheap rows.

## Minimum First Implementation

Build only enough code to run Phase 1:

```text
1. extract B0/B1/B2 summaries
2. cache R01-R17 UWB/OptiTrack pairing and fixed beta_s alignment
3. cache Vicon rigid-body pose for R01-R17 and BS2DCE/BSDC91
4. generate L0 perfect IMU on the fixed aligned time basis
5. generate L2 MPU6050-like IMU
6. run T11 pure IMU drift diagnostics
7. run T2 position-prior fusion
8. run T3 loose EKF
9. run first T6 tight EKF prototype
10. write one Phase 1 report
```

Only after Phase 1 is correct should we add the larger L/I/T sweep.

## Completeness Audit

These are the main things that can still weaken the simulation if they are not
handled explicitly.

Must have before Phase 1:

```text
G1 frame convention contract:
  world frame, body frame, antenna frame, IMU frame, gravity direction, and
  units must be written down and tested.

G1 specific-force convention:
  simulated accelerometer output must clearly state whether it includes gravity
  and which sign convention is used.

initialization policy:
  T11/T12 pure IMU and every fusion T solver must specify initial position,
  velocity, orientation, and bias initialization.

lever-arm policy:
  IMU-to-UWB-antenna lever arm must be explicit. Perfect Vicon IMU can use the
  fitted wand body; realistic IMU rows must define whether the IMU is at the
  antenna, body origin, or an offset point.

G4 alignment freeze:
  R01-R17 fixed beta_s alignment must be read-only during fusion sweeps.

G5 deterministic randomness:
  every L/I row with noise, bias, dropout, or vibration must use recorded seeds.

G2 drift sanity:
  L2/I3/T11 must show realistic drift without UWB correction. If it does not,
  stop before Phase 2 and inspect truth leakage, sign conventions, or overly
  optimistic sensor parameters.

G6 metric sanity:
  Phase 1 report must include P50, P95, deltaR, ROTO geometry, divergence, and
  fusion-health metrics for every applicable row.
```

Should have before Phase 2:

```text
G2 sensor parameter provenance:
  L2/L3/L4/L5/L6/L7 sensor properties should say whether they are datasheet-like,
  intentionally pessimistic, or hand-tuned stress models.

G5 Monte Carlo repeats for IMU noise:
  one random IMU realization is not enough for final claims. Screening can use
  one seed, but final rows should use multiple seeds.

observability checks:
  bias/orientation states may not be observable in all ROTO motions. Fusion
  health must report whether the estimator is pretending to estimate states it
  cannot actually constrain.

raw-range availability contract:
  tight fusion T6-T8 needs a stable raw range table format with anchor IDs,
  timestamps, per-link measurements, and missing-link representation.

G3 range-bias policy:
  UWB tag/anchor bias corrections must be defined for R2/R3/R4 so tight fusion
  does not blame IMU for stable range offsets.

train/test separation:
  any parameter tuned on R01-R17 must be labeled as in-dataset tuning, not an
  independent generalization result.
```

Should have before Phase 3:

```text
G3/G5 stress-model calibration:
  dropout, NLOS-like bias, vibration, timestamp jitter, and extrinsic error
  magnitudes must be named and justified.

failure taxonomy:
  divergence, invalid input, non-observability, missing range links, bad IMU
  frame, and memory/runtime failures should get separate status labels.

future-data-window accounting:
  session-window T9 and post-filter P4 must report how much future data they use.
  This is metadata for PC-side session-solver comparison, not a penalty.

real-IMU bridge:
  reserve L9 for future real tag IMU replay and keep synthetic CSV fields close
  to the real firmware IMU output schema where practical.

G6 final ranking audit:
  the final recommendation candidates must be chosen from automatic verdicts
  plus visual inspection, not from one scalar error column.
```

Nice to have:

```text
magnetometer rows:
  L3 can include a magnetometer-like channel, but ROTO indoor magnetic distortion
  should make it a diagnostic, not a default final-pipeline assumption.

Allan/noise plots:
  useful for explaining IMU drift profiles, especially L2/L5/L7.

interactive visual browser:
  optional later; PNG contact sheets are enough for the first run.

unit tests:
  small synthetic circular-motion tests with known IMU/range truth should verify
  sign conventions before using R01-R17.
```

Completeness rule:

```text
The simulation is not complete just because many A/U/R/P/L/I/T combinations ran.
It is complete only if frame conventions, time alignment, IMU drift generation,
UWB range-bias handling, fusion health, visual inspection, and runtime manifests
are all present and reproducible.
```

## Main Risks

```text
R1: double differentiation of Vicon position can amplify marker noise
    Mitigation: spline/Savitzky-Golay smoothing before acceleration.

R2: synthetic IMU frame conventions can silently flip axes
    Mitigation: explicit frame report, gravity-norm sanity, round-trip checks.

R3: tight raw-range EKF may look worse because range biases dominate
    Mitigation: include R2/R3 bias and robust residual variants.

R4: brute-force output volume can bury the useful result
    Mitigation: summary-only full-matrix sweeps plus selected heavy trajectory
    outputs. Never reduce the compute matrix to manage PNG/CSV volume.

R5: GPU acceleration may not help if implemented as many tiny Python loops
    Mitigation: batch rows by solver family and profile Phase 2 before scaling.
```

## Immediate Next Step

Implement and run the official Phase 2 runner.

The first Phase 2 execution starts at:

```text
runs/phase2_screening/<run_id>/stage0_gate_fulfillment/
```

This stage must upgrade `G5_noise_seed_repeats` to `PASS_SCREENING` and either
upgrade `G3_range_bias_policy` to `PASS` or disable tight-fusion rows with an
explicit exclusion reason before broad screening starts.
