# Phase 2-R Calibration Observability Report

The operator-bound replay produced a limited conditional calibration, not an authoritative body calibration. Soft low-dynamic evidence supports weak gyro-bias estimates and anonymous sensor-frame specific-force directions. Functional gyro axes are retained as antipodal, mapping-conditional distributions. Full segment-to-IMU rotation, translation, accelerometer bias, joint centres, bone lengths, compliance, world trajectory, contact and IMU-to-UWB antenna geometry are not identified.

Dynamic accelerometer factors are disabled because this run does not establish the differentiable translational trajectory and independently measured lever arms required by the rigid-body acceleration equation. The mounting cluster is diagnostic only and has factor count zero, so standing accelerometer samples are not double-counted. Metric UWB factor count is zero because device antenna metrology is pending.

The data-only rank remains deficient across the required tolerance sweep; finite priors make the prior-inclusive system numerically full rank but do not create evidence. High-sensitivity segment extrinsic modes remain unidentified, therefore the P3 consumer is conditional-only.
