# RECV TDMA Session Analysis

- session_dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_14/recv_tdma_capture/recv_tdma_20260422_052531`
- layout_json: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/overnight_v3_multitag_20260422_0234/cycle_14/workflow/solve_v3_box/anchor_layout_v3_box.json`

| Tag | Mode | position_samples | mean solve RMS (mm) | main metric |
|---|---:|---:|---:|---:|
| BSF66F | static | 1356 | 29.81 | 1021.85 RMS |
| BS2DCE | roto | 633 | 44.78 | 416.30 radius |
| BSDC91 | roto | 398 | 60.85 | 544.10 radius |

## BSF66F

- mode: `static`
- position_samples: `1356`
- position_mean_mm: x=`2743.92` y=`2132.98` z=`-122.12`
- solve_residual_mean_rms_mm: `29.81`
- static_rms_mm: `1021.85`
- static_p95_3d_mm: `1651.53`

## BS2DCE

- mode: `roto`
- position_samples: `633`
- position_mean_mm: x=`1948.47` y=`2023.52` z=`-737.41`
- solve_residual_mean_rms_mm: `44.78`
- radius_mm: `416.30`
- circle_center_xy_mm: x=`1878.03` y=`2125.66`
- radial_rms_mm: `108.73`
- z_std_mm: `591.55`

## BSDC91

- mode: `roto`
- position_samples: `398`
- position_mean_mm: x=`2113.95` y=`2007.26` z=`-970.62`
- solve_residual_mean_rms_mm: `60.85`
- radius_mm: `544.10`
- circle_center_xy_mm: x=`2145.02` y=`2049.19`
- radial_rms_mm: `218.30`
- z_std_mm: `676.85`

