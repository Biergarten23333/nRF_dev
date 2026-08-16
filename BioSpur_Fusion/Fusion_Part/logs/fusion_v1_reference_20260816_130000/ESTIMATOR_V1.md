# Estimator v1 status

No fitted estimator is claimed. The available clean components are asynchronous
pose interpolation, articulated FK, Cauchy range weighting, and per-pair health
hysteresis. The intended state is pelvis world SE(3), root velocity,
segment-relative SO(3), and per-node IMU biases. Raw individual ranges are
evaluated at their common time; UWB supplies conservative low-frequency world
information and cannot overwrite a node position.

Static fused initialization and the range-plus-orientation smoother remain to
be implemented. Numerical covariances have not been selected because functional
calibration and articulated residual statistics are not yet available.

