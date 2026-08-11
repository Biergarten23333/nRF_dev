# BSFC2CC held-out stationary validation

Primary verdict: `C2CC_STATIONARY_CAPTURE_FAIL`

The formal run started at `2026-08-11T22:03:16.193+02:00` and stopped at `2026-08-11T22:13:16.193+02:00` after `600.0001271200017` seconds with `120357` decoded IMU samples and `5015` decoded UWB sweeps. The raw lossless capture gate is `FAIL` because the early common discontinuity contains `370` missing IMU samples and `16` missing UWB sweeps. CRC/decode/queue/serial errors, reconnects and reboots were zero, but those facts cannot override the pre-registered zero-gap gate.

The post-discontinuity suffix was replayed diagnostically without interpolation or parameter changes. Frozen S2P gate: `PASS`. Frozen S2R gate: `PASS`. Neither entered motion suspicion or conflict. Published RMS of zero, when observed, demonstrates immutable lock semantics only; it is not absolute positioning accuracy. B0/S1/S2 and candidate noise values are in `PER_MODE_METRICS.csv`.

Listener union cadence was `8.33333160879` Hz; receiver visibility is preserved in `LISTENER_SUMMARY.json`. No new capture data was used to tune a threshold, covariance, process noise, dwell, NIS gate, per-link variance, or conflict threshold.

This stationary experiment does not validate movement release, relock, vector inertial propagation, sensor-to-V4 rotation, dynamic or absolute trajectory accuracy, human assignment, or IK/FK.
