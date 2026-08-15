# IMU_MULTI_ACTION_ENGINEERING_PREVIEW_V1

This is a non-clinical, IMU-only articulated engineering preview. It does not
change Revision C, V3, V4, or V4.1 and does not claim clinical joint centres,
clinical angles, subject-specific dimensions, absolute heading, or UWB
position.

The calibration allowlist contains exactly eleven labelled calibration
actions. Phase A must pass before any nonlinear calibration solver is created.
Golf and boxing remain held-out until calibration freeze/reload, continuous
label-blind replay, and all eleven calibration previews pass. Walk,
final_still, UWB/T4/Anchor, and operator measurements remain sealed.

Frame convention: `q_NB` is scalar-first Hamilton and its active rotation
maps board vectors into the node-local gravity frame. Gyro increments are
board-frame increments and right-multiply the attitude. Absolute yaw is not
observed. The product removes one common display-yaw gauge and never publishes
per-segment axial twist.
