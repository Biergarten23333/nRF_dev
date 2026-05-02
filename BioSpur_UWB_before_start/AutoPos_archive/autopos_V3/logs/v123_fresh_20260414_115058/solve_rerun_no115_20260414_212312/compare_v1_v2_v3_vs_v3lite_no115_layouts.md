# AutoPos V1 / V2 / V3 Layout Compare (Rigid-Aligned)

- V1: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v1/anchor_layout_v1_soft_iterative.json`
- V2: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v2/v2_fused/anchor_layout_v2_iterative.json`
- V3: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_no115_20260414_212312/v3_lite_no115/v3_fused/anchor_layout_v3_lite_iterative.json`

## Summary (meters)

| Compare | n | rms | max |
|---|---:|---:|---:|
| V2 aligned->V1 | 8 | 0.000000 | 0.000000 |
| V3 aligned->V1 | 8 | 2.940141 | 3.813762 |
| V3 aligned->V2 | 8 | 2.940141 | 3.813762 |

## Notes
- Uses rigid alignment (translate + rotate), no scaling.
- RMS/max are across anchors A..H that exist in both layouts.

