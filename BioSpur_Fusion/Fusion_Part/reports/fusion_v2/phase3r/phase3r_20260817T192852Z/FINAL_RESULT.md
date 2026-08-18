# BioSpur Fusion Phase 3-R final result

## Verdict

`PASS_PHASE3R_IMU_ONLY_ARTICULATED_POSE_ENGINEERING_BASELINE`

`STAGE_COMPLETE_NEEDS_NEW_INDEPENDENT_VALIDATION_CAPTURE`

The previous Phase 3 history remains unchanged and is classified
`REJECTED_AS_IMU_POSE_CORE` / `STAGE_INCOMPLETE_CORE_ESTIMATOR_NOT_IMPLEMENTED`.
Phase 4 was not started.

Implementation commit: `4f35bbfce16b6efd6ec5fc793d13ee85d17423a5`.
The exact detached-SHA qualification artifact is
`PHASE3R_QUALIFICATION_DETACHED_4f35bbfce.json`, SHA-256
`6edd49ab1a213e127f23cdbf6ed90bf4b4ac11bfa8f5d110c3fa9a26fa2f6567`.

## Executed estimator and open-source paths

Official VQF v2.1.2 ran on all ten nodes for every action, producing 1,761,178
decoded IMU samples, 43,959 production frames and B0 trajectories. Its
deterministic double replay is tested; it is a comparator/initializer, not
truth or a duplicate measurement factor. Official qmt v0.2.4 executed neutral
and T-pose reset (maximum reset residual `5.55e-16 rad`), four hinge-axis
estimations and four heading corrections. Axis/heading confidence pairs were:
left elbow `0.576/0.571`, right elbow `0.390/0.704`, left knee `0.934/0.296`,
right knee `0.935/0.203`; the last heading path was rejected by the frozen
0.25 threshold. PIP and TransPose were architecture-only references; incompatible
six-sensor pretrained paths fail closed.

The production chain is a ten-node variable-dt 9-state ESKF followed by one
coupled 30-dimensional SO(3) normal system. It applies
`R_WS = R_WI R_IS`; the ten calibrations are non-identity, C2CC is separate
from H9, and the mapping is immutable. Gravity tests cover ±5°, ±45°, random
tilt, +Y installation, permutations, antipodes and sign mutation. Rest updates
are limited to the formal still intervals; preparation/recovery cannot update
bias. The full solver uses confidence-scaled robust anatomical priors, an
explicit 27-dimensional joint-compliance state with covariance, and an
objective-decreasing line search. Functional-source actions exclude their own
qmt-derived joint prior from Production and initialize from B0.

## Synthetic qualification

| Result | Measured | Gate |
|---|---:|---:|
| Noiseless gauge-aligned orientation max | `6.53e-10°` | `≤0.1°` |
| Noisy relative-joint P95 | `1.733°` | `≤5°` |
| Static tilt RMS | `0.920°` | `≤2°` |
| Fixed-bone variation | `3.33e-16` | `≤1e-9` |
| Gyro-bias error median / P95 | `4.25e-4 / 6.24e-4 rad/s` | finite pre-freeze range |
| MC coverage, 10 independent trials | `0.9894 ± 0.00535 SE` | `0.85–0.99` |

All nine required ablations changed the final pose. Data-only SVD was
`rank 29 / nullity 1` at every frozen tolerance from `1e-4` through `1e-8`,
with nonzero condition number `32.9353`; the weak-prior-inclusive matrix was
`rank 30 / nullity 0`, condition number `2059.67`. The remaining null direction
is the declared global-yaw gauge, not a whole-body invalidation.

## Real replay

All 19 promoted development windows and three H diagnostics completed. The
three H windows remain `CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC`. UWB numeric
decode, arrays, statistics, factors and initialization were all zero. Every
segment and relative joint reported 100% engineering availability; this is a
covariance/continuity result, not external accuracy. Maximum fixed-bone
variation was `3.89e-16`; the worst Production/B0 aligned 50 Hz step ratio was
`1.405`. Initial/final formal-still Production excursions were `3.69°/4.97°`,
versus B0 `9.31°/9.24°`.

Each action stores complete VQF quaternion/bias/bias-uncertainty/rest state
with sample-UID lineage, complete B0/B1/P segment and joint trajectories and
uncertainty, and an explicit baseline manifest. The maximum algorithmic
measurement-age P95 was `4.0 ms`; compute runtime is reported separately and
is not claimed as sensor-to-host latency.

Expected production responses were present: T-pose arms `89.2°/95.6°`, pelvis
circle pelvis/torso `95.4°/65.1°`, left/right shoulder `113.6°/129.7°`,
left/right elbow forearms `147.2°/169.0°`, left/right hip thighs
`97.8°/103.1°`, left/right seated-knee shanks `113.8°/112.1°`, left/right
heel-raise shanks `15.9°/9.2°`, trunk flexion/rotation `37.4°/76.2°`, squat
thighs `103.9°/96.6°`, and left/right heel-to-butt shanks `133.0°/118.6°`.
Supporting motion by other segments is retained and not treated as operator
error.

H00 walk, H01 boxing and H02 golf produced 100% engineering availability and
B1-to-production median differences of `24.85°`, `21.78°`, and `8.15°`.
They support stress diagnostics and visualization only, not held-out accuracy.

## Scope and remaining limits

`OPERATOR_MAPPED_SESSION_SCOPE`; `AUTOMATIC_NODE_ASSOCIATION_DEFERRED`;
`ROOT_WORLD_POSITION_UNAVAILABLE`; `GLOBAL_YAW_GAUGE_ACTIVE`; `NO_UWB_FUSION`;
`NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM`. Head and hands are model-inferred;
feet are unavailable. Scale is `MODEL_INFERRED_SCALE_CONDITIONAL`.

The next scientifically necessary acquisition is a new independent evaluation
capture with external pose truth if accuracy or generalization is to be claimed.
It is not needed to establish this engineering baseline.

External evidence root:
`/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r-evidence/phase3r_20260817T192852Z`.
