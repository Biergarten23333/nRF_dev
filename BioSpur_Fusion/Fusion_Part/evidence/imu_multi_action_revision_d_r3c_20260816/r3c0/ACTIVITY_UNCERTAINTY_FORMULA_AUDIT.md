# R3C-0 activity uncertainty formula audit

The R3B implementation at `Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3b_topology.py` lines 58–80 computes the local relative increment correctly, but lines 73–78 use absolute Q2 pose covariance as adjacent-frame increment noise. `lowest_activity_plateau` lines 83–102 then takes the median of that value as the baseline scale.

For each node, Q2 starts with tilt variance `(2 deg)^2` and unobserved yaw variance `(180 deg)^2`. R3B forms `P_rel(t)=P_parent(t)+P_child(t)`, then `P_rel(t)+P_rel(t-dt)`, assumes all four pose errors are independent, takes `sqrt(trace/3)`, and divides by the common-grid `dt≈0.02 s`. The Q2 cache does not preserve `Cov(theta_t, theta_t-dt)`, so the strong temporal correlation and common global-yaw gauge cannot be cancelled. The resulting median is approximately 181.4 rad/s and dominates the empirical quiet scale.

No covariance was mistaken directly for a standard deviation, no radian/degree conversion occurs in this path, and `dt` is divided once. The scientific errors are `ABSOLUTE_ATTITUDE_COVARIANCE_USED_AS_INCREMENT_NOISE`, `TEMPORAL_CROSS_COVARIANCE_OMITTED`, and `GLOBAL_YAW_GAUGE_INCLUDED_IN_LOCAL_ACTIVITY`. Parent/child cross-covariance is also unavailable, so independence cannot be justified.

The replacement candidate is an empirical same-signal quiet-plateau scale with a predeclared process floor. With Q2 gyro noise `0.003 rad/s/sqrt(Hz)`, two independent sensor rate increments at 50 Hz give `sqrt(2)*0.003/sqrt(0.02) = 0.030000000 rad/s`; the preserved pre-data floor is 0.035 rad/s, so the candidate floor is 0.035000000 rad/s. Absolute Q2 covariance remains available only for validity/confidence reporting.

Historical reproducibility classification: `METADATA_TARGET_MISATTRIBUTION`. Exact replay gives `shoulder_L=0.7979315831` and first raises at `elbow_R` with `0.6199677939`. Original R3 artifacts remain immutable.
