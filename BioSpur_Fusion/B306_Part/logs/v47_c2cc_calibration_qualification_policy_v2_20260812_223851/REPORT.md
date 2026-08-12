# BSFC2CC calibration qualification policy V2

Policy: `C2CC_CALIBRATION_QUALIFICATION_POLICY_V2`

Primary V2 verdict: **C2CC_DEVICE_CALIBRATION_VALIDATED**

Device disposition: `FROZEN_CANDIDATE_PENDING_REVALIDATION` -> `FROZEN_CALIBRATION_VALIDATED`

## Gate results

- `SYSTEMATIC_CALIBRATION_GATE`: `PASS`. The frozen `DIAGONAL_SCALE` profile improved RMSE from 0.007403 g to 0.001444 g; corrected P95/P99 are 0.002120/0.002672 g. No parameters were refit.
- `RAW_SENSOR_TRANSIENT_DIAGNOSTIC`: `OBSERVED_NON_BLOCKING`. The formal population retains 2 events in 38530 accepted samples, rate 5.190760446405398e-05, exact 95% CI [6.2863135328378424e-06, 0.00018749540224892103]. The unchanged V1 confidence-bound rule therefore reproduces `C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL`.
- `CAPTURE_INTEGRITY`: `PASS`. Sequence, timestamp, queue, and reconnect checks remain closed.
- `RUNTIME_OUTLIER_CONTAINMENT`: `PASS`. Both extreme events were evaluated causally and rejected with NIS 924.85 and 911.45; covariance stayed positive and the next nominal sample was accepted.

## Engineering interpretation

Rare isolated single-sample accelerometer outliers were observed in both historical and revalidation datasets. The events remain fully retained in raw evidence. In the formal revalidation run, all observed events were isolated, lacked corroborating gyro/handling or transport-integrity evidence, and were causally rejected by Q1 without downstream quaternion, covariance, or motion-state corruption. Under qualification policy V2, isolated raw sensor transients that are successfully contained are recorded as non-blocking sensor diagnostics rather than calibration failures.

`REPEATED_SENSOR_ANOMALY` does not imply a proven hardware defect.

The revised policy does not retroactively rewrite any historical verdict. The historical primary verdict remains `C2CC_DEVICE_CALIBRATION_FAIL`; the existing formal V1 report remains `C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL`. Historical and formal populations were not merged. The two formal events remain at pose 5 / seq 29761 and pose 6 / seq 45999.

This PASS is limited to the observed isolated single-sample anomaly class. It does not establish safety for arbitrary multi-sample bursts. Raw outlier existence is not runtime-containment failure; failure requires escape from the causal gate or meaningful downstream corruption.

## Provenance and scope

There were two zero-pose failed pre-capture attempts. The operator charged the board after those aborted attempts; charging ended and the charger was removed before the accepted formal collector opened. No charging occurred during the formal run, and Codex performed no charging or hardware mutation. These attempts are not merged into the formal population.

This was an offline derivation only: no capture, serial/BLE/J-Link/SWD/RTT access, OTA, upload, reboot, configuration, power, charging, or physical action occurred. BSF31CC and all other boards are untouched. No BSFC2CC calibration value is transferred to another board.

`FROZEN_CALIBRATION_VALIDATED` is a calibration disposition, not a deployable-state claim. Independent deployment/integration readiness outside this offline calibration policy remains unevaluated and is the remaining blocker before declaring BSFC2CC deployable.
