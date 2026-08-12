# BSFC2CC black-box arbitrary-pose intrinsic calibration

Primary verdict: **C2CC_DEVICE_CALIBRATION_FAIL**.

The capture itself is complete: 18 accepted calibration poses, four untouched held-out poses, one serial open and one raw timeline. All physical placements were made manually by the operator. Calibration parameters were frozen before validation and changed zero times afterward. Startup live catch-up remained `STARTED_DEGRADED` after 180 seconds as allowed by the protocol; the raw stream continued without a second timeline.

## Result

The preregistered least-complex selection chose `DIAGONAL_SCALE`. Held-in leave-one-pose-out RMSE was 0.001876705 g. Held-out gravity-norm RMSE improved from 0.006981630 g to 0.002507236 g: an absolute improvement of 0.004474394 g and a relative improvement of 64.088%. Coverage passed (minimum direction-covariance eigenvalue 0.273648; design condition 1.283426).

The strict result is nevertheless FAIL because held-out pose 4 contained a sample with 0.109794103 g absolute corrected norm residual, exceeding the frozen 0.060 g catastrophic-residual gate. This sample is retained at sequence 47734 / node timestamp 3845600899 us. It is not removed as an outlier and validation data are not moved into training. Its gyro remained quiet while one accelerometer channel dipped for one sample, so it is consistent with a single-sample accelerometer anomaly rather than operator handling; the preregistered gate still fails.

The complete timeline has one IMU sequence discontinuity in the startup stale prefix before any accepted pose; every accepted calibration and held-out window has zero sequence gaps. The UWB/tag side emitted 260 reset-diagnostic records during the run. These are retained as UWB diagnostics, are not B306 disconnect/reconnect events, and were not used as an IMU calibration acceptance signal.

## Boundaries

This establishes neither physical PCB X/Y/Z names nor yaw, V4 up, sensor-to-V4 rotation, lever arm, dynamic accuracy, cohort-wide calibration, or human-body mounting calibration. The numeric profile is host-side, failed validation, and is not deployable. The cohort candidate contains only source-proven raw ordering and units and remains ineligible pending a passing device validation.

No OTA, upload, pending/PREPARE/COMMIT, reboot, flash, J-Link/SWD/RTT, AutoPos, configuration, power-cycle, charging, or calibration write to firmware occurred.
