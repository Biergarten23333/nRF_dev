# BioSpur Fusion Phase 3 final report

## Verdict

Primary verdict: `STAGE_COMPLETE_NEEDS_USER_CAPTURE`.

Completed secondary results: `PHASE3_OPERATOR_MAPPED_RESEARCH_FRAMEWORK_COMPLETE`,
`PHASE3_CONDITIONAL_RUNTIME_IMU_ONLY_POSE_AVAILABLE`,
`AUTOMATIC_NODE_ASSOCIATION_DEFERRED`, and `RESEARCH_CALIBRATION_LIMITED`.
Full `PASS_TEN_SEGMENT_RUNTIME_IMU_ONLY_ARTICULATED_POSE` was not obtained.

## Mapping authority and lineage

The append-only v2.1 plan addendum and policy were created and hash-bound. They
narrowly supersede Canonical v2.0 sections 17, 19, and 20 only where those
clauses required AutoMapping as the sole route. Every new session/donning now
requires its own explicit binding; missing, duplicate, stale, cross-session, or
AutoMapping-Top1 substitutions fail closed.

The exact binding came from Phase 2-R
`OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json`, SHA-256
`cca303d226a6106f7c12e529ee8d530fef481cc568d0d15342e0abc41a53d1b6`,
with authority `OPERATOR_RECORDED_POST_CAPTURE`. The normalized 10-by-10
bijection is scoped only to capture `Capture_2_with_JOINT_LABEL`, session
`capture_2_with_joint_label`, donning
`capture_2_with_joint_label_donning_01`. `BSFC2CC` is present and `BSFC22C` is
rejected. Runtime mapping is immutable.

Phase 2-R automatic association remains failed (Top-1 8/10, truth rank 3,
failed statistical gates, `TRUTH_CONTAMINATED_DEVELOPMENT_REVISION`). It was not
retried and no AutoMapping score, Top-K, historical mapping constant, Q1, VQF,
T4, old pose, or UltraInertialPoser value entered the estimator.

## Runtime state, factors and access

The causal state has 123 local dimensions: ten orientation tangents (30), root
local position/velocity (6), ten gyro biases (30), ten sensor-frame
accelerometer biases (30), and nine finite-covariance joint compliance blocks
(27). Root orientation is the pelvis orientation. There is no per-node free XYZ
or per-frame bone stretch.

Across the 19 development windows, nonzero production counts were gyro
propagation 1,140,877; gyro-bias process 1,140,877; accelerometer-bias state
1,141,074; low-dynamic specific force 888,643; soft joint closure 853,788;
dominant-axis/ROM 284,164; temporal process 1,140,877. Nonzero Jacobian blocks
were segment orientation, gyro bias, accelerometer bias/tilt coupling, soft
joint compliance, and temporal process.

Real dynamic specific force count was zero. The full tangential/centripetal
formula passed independent synthetic oracle tests, but real activation lacked a
finite, proven segment/joint-reference-origin-to-IMU-sensitive-origin lever-arm
distribution and differentiable metric translation. This blocks full dynamic
calibration and degrades acceleration-sensitive orientation, root motion, joint
position, and bias separation.

Each raw accelerometer UID was consumed once. Independent gravity,
mounting-cluster and Phase 1 orientation factor counts were zero. Phase 1
orientation initializer count was also zero in the current reference path.
Derived bias/extrinsic/axis/anatomy evidence remained conditional and was not
reintroduced as independent evidence.

The consolidated ledger contains 159 entries, 102,551,130 streamed bytes,
23,002,357 decoded numeric IMU scalars, 41 IMU array materializations and
14,875,960 factor consumptions. Invalid/redo/deleted numeric consumption was
zero. All 19 promoted development windows were read; D2 remained
`D2_NOT_REOPENED_BY_PHASE3`.

H00/H01/H02 were opened exactly once each after the final release envelope.
They produced 80,005 / 80,010 / 79,980 IMU observations. UWB numeric decode,
arrays, statistics, initializer consumption and factors were all zero.

## Observability, uncertainty and outputs

The 123-dimensional structural whitened information audit reported data-only
rank/nullity 70/53 and prior-inclusive 123/0 at every frozen tolerance from
1e-4 through 1e-8. Prior-inclusive rank is not evidence. Declared gauges and
weak modes are global translation, global yaw, possible common velocity,
independent segment/subtree heading, sensor-to-segment twist/sign, anatomy
scale, and joint centres.

Ten instrumented orientation streams and conditional relative-joint states are
always emitted. They are not all usable under the 15° conditional cone:
development usable availability was 8.8%, and H00/H01/H02 were 0%. Root local
state continues with drifting uncertainty; world translation is unavailable.
Global yaw and common velocity remain gauge/weak modes. Positions are
`MODEL_INFERRED_SCALE_CONDITIONAL`, head/hands are `MODEL_INFERRED`, feet are
`UNAVAILABLE`, and contact is `CONTACT_UNOBSERVABLE`. Contact and hard-ZUPT
factor counts are zero.

Synthetic qualification used 200 independent trials. Noiseless maximum error
was 1.48e-15 rad. Coverage was 0.92 with Wilson interval [0.874, 0.9502], which
contains 0.95; matched-gap additional-uncertainty fraction was 1.00. Normal
motion median/P95 were 7.75°/10.04°: P95 passed 15°, but median failed the 5°
gate. Revision A under-coverage and the original common-clock failure are
retained as failed evidence.

All 19 development windows and all three holdouts emitted 100% of scheduled
records with changing measurement cutoffs. Duplicate development replays were
byte-identical. H00 (in-scope continuity gate), boxing, and golf had no crash,
NaN, last-frame hold, frame swap, segment permutation, or covariance collapse.
The holdout did not cause a source/config/threshold change. It validates only
degraded-state continuity and failure characterization, not usable pose or
accuracy.

CanonicalHumanState v0.1 includes subject/capture/session/donning identity,
frozen mapping and conditional-calibration references, ten segments,
cutoff/output time, estimate kind, latency, L0 realization, local root and
segment state, conditional tangent covariance semantics, gauges, validity and
degraded reasons, inferred/unavailable flags, and `active_modality=IMU_ONLY`.
Unavailable DOFs are not serialized as valid zeros.

The P4 probe constructed the exact Phase 3 estimator with range disabled,
opened no UWB loader and produced an identical initial state. Its future range
interface is additive-only; quaternion overwrite and hard-reset APIs are absent.
No Phase 4 range factor was implemented.

## Qualification, publication and next evidence

Implementation commits are `ea3d7d56fc7c6c75e0f23bb4d57bdafb44b2b9ea`
and forward repair `a3c3c2f6e45f8f7c2de8e9ad4c030a245238bdec`.
The exact final implementation passed 186 tests plus compileall, with zero
failed/skipped/xfail/waived/ignored. Attestation and remote SHAs remain
`PENDING` in tracked artifacts and are recorded after push in the repo-external
publication envelope.

The protected worktree start snapshot was HEAD
`9480b3f4c620fe13aaae5dc127e8105393d6d392`, index tree
`41cf15990945863783718d0706e6a64a66e6ff04`, porcelain-v2 record count
106,505 and porcelain-v2 SHA-256
`3e4b27cdb14450066c0498814313a71290dadbd01f24fd861e1d4e328204e003`.
Its prepublication snapshot is identical; the final after-publication equality,
attestation SHA, remote SHA and `PUBLICATION_SUCCESS` status are intentionally
repo-external and remain `PENDING` here.

To qualify usable ten-segment pose in a new release, collect one coordinated
package: independently measured per-node segment-to-IMU rotation including
twist/sign and segment-origin-to-IMU lever arms with covariance; subject
anthropometry and joint-centre evidence; production IMU intrinsic/bias
qualification; independent excitation/reference that resolves subtree heading;
and a new numerically unopened evaluation capture with its own confirmed
donning binding. External optical truth is required before any accuracy claim.

Phase 4 UWB fusion was not started.
Automatic node association remained deferred and was not used by the estimator.
No UWB measurement numeric value was consumed by Phase 3.
No external pose, metric-world, clinical-angle or accuracy claim is made.
