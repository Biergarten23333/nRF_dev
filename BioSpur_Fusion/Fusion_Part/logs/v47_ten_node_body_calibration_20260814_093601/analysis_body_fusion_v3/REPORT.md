# Ten-node body Fusion V3

Top-level verdict: `BLOCKED_FRAME_OBSERVABILITY`.

FULL_SEGMENT_POSE_CALIBRATION: `FAIL_SEGMENT_POSE_NULLSPACE`.

STICK_FIGURE_CENTERLINE_CALIBRATION: `FAIL_CENTERLINE_OBSERVABILITY`.

The V2 hard-coded `current_capture_frame_gate()` did not evaluate dataset observability; its correct conceptual status is `BLOCKED_FRAME_CALIBRATION_NOT_IMPLEMENTED`. V3 instead ran the calibration-only articulated optimization and its actual numerical residual Jacobian inside a filesystem-isolated process that could see neither held-out payloads nor the raw capture.

Computed nullspace-affected parameters: R_Forearm_L_from_sensor.x, R_Forearm_L_from_sensor.y, R_Forearm_L_from_sensor.z, R_Forearm_R_from_sensor.x, R_Forearm_R_from_sensor.y, R_Forearm_R_from_sensor.z, R_Shank_L_from_sensor.x, R_Shank_L_from_sensor.y, R_Shank_L_from_sensor.z, R_Shank_R_from_sensor.x, R_Shank_R_from_sensor.y, R_Shank_R_from_sensor.z, R_Thigh_L_from_sensor.x, R_Thigh_L_from_sensor.y, R_Thigh_L_from_sensor.z, R_Thigh_R_from_sensor.x, R_Thigh_R_from_sensor.y, R_Thigh_R_from_sensor.z, R_UpperArm_L_from_sensor.x, R_UpperArm_L_from_sensor.y, R_UpperArm_L_from_sensor.z, R_UpperArm_R_from_sensor.x, R_UpperArm_R_from_sensor.y, R_UpperArm_R_from_sensor.z. The held-out walk/final-still ledger remained sealed and no visualization was produced. No external-accuracy or clinical-angle claim is made.

The actual physical Jacobian is 1683 x 45 with rank 37 and nullity 8 after
the global-yaw coordinate gauge was explicitly removed. Fixed +/-1e-3 null
perturbations classify three directions as segment axial twist only. Five
directions produce segment-axis displacement just above the predeclared 1e-7
invariance tolerance (1.104e-7 to 1.509e-7 norm), although their maximum
joint-centre and antenna displacement remains below 5.19e-8 m. They therefore
cannot be waived as pure axial twist under the frozen rule.

T-Pose and initial-still removal are reported as mandatory-action dependence,
not ordinary optional LoAO. Optional-action LoAO also fails. Independent
full-data multi-start passes, while deterministic interleaved-knot fits differ
by 226.194 mm in left-thigh length and 163.231 mm in torso separation. Both
fits hit the shoulder-half-width and hip-vertical bounds, and their full-data
physical SSE differs by only 0.1383%. This supports genuine data-subset/action
dependence rather than a different local minimum; fitted lever arms were not
available to trade against length. Geometry is therefore not freezeable even
independently of the centerline observability failure.
