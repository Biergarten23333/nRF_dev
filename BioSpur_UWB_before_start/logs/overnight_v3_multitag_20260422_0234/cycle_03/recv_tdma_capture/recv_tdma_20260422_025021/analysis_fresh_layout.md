# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_03/recv_tdma_capture/recv_tdma_20260422_025021`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_03/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 2583 | 44.82 | 387.75 RMS |
| BS2DCE | roto | 762 | 48.99 | 935.51 radius |
| BSDC91 | roto | 533 | 63.39 | 555.30 radius |

## BSF66F

- mode: `static`
- position_samples: `2583`
- position_mean_mm: x=`2754.95` y=`2038.31` z=`932.03`
- solve_residual_mean_rms_mm: `44.82`
- static_rms_mm: `387.75`
- static_p95_3d_mm: `364.32`

## BS2DCE

- mode: `roto`
- position_samples: `762`
- position_mean_mm: x=`1784.76` y=`1767.54` z=`1133.29`
- solve_residual_mean_rms_mm: `48.99`
- radius_mm: `935.51`
- circle_center_xy_mm: x=`1276.64` y=`2376.59`
- radial_rms_mm: `228.58`
- z_std_mm: `243.15`

## BSDC91

- mode: `roto`
- position_samples: `533`
- position_mean_mm: x=`2089.41` y=`1956.47` z=`1138.91`
- solve_residual_mean_rms_mm: `63.39`
- radius_mm: `555.30`
- circle_center_xy_mm: x=`2095.04` y=`2030.19`
- radial_rms_mm: `202.51`
- z_std_mm: `499.05`

