# Fusion V2 test results

All tests were run offline on 2026-08-14. No serial, BLE, Listener hardware,
J-Link, SWD or RTT interface was opened.

## Focused, synthetic, integration and relevant regression suite

```text
PYTHONPATH=Fusion_Part/src:B306_Part/tools python3 -m pytest -q \
  Fusion_Part/tests \
  B306_Part/tools/tests/test_v47_q1_eskf.py \
  B306_Part/tools/tests/test_v47_q1_covariance_repair.py \
  B306_Part/tools/tests/test_constrained_body_analysis.py \
  B306_Part/tools/tests/test_analyze_superframe_alignment.py \
  B306_Part/tools/tests/test_body_calibration_capture.py \
  B306_Part/tools/tests/test_body_calibration_v1.py \
  B306_Part/tools/tests/test_v47_real_sensor_analysis.py

94 passed in 8.74 s
```

This covers typed-event invariants, whole-epoch clock recovery, explicit timer
reset boundaries, arrival-jitter non-authority, canonical T4 binding ownership,
rejected-update byte identity, 0.5/1/2 m flyers, proper quaternions and PD
covariance, bounded interpolation, immutable articulated geometry, dropout,
shared-body correction propagation, unilateral/isolated motion, deterministic
joint replay, arbitrary mount/frame recovery, degenerate frame failure,
current-capture Gate 0/boot segmentation, exact accounting and fail-closed
held-out behavior.

Total: **94 passed, 0 failed**.

## Deterministic replay

Two full calibration-only derivations used the same immutable typed ledger.
`DETERMINISTIC_REPLAY.json` reports `pass=true`; clock models/residuals, all
90,017 T4 input verdicts, Q1 timeline content, frame/body/freeze status and
numerical results have identical semantic hashes. The 24 T4 solver failures
are present both in `UWB_FRONTEND_AUDIT.csv` and
`MEASUREMENT_REJECTION_LEDGER.csv`.

Raw and layout SHA-256 were reverified after replay. No MP4 or GIF was created.
