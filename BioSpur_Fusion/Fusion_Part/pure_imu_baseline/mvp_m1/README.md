# BioSpur Pure-IMU MVP-M1

This package implements the raw-authoritative five-layer product path: frozen Stage 1 replay evidence, a separate float64 normalized view, an explicit common global-yaw display gauge, immutable fixed-geometry FK, and replay/viewer/export adapters.

Run from `Fusion_Part`:

```bash
PYTHONPATH=. python3 -m pure_imu_baseline.mvp_m1.cli run --output /tmp/biospur_pure_imu_mvp_m1_<UTC>
```

The run refuses to overwrite an existing output directory. It does not decode a capture, acquire hardware, read UWB numeric data, commit, or push.
