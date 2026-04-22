# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/recv_tdma_capture/recv_tdma_20260422_014434`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/v3_box_radius_refit_20260422_013724/solve_v3_box/anchor_layout_v3_box_fixed_noref.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 1886 | 33.03 | 121.91 RMS |
| BS2DCE | roto | 749 | 42.94 | 1008.23 radius |
| BSDC91 | roto | 827 | 55.16 | 568.47 radius |

## BSF66F

- mode: `static`
- position_samples: `1886`
- position_mean_mm: x=`2703.49` y=`2110.54` z=`-1065.07`
- solve_residual_mean_rms_mm: `33.03`
- static_rms_mm: `121.91`
- static_p95_3d_mm: `211.57`

## BS2DCE

- mode: `roto`
- position_samples: `749`
- position_mean_mm: x=`1742.22` y=`1823.99` z=`-1031.95`
- solve_residual_mean_rms_mm: `42.94`
- radius_mm: `1008.23`
- circle_center_xy_mm: x=`1278.55` y=`2322.31`
- radial_rms_mm: `204.89`
- z_std_mm: `290.45`

## BSDC91

- mode: `roto`
- position_samples: `827`
- position_mean_mm: x=`2016.81` y=`2015.28` z=`-1133.64`
- solve_residual_mean_rms_mm: `55.16`
- radius_mm: `568.47`
- circle_center_xy_mm: x=`2012.85` y=`2117.06`
- radial_rms_mm: `190.53`
- z_std_mm: `475.23`

