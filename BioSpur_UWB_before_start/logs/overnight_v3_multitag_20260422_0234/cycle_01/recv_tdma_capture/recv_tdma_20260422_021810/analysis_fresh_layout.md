# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_01/recv_tdma_capture/recv_tdma_20260422_021810`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_01/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3093 | 46.42 | 381.93 RMS |
| BS2DCE | roto | 1314 | 51.32 | 1021.45 radius |
| BSDC91 | roto | 1177 | 62.82 | 565.02 radius |

## BSF66F

- mode: `static`
- position_samples: `3093`
- position_mean_mm: x=`2728.83` y=`2058.41` z=`962.96`
- solve_residual_mean_rms_mm: `46.42`
- static_rms_mm: `381.93`
- static_p95_3d_mm: `271.48`

## BS2DCE

- mode: `roto`
- position_samples: `1314`
- position_mean_mm: x=`1735.75` y=`1782.95` z=`1153.93`
- solve_residual_mean_rms_mm: `51.32`
- radius_mm: `1021.45`
- circle_center_xy_mm: x=`1195.86` y=`2492.44`
- radial_rms_mm: `224.40`
- z_std_mm: `238.61`

## BSDC91

- mode: `roto`
- position_samples: `1177`
- position_mean_mm: x=`2063.82` y=`1945.71` z=`1152.25`
- solve_residual_mean_rms_mm: `62.82`
- radius_mm: `565.02`
- circle_center_xy_mm: x=`2069.98` y=`2029.73`
- radial_rms_mm: `201.35`
- z_std_mm: `473.75`

