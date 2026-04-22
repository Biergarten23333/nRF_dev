# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_07/recv_tdma_capture/recv_tdma_20260422_035431`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_07/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3078 | 56.78 | 128.00 RMS |
| BS2DCE | - | 0 | - | error |
| BSDC91 | roto | 1013 | 64.62 | 573.60 radius |

## BSF66F

- mode: `static`
- position_samples: `3078`
- position_mean_mm: x=`2768.91` y=`2021.16` z=`1141.71`
- solve_residual_mean_rms_mm: `56.78`
- static_rms_mm: `128.00`
- static_p95_3d_mm: `228.50`

## BS2DCE

- error: `no position samples`

## BSDC91

- mode: `roto`
- position_samples: `1013`
- position_mean_mm: x=`2087.52` y=`1964.98` z=`1249.51`
- solve_residual_mean_rms_mm: `64.62`
- radius_mm: `573.60`
- circle_center_xy_mm: x=`2131.62` y=`2038.82`
- radial_rms_mm: `210.22`
- z_std_mm: `434.30`

