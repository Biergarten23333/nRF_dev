# Phase 0 architecture contract

Scope is governance only. Runtime estimator/factor/UWB-factor counts are `NOT_IMPLEMENTED_IN_PHASE_0` and zero. Identity is the exact ten hardware IDs; anatomy is unknown. The preferred future Phase 3 state is root SE(3), position/velocity, relative articulated SO(3) states/rates, ten gyro biases, ten accelerometer biases, finite-covariance closure, contact/mode state, conditional local-Gaussian marginals and a gauge register.

Future calibration direction is raw IMU + scripted action semantics + noisy fixed-anchor UWB + independent priors → jointly estimated latent pose, mapping hypotheses, extrinsics, lever arms, geometry, joints and conditional uncertainty. Phase 1 orientation is initializer/diagnostic only if Phase 2 also consumes its raw IMU.

Future factors: variable-time gyro propagation, specific-force with tangential/centripetal lever-arm terms, robust low-dynamic gravity mode, both bias priors/random walks, finite-sigma articulated closure, soft ROM/dominant-axis priors, temporal dynamics, stillness/contact and soft evidence-gated ZUPT. `f_m = R_SW(a_sensor^W-g^W)+b_a+n_a`; `a_sensor^W=a_origin^W+dot(omega)^W×r^W+omega^W×(omega^W×r^W)`. No factor is active in Phase 0.

Rejected commit: `9480b3f4c620fe13aaae5dc127e8105393d6d392`; statuses `CURRENT_9480_ESTIMATOR_REJECTED` and `ESTIMATOR_IMPLEMENTED_BUT_SCIENTIFICALLY_INVALID`.
