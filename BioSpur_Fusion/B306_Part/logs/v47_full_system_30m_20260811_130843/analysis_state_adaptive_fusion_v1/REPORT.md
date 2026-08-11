# v47 state-adaptive Fusion small experiment

## Verdict

`FUSION_SMALL_EXPERIMENT_FAIL`. This is an offline relative-geometry experiment, not an absolute localization-accuracy claim.

The held-out median ten-node RMS scatter is 60.859 mm for unsmoothed B0 and 12.694 mm for S1 (79.14% reduction). Both annotated node moves released the stationary lock in-window: **False**; both reacquired a stationary platform: **False**. Across 38 frozen table-vibration intervals and ten nodes, false persistent S1 platform transitions: **0**.

## Interpretation

S1 propagates position/velocity on reconstructed hardware time, uses IMU only for independently sampled motion evidence, performs real zero-velocity measurements while stationary, buffers individual stationary T4 points into a robust slow consensus, and applies gated Kalman position updates while moving. Thus UWB constrains drift without overwriting every high-rate state. No filter reset is used for normal transitions.

I0 and I1 are valid local inertial controls but their arbitrary local frames cannot be numerically compared with V4 position. H2, H5 and H3 retain their exact historical definitions and are explicitly blocked at the spatial frame-binding gate. T5's UWB-corrects-IMU direction most closely matches the desired architecture; T2's fixed alpha and T3's fixed output blend should not be inherited.

Full vector inertial propagation remains `BLOCKED_FRAME_BINDING`. A known manual trajectory capture is justified conditionally, specifically to measure the sensor/body-to-V4 transform and provide independent trajectory evidence.

## Calibration required before human-body IK/FK

Required work is: a surveyed V4 gravity/up direction; signed sensor-axis verification; per-mount board-to-body extrinsics including yaw; accelerometer six-face scale/misalignment calibration; gyro bias, temperature and scale characterization; lever arms; hardware-time validation against the trajectory reference; dynamic per-link UWB covariance/outlier characterization; and an external ground-truth manual trajectory. One static pose is not a complete calibration.

All windows are half-open `[start,end)`. Parameters use only T0+1–240 s with frozen table intervals excluded. T0+240–484 s, both moves, and later data were not used for threshold tuning. Raw evidence was read only.

## Reproducibility

Two independent full derivation/replay runs produced byte-identical core JSON, CSV, Markdown and SVG outputs. Runtime metadata is not embedded.
