# BSFC2CC calibration revalidation v2

Primary verdict: **C2CC_DEVICE_CALIBRATION_REVALIDATION_CONDITIONAL**.

The historical primary verdict remains **C2CC_DEVICE_CALIBRATION_FAIL** and was not rewritten. The `DIAGONAL_SCALE` parameters were loaded by SHA-256 and were never refit. Gate A systematic calibration: `PASS`. Gate B sensor transients: `CONDITIONAL`. Capture integrity: `PASS`. Runtime Q1 causal rejection: `PASS`.

All six physical poses were created manually. Calibration remained host-side. No OTA, upload, reboot, flash, J-Link/SWD/RTT, AutoPos, configuration, or power-cycle action occurred during the formal run. Numeric parameters are not promoted to any other board; BSF31CC remains excluded.

The historical forensic audit is retained in the clearly linked sibling directory [`../v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit/`](../v47_c2cc_calibration_revalidation_v2_20260812_214846/historical_transient_audit/). It found two separated one-sample events in the old accepted stationary population and therefore uses disposition `REPEATED_SENSOR_ANOMALY`, without asserting a hardware defect or changing the old FAIL.

Two zero-pose pre-capture attempts and one passive observation are documented in `PRECAPTURE_ATTEMPTS.json`; none is merged into this formal raw timeline. The operator charged the board between those aborted attempts and the accepted formal run. Charging had ended and the charger was removed before this run's collector opened; no charging occurred during this formal run. Codex performed no charging or other hardware mutation.
