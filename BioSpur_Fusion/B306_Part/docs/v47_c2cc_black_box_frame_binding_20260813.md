# BSFC2CC black-box frame-binding result

Verdict: **BLOCKED_INSUFFICIENT_EXCITATION**.

The accepted capture is `B306_Part/logs/v47_c2cc_frame_binding_20260813_162341`.
It used one Fusion CDC open, one raw file, one decoder timeline, and a formal T0
after natural live catch-up. Raw SHA-256 is
`5e4d252a9d3e71d572ffd2463c5d0a020ac43bd0310dcc9ecdcf3abbf9383b86`.
Raw byte accounting closed at 6,310,164 bytes, with zero decoded/raw/log queue
drops, zero reader exceptions, zero action-window IMU/UWB sequence gaps, and no
connection, reboot, or reset event. The four-byte opening fragment and one-byte
closing fragment are stream-boundary fragments; there is no interior parse
error.

Mount B's stationary raw gravity direction differs from Mount A by 92.150
degrees, so the physical remount was measurably different. Both horizontal
calibration directions passed online checks for both mounts. The independently
repeated vertical blocks did not pass the frozen limited-rotation gate:

| Mount | vertical span | T4 solutions | gyro P95 | frozen maximum |
|---|---:|---:|---:|---:|
| A | 1.575 m | 205 | 41.594 deg/s | 12.000 deg/s |
| B | 1.363 m | 156 | 43.253 deg/s | 12.000 deg/s |

The capture state machine incorrectly advanced after the failed retry. The
implementation now raises `PROTOCOL_BLOCKED_INSUFFICIENT_EXCITATION` before
issuing any following instruction. The recorded later blocks remain valid
diagnostic evidence, but the frozen fit requires all three accepted calibration
translations. No post-capture threshold relaxation, transform fit, held-out
direction score, oracle calibration, or spatial Q1/T4 trajectory is claimed.

The repaired Q1 attitude-only diagnostics processed 76,134 Mount A and 73,740
Mount B IMU samples. Quaternion norm error remained at or below
`2.220446049250313e-16`, covariance remained Cholesky-valid, and there were no
filter resets or covariance clips. This is numerical diagnostic evidence only;
it does not substitute for the blocked sensor-to-V4 binding.

Two independent derivations produced byte-identical copies of all 12 core
JSON/CSV outputs. Focused frame-binding and capture tests pass 15/15. The full
host suite passes 365 tests with one unrelated stale assertion: the test expects
Master marker `dk-fusion-imu-relay-v31`, while current source selects v36 or the
CCC reproduction marker.

This result is not ready to advance to the ten-node arbitrary-wear T-pose/body
calibration. Repeat C2CC with a non-metal vertical guide or two-person carrier
handling that keeps angular rate below the already-frozen gate. Do not tune the
gate from this held-out dataset.
