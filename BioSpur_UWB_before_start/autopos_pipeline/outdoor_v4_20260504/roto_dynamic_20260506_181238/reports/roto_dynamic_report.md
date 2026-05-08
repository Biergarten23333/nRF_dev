# Roto Tag Dynamic Positioning Error: Circle-Fit Residual Analysis

Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/roto_dynamic_20260506_181238`

Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/solves/anchor_layout_interonly_linear_outdoor_500set_20260504.json`. Anchor delays loaded from layout, defaulting to 0 where absent.

## Table 1: Per-Capture Dynamic Error

| Capture | Tag | N frames | Radius(mm) | Radial sigma | Z-plane sigma | 3D sigma | 3D RMS |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ID28 | BS2DCE | 1200 | 493.1 | 126.9 | 158.7 | 111.0 | 203.8 |
| ID28 | BSDC91 | 1203 | 621.8 | 92.8 | 144.5 | 102.3 | 171.8 |
| ID29 | BS2DCE | 1200 | 502.1 | 95.4 | 185.3 | 127.9 | 208.5 |
| ID29 | BSDC91 | 1201 | 597.4 | 69.5 | 162.6 | 123.2 | 176.8 |
| ID30 | BS2DCE | 1200 | 450.4 | 68.0 | 156.9 | 100.7 | 171.0 |
| ID30 | BSDC91 | 1201 | 600.2 | 109.3 | 204.6 | 153.0 | 232.1 |
| ID31 | BS2DCE | 1201 | 496.5 | 104.6 | 156.7 | 106.9 | 188.6 |
| ID31 | BSDC91 | 1201 | 601.8 | 82.1 | 109.4 | 68.2 | 136.8 |
| MEAN |  | 1201 | 545.4 | 93.6 | 159.8 | 111.7 | 186.2 |


## Table 2: Static vs Dynamic Comparison

| Metric | Static (ID02) | Dynamic (roto mean) | Degradation |
| --- | --- | --- | --- |
| X/Radial std | 22.5 | 93.6 | 4.15x |
| Z std | 34.5 | 159.8 | 4.64x |
| 3D std | 41.2 | 111.7 | 2.71x |


## Table 3: Roto Arm Geometry Verification

| Capture | Inner R (mm) | Outer R (mm) | Delta R (mm) | Expected Delta R | Error |
| --- | --- | --- | --- | --- | --- |
| ID28 | 493.1 | 621.8 | 128.7 | 120.0 | 8.7 |
| ID29 | 502.1 | 597.4 | 95.3 | 120.0 | -24.7 |
| ID30 | 450.4 | 600.2 | 149.8 | 120.0 | 29.8 |
| ID31 | 496.5 | 601.8 | 105.3 | 120.0 | -14.7 |


## Table 4: Circle Fit Quality

| Capture | Tag | Plane tilt (deg) | Circle fit R2 | Outlier % (>3sigma) |
| --- | --- | --- | --- | --- |
| ID28 | BS2DCE | 14.3 | -0.0171 | 9.0 |
| ID28 | BSDC91 | 9.9 | -0.0056 | 4.4 |
| ID29 | BS2DCE | 17.4 | -0.0092 | 6.1 |
| ID29 | BSDC91 | 13.7 | -0.0034 | 3.2 |
| ID30 | BS2DCE | 7.9 | -0.0058 | 7.5 |
| ID30 | BSDC91 | 4.5 | -0.0084 | 5.1 |
| ID31 | BS2DCE | 28.2 | -0.0113 | 5.6 |
| ID31 | BSDC91 | 20.4 | -0.0047 | 11.9 |


## Table 5: Per-Capture Fitted Circle Parameters

| Capture | Tag | Center X | Center Y | Center Z | Normal (nx,ny,nz) |
| --- | --- | --- | --- | --- | --- |
| ID28 | BS2DCE | 2252.0 | 1574.8 | 1054.2 | (-0.225,-0.101,0.969) |
| ID28 | BSDC91 | 2181.1 | 1553.6 | 932.3 | (-0.117,-0.126,0.985) |
| ID29 | BS2DCE | 2255.9 | 1564.5 | 986.5 | (0.156,0.255,0.954) |
| ID29 | BSDC91 | 2241.5 | 1538.9 | 911.0 | (0.229,0.058,0.972) |
| ID30 | BS2DCE | 2219.0 | 1576.5 | 1021.9 | (0.114,0.075,0.991) |
| ID30 | BSDC91 | 2228.6 | 1613.6 | 957.5 | (0.072,-0.031,0.997) |
| ID31 | BS2DCE | 2274.2 | 1579.0 | 1135.2 | (-0.428,-0.201,0.881) |
| ID31 | BSDC91 | 2275.8 | 1530.4 | 991.1 | (-0.346,-0.042,0.937) |


## Table 6: Literature Comparison

| System | Dynamic 3D error | Sensors | Source |
| --- | --- | --- | --- |
| AutoPos (this work) | 111.7 mm std / 186.2 mm RMS | Pure UWB | Outdoor roto circle fit |
| Pure UWB literature range | 100-300 mm | Pure UWB | Survey-level range |
| UWB+IMU fusion range | 50-150 mm | UWB+IMU | Survey-level range |
| UWB+VIO fusion range | 30-70 mm | UWB+VIO | Survey-level range |
| DW1000 datasheet | +/-300 mm | Pure UWB | Qorvo typical ranging accuracy |


## Figures

- `figures/roto_trajectory_3d.png`
- `figures/roto_residual_histogram.png`
- `figures/static_vs_dynamic_bar.png`
- `figures/roto_residual_timeseries.png`
- `figures/radius_verification.png`


## Key Findings

1. Mean dynamic circle-fit error is **111.7 mm 3D sigma** and **186.2 mm 3D RMS** across 8 tag/capture fits.
2. Static ID02 3D std is **41.2 mm**, so dynamic circle-fit sigma is **2.71x** static by this metric.
3. Radial scatter averages **93.6 mm**, while plane-normal scatter averages **159.8 mm**. The dominant component is plane-normal/Z.
4. Fitted radius separation averages **119.8 mm** versus expected **120 mm**, error **-0.2 mm**.
5. Same-capture two-tag center/normal consistency: ID28 center_diff=142.6mm normal_diff=6.4deg; ID29 center_diff=81.0mm normal_diff=12.1deg; ID30 center_diff=74.9mm normal_diff=6.5deg; ID31 center_diff=152.1mm normal_diff=10.8deg.
6. Compared with broad pure-UWB dynamic literature ranges (100-300 mm), this setup is within the usual pure-UWB range, and approaches lower-end UWB+IMU numbers depending on whether sigma or RMS is used.
7. Sweep-vs-residual drift correlations: ID28 r=-0.03; ID29 r=-0.01; ID30 r=+0.02; ID31 r=+0.00. Values near zero indicate no strong monotonic time drift.
8. Circle-fit R2 and outlier percentage should be checked before treating a radius as physical; poor R2 means the solved trajectory is not well described by a single rigid circle.