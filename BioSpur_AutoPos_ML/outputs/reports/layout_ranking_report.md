# Baseline Layout Ranking

Generated: `2026-05-31T22:22:03.364323+00:00`

## Summary

- Ranked layouts: `117`
- Score groups: `14`
- Score direction: lower is better
- Scores are normalized within each source group, not globally across rooms.
- No GPU is used by this script.

## Confidence

- `evaluation_matched`: 50
- `geometry_only_low`: 17
- `partial_evaluation`: 50

## Top Layout Per Group

| Group | Rank | Layout | Version | Variant | Score | Confidence | Reason |
|---|---:|---|---|---|---:|---|---|
| `28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check` | 1 | `0752b3202614dca0` | `v2` | `default` | 10.367 | `evaluation_matched` | autopos_rms=52.4mm; static_p95=89.1mm; roto_p95=39.8mm |
| `28052026_Erlangen_Smoke/solver/outputs/v1_to_v4_io_field_check` | 1 | `ebda6c001ec839b2` | `v2` | `default` | 5.215 | `partial_evaluation` | autopos_rms=45.0mm |
| `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | 1 | `d0329b8b5bd6d38d` | `v3-lite` | `default` | 8.436 | `partial_evaluation` | autopos_rms=77.2mm; risk=unexpected_layer_order |
| `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `526c5cfba000a235` | `v3-lite` | `us_height` | 8.027 | `partial_evaluation` | autopos_rms=81.9mm |
| `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `aad717661a728eef` | `v3-lite` | `default` | 8.451 | `partial_evaluation` | autopos_rms=66.6mm; risk=unexpected_layer_order |
| `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | 1 | `bda2d0e51cf32bc2` | `v2` | `us_height` | 13.628 | `partial_evaluation` | autopos_rms=48.9mm |
| `Outdoor_LOS_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `b5d368d15c9d301e` | `v2` | `us_height` | 2.564 | `partial_evaluation` | autopos_rms=62.1mm |
| `Outdoor_LOS_3/solver/outputs/v1_to_v4_io_field_check` | 1 | `172b3b835dda9c0d` | `v3-lite` | `default` | 6.263 | `partial_evaluation` | autopos_rms=57.4mm |
| `outdoor_20260513/FULL-COMPARE` | 1 | `a46dcba7a6dc6a8d` | `v3-lite` | `default` | 0.263 | `geometry_only_low` | geometry-only score |
| `outdoor_20260513/FULL-COMPARE-1000` | 1 | `21d850e593ebb432` | `v4-io-roto` | `default` | 22.606 | `evaluation_matched` | autopos_rms=57.9mm; static_p95=71.9mm; roto_p95=45.4mm |
| `outdoor_20260513/FULL-COMPARE-500` | 1 | `c855e34a218d0254` | `v4-io-roto` | `default` | 24.065 | `evaluation_matched` | autopos_rms=56.9mm; static_p95=74.5mm; roto_p95=46.0mm |
| `outdoor_20260513/FULL-COMPARE-500+500` | 1 | `c154e86f58c62fcf` | `v4-io-roto` | `consensus` | 23.607 | `evaluation_matched` | autopos_rms=57.5mm; static_p95=75.1mm; roto_p95=46.0mm; split_align=8.3mm |
| `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | 1 | `175c6752d9dd2ac4` | `v4-io-roto` | `us_height` | 16.826 | `geometry_only_low` | geometry-only score |
| `outdoor_v4_20260504/FULL-COMPARE` | 1 | `bcd7d23aab9bdac7` | `v3-full` | `default` | 4.550 | `geometry_only_low` | geometry-only score |

## Erlangen Official Ranking

| Rank | Version | Score | Autopos RMS | Static p95 | Roto p95 |
|---:|---|---:|---:|---:|---:|
| 1 | `v2` | 10.367 | 52.3579327 | 89.0684618 | 39.8498037 |
| 2 | `v3-lite` | 11.310 | 52.2910968 | 89.4462916 | 39.9380231 |
| 3 | `v4-io` | 15.225 | 48.1687588 | 88.198864 | 42.5838675 |
| 4 | `v3-full` | 76.748 | 102.496576 | 95.0862748 | 52.2513083 |
| 5 | `v1-old` | 79.502 | 85.2463578 | 94.6402388 | 59.878135 |

## Method

The baseline score combines matched `version_summary.csv` metrics where available:
AutoPos RMS/p95, static p95/max, roto radius/center metrics, split alignment, and light geometry terms.
Rows without matched evaluation metrics are kept as geometry-only, low-confidence candidates.
