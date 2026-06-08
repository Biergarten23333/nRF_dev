# Axis DOP Drop1-4 Redundancy Analysis

Generated: `2026-06-07T20:55:02.001628+00:00`

## Summary

- Layouts analyzed: `117`
- Source groups: `14`
- Input masks: exhaustive `drop1`, `drop2`, `drop3`, and `drop4` combinations.
- `drop4` is the surviving-four-anchor case: each row keeps only four anchors.
- Lower score/DOP is better. Worst columns mean the most fragile outage combination.

## Best Surviving-4 Layout Per Group

| Group | Best Version | Variant | Worst Drop4 | Worst Score | Worst VDOP p95 | Worst Layout | Worst Layout Score |
|---|---|---|---|---:|---:|---|---:|
| `28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropCDEF` | 16.219 | 18.630 | `v4-io:default` | 17.982 |
| `28052026_Erlangen_Smoke/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropBCEH` | 14.468 | 18.778 | `v4-io:default` | 17.805 |
| `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropABGH` | 11.567 | 15.629 | `v3-full:default` | 13.793 |
| `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `default` | `dropABGH` | 11.219 | 14.636 | `v4-io:default` | 13.766 |
| `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropBDFH` | 12.360 | 16.940 | `v4-io:default` | 24.102 |
| `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `us_height` | `dropCDEF` | 13.174 | 18.918 | `v3-full:default` | 23.169 |
| `Outdoor_LOS_2/solver/outputs/v1_to_v4_io_field_check` | `v2` | `us_height` | `dropCDEF` | 15.915 | 21.400 | `v4-io:default` | 19.122 |
| `Outdoor_LOS_3/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropCDEF` | 16.774 | 22.138 | `v4-io:default` | 19.155 |
| `outdoor_20260513/FULL-COMPARE` | `v1` | `default` | `dropCDEF` | 17.878 | 25.166 | `v4:default` | 20.410 |
| `outdoor_20260513/FULL-COMPARE-1000` | `v1-old` | `default` | `dropCDEF` | 17.878 | 25.166 | `v4-io-roto:default` | 22.647 |
| `outdoor_20260513/FULL-COMPARE-500` | `v1-old` | `default` | `dropCDEF` | 17.133 | 24.045 | `v4-io-roto:default` | 22.809 |
| `outdoor_20260513/FULL-COMPARE-500+500` | `v1-old` | `first500` | `dropCDEF` | 17.133 | 24.045 | `v4-io-roto:first500` | 22.809 |
| `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | `v1-old` | `us_height` | `dropCDEF` | 17.215 | 24.485 | `v4-io-roto:us_height` | 22.353 |
| `outdoor_v4_20260504/FULL-COMPARE` | `v1` | `default` | `dropABGH` | 17.831 | 21.429 | `v3-lite:default` | 20.504 |

## Most Fragile Surviving-4 Layouts Overall

| Capture | Group | Version | Variant | Worst Drop4 | Worst Score | all8 Score | Score Ratio | Worst VDOP p95 |
|---|---|---|---|---|---:|---:|---:|---:|
| `Garage_test_nah_2` | `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `default` | `dropCDEF` | 24.102 | 1.144 | 21.063 | 31.167 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropCDEF` | 23.169 | 1.182 | 19.598 | 32.524 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500` | `v4-io-roto` | `default` | `dropADFG` | 22.809 | 1.148 | 19.867 | 28.872 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io-roto` | `first500` | `dropADFG` | 22.809 | 1.148 | 19.867 | 28.872 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io-roto` | `consensus` | `dropADFG` | 22.721 | 1.149 | 19.780 | 28.785 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-1000` | `v4-io-roto` | `default` | `dropADFG` | 22.647 | 1.149 | 19.706 | 28.620 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io-roto` | `last500_aligned` | `dropADFG` | 22.529 | 1.149 | 19.603 | 28.506 |
| `outdoor_20260513` | `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | `v4-io-roto` | `us_height` | `dropADFG` | 22.353 | 1.150 | 19.444 | 27.338 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `default` | `dropBCEH` | 21.239 | 1.187 | 17.895 | 28.337 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v3-full` | `last500_aligned` | `dropADFG` | 21.183 | 1.139 | 18.602 | 28.770 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `us_height` | `dropCDEF` | 20.984 | 1.181 | 17.773 | 30.400 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `us_height` | `dropBCEH` | 20.812 | 1.187 | 17.540 | 27.451 |
| `outdoor_v4_20260504` | `outdoor_v4_20260504/FULL-COMPARE` | `v3-lite` | `default` | `dropCDEF` | 20.504 | 1.091 | 18.788 | 25.922 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io-td` | `last500_aligned` | `dropABGH` | 20.480 | 1.149 | 17.826 | 28.898 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io` | `last500_aligned` | `dropABGH` | 20.480 | 1.149 | 17.826 | 28.898 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v5` | `last500_aligned` | `dropABGH` | 20.480 | 1.149 | 17.826 | 28.898 |
| `outdoor_v4_20260504` | `outdoor_v4_20260504/FULL-COMPARE` | `v2` | `default` | `dropCDEF` | 20.431 | 1.092 | 18.704 | 25.853 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE` | `v4` | `default` | `dropABGH` | 20.410 | 1.149 | 17.758 | 28.812 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-1000` | `v4-io` | `default` | `dropABGH` | 20.410 | 1.149 | 17.758 | 28.812 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-1000` | `v4-io-td` | `default` | `dropABGH` | 20.410 | 1.149 | 17.758 | 28.812 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-1000` | `v5` | `default` | `dropABGH` | 20.410 | 1.149 | 17.758 | 28.812 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io-td` | `consensus` | `dropABGH` | 20.304 | 1.149 | 17.666 | 28.639 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v4-io` | `consensus` | `dropABGH` | 20.304 | 1.149 | 17.666 | 28.639 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v5` | `consensus` | `dropABGH` | 20.304 | 1.149 | 17.666 | 28.639 |
| `outdoor_20260513` | `outdoor_20260513/FULL-COMPARE-500+500` | `v3-full` | `consensus` | `dropADFG` | 20.243 | 1.138 | 17.788 | 28.803 |
