# AutoPos Layout Quality Report

- Distances: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/pairs_all.csv`
- Pair count available: `28`
- Floating ref session: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/floating_ref115_train`

## V1
- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v1/anchor_layout_v1_soft_iterative.json`
- Anchors: `8`
- Distance fit: pairs=28 rms=2502.55mm max=5238.15mm p50=1801.57mm p90=4137.82mm
- Floating tag fit: rms=24.35mm max=43.26mm anchors_used=8
- Worst pairs (abs err mm):
  - B-D: 5238.15 (pred=33.5 meas=5271.7)
  - A-C: 4904.66 (pred=75.2 meas=4979.9)
  - B-C: 4137.82 (pred=32.2 meas=4170.1)
  - A-D: 4059.07 (pred=53.7 meas=4112.8)
  - C-D: 4028.60 (pred=34.8 meas=4063.4)
  - D-F: 3870.07 (pred=1459.9 meas=5330.0)
  - A-B: 3696.61 (pred=46.9 meas=3743.5)
  - C-E: 2754.27 (pred=2693.7 meas=5448.0)

## V2
- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v2/v2_fused/anchor_layout_v2_iterative.json`
- Anchors: `8`
- Distance fit: pairs=28 rms=2502.55mm max=5238.15mm p50=1801.57mm p90=4137.82mm
- Floating tag fit: rms=24.35mm max=43.26mm anchors_used=8
- Worst pairs (abs err mm):
  - B-D: 5238.15 (pred=33.5 meas=5271.7)
  - A-C: 4904.66 (pred=75.2 meas=4979.9)
  - B-C: 4137.82 (pred=32.2 meas=4170.1)
  - A-D: 4059.07 (pred=53.7 meas=4112.8)
  - C-D: 4028.60 (pred=34.8 meas=4063.4)
  - D-F: 3870.07 (pred=1459.9 meas=5330.0)
  - A-B: 3696.61 (pred=46.9 meas=3743.5)
  - C-E: 2754.27 (pred=2693.7 meas=5448.0)

## V3_with115
- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v3_lite/v3_fused/anchor_layout_v3_lite_iterative.json`
- Anchors: `8`
- Distance fit: pairs=28 rms=2529.79mm max=5258.31mm p50=1825.83mm p90=4155.91mm
- Floating tag fit: rms=10.50mm max=19.04mm anchors_used=8
- Worst pairs (abs err mm):
  - B-D: 5258.31 (pred=13.4 meas=5271.7)
  - A-C: 4955.07 (pred=24.8 meas=4979.9)
  - B-C: 4155.91 (pred=14.2 meas=4170.1)
  - A-D: 4095.44 (pred=17.3 meas=4112.8)
  - C-D: 4050.26 (pred=13.1 meas=4063.4)
  - D-F: 3887.61 (pred=1442.4 meas=5330.0)
  - A-B: 3730.06 (pred=13.4 meas=3743.5)
  - C-E: 2764.16 (pred=2683.9 meas=5448.0)

## V3_no115
- Layout: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_rerun_no115_20260414_212312/v3_lite_no115/v3_fused/anchor_layout_v3_lite_iterative.json`
- Anchors: `8`
- Distance fit: pairs=28 rms=193.66mm max=564.83mm p50=98.79mm p90=307.65mm
- Floating tag fit: rms=1946.47mm max=3037.09mm anchors_used=8
- Worst pairs (abs err mm):
  - F-H: 564.83 (pred=5415.5 meas=5980.3)
  - B-C: 450.34 (pred=3719.7 meas=4170.1)
  - A-C: 307.65 (pred=5287.5 meas=4979.9)
  - A-D: 297.66 (pred=3815.1 meas=4112.8)
  - C-D: 272.26 (pred=3791.1 meas=4063.4)
  - A-E: 232.05 (pred=1637.4 meas=1869.5)
  - A-B: 202.32 (pred=3541.2 meas=3743.5)
  - B-F: 173.96 (pred=1632.4 meas=1458.5)

