# BSFC2CC two-mount black-box frame-binding experiment

Primary verdict: **BLOCKED_INSUFFICIENT_EXCITATION**.

The one-open formal capture itself closed cleanly: one serial open, one raw file, byte accounting closed, zero queue drops, and no connection/reset event. Mount B's raw gravity vector differed from Mount A by 92.150 degrees, so the remount was real.

Both independently attempted vertical calibration blocks failed the frozen limited-rotation gate after their single explicit retry (A gyro P95 41.594 dps; B 43.253 dps; frozen maximum 12.000 dps). The interaction implementation incorrectly advanced after a failed retry. This is retained as a protocol defect; it does not authorize fitting with invalid calibration data.

Consequently no proper sensor-to-V4 rotation was fitted, no held-out direction score was computed, and no spatial Q1/T4 replay was claimed. Attitude-only repaired-Q1 diagnostics remained finite and Cholesky-valid. Empty spatial trajectory CSVs are deliberate fail-closed artifacts, not missing output. Raw T4 trajectories and every operator bracket remain available for diagnosis.

This result is not ready for a ten-node arbitrary-wear T-pose/body-calibration experiment. The next attempt needs a carrier/guide that suppresses rotation during vertical translation, and the state machine must stop after an unsuccessful retry instead of advancing. No threshold may be relaxed using this held-out capture.
