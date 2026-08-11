# Quaternion ESKF foundation

Primary verdict: `QUATERNION_ESKF_FOUNDATION_CONDITIONAL`

Q1 propagates a real normalized scalar-first `q_NB` from bias-corrected gyro and carries genuine accelerometer/gyro bias states plus a 15-dimensional error covariance. Gravity, ZUPT and bound T4 position are Kalman measurements with Joseph covariance updates. Synthetic bound-frame cases verify the complete coupled mathematics; real captures verify local attitude and the hard motion veto without inventing an N↔V4 transform.

The independent stationary capture retains zero false MOVING and finite symmetric PSD covariance. All five completed rotation phases produce angular accumulation and a MOVING transition with no stationary relock during independent supported motion. The tabletop regression detects the frozen C2CC/AA61 moves; fleet-context qualification has `7` failing node rows after accounting for each frozen vibration interval and the exact one-second causal feature memory. Standalone shock response remains reported separately as diagnostic evidence. These are internal-consistency claims only; no absolute attitude/RPM/axis/trajectory truth exists.

`S2R_QUARANTINED_OFFLINE_ONLY`. `SPATIAL_ACCELERATION_COUPLING_BLOCKED_FRAME_BINDING` remains true. The implemented system is position/state Fusion in S2P plus a Q1 attitude/ESKF foundation; it is not yet fully coupled real inertial/UWB Fusion. Missing facts are signed physical sensor axes, measured physical gravity/up in V4, `R_V4_N`, yaw reference/uncertainty refinement, and lever arm. The compact six-face/signed-axis/V4-up calibration in `NEXT_CALIBRATION_EXPERIMENT.md` activates the next stage.
