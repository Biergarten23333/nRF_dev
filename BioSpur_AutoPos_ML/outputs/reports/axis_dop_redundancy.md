# Axis DOP Redundancy Analysis

Generated: `2026-05-31T23:15:22.857048+00:00`

## Summary

- Layouts analyzed: `117`
- Source groups: `14`
- Input masks: `all8` plus every single-anchor drop `dropA`...`dropH`.
- Lower score/DOP is better. Worst-drop columns mean the most fragile single-anchor outage.

## Best Robust Layout Per Group

| Group | Best Robust Version | Variant | Worst Drop | Worst Score | Worst VDOP p95 | Best all8 Version | Worst Layout |
|---|---|---|---|---:|---:|---|---|
| `28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropH` | 1.088 | 1.159 | `v3-lite` | `v3-full:default` |
| `28052026_Erlangen_Smoke/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropG` | 1.144 | 1.238 | `v3-lite` | `v3-full:default` |
| `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `default` | `dropH` | 1.431 | 1.723 | `v1-old` | `v4-io:default` |
| `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `us_height` | `dropF` | 1.400 | 1.664 | `v1-old` | `v2:default` |
| `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `default` | `dropA` | 1.399 | 1.602 | `v1-old` | `v3-full:default` |
| `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropD` | 1.505 | 1.775 | `v1-old` | `v4-io:default` |
| `Outdoor_LOS_2/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `us_height` | `dropD` | 1.306 | 1.476 | `v3-lite` | `v1-old:default` |
| `Outdoor_LOS_3/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropD` | 1.317 | 1.480 | `v3-lite` | `v1-old:default` |
| `outdoor_20260513/FULL-COMPARE` | `v3-full` | `default` | `dropA` | 1.426 | 1.633 | `v3-full` | `v2:default` |
| `outdoor_20260513/FULL-COMPARE-1000` | `v3-full` | `default` | `dropA` | 1.426 | 1.633 | `v3-full` | `v2:default` |
| `outdoor_20260513/FULL-COMPARE-500` | `v4-io-roto` | `default` | `dropC` | 1.428 | 1.649 | `v3-full` | `v4-io-wand:default` |
| `outdoor_20260513/FULL-COMPARE-500+500` | `v3-full` | `last500_aligned` | `dropG` | 1.424 | 1.631 | `v3-full` | `v4-io-wand:first500` |
| `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | `v3-full` | `us_height` | `dropG` | 1.419 | 1.595 | `v3-full` | `v1-old:us_height` |
| `outdoor_v4_20260504/FULL-COMPARE` | `v3-full` | `default` | `dropF` | 1.366 | 1.571 | `v3-lite` | `v1:default` |

## Most Fragile Layouts Overall

| Capture | Group | Version | Variant | Worst Drop | Worst Score | all8 Score | Score Ratio | Worst VDOP p95 |
|---|---|---|---|---|---:|---:|---:|---:|
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `default` | `dropG` | 1.534 | 1.187 | 1.293 | 1.822 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `us_height` | `dropD` | 1.530 | 1.187 | 1.290 | 1.799 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropC` | 1.529 | 1.182 | 1.294 | 1.807 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `us_height` | `dropC` | 1.529 | 1.181 | 1.295 | 1.809 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `default` | `dropD` | 1.527 | 1.161 | 1.315 | 1.792 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v1-old` | `us_height` | `dropD` | 1.523 | 1.158 | 1.315 | 1.774 |
| `Garage_Test` | `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `default` | `dropH` | 1.516 | 1.179 | 1.286 | 1.852 |
| `Garage_Test` | `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v2` | `default` | `dropC` | 1.512 | 1.178 | 1.283 | 1.840 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v2` | `us_height` | `dropD` | 1.511 | 1.174 | 1.287 | 1.764 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `us_height` | `dropD` | 1.510 | 1.174 | 1.287 | 1.763 |
| `Garage_Test` | `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropC` | 1.510 | 1.177 | 1.283 | 1.837 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v2` | `default` | `dropD` | 1.506 | 1.176 | 1.280 | 1.777 |
| `Outdoor_LOS` | `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropD` | 1.505 | 1.176 | 1.280 | 1.775 |
| `Garage_Test` | `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | `v3-full` | `default` | `dropH` | 1.500 | 1.162 | 1.292 | 1.821 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v2` | `default` | `dropC` | 1.494 | 1.164 | 1.283 | 1.813 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v2` | `us_height` | `dropH` | 1.492 | 1.165 | 1.281 | 1.806 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `default` | `dropC` | 1.491 | 1.163 | 1.283 | 1.809 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v3-lite` | `us_height` | `dropH` | 1.489 | 1.163 | 1.280 | 1.804 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `default` | `dropH` | 1.488 | 1.159 | 1.284 | 1.809 |
| `Garage_test_2` | `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | `v4-io` | `us_height` | `dropH` | 1.482 | 1.159 | 1.278 | 1.794 |
