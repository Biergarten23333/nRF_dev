# Ten-node body calibration v1 — frozen offline protocol

Status: offline protocol and software gates frozen. It does not authorize
hardware access before the exact `START_BODY_CAPTURE` token.

## Scope and invariants

The session jointly estimates the anonymous nine-node assignment, the known
`BSF31CC` central constraint, left/right side, each session's sensor-to-segment
proper rotation, gravity-aligned navigation frame, initial state and uncertainty.
Global yaw is an explicit gauge freedom. Ambiguous assignments remain ambiguous:
the report must include the best and second hypothesis, absolute costs, margin,
constraint residuals, left/right confidence, extrinsic matrices/quaternions,
unobservable gauges, and an `sufficient_for_ik_fk` verdict.

PCB axes, T-Pose direction, manual motion direction/amplitude/angle/speed and
IMU double-integrated displacement are never ground truth. Wrist/ankle mounts
may be mirrored or reversed independently; estimated extrinsics must have
determinant +1. Motion names supply only active-limb, left/right time labels and
the frozen fit/validation segmentation.

## Frozen lineage

The collector lineage is `v47_c2cc_continuous_capture.py`: one CDC open, raw
recording from the first byte, retained stale prefix, data-derived live catch-up,
stable decoded queue, in-stream formal T0, continuous decoding, and clean stop.
The body capture uses the same lifecycle for all ten peers and one raw COBS file.
No standalone preflight may stop or reopen it.

The exact fleet contract is:

* Fusion Master `dk-fusion-imu-relay-v36`;
* exactly the ten identities listed by `v47_real_data_adapter.NODES`, with
  `BSF31CC` the sole known central identity;
* every peer connected and subscribed, marker `b306-imu-relay-v47`, FWID
  `f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed`,
  active image SHA-256
  `90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98`,
  and `confirmed=1`;
* no missing, duplicate, or unexpected peer; Listener poll receivers healthy.

Geometry is exclusively
`deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json`, SHA-256
`20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1`.
`CAPTURE_BOUND_GEOMETRY_MANIFEST.json` binds Anchor IDs 0–7 to canonical A–H
identities, declares relative geometry/coordinate convention, and passes the
single-owner delay convention. The reflected intermediate
`V4IO/anchor_layout.json` is rejected by path and cannot be selected.

UWB positions come from frozen `UWB_TAG_T4`; its per-frame covariance/outlier
state is retained. Quaternion propagation and update behavior comes from the
repaired Q1 implementation: norm and NIS rejection, session gyro-bias tracking,
quaternion normalization/sign continuity, finite/symmetric/positive-definite
covariance and Cholesky checks. Production IMU policy is identity accelerometer
matrix, shared accelerometer bias zero, and per-node initial-still gyro bias.
C2CC/31CC device calibrations may be run only as oracle sensitivity variants
and never copied to production parameters.

## Authoritative slot input

The operator-authorized topology is frozen in `TEN_NODE_BODY_SLOTS_V1.json`:
Central (front of the rib-cage triangle), bilateral Wrist, Elbow, Knee and
Ankle, plus mid-waist Pelvis. `BSF31CC` is constrained to Central; no other BSF
mapping is encoded. Graph edges represent the observed segment chains only:
Central–Pelvis, Central–Elbow–Wrist bilaterally, and
Pelvis–Knee–Ankle bilaterally. No numeric segment length is operator-approved;
reasonable lengths are session-estimated and uncertainty is reported.

## Evidence and estimator

Fit evidence comprises rotation-invariant gyro/acceleration temporal features,
left/right asymmetric token windows, adjacent-joint relative motion, still/hold
features, T4 positions and dynamic uncertainty/outlier fields, and topology plus
plausible segment-length residuals. A deterministic multi-hypothesis search
scores all assignment candidates subject to the known central constraint. For
each candidate it estimates per-session proper rotations and initial state, then
reports a decomposed cost ledger. Low margin or equivalent symmetry produces a
non-identifiable verdict, never a forced assignment.

The `walk` and `final_still` phases are frozen as untouched validation. All
earlier phases are fitting data. Validation cannot alter assignments,
extrinsics, hyperparameters, thresholds or covariance. It only produces the
held-out residuals and final IK/FK readiness verdict.

## One-open capture and accounting

Lifecycle:

`one serial open → record first byte/warm-up prefix → detect stale-to-live → live catch-up → read-only in-stream readiness → stable queue → FORMAL_T0 → token-bracketed actions → final still → clean stop`

Warm-up, Fusion IMU/UWB, Listener, reply/token/identity records and host
monotonic timestamps remain in the same run. Start/end fragments are separately
classified. Raw bytes must close as written bytes = decoder-consumed bytes +
prefix fragment + suffix fragment. The formal window requires zero sequence gap,
duplicate, timestamp reversal and queue drop; no interpolation or deletion can
repair a failure. A failed gate preserves evidence and blocks analysis.

Read-only readiness commands occur inside this open lifecycle. No OTA, upload,
PREPARE/COMMIT, flash, reset/reboot, power/charge action, AutoPos, Anchor/Master
configuration or BMD101 operation is part of this protocol.

## Operator state machine

Before hardware access, the operator must send exact `START_BODY_CAPTURE`.
After the collector is open, warm-up/live catch-up and readiness have passed,
the UI displays one action and its unique `READY_<ACTION>` token. Receipt records
`TOKEN_RECEIVED`; the next complete 10.000 seconds are
`PRE_ACTION_TRANSITION_UNSCORED`. Only after that deadline may the chat emit the
standalone bell-formatted `ACTION_START` and record its monotonic timestamp.
After the action the operator remains still for at least three seconds, walks
back, and enters `STOP`. `STOP` means only ACTION_STOP and never closes the
collector. IMU/UWB segmentation finds the final target-motion → >=3 s still
boundary; renewed motion is `POST_ACTION_TRANSITION_UNSCORED`, and STOP is only
an upper bound. The only whole-capture emergency token is `ABORT_CAPTURE`.
Duplicate/wrong/early tokens are rejected and recorded.

| Phase | Prompt summary | Guide | Partition |
|---|---|---:|---|
| initial_still | natural standing still | 5 s | fit |
| t_pose | T-Pose still | 5 s | fit |
| arms | left, right, then both arms | 12 s | fit |
| elbows | left/right flexion and forearm rotation | 15 s | fit |
| knees | left then right knee raise | 12 s | fit |
| heels | left then right heel back | 12 s | fit |
| squats | two natural squats | 10 s | fit |
| trunk | left/right turn, forward bend/recover | 12 s | fit |
| walk | small walk, turn, return still | 12 s | untouched validation |
| final_still | natural standing still | 8 s | untouched validation |

Scored action guidance totals 103 seconds and remains below 120 seconds. Ten
mandatory pre-action transitions add exactly 100 seconds of unscored collector
time; post-action stills and return walks add further unscored wall time.

## Run artifacts and deterministic replay

Formal runs live only at
`B306_Part/logs/v47_ten_node_body_calibration_20260814_<HHMMSS>/`. Required
artifacts include the single raw file, frozen input hashes/preregistration,
readiness evidence, Listener data, identity/reply records, lifecycle and token
ledger, byte-accounting and integrity verdicts, T4/Q1 derived data, hypothesis
ranking, decomposed residuals, fit output, untouched validation output, replay
hash and final report. Raw evidence is never overwritten.

Host-only implementation is in `tools/body_calibration_v1/`; tests are
`tools/tests/test_body_calibration_v1.py` and
`tools/tests/test_body_calibration_capture.py`. The dry-run command is:

```bash
PYTHONPATH=B306_Part/tools python3 -m body_calibration_v1.dry_run
```

The same seed and inputs must produce byte-identical JSON. Preparation remains
fail-closed until the authoritative slot file is supplied and validated.
