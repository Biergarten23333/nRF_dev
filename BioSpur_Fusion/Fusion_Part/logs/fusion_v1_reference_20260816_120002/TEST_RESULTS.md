# Test results

Command:

```text
cd /mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part
python3 -m pytest fusion_v1/tests -q
```

Result: **9 passed in 0.37 s**.

Covered: CRC known vector, COBS decoding, host header, incomplete EOF tail,
IMU `base_us + delta_us`, articulated FK length invariance, asynchronous SE(3)
interpolation, Cauchy downweighting, and health hysteresis.

Not covered and not claimed: common-clock fit, raw sensor scale/axes, real-data
FK calibration, optimizer convergence, or end-to-end validation.

