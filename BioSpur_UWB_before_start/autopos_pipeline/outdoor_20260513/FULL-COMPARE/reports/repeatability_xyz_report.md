# AutoPos Repeatability Report: XYZ-Decomposed RMS

This report follows the concept-report style: static positioning quality is decomposed into `X_std`, `Y_std`, and `Z_std`, instead of only reporting a single 3D number. Because no OptiTrack ground truth is available, these metrics are **repeatability / reproducibility** metrics, not absolute positioning error.

## Metric Definitions

- `X_std`, `Y_std`, `Z_std`: standard deviation of solved fixed-tag positions over one static capture.
- `3D repeatability RMS`: `sqrt(X_std^2 + Y_std^2 + Z_std^2)`.
- `Inter-anchor RMS`: residual RMS between solved anchor-anchor distances and fused sweep distances.
- `Roto circle RMS`: dynamic repeatability, measured as RMS residual to a fitted 3D circle.
- `p95`: 95th percentile across static capture-level `3D repeatability RMS` values.
- `Z/(XY)` ratio: `Z_median / sqrt(X_median^2 + Y_median^2)`, a quick indicator of whether vertical repeatability is the weak direction.

## Main XYZ Summary

| Dataset | Version | Inter RMS | X med | Y med | Z med | 3D med | 3D best | 3D p75 | 3D p95 | 3D worst | Roto med | Z/(XY) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 20260513_clean | v1 | 64.23 | 22.89 | 14.32 | 35.09 | 44.61 | 31.75 | 53.74 | 66.64 | 72.32 | 115.45 | 1.30 |
| 20260513_clean | v2 | 40.43 | 25.49 | 15.82 | 38.19 | 48.80 | 33.72 | 57.59 | 73.03 | 94.07 | 103.49 | 1.27 |
| 20260513_clean | v3lite | 40.82 | 25.19 | 15.82 | 38.11 | 48.83 | 33.70 | 57.72 | 72.60 | 93.77 | 103.43 | 1.28 |
| 20260513_clean | v3full | 66.42 | 27.39 | 18.15 | 41.23 | 55.87 | 40.05 | 69.73 | 92.82 | 137.16 | 112.87 | 1.25 |
| 20260513_clean | v4 | 44.34 | 25.62 | 15.77 | 37.96 | 48.98 | 34.27 | 59.74 | 92.91 | 104.28 | 106.88 | 1.26 |
|  |  |  |  |  |  |  |  |  |  |  |  |  |
| 20260504_bad_DH | v1 | 66.10 | 28.92 | 40.36 | 62.07 | 78.20 | 53.72 | 86.66 | 125.14 | 157.82 | 151.64 | 1.25 |
| 20260504_bad_DH | v2 | 42.39 | 18.62 | 27.92 | 39.87 | 54.91 | 36.84 | 64.75 | 167.10 | 291.03 | 139.63 | 1.19 |
| 20260504_bad_DH | v3lite | 42.42 | 18.62 | 27.93 | 39.78 | 54.94 | 36.81 | 64.87 | 168.22 | 289.41 | 139.51 | 1.19 |
| 20260504_bad_DH | v3full | 74.89 | 18.07 | 29.42 | 41.56 | 58.63 | 36.51 | 72.88 | 110.46 | 142.25 | 136.17 | 1.20 |
| 20260504_bad_DH | v4 | 48.03 | 18.41 | 28.20 | 39.46 | 55.34 | 36.02 | 77.49 | 159.20 | 182.83 | 139.15 | 1.17 |
|  |  |  |  |  |  |  |  |  |  |  |  |  |

## Key Interpretation

1. The vertical axis is consistently the weak direction. In the 20260513 clean run, V4 median `Z_std` is 37.96 mm, while `X_std` and `Y_std` are 25.62 mm and 15.77 mm. The `Z/(XY)` ratio is 1.26, meaning Z contributes more than the horizontal components combined.
2. The old V1 baseline is much more vulnerable to bad sweep data. On the 20260504 bad-D/H dataset, V1 static 3D median repeatability is 78.2 mm, while V2/V3/V4 are around 55 mm.
3. On the cleaner 20260513 data, all modern methods cluster around 49 mm static median repeatability. This suggests the current field floor is no longer dominated by D/H corruption, but by geometry, vertical observability, and tag/anchor range noise.
4. The p95 column should be used to discuss tail robustness. For example, 20260513 V4 has median 48.98 mm but p95 94.54 mm, indicating a small number of difficult static poses dominate the tail.

## V4 Worst Static Captures

### 20260513_clean

| ID | X std | Y std | Z std | 3D std |
|---|---:|---:|---:|---:|
| ID08 | 50.13 | 29.20 | 86.65 | 104.28 |
| ID09 | 51.17 | 21.16 | 76.46 | 94.41 |
| ID07 | 30.50 | 18.81 | 70.86 | 79.41 |
| ID18 | 29.61 | 13.98 | 64.62 | 72.44 |
| ID17 | 25.70 | 19.96 | 56.52 | 65.22 |
| ID19 | 34.45 | 16.77 | 48.86 | 62.09 |
| ID20 | 24.48 | 14.49 | 49.83 | 57.38 |
| ID05 | 28.10 | 15.48 | 43.29 | 53.88 |

### 20260504_bad_DH

| ID | X std | Y std | Z std | 3D std |
|---|---:|---:|---:|---:|
| ID05 | 55.73 | 40.34 | 169.39 | 182.83 |
| ID23 | 26.92 | 44.79 | 173.22 | 180.93 |
| ID04 | 36.99 | 53.20 | 87.00 | 108.48 |
| ID14 | 18.80 | 41.70 | 83.32 | 95.05 |
| ID13 | 41.36 | 40.62 | 65.08 | 87.15 |
| ID01 | 20.71 | 32.12 | 68.90 | 78.78 |
| ID10 | 30.94 | 34.59 | 63.10 | 78.33 |
| ID11 | 20.55 | 18.62 | 71.46 | 76.65 |

## Suggested Report Wording

```text
Since no external optical ground truth is available, we evaluate AutoPos using repeatability and internal consistency. For static tags, we report per-axis standard deviation (X/Y/Z) and the 3D repeatability RMS, defined as the Euclidean norm of the three standard deviations. This decomposition shows whether the vertical direction is the limiting factor. Dynamic repeatability is evaluated using rotating-tag circle residuals, and calibration self-consistency is evaluated using inter-anchor residual RMS.
```

## Output Files

- `autopos_pipeline/outdoor_20260513/FULL-COMPARE/tables/repeatability_xyz_summary.csv`
- `autopos_pipeline/outdoor_20260513/FULL-COMPARE/reports/repeatability_xyz_report.md`

## Bad-D/H Sensitivity

Important caveat: static repeatability is not the same as absolute accuracy. If a bad anchor biases the solved layout consistently, a fixed tag can still produce a tight point cloud, so the static median `3D_std` may not increase dramatically. Bad-anchor effects are more visible in inter-anchor self-consistency, dynamic roto residuals, p95/worst tails, and old-baseline degradation.

| Version | Δ static median | Δ static p95 | Δ roto median | Δ inter RMS |
|---|---:|---:|---:|---:|
| v1 | 33.59 | 58.50 | 36.19 | 1.87 |
| v2 | 6.11 | 94.08 | 36.14 | 1.97 |
| v3lite | 6.11 | 95.61 | 36.08 | 1.59 |
| v3full | 2.76 | 17.64 | 23.30 | 8.47 |
| v4 | 6.36 | 66.29 | 32.27 | 3.70 |

Here `Δ = 20260504_bad_DH - 20260513_clean`. The modern V4 static median only worsens by about 6.36 mm, but the p95 worsens by 66.29 mm and roto median worsens by 32.27 mm. This means the bad-D/H effect is mostly a tail/dynamic/geometry-consistency problem, not a simple median-repeatability problem.
