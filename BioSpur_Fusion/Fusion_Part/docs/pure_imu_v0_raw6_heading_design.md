# Pure-IMU V0 raw-six-axis relative-heading design

Decision date: 2026-08-28. Scope: the ten-node Capture1/Capture2 body graph only.

## Decision

The product calibration path no longer requires the artificial B4 supported
whole-body en-bloc rotation. B4 is an optional synthetic/laboratory
observability diagnostic and contributes no residual when absent. Each capture
is calibrated independently from raw accelerometer and gyroscope samples. One
pelvis/global yaw is fixed as the sole gauge; the other nine node headings are
estimated once per capture.

No magnetometer, vendor/global quaternion, historical QMT_OFF state, shared IK,
corrected quaternion, action label as pose truth, viewer pose, UWB spatial
payload, Hxx, or Capture3 payload is an input to this path. The direct FK viewer
is a downstream consumer and cannot affect the fit.

## Audited data contract

The immutable selection is
`logs/pure_imu_v0_raw6_edge_global_20260828T050124Z/METADATA_PRESELECTION.json`
(SHA-256
`f32bd72c02cf4333efd21c7e8faf9546a8474312d511dcf92950d5b8240d4581`).
It was written from notices, action-event files, manifests, protocol metadata,
and timing bounds before any selected raw payload was opened or any action
payload hash was computed. It binds capture role, action, accepted attempt,
formal and complete episode bounds, train/held-out role, intended factors, and
exclusions. Later payload hashes are written to separate binding records; they
cannot revise the selection.

Capture1 is
`v47_ten_node_body_calibration_20260814_093601`. Its raw container is
`logs/v47_ten_node_body_calibration_20260814_093601/continuous_collector/fusion_host_raw.cobs.bin`;
its action notices are in the capture-level `ACTION_EVENTS.jsonl`. Capture2 is
`phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2`.
Its raw container is `system/fusion_continuous/fusion_host_raw.cobs.bin`, with
per-action `events/ACTION_EVENTS.jsonl`, `manifest/CAPTURE_MANIFEST.json`, and
the sealed node-to-body mapping. Capture2 timing lookup remains bounded and
instrumented at the `os.read`/`os.lseek` layer: binary probes are itemized,
sequential reads are limited to the selected action plus two superframes, the
actual byte-union proves no full traversal, and Golf/Boxing/Hxx timing intervals
must have zero byte intersection.

Accepted IMU rows have `global_time_ns`, `status`, `acc_raw[3]`, and
`gyro_raw[3]`. The conversion is
`acc_raw / 2048 * 9.80665 m/s^2` and
`gyro_raw / 16.384 deg/s`, converted to rad/s. Only `status == 1` rows enter the
frontend. Ten native streams are intersected on the shared global time axis and
resampled to 50 Hz. Every selected episode is the complete signal-verified
pre-rest, transition-to-action, formal action/hold, return transition, and
post-rest sequence. A formal-plateau-only rerun quantifies information removed
with the transitions.

The graph is the exact tree:

```text
pelvis -> torso -> upper_arm_left -> forearm_left
                -> upper_arm_right -> forearm_right
       -> thigh_left -> shank_left
       -> thigh_right -> shank_right
```

Capture-local identity is mandatory because Capture1 and Capture2 swap the two
forearm node assignments. Parameters never cross capture boundaries.

## Upstream baseline and reuse boundary

- [VQF](https://github.com/dlaidig/vqf), Python package 2.0.1: reused directly.
  A new VQF instance runs independently for every node and complete episode.
  `updateBatch(gyr, acc)` is called without a magnetometer and only `quat6D`,
  rest detection, and gyro-bias estimates are consumed. VQF's magnetometer-free
  yaw drift is expected; relative heading is supplied by the edge constraints,
  not mistaken for absolute yaw.
- [QMT](https://github.com/dlaidig/qmt), Python package 0.2.4: the official
  `jointAxisEstHingeOlsson` implementation is reused on raw acc/gyr for elbow
  and knee axes. This is the open-source method of
  [Olsson et al. (2020)](https://www.mdpi.com/1424-8220/20/12/3534).
  Three deterministic axis starts are retained and reported.
- QMT `headingCorrection` is reused with its official `proj` constraint after
  independently estimated parent/child axes are normalized to a common joint
  frame. The axis-alignment residual has two independent tangent-plane
  components per sample, providing the appropriate 2-DoF axis constraint for
  hinge-dominant elbow and knee edges. Per-action heading, rating, state, and
  spread remain visible.
- Joint-center specific-force closure is reimplemented from rigid-body
  kinematics, following the observability structure discussed by
  [Kok et al.](https://arxiv.org/abs/2102.02675). Parent and child lever arms are
  linear nuisance parameters profiled from the measurements without priors or
  bounds. Its rank, curvature, full-circle heading profile, physical residual,
  lever magnitude, and held-out residual are reported independently of B4 and
  compared against QMT on hinge edges.
- Broad 3-DoF shoulder, hip, and trunk connection plus ROM constraints follow
  the magnetometer-free kinematic-chain structure of
  [Lehmann et al.](https://arxiv.org/abs/2002.00639). ROM inequalities constrain
  excursions, not a labelled absolute pose. Display geometry is downstream and
  is never counted as measurement information.

## Edgewise baseline and unified refinement

The first result is deliberately edgewise. Elbow and knee headings use QMT
projection when QMT qualifies; shoulder, hip, and trunk headings use the
independently profiled B5 joint-center factor. Edge offsets are accumulated
from the pelvis through the tree and B5 is evaluated at the QMT result for a
direct comparison.

The refinement is one time-resolved, multi-action objective over nine
capture-wide headings. B5 lever arms, hinge axes, and a capture-shared
connection zero are the only nuisance quantities. There is no per-action
heading reset. Because the body graph is a tree, the nine pelvis-gauged node
headings are bijective with the nine edge differences and the edge nuisance
parameters are local. This permits a principled staged global method: for every
broad independent nine-dimensional start, every edge is profiled over a full
72-point `2*pi` circle with its B5 lever nuisance, then all nine node headings
are jointly refined. Raw starts, profile grids, profiled starts, final costs,
headings, Jacobian singular values, and multistart spread are retained.

The earlier unconstrained broad-start failure (failed fits and 152.97 degrees
spread) is preserved in
`SYNTHETIC_BROAD_START_FAILURE_PROVISIONAL.json`. A later local 0.35-rad seed
experiment is not qualifying evidence and does not supersede that record.

### Excitation-aware QMT qualification

An all-training-action spread is retained as a diagnostic but is not an edge
qualification gate: an unrelated still, trunk, contralateral, or shoulder
episode is not required to calibrate every elbow and knee. Before reevaluating
either real capture, QMT eligibility was fixed to the immutable preselection:
the episode must name the exact parent-child edge and both `qmt_proj_heading`
and `hinge_axis` factors. A mapped training episode is then admitted by signal
only when QMT median rating is at least 0.25, at least 25 rated rows exist
without the low-information fallback, at least 25 motion rows exceed 10 deg/s
relative rate about the estimated hinge axes, relative-axis-rate q90 is at
least 15 deg/s, and both gyro streams are finite. Olsson parent-axis multistart
spread must be at most 15 degrees. If multiple mapped training windows qualify,
their rating-times-row-count weighted pooled headings must span at most 15
degrees. A single mapped window makes no cross-window spread claim.

Axes are fitted only from the mapped training windows. QMT heading diagnostics
are still executed and retained for every selected episode. Mapped held-out
windows are pooled separately for comparison and never enter the fitted edge
heading. These semantics create one edge baseline per capture, not per-action
calibration parameters, and do not change the later nine-heading objective,
rank threshold, or full-circle multistart gate.

## Acceptance semantics

Synthetic truth is generated independently from analytic SO(3) trajectories
and rigid-body joint-center mechanics. Synthetic success is only a prerequisite
to opening real payloads. Real PASS additionally requires, for each capture,
rank nine after the single gauge, broad independent multistart agreement,
qualified edge factors, physically plausible unbounded lever estimates,
held-out generalization, complete five-phase windows, bounded hostile timing
access, drift/stillness and transition ablations, and numerically and visually
coherent direct FK. A missing gate is reported as the exact edge/factor/action;
absence of B4 is never a blocker.

The direct-FK audit does not treat constant link length as sufficient. Pelvis
and torso display axes are derived from the fitted bilateral hip/shoulder joint
centers; limb axes use the fitted proximal/distal centers and hinge axes. The
viewer then applies the already-fitted headings without IK or pose correction.
Every displayed edge must remain within its predeclared ROM with no more than
5% of frames beyond the limit plus 5 degrees and no frame beyond the limit plus
20 degrees. Independently of the viewer, fitted joint-center separations must
fall in deliberately broad adult ranges: torso 0.15--0.55 m, each upper arm
0.18--0.45 m, and each thigh 0.25--0.60 m. These are validation gates, not
lever priors and do not contribute optimizer rank.

The unified objective is a refinement of the independently qualified edgewise
baseline, not a license to replace it silently. For every edge, the final
relative heading must remain within 20 degrees of its edgewise QMT (hinges) or
B5 (other joints) baseline. The per-edge differences are reported. A larger
change is a factor-conflict failure even when the joint optimizer has full rank
and all broad starts reach the same cost.

Capture1 `initial_still` attempt 2 has the independent protocol description
"natural standing". That description is never a pose factor, calibration
target, display correction, or source of orientation truth. It is used only
after fitting as a qualitative sanity gate: the 95th percentile of the maximum
displayed joint angle must be no more than 45 degrees. Disagreement diagnoses
the raw6 calibration/extrinsic/FK path; it is not reinterpreted as the
operator's pose and is never injected to rescue the fit.
