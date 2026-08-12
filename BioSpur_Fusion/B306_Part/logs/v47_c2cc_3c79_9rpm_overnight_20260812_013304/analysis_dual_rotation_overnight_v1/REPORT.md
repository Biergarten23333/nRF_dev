# BSFC2CC + BSF3C79 overnight rotation analysis

Primary verdict: `DUAL_NODE_9RPM_OVERNIGHT_FAIL`

The formal host segment lasted 11.549 h. Both nodes supplied supported Fusion IMU/UWB rotation evidence for 7.284 h before BSF3C79's first evidence-backed degradation, so the six-hour exposure target was met. BSF3C79 first disconnected at 7.284 h; BSFC2CC first degraded at 7.651 h. The later reconnect/reset tail is battery-depletion evidence and is excluded from nominal rotation metrics.

The capture and Listener evidence closed without host queue drops or reader failure; raw byte accounting is exact. The run is not a protocol-complete PASS: motion began before T0, mounting/safety/overnight tokens were absent, and battery depletion made final stationary recovery impossible.

Frozen Q1 propagation encountered a covariance numerical failure during the supported interval. The hard IMU motion veto did not falsely relock. Exact failure times and covariance values are in `NUMERICAL_INTEGRITY.json` and `Q1_ATTITUDE_STABILITY.csv`.

T4 orbit results use the unchanged current-room V4-io geometry. The apparent radius ordering is BSF3C79 > BSFC2CC with ratio 1.3328917255816475; this is an inference, not an operator-confirmed long/short assignment. Per-hour gyro agreement and T4 apparent angular rates are self-consistency diagnostics only.

All motor actions were manual. Software never controlled or claimed to control the motor. No OTA, upload, pending, PREPARE/COMMIT, flash, reboot, J-Link/SWD/RTT, AutoPos or configuration mutation occurred. Offline analysis did not access hardware, and all source evidence hashes matched before and after.
