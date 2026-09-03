# Pure-IMU baseline

This package is the compact, shared Capture 1/2/3 engineering baseline. It decodes only IMU payloads, reconstructs every sample on B306 TIMER2, runs six-axis VQF independently for each node/capture, performs one fixed neutral-pose mounting calibration per donning epoch, resamples with SLERP, computes parent-child quaternions, and applies fixed-length forward kinematics.

Run tests first:

```bash
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part
PYTHONPATH=. pytest -q pure_imu_baseline/tests
```

Then run all three captures:

```bash
PYTHONPATH=/tmp/biospur_vqf_runtime:. python3 -m pure_imu_baseline.cli run \
  --output /tmp/biospur_pure_imu_baseline_c123_<UTC>
```
