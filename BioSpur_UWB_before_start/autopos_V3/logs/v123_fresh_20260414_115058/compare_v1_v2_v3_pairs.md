# AutoPos V1 / V2 / V3-lite Pair Distance Compare

- V1: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v1/final_pair_distances.csv`
- V2: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v2/v2_fused/final_pair_distances_v2.csv`
- V3-lite: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3/logs/v123_fresh_20260414_115058/solve_20260414_115058/v3_lite/v3_fused/final_pair_distances_v2.csv`

## Per-Pair Table (mm)

- Note: `distance_mm==0` treated as missing for all versions (`--zero-as-missing`).

| Pair | V1 | V2 | V3 | abs(V2-V1) | abs(V3-V1) | abs(V3-V2) |
|---|---:|---:|---:|---:|---:|---:|
| A-B | 3659.79 | 3659.79 | 3659.79 | 0.00 | 0.00 | 0.00 |
| A-C | 4979.82 | 4979.82 | 4979.82 | 0.00 | 0.00 | 0.00 |
| A-D | 4040.64 | 4040.64 | 4040.64 | 0.00 | 0.00 | 0.00 |
| A-E | 1893.09 | 1893.09 | 1893.09 | 0.00 | 0.00 | 0.00 |
| A-F | 3994.21 | 3994.21 | 3994.21 | 0.00 | 0.00 | 0.00 |
| A-G | 5419.67 | 5419.67 | 5419.67 | 0.00 | 0.00 | 0.00 |
| A-H | 4205.26 | 4205.26 | 4205.26 | 0.00 | 0.00 | 0.00 |
| B-C | 4207.96 | 4207.96 | 4207.96 | 0.00 | 0.00 | 0.00 |
| B-D | 5212.76 | 5212.76 | 5212.76 | 0.00 | 0.00 | 0.00 |
| B-E | 3962.97 | 3962.97 | 3962.97 | 0.00 | 0.00 | 0.00 |
| B-F | 1470.96 | 1470.96 | 1470.96 | 0.00 | 0.00 | 0.00 |
| B-G | 4108.19 | 4108.19 | 4108.19 | 0.00 | 0.00 | 0.00 |
| B-H | 5510.79 | 5510.79 | 5510.79 | 0.00 | 0.00 | 0.00 |
| C-D | 3999.21 | 3999.21 | 3999.21 | 0.00 | 0.00 | 0.00 |
| C-E | 5444.56 | 5444.56 | 5444.56 | 0.00 | 0.00 | 0.00 |
| C-F | 4080.23 | 4080.23 | 4080.23 | 0.00 | 0.00 | 0.00 |
| C-G | 1592.11 | 1592.11 | 1592.11 | 0.00 | 0.00 | 0.00 |
| C-H | 4374.45 | 4374.45 | 4374.45 | 0.00 | 0.00 | 0.00 |
| D-E | 4137.81 | 4137.81 | 4137.81 | 0.00 | 0.00 | 0.00 |
| D-F | 5367.68 | 5367.68 | 5367.68 | 0.00 | 0.00 | 0.00 |
| D-G | 4111.12 | 4111.12 | 4111.12 | 0.00 | 0.00 | 0.00 |
| D-H | 1763.70 | 1763.70 | 1763.70 | 0.00 | 0.00 | 0.00 |
| E-F | 3412.42 | 3412.42 | 3412.42 | 0.00 | 0.00 | 0.00 |
| E-G | 5334.53 | 5334.53 | 5334.53 | 0.00 | 0.00 | 0.00 |
| E-H | 3717.14 | 3717.14 | 3717.14 | 0.00 | 0.00 | 0.00 |
| F-G | 3850.83 | 3850.83 | 3850.83 | 0.00 | 0.00 | 0.00 |
| F-H | 6021.54 | 6021.54 | 6021.54 | 0.00 | 0.00 | 0.00 |
| G-H | 3906.59 | 3906.59 | 3906.59 | 0.00 | 0.00 | 0.00 |

## Summary (absolute delta, mm)

| Compare | n | mean | rms | max |
|---|---:|---:|---:|---:|
| V2 vs V1 | 28 | 0.00 | 0.00 | 0.00 |
| V3 vs V1 | 28 | 0.00 | 0.00 | 0.00 |
| V3 vs V2 | 28 | 0.00 | 0.00 | 0.00 |

Notes:
- Differences here are *algorithmic output deltas* given the captured data; if the runs used different sweep set counts or different hardware state, deltas can be dominated by data differences rather than solver differences.

