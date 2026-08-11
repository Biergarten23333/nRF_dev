# Dataset capability matrix

| Question | Disposition |
|---|---|
| Static node-specific IMU/UWB noise | Supported, calibration plus held-out static window |
| S1 failure mechanism | Timestamp-level causal closure |
| S2 lock/conflict mechanics | Internal consistency, development dataset only |
| S2 motion generalization | Requires new held-out trajectory |
| Solved-position versus raw-range plumbing | Supported internally with fixed geometry/delay |
| Absolute localization accuracy | Unavailable; no ground truth |
| Full vector inertial propagation | Blocked by sensor-to-V4 binding |
| Node-to-body assignment / IK/FK | NOT_APPLICABLE_TO_CURRENT_CAPTURE |
