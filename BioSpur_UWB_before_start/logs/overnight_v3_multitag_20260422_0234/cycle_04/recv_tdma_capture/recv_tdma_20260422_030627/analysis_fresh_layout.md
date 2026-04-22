# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_04/recv_tdma_capture/recv_tdma_20260422_030627`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_04/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3018 | 33.79 | 111.52 RMS |
| BS2DCE | roto | 1013 | 47.15 | 468.65 radius |
| BSDC91 | - | 0 | - | error |

## BSF66F

- mode: `static`
- position_samples: `3018`
- position_mean_mm: x=`2800.09` y=`1983.21` z=`948.90`
- solve_residual_mean_rms_mm: `33.79`
- static_rms_mm: `111.52`
- static_p95_3d_mm: `206.26`

## BS2DCE

- mode: `roto`
- position_samples: `1013`
- position_mean_mm: x=`1938.64` y=`1897.73` z=`1184.63`
- solve_residual_mean_rms_mm: `47.15`
- radius_mm: `468.65`
- circle_center_xy_mm: x=`1915.15` y=`2068.09`
- radial_rms_mm: `120.12`
- z_std_mm: `370.62`

## BSDC91

- error: `no position samples`

