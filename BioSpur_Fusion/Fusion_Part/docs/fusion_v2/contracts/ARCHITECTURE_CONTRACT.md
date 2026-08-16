# Phase 0-R2 architecture contract

Governance only: no estimator, mapping, pose or UWB factor is implemented. The rejected execution `dae448f2ee96f069bcc669b310b1c0acad421ccc` → `6babf4f3b3757eee29dd5bba1fd6592c34c4394a` is preserved as `REJECTED_PHASE0_QUALIFICATION_EXECUTION`; none of its PASS claims are inherited.

Future preferred state: root SE(3), position/velocity, relative articulated SO(3) states/rates, ten gyro and accelerometer biases with random walks, finite-covariance closure, contact/mode state, conditional marginals and a gauge register. Future calibration jointly estimates latent trajectory, mapping hypotheses, extrinsics, lever arms, geometry, joints and uncertainty from raw IMU, action semantics, calibration-only fixed-anchor UWB and independent priors. No Phase 0 factor is active.
