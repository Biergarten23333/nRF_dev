# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_24/recv_tdma_capture/recv_tdma_20260422_075341`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_24/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 1355 | 43.54 | 230.38 RMS |
| BS2DCE | roto | 2 | 64.29 | 238.96 radius |
| BSDC91 | roto | 1 | 26.65 | 0.00 radius |

## BSF66F

- mode: `static`
- position_samples: `1355`
- position_mean_mm: x=`2784.89` y=`1978.41` z=`1092.31`
- solve_residual_mean_rms_mm: `43.54`
- static_rms_mm: `230.38`
- static_p95_3d_mm: `261.95`

## BS2DCE

- mode: `roto`
- position_samples: `2`
- position_mean_mm: x=`1190.47` y=`2385.86` z=`794.89`
- solve_residual_mean_rms_mm: `64.29`
- radius_mm: `238.96`
- circle_center_xy_mm: x=`1190.47` y=`2385.86`
- radial_rms_mm: `0.00`
- z_std_mm: `257.09`

## BSDC91

- mode: `roto`
- position_samples: `1`
- position_mean_mm: x=`1681.38` y=`1468.09` z=`1515.01`
- solve_residual_mean_rms_mm: `26.65`
- radius_mm: `0.00`
- circle_center_xy_mm: x=`1681.38` y=`1468.09`
- radial_rms_mm: `0.00`
- z_std_mm: `0.00`

