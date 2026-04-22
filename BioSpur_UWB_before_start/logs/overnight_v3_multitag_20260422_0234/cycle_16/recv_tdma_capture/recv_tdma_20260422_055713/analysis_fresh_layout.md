# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_16/recv_tdma_capture/recv_tdma_20260422_055713`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_16/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 717 | 44.82 | 161.92 RMS |
| BS2DCE | roto | 697 | 37.32 | 434.66 radius |
| BSDC91 | roto | 248 | 58.12 | 541.00 radius |

## BSF66F

- mode: `static`
- position_samples: `717`
- position_mean_mm: x=`2749.87` y=`2001.10` z=`1148.00`
- solve_residual_mean_rms_mm: `44.82`
- static_rms_mm: `161.92`
- static_p95_3d_mm: `218.83`

## BS2DCE

- mode: `roto`
- position_samples: `697`
- position_mean_mm: x=`1938.18` y=`2012.53` z=`854.67`
- solve_residual_mean_rms_mm: `37.32`
- radius_mm: `434.66`
- circle_center_xy_mm: x=`1929.41` y=`2009.24`
- radial_rms_mm: `169.60`
- z_std_mm: `411.91`

## BSDC91

- mode: `roto`
- position_samples: `248`
- position_mean_mm: x=`1991.45` y=`1917.13` z=`1174.35`
- solve_residual_mean_rms_mm: `58.12`
- radius_mm: `541.00`
- circle_center_xy_mm: x=`2070.67` y=`1924.69`
- radial_rms_mm: `166.40`
- z_std_mm: `432.58`

