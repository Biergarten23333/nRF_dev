# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_15/recv_tdma_capture/recv_tdma_20260422_054125`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_15/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 1199 | 46.67 | 577.04 RMS |
| BS2DCE | roto | 565 | 43.12 | 418.67 radius |
| BSDC91 | roto | 470 | 58.66 | 527.44 radius |

## BSF66F

- mode: `static`
- position_samples: `1199`
- position_mean_mm: x=`2756.75` y=`2026.15` z=`930.73`
- solve_residual_mean_rms_mm: `46.67`
- static_rms_mm: `577.04`
- static_p95_3d_mm: `1918.80`

## BS2DCE

- mode: `roto`
- position_samples: `565`
- position_mean_mm: x=`1915.86` y=`2014.39` z=`866.05`
- solve_residual_mean_rms_mm: `43.12`
- radius_mm: `418.67`
- circle_center_xy_mm: x=`1866.56` y=`2083.48`
- radial_rms_mm: `115.21`
- z_std_mm: `527.64`

## BSDC91

- mode: `roto`
- position_samples: `470`
- position_mean_mm: x=`2046.42` y=`1930.74` z=`1239.59`
- solve_residual_mean_rms_mm: `58.66`
- radius_mm: `527.44`
- circle_center_xy_mm: x=`2092.03` y=`1978.58`
- radial_rms_mm: `164.96`
- z_std_mm: `411.99`

