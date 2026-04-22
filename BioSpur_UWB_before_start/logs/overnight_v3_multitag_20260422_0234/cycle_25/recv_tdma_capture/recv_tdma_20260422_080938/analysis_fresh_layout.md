# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_25/recv_tdma_capture/recv_tdma_20260422_080938`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_25/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 2649 | 55.90 | 135.46 RMS |
| BS2DCE | roto | 56 | 74.18 | 921.74 radius |
| BSDC91 | roto | 6 | 105.81 | 982.72 radius |

## BSF66F

- mode: `static`
- position_samples: `2649`
- position_mean_mm: x=`2649.03` y=`2001.17` z=`1136.81`
- solve_residual_mean_rms_mm: `55.90`
- static_rms_mm: `135.46`
- static_p95_3d_mm: `236.19`

## BS2DCE

- mode: `roto`
- position_samples: `56`
- position_mean_mm: x=`1878.18` y=`1936.79` z=`1066.70`
- solve_residual_mean_rms_mm: `74.18`
- radius_mm: `921.74`
- circle_center_xy_mm: x=`2011.27` y=`2523.95`
- radial_rms_mm: `188.64`
- z_std_mm: `590.81`

## BSDC91

- mode: `roto`
- position_samples: `6`
- position_mean_mm: x=`1474.61` y=`1884.81` z=`1475.06`
- solve_residual_mean_rms_mm: `105.81`
- radius_mm: `982.72`
- circle_center_xy_mm: x=`2363.06` y=`2243.38`
- radial_rms_mm: `33.99`
- z_std_mm: `759.99`

