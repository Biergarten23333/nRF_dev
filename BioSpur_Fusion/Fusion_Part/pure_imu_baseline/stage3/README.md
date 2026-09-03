# BioSpur pure-IMU Stage 3

`BIOPSUR_GRAPH_RELATIVE_HEADING_STABILIZER_V1` is a shared causal, rate-only
global-Z corrector over immutable Stage 1 `q_GB`. It uses raw-IMU stationarity
to estimate differential spatial yaw-rate drift, preserves the pelvis gauge,
and resets the affected graph/subtree at gaps. Kinematic joint constraints are
withheld until a functional-axis and pronation-safe 2-DoF model is qualified.

Run only after checking the disk gate:

```bash
PYTHONPATH=. python3 -m pure_imu_baseline.stage3.cli run --output /tmp/biospur_pure_imu_stage3_relative_heading_<UTC>
```
