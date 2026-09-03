# Pure-IMU Stage 2

This isolated extension consumes the frozen Stage 1 `CAPTURE#_REPLAY_DATA.npz` files without changing them. It emits offline browser viewers backed by gzip-compressed typed arrays, exact byte-roundtrip parity evidence, direct 3D geometry and fixed-camera projection diagnostics, common-body-yaw and relative-orientation decomposition, tilt and gap/reset diagnostics, and a frozen next-stage correction contract.

Run from `Fusion_Part`:

```bash
PYTHONPATH=. python3 -m pure_imu_baseline.stage2.cli run \
  --stage1 /tmp/biospur_pure_imu_baseline_c123_20260823T091031Z \
  --output /tmp/biospur_pure_imu_interactive_stage2_<UTC>
```

Open `C123_INTERACTIVE_3D_VIEWER_INDEX.html` directly. `launch_viewer.sh` provides a localhost fallback for browsers configured to block local scripts.
