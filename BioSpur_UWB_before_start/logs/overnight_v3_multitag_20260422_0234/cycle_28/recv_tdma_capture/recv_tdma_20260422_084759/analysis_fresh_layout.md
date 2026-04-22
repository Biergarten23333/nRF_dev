# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_28/recv_tdma_capture/recv_tdma_20260422_084759`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_28/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3053 | 42.95 | 578.92 RMS |
| BS2DCE | roto | 40 | 41.70 | 471.68 radius |
| BSDC91 | roto | 27 | 55.80 | 525.27 radius |

## BSF66F

- mode: `static`
- position_samples: `3053`
- position_mean_mm: x=`2724.28` y=`2028.02` z=`946.76`
- solve_residual_mean_rms_mm: `42.95`
- static_rms_mm: `578.92`
- static_p95_3d_mm: `1904.93`

## BS2DCE

- mode: `roto`
- position_samples: `40`
- position_mean_mm: x=`1863.90` y=`1935.96` z=`998.68`
- solve_residual_mean_rms_mm: `41.70`
- radius_mm: `471.68`
- circle_center_xy_mm: x=`1839.70` y=`2041.11`
- radial_rms_mm: `107.77`
- z_std_mm: `672.18`

## BSDC91

- mode: `roto`
- position_samples: `27`
- position_mean_mm: x=`1950.08` y=`1788.56` z=`868.91`
- solve_residual_mean_rms_mm: `55.80`
- radius_mm: `525.27`
- circle_center_xy_mm: x=`1910.17` y=`1900.05`
- radial_rms_mm: `118.58`
- z_std_mm: `427.48`

