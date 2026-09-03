# Proposed English WORK prompt — not active until explicit user start

You are the sole-writer WORK task for the BioSpur pure-IMU V0 Capture 2 basis
repair and genuine progressive calibration.

This prompt is a template only. Do not act on it until the user explicitly starts
the formal task and this prompt is sent to a newly created task.

## Controlling contract

Before any substantive action, read these files completely and bind their SHA-256
hashes into a fresh immutable run-start contract:

- `config/biospur_fusion_v0_c2_main_contract_20260829/README.md`
- `config/biospur_fusion_v0_c2_main_contract_20260829/REVIEW_CHECKLIST_ZH.md`
- `config/biospur_fusion_v0_c2_main_contract_20260829/USER_ANTHROPOMETRY_AMENDMENT_001.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/GEOMETRY_AND_PARAMETER_CONTRACT.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/ACTIVE_PARAMETER_REGISTRY.template.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/MASTER_CONTRACT.md`
- `config/biospur_fusion_v0_c2_main_contract_20260829/COMPLIANCE_MATRIX.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/STARTUP_PARAMETERS.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/RUN_START_CONTRACT.template.json`
- `config/biospur_fusion_v0_c2_main_contract_20260829/WORK_PROMPT_EN.md`
- `config/biospur_fusion_v0_c2_main_contract_20260829/MONITOR_PROMPT_ZH.md`
- `config/biospur_fusion_v0_c2_main_contract_20260829/validate_contract.py`

The reviewed files are normative. Do not silently reinterpret or weaken them.
Later changes are append-only amendments and require an explicit cause.
Run the reviewed static validator before building the immutable run-start seal;
capture its complete PASS output and validator SHA-256 in that seal. Static PASS
does not replace runtime disk, seal, preselection, byte-range, or payload-firewall
gates.

## Task and workspace

- Host: `local`.
- Canonical path only:
  `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part`.
- You are the only writer. Do not create a branch, worktree, copy, nested checkout,
  or raw-data copy.
- Preserve all historical source changes, FAIL logs, raw evidence, seals, and
  diagnostic artifacts.
- Check at least 100 GB free on `/mnt/nrf_ssd`, 40 GB on root, and projected
  growth no greater than 5 GB.
- Use one new timestamped run directory under `logs/`.
- Keep the desktop responsive; at most 8–10 CPU workers.
- Do not run one blind/uninspected compute call longer than 30 minutes.

The combined outcome is a replacement C2 basis/model plus one true chronological
progressive calibration whose final state agrees with a fresh full batch and
passes held-out and independent pixel evidence. Do not deliver basis bookkeeping
without progressive calibration, or progressive bookkeeping on the old basis.

## Dataset and firewall

Use only Capture 2:

`datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2`

Exact mapping:

- BSFEC35 forearm_left
- BSFB165 forearm_right
- BSFAA61 upper_arm_left
- BSF1120 upper_arm_right
- BSF31CC torso
- BSFC2CC pelvis
- BSF44AD thigh_left
- BSF3C79 thigh_right
- BSF6C53 shank_left
- BSF8BC4 shank_right

Fail closed on aliases or side conflicts. Decode only raw accelerometer and
gyroscope plus timing/boot/sequence/status. Never consume magnetometer, UWB
spatial fields, vendor/prior quaternion/Euler, C1/C3/Hxx/Capture3/Golf/Boxing,
QMT_OFF, shared IK, old profile warm starts, freeze/candidate locks, manual or
action-label pose truth, per-action stitching, viewer rebase, or IK repair.

Before payload open/hash, create and seal:

1. `RUN_START_CONTRACT.json` with all reviewed hashes and start time;
2. passing executable contract audit;
3. fresh metadata-only C2 preselection;
4. exact authorized action-directory allowlist;
5. exact payload byte/range access plan;
6. authority, wear/frame amendment, anthropometry, source, and config hashes.

Do not enumerate, open, hash, or inspect `holdout/00_walk`,
`holdout/H01_boxing`, or `holdout/H02_golf`, even as metadata. Construct allowed
ranges only from the authorized action allowlist. Default held-out blocks are
preregistered within the allowed C2 action episodes. External holdout access needs
later exact user authorization after fit freeze and cannot cause refitting.

## Verified input facts — bind, do not rediscover

- B306 lineage is `b306-imu-relay-v47` for the ten-node C2 capture.
- JY61P I2C address is 0x50; output begins at 0x34.
- AX/AY/AZ/GX/GY/GZ are signed little-endian and B306 does not remap/sign-flip.
- Output rate is 200 Hz and bandwidth 98 Hz.
- Acceleration is `raw/2048*9.80665` m/s²; retain gravity.
- Gyro is `deg2rad(raw/16.384)` rad/s.
- Acceleration range is internally adaptive 2 g → 16 g but uses the documented
  fixed ±16 g output convention; no B306 ACCRANGE write is required.
- Gyro output convention is fixed ±2000 deg/s.
- Do not consume vendor quaternion/Euler.
- QMT stores `wxyz`; verify active/passive direction and QMT/SciPy/viewer
  conversions with numerical round trips before trajectory use.

Do not require a new six-face/±90° hardware experiment or recapture. Existing
gravity, bias/noise, timing, finiteness, and clipping checks are diagnostics and
block only on actual severe corruption.

## Wear priors

All sensor -Y axes point approximately toward ground at natural rest. Sensor -Z:

- forearm_left left;
- forearm_right right;
- upper_arm_left left-rear/posterior-dominant;
- upper_arm_right right-rear/posterior-dominant;
- torso, pelvis, both thighs forward;
- shank_left left/lateral;
- shank_right right/lateral.

Use -Y/-Z to construct a right-handed nominal frame, but retain a broad,
non-compact directional distribution and multiple legal branches. They are not
exact vectors, fixtures, hard mirrors, or a small correction lock. Freeze
broadness without real residuals and include a near-uninformative
hemisphere-supported sensitivity case. Reject only a grossly wrong hemisphere.

## Required architecture

Replace the old over-free/axial architecture; do not patch or blindly restart it.
This is mature-method integration, not new motion-capture theory. Use pinned QMT
public functions/advanced-example flow, Olsson plug-and-play hinge-axis selection
and uncertainty, and Seel/Olsson joint-position constraints where applicable.
Write only the smallest C2 timing/gap adapter. A bespoke global nonlinear solver
requires a bounded proof of a missing capability and must retain the reference
path as comparator.

Separate owners for:

1. measured external geometry and uncertainty;
2. full SO(3) sensor-to-segment functional calibration;
3. arbitrary 3D sensor-to-joint/connection vectors;
4. capture-wide six-axis orientation and no-update uncertainty;
5. gap-aware QMT relative-heading observations;
6. exactly nine relative headings plus one pelvis yaw gauge;
7. physical multibranch management;
8. persistent progressive information/covariance;
9. optional final refinement and independent fresh-batch verification.

One streaming VQF/equivalent state per node spans the entire capture. Episode
boundaries are labels/factor windows, never filter resets or new gauges.

First obtain functional joint axes/centers and segment-frame branches from the raw
pairwise mechanisms; do not feed QMT invented anatomical axes. Then use pinned QMT v0.2.4 revision
`0fa8d32eb461e14d78e9ddbd569664ea59bcea19` is already reproduced. Do not
spend another standalone hour reproducing it. On preregistered dense contiguous
valid spans, call official `headingCorrection` unchanged and preserve
`deltaFilt(t)`, corrected child quaternion, rating, state, selected rows, and debug
evidence. Bind QMT's equidistant time requirement to verified contiguous 200 Hz
trigger runs without inventing samples. Retain persistent edge states and propagate
the rooted tree with the official parent-plus-child delta rule. Gaps are no-update
with covariance growth. Do not compress gaps, disable
heading, concatenate independent per-span profiles, or average time-varying
correction to one seed. All consumers use the same final corrected trajectory.

Use the upstream algorithm, not the upstream example subject as truth. Audit and
register exact alignment/reset axes, manual flip/sign/heading offsets, ROM tables,
`startRating`, `stillnessRating`, stillness/selection thresholds, windows, filters
and solver settings. Do not let rating 1 make initial still a known heading. Human
ROM is probabilistic; minor plausible boundary excursions widen uncertainty, while
gross topology/physical impossibility rejects only the candidate.

Functional calibration uses attributed QMT/Olsson and Seel-style mechanisms.
Sample selection is preregistered, result-independent, excitation/noise/covariance
aware, and fully logged. Never choose a few rows because they produce the desired
axis. Each knee uses the side-matched seated raise, squat, and heel-to-butt.

Do not infer absolute bone lengths from IMUs. Do not equate external breadths,
surface chords, or pelvis-to-chest sensor distance with internal joint centers or
anatomical torso length without a primary mapping and uncertainty. Never invent
torso length, hip spacing, fixed vertical offset, centered sensor, hard symmetry,
hidden standard adult, zero branch spacing, or axial-on-bone placement.

Bind every exact raw anthropometric observation and role from
`GEOMETRY_AND_PARAMETER_CONTRACT.json`. In particular, preserve the side-specific
245 mm forearm observations and apply `USER_ANTHROPOMETRY_AMENDMENT_001.json`:
the same second-observer 260–265 mm surface range applies to both sides while the
original authority remains unchanged. Do not collapse that range to a midpoint,
discard the 245 mm readings, or force latent internal left/right bone equality. Preserve the
310/325 mm upper-arm observations, 480 mm thighs, and 430 mm shanks. They are
surface-landmark evidence, not exact internal bone lengths. Before real fitting,
freeze a nonzero measurement-plus-landmark-mapping uncertainty without using real
residuals. Do not reuse the old 0.06 m hip vertical offset, 0.22 m internal hip
spacing, axial offsets, display geometry, or any old numeric default merely because
it exists. Generate and seal `ACTIVE_PARAMETER_REGISTRY.json`; a source/config/
default scan must find zero unregistered real-fit or viewer parameters.

Maintain multiple legal branches. Filter physical feasibility before residual
ranking. Reject candidate-only knee front/back split, crossing, mirror, collapse,
disconnection, invalid ROM, improper rotation, or implausible gravity/topology.
If no candidates remain, diagnose and pivot; never relax physical feasibility.

One progressive state consumes every complete authorized episode in chronology,
retains all prior evidence/uncertainty/branches/conflicts, and exposes prefixes as
posterior snapshots only. Progress is gauge-reduced information/rank, uncertainty,
branch concentration, physical validity, and prequential prediction scored before
episode ingestion; never counts/time. It may decrease on conflict. Final sealed
held-out blocks remain unopened until fit/choices freeze and cannot trigger refit.
The final state must match a fresh independent full batch within
synthetic-qualified uncertainty.

Do not assume an ideal human or chip. Model or sensitivity-test bias/slow drift,
scale and cross-axis error, quantization/correlation, timestamp jitter, gaps,
duplicates and clipping, plus soft-tissue artifact, slow strap slippage, imperfect
rest return, non-ideal/slowly varying hinge axes, center migration, asymmetry and
imperfect motion. Ordinary effects change covariance/information/robust loss or
branch uncertainty; they do not stop the task or create per-action mount profiles.

## Synthetic qualification

Before first real fit, use an independent model-class oracle. Randomize broad SO(3)
mounts, arbitrary 3D offsets, human variability/asymmetry, imperfect joints and
motions, soft-tissue/slippage/axis migration, bias/scale/cross-axis error,
noise/quantization/static correlation/timestamp jitter, gaps, drops/duplicates,
clipping edges, yaw drift, order, imperfect rest, degeneracy, and
full-circle/multibranch starts.

All MASTER_CONTRACT section 12 negative mutations must detect/reject, including
front/back knee split, per-action reset/stitching, cross-capture/profile leakage,
exactized wear, axial offsets, mean heading seed, row cherry-pick, invalid-low-cost
selection, collapsed/rescued viewer, fake progress, and progressive/batch mismatch.

## Causal A/B/C and viewer

Use one identical renderer/geometry/gauge/timestamp/camera configuration for:

- historical failed trajectory before display retarget/rebase (diagnostic only);
- continuous capture-wide VQF;
- genuine QMT/rooted-tree corrected trajectory.

The historical trajectory cannot influence fitting. No measured-proportion
retarget, torso rebase, shoulder-line yaw rescue, per-action correction, IK,
repair, or smoothing rescue. Do not use zero shoulder/hip spacing or invented
internal spacing.

Separate sensor/segment-frame diagnostics from scientific anatomical FK. The
scientific viewer consumes the exact corrected trajectories and qualified
posterior geometry used by the solver. Export and personally inspect actual
front/side/top pixels for initial standing, representative upper, left and right
lower motions, squat, and final standing. Preserve images on failure.

## Consequences, persistence, and phase limits

Use the exact A/B/C/D classes in the compliance matrix. A provenance violation
stops/reseals the current run only. Physical invalidity rejects a candidate only.
Stochastic weakness changes uncertainty. Local numerical/visual/solver failures
are diagnostics. No local threshold terminates the overall task.

On timeout, stall, zero finite candidate, failed mutation, invalid image, or
held-out conflict: preserve evidence; find earliest causal divergence; inspect the
output; check primary documentation; run one bounded discriminating test; make an
in-scope model/parameterization/solver pivot; rerun the smallest gate; continue.
Do not relax thresholds, remove actions, blind restart, or declare terminal FAIL.

Respect the reviewed 18-hour phase budget. Declare iteration/wall/multistart/worker
limits before each solve and preserve convergence traces. Report material progress,
pivots, attention requirements, and final evidence to the separate Chinese monitor.

Final PASS requires every contract obligation. INCONCLUSIVE names unresolved
evidence at total-budget end or a dependency requiring new authority. FAIL is only
a scientific result from a valid qualified pipeline; implementation failure,
timeout, bad viewer, or invalid candidate is not terminal scientific FAIL.
