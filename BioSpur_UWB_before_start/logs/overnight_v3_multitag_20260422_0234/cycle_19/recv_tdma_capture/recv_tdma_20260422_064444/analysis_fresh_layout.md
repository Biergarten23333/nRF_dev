# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_19/recv_tdma_capture/recv_tdma_20260422_064444`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_19/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 25 | 41.02 | 593.66 RMS |
| BS2DCE | roto | 393 | 41.82 | 464.13 radius |
| BSDC91 | roto | 126 | 61.34 | 550.23 radius |

## BSF66F

- mode: `static`
- position_samples: `25`
- position_mean_mm: x=`2732.41` y=`2025.86` z=`954.37`
- solve_residual_mean_rms_mm: `41.02`
- static_rms_mm: `593.66`
- static_p95_3d_mm: `1658.98`

## BS2DCE

- mode: `roto`
- position_samples: `393`
- position_mean_mm: x=`1946.40` y=`2048.03` z=`844.24`
- solve_residual_mean_rms_mm: `41.82`
- radius_mm: `464.13`
- circle_center_xy_mm: x=`1947.02` y=`1999.83`
- radial_rms_mm: `180.79`
- z_std_mm: `476.54`

## BSDC91

- mode: `roto`
- position_samples: `126`
- position_mean_mm: x=`1932.79` y=`1910.28` z=`1257.27`
- solve_residual_mean_rms_mm: `61.34`
- radius_mm: `550.23`
- circle_center_xy_mm: x=`2077.10` y=`1944.03`
- radial_rms_mm: `152.87`
- z_std_mm: `432.86`

