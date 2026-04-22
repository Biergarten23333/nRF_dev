# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_33/recv_tdma_capture/recv_tdma_20260422_102238`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_33/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 81 | 25.92 | 715.51 RMS |
| BS2DCE | roto | 719 | 45.27 | 893.92 radius |
| BSDC91 | roto | 408 | 50.85 | 615.93 radius |

## BSF66F

- mode: `static`
- position_samples: `81`
- position_mean_mm: x=`2529.47` y=`2422.10` z=`-814.33`
- solve_residual_mean_rms_mm: `25.92`
- static_rms_mm: `715.51`
- static_p95_3d_mm: `908.21`

## BS2DCE

- mode: `roto`
- position_samples: `719`
- position_mean_mm: x=`1788.40` y=`2270.31` z=`-562.00`
- solve_residual_mean_rms_mm: `45.27`
- radius_mm: `893.92`
- circle_center_3d_mm: x=`2411.44` y=`2315.36` z=`-831.90`
- plane_normal: x=`-0.2886` y=`-0.5788` z=`-0.7627`
- plane_rms_mm: `291.23`
- radial_rms_mm: `244.57`
- z_std_mm: `305.54`

## BSDC91

- mode: `roto`
- position_samples: `408`
- position_mean_mm: x=`2191.54` y=`1816.63` z=`-676.81`
- solve_residual_mean_rms_mm: `50.85`
- radius_mm: `615.93`
- circle_center_3d_mm: x=`2197.51` y=`1840.37` z=`-775.04`
- plane_normal: x=`0.9980` y=`0.0067` z=`0.0623`
- plane_rms_mm: `363.15`
- radial_rms_mm: `250.48`
- z_std_mm: `509.36`

