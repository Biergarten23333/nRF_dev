# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_17/recv_tdma_capture/recv_tdma_20260422_061307`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_17/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 193 | 46.03 | 130.44 RMS |
| BS2DCE | roto | 789 | 45.33 | 448.16 radius |
| BSDC91 | - | 0 | - | error |

## BSF66F

- mode: `static`
- position_samples: `193`
- position_mean_mm: x=`2760.52` y=`2017.19` z=`1131.50`
- solve_residual_mean_rms_mm: `46.03`
- static_rms_mm: `130.44`
- static_p95_3d_mm: `286.02`

## BS2DCE

- mode: `roto`
- position_samples: `789`
- position_mean_mm: x=`1923.72` y=`2082.79` z=`752.51`
- solve_residual_mean_rms_mm: `45.33`
- radius_mm: `448.16`
- circle_center_xy_mm: x=`1933.52` y=`2032.45`
- radial_rms_mm: `182.56`
- z_std_mm: `439.76`

## BSDC91

- error: `no position samples`

