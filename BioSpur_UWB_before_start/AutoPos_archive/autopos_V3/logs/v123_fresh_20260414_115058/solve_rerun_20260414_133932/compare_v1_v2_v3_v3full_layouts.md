# AutoPos V1 / V2 / V3-lite / V3-full Layout Compare (Rigid-Aligned)

- V1: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_20260414_133932/v1/anchor_layout_v1_soft_iterative.json`
- V2: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_20260414_133932/v2/v2_fused/anchor_layout_v2_iterative.json`
- V3-lite: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_20260414_133932/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- V3-full: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_20260414_133932/v3_full/anchor_layout_v3_full.json`

## Summary (meters)

| Compare | n | rms | max |
|---|---:|---:|---:|
| V2 aligned->V1 | 8 | 0.000000 | 0.000000 |
| V3 aligned->V1 | 8 | 0.063448 | 0.083728 |
| V3full aligned->V1 | 8 | 1.577816 | 1.682286 |
| V3 aligned->V2 | 8 | 0.063448 | 0.083728 |
| V3full aligned->V2 | 8 | 1.577816 | 1.682286 |
| V3full aligned->V3 | 8 | 1.589359 | 1.684511 |

## Notes
- Uses rigid alignment (translate + rotate), no scaling.
- RMS/max are across anchors A..H that exist in both layouts.

