# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_27/recv_tdma_capture/recv_tdma_20260422_083102`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_27/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 3097 | 59.74 | 145.95 RMS |
| BS2DCE | roto | 25 | 52.98 | 885.97 radius |
| BSDC91 | roto | 6 | 73.61 | 2034.93 radius |

## BSF66F

- mode: `static`
- position_samples: `3097`
- position_mean_mm: x=`2779.04` y=`2052.96` z=`1009.93`
- solve_residual_mean_rms_mm: `59.74`
- static_rms_mm: `145.95`
- static_p95_3d_mm: `165.04`

## BS2DCE

- mode: `roto`
- position_samples: `25`
- position_mean_mm: x=`1445.47` y=`2326.66` z=`790.76`
- solve_residual_mean_rms_mm: `52.98`
- radius_mm: `885.97`
- circle_center_xy_mm: x=`1896.81` y=`1874.12`
- radial_rms_mm: `194.27`
- z_std_mm: `190.61`

## BSDC91

- mode: `roto`
- position_samples: `6`
- position_mean_mm: x=`2272.94` y=`2365.40` z=`530.83`
- solve_residual_mean_rms_mm: `73.61`
- radius_mm: `2034.93`
- circle_center_xy_mm: x=`3667.05` y=`3644.37`
- radial_rms_mm: `116.18`
- z_std_mm: `175.41`

