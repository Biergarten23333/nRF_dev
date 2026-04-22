# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_06/recv_tdma_capture/recv_tdma_20260422_033831`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_06/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3012 | 43.81 | 464.42 RMS |
| BS2DCE | roto | 1268 | 47.13 | 443.51 radius |
| BSDC91 | - | 0 | - | error |

## BSF66F

- mode: `static`
- position_samples: `3012`
- position_mean_mm: x=`2871.78` y=`2015.70` z=`-782.70`
- solve_residual_mean_rms_mm: `43.81`
- static_rms_mm: `464.42`
- static_p95_3d_mm: `995.18`

## BS2DCE

- mode: `roto`
- position_samples: `1268`
- position_mean_mm: x=`2169.38` y=`1941.90` z=`-291.23`
- solve_residual_mean_rms_mm: `47.13`
- radius_mm: `443.51`
- circle_center_xy_mm: x=`2167.14` y=`2044.10`
- radial_rms_mm: `154.56`
- z_std_mm: `448.85`

## BSDC91

- error: `no position samples`

