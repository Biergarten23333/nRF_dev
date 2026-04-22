# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_12/recv_tdma_capture/recv_tdma_20260422_050410`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_12/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 1317 | 35.35 | 404.88 RMS |
| BS2DCE | roto | 198 | 36.84 | 382.12 radius |
| BSDC91 | roto | 588 | 63.72 | 544.09 radius |

## BSF66F

- mode: `static`
- position_samples: `1317`
- position_mean_mm: x=`2744.63` y=`2036.47` z=`1008.54`
- solve_residual_mean_rms_mm: `35.35`
- static_rms_mm: `404.88`
- static_p95_3d_mm: `268.00`

## BS2DCE

- mode: `roto`
- position_samples: `198`
- position_mean_mm: x=`1850.25` y=`1882.32` z=`1220.60`
- solve_residual_mean_rms_mm: `36.84`
- radius_mm: `382.12`
- circle_center_xy_mm: x=`1844.12` y=`1888.96`
- radial_rms_mm: `79.82`
- z_std_mm: `445.41`

## BSDC91

- mode: `roto`
- position_samples: `588`
- position_mean_mm: x=`2044.73` y=`1951.51` z=`1213.15`
- solve_residual_mean_rms_mm: `63.72`
- radius_mm: `544.09`
- circle_center_xy_mm: x=`2086.03` y=`2006.29`
- radial_rms_mm: `183.19`
- z_std_mm: `443.41`

