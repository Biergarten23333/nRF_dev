# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_10/recv_tdma_capture/recv_tdma_20260422_043200`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_10/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 2789 | 44.43 | 351.44 RMS |
| BS2DCE | roto | 1537 | 42.94 | 887.66 radius |
| BSDC91 | roto | 794 | 65.04 | 575.60 radius |

## BSF66F

- mode: `static`
- position_samples: `2789`
- position_mean_mm: x=`2752.71` y=`2042.44` z=`1027.29`
- solve_residual_mean_rms_mm: `44.43`
- static_rms_mm: `351.44`
- static_p95_3d_mm: `232.25`

## BS2DCE

- mode: `roto`
- position_samples: `1537`
- position_mean_mm: x=`1887.46` y=`1889.62` z=`1149.86`
- solve_residual_mean_rms_mm: `42.94`
- radius_mm: `887.66`
- circle_center_xy_mm: x=`1317.18` y=`2315.59`
- radial_rms_mm: `222.96`
- z_std_mm: `238.51`

## BSDC91

- mode: `roto`
- position_samples: `794`
- position_mean_mm: x=`2089.89` y=`1980.24` z=`1222.93`
- solve_residual_mean_rms_mm: `65.04`
- radius_mm: `575.60`
- circle_center_xy_mm: x=`2133.74` y=`2044.63`
- radial_rms_mm: `207.35`
- z_std_mm: `457.63`

