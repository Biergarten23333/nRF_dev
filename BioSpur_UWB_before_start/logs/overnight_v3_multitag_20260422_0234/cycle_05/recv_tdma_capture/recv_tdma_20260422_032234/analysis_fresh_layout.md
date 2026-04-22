# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_05/recv_tdma_capture/recv_tdma_20260422_032234`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_05/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 2917 | 46.53 | 408.96 RMS |
| BS2DCE | roto | 136 | 32.99 | 408.70 radius |
| BSDC91 | roto | 29 | 64.75 | 553.93 radius |

## BSF66F

- mode: `static`
- position_samples: `2917`
- position_mean_mm: x=`2813.68` y=`2078.63` z=`-702.69`
- solve_residual_mean_rms_mm: `46.53`
- static_rms_mm: `408.96`
- static_p95_3d_mm: `895.01`

## BS2DCE

- mode: `roto`
- position_samples: `136`
- position_mean_mm: x=`1951.44` y=`2121.73` z=`-363.12`
- solve_residual_mean_rms_mm: `32.99`
- radius_mm: `408.70`
- circle_center_xy_mm: x=`1952.60` y=`2120.60`
- radial_rms_mm: `124.90`
- z_std_mm: `586.04`

## BSDC91

- mode: `roto`
- position_samples: `29`
- position_mean_mm: x=`2256.71` y=`2142.54` z=`-835.79`
- solve_residual_mean_rms_mm: `64.75`
- radius_mm: `553.93`
- circle_center_xy_mm: x=`2232.95` y=`2177.46`
- radial_rms_mm: `208.41`
- z_std_mm: `757.19`

