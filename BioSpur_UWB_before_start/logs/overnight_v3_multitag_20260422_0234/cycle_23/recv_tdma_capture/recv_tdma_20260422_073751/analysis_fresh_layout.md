# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_23/recv_tdma_capture/recv_tdma_20260422_073751`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_23/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 774 | 47.37 | 829.65 RMS |
| BS2DCE | roto | 5 | 35.65 | 486.50 radius |
| BSDC91 | roto | 1 | 11.62 | 0.00 radius |

## BSF66F

- mode: `static`
- position_samples: `774`
- position_mean_mm: x=`3022.75` y=`2030.47` z=`1032.48`
- solve_residual_mean_rms_mm: `47.37`
- static_rms_mm: `829.65`
- static_p95_3d_mm: `2031.75`

## BS2DCE

- mode: `roto`
- position_samples: `5`
- position_mean_mm: x=`2033.71` y=`1898.71` z=`1008.66`
- solve_residual_mean_rms_mm: `35.65`
- radius_mm: `486.50`
- circle_center_xy_mm: x=`2191.35` y=`2073.24`
- radial_rms_mm: `91.93`
- z_std_mm: `469.22`

## BSDC91

- mode: `roto`
- position_samples: `1`
- position_mean_mm: x=`1876.48` y=`1313.08` z=`1709.62`
- solve_residual_mean_rms_mm: `11.62`
- radius_mm: `0.00`
- circle_center_xy_mm: x=`1876.48` y=`1313.08`
- radial_rms_mm: `0.00`
- z_std_mm: `0.00`

