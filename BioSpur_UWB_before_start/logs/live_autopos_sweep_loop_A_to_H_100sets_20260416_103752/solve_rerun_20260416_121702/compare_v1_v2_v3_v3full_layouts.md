# AutoPos V1 / V2 / V3-lite / V3-full Layout Compare (Rigid-Aligned)

- V1: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_121702/v1/anchor_layout_v1_soft_iterative.json`
- V2: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_121702/v2/v2_fused/anchor_layout_v2_iterative.json`
- V3-lite: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_121702/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- V3-full: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_100sets_20260416_103752/solve_rerun_20260416_121702/v3_full/anchor_layout_v3_full.json`

## Summary (meters)

| Compare | n | rms | max |
|---|---:|---:|---:|
| V2 aligned->V1 | 8 | 0.000000 | 0.000000 |
| V3 aligned->V1 | 8 | 0.010260 | 0.012827 |
| V3full aligned->V1 | 8 | 0.036414 | 0.050710 |
| V3 aligned->V2 | 8 | 0.010260 | 0.012827 |
| V3full aligned->V2 | 8 | 0.036414 | 0.050710 |
| V3full aligned->V3 | 8 | 0.045314 | 0.061054 |

## Notes
- Uses rigid alignment (translate + rotate), no scaling.
- RMS/max are across anchors A..H that exist in both layouts.

