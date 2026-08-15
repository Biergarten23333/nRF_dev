# IMU_RELATIVE_ORIENTATION_PREVIEW_V0 product contract

This is a non-clinical, relative-orientation motion preview. It uses real IMU
samples, a fixed generic skeleton, and a pelvis-origin display gauge. It does
not estimate subject dimensions, absolute position, compass heading, clinical
joint angles, or anatomical ground truth. It is independent of the failed S2
scientific-calibration checkpoint.

The eleven calibration actions may determine preview-only sensor axes,
relative headings, continuous low-frequency yaw-drift nuisance coordinates,
functional axes, neutral zeros, and IMU biases. Labels may select calibration
residuals and post-replay slices. Labels must not reset replay state. Replay is
one continuous timestamp-driven timeline with no pose, heading, extrinsic,
contact, root, velocity, or ankle reset.

Golf and boxing remain sealed until the calibration-only numerical, continuity,
ablation, and rendered-preview gates pass. Walk and final_still remain sealed
for the entire product. UWB, T4, Anchor geometry, and operator measurements are
not valid inputs.
