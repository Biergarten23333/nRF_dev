# Phase 1 raw-IMU frontend method

Each hardware ID owns an independent state
`(R_L0_i_S_i, b_g_i, b_a_i, P_i)`.  The local right-error state is ordered
`(delta_theta, delta_b_g, delta_b_a)` and has dimension nine.  Gyroscope
samples propagate `R` on SO(3) with the native TIMER2-derived variable `dt`;
`b_g` has a random-walk model.  The sensor-frame three-vector `b_a` is an
explicit session-constant state with a finite weak prior.  Gravity-only data
do not make full accelerometer bias observable, so the accepted Phase 1 result
must label this state `WEAK_PRIOR_DOMINATED` and must not claim intrinsic
calibration.

The accelerometer enters once, through a soft-gated gravity likelihood.  Gate
weight decreases with gravity-norm residual, angular rate, and jerk.  The same
path supplies stillness evidence; no duplicate gravity factor is formed.
Invalid, saturated, CRC-failed, reset, boot, wrap, and gap conditions are
handled explicitly.  A long gap propagates no unobserved missing angle,
inflates local conditional covariance, and does not hard-reset orientation.

The reported covariance is conditional on timing nuisance.  Common-clock
affine uncertainty remains correlated within node/boot/clock segment.  Sample
age stays an `UNKNOWN_BOUNDED` 0--5 ms nuisance and is reported as a separate
orientation-sensitivity envelope, never silently converted to independent
Gaussian noise.

Every node/boot has its own yaw gauge.  Gravity cannot contract that gauge.
The outputs have no body mapping, anatomical role, UWB input, Q1/T4 input, or
historical pose/calibration/mapping dependency.  Phase 1 orientation may be
used by Phase 2 only as an initializer or diagnostic; it is not an independent
measurement when the same raw IMU is consumed downstream.

