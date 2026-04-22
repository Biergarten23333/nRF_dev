# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_09/recv_tdma_capture/recv_tdma_20260422_041600`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_09/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 2992 | 33.63 | 608.56 RMS |
| BS2DCE | roto | 6 | 60.08 | 663.13 radius |
| BSDC91 | - | 0 | - | error |

## BSF66F

- mode: `static`
- position_samples: `2992`
- position_mean_mm: x=`2734.43` y=`2067.01` z=`-754.42`
- solve_residual_mean_rms_mm: `33.63`
- static_rms_mm: `608.56`
- static_p95_3d_mm: `1644.02`

## BS2DCE

- mode: `roto`
- position_samples: `6`
- position_mean_mm: x=`2057.11` y=`2462.33` z=`-118.22`
- solve_residual_mean_rms_mm: `60.08`
- radius_mm: `663.13`
- circle_center_xy_mm: x=`1804.07` y=`3009.67`
- radial_rms_mm: `47.54`
- z_std_mm: `108.80`

## BSDC91

- error: `no position samples`

