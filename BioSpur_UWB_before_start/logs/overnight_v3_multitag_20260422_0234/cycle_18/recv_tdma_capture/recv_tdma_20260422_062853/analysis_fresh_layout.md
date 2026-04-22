# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_18/recv_tdma_capture/recv_tdma_20260422_062853`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_18/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 45 | 40.02 | 636.15 RMS |
| BS2DCE | roto | 285 | 42.35 | 444.77 radius |
| BSDC91 | roto | 450 | 60.46 | 527.89 radius |

## BSF66F

- mode: `static`
- position_samples: `45`
- position_mean_mm: x=`2768.88` y=`1973.60` z=`986.42`
- solve_residual_mean_rms_mm: `40.02`
- static_rms_mm: `636.15`
- static_p95_3d_mm: `275.59`

## BS2DCE

- mode: `roto`
- position_samples: `285`
- position_mean_mm: x=`1961.14` y=`2050.66` z=`744.66`
- solve_residual_mean_rms_mm: `42.35`
- radius_mm: `444.77`
- circle_center_xy_mm: x=`1981.51` y=`1992.10`
- radial_rms_mm: `186.25`
- z_std_mm: `455.17`

## BSDC91

- mode: `roto`
- position_samples: `450`
- position_mean_mm: x=`1993.70` y=`1926.42` z=`982.19`
- solve_residual_mean_rms_mm: `60.46`
- radius_mm: `527.89`
- circle_center_xy_mm: x=`2024.07` y=`1958.26`
- radial_rms_mm: `188.03`
- z_std_mm: `544.80`

