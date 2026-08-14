# Offline analysis tests

- Final independent full replay: PASS (`run_c`, `run_d`).
- Deterministic core artifacts: PASS, 36/36 byte-identical.
- Raw immutability: PASS before/after both runs.
- Focused existing body-calibration, capture, canonical geometry/T4, repaired-Q1 tests: 71 passed, 1 dependency deprecation warning.
- Test environment: system Python with the already-installed NCS toolchain pytest site-packages; no package was installed.

Command:

```text
PYTHONPATH=/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages python3 -m pytest -q B306_Part/tools/tests/test_body_calibration_capture.py B306_Part/tools/tests/test_body_calibration_v1.py B306_Part/tools/tests/test_current_room_autopos_positioning.py B306_Part/tools/tests/test_v47_q1_covariance_repair.py B306_Part/tools/tests/test_v47_q1_eskf.py
```
