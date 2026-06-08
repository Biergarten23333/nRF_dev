# Layout Score v2

Generated: `2026-06-07T20:55:05.842974+00:00`

## Summary

- Scored layouts: `117`
- Score groups: `14`
- Production score excludes OptiTrack labels.
- OptiTrack validation score is shown only where ground truth exists.
- Lower score is better.
- No GPU is used by this script.

## Confidence

- `evaluation_matched`: 95
- `geometry_only_low`: 17
- `optitrack_validated`: 5

## Top By Group

| Group | Rank | Version | Variant | Layout | Score v2 | Eval | Geo | DOP | Confidence |
|---|---:|---|---|---|---:|---:|---:|---:|---|
| `28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check` | 1 | `v2` | `default` | `0752b3202614dca0` | 7.700 | 9.796354 | 2.250120 |  | `optitrack_validated` |
| `28052026_Erlangen_Smoke/solver/outputs/v1_to_v4_io_field_check` | 1 | `v2` | `default` | `ebda6c001ec839b2` | 2.796 | 0.000000 | 10.065080 |  | `evaluation_matched` |
| `Garage_Test/solver/outputs/v1_to_v4_io_field_check` | 1 | `v3-lite` | `default` | `d0329b8b5bd6d38d` | 4.151 | 0.000000 | 14.943542 |  | `evaluation_matched` |
| `Garage_test_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `v3-lite` | `us_height` | `526c5cfba000a235` | 3.489 | 0.000000 | 12.561184 |  | `evaluation_matched` |
| `Garage_test_nah_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `v3-lite` | `default` | `aad717661a728eef` | 7.040 | 0.000000 | 25.342445 |  | `evaluation_matched` |
| `Outdoor_LOS/solver/outputs/v1_to_v4_io_field_check` | 1 | `v2` | `default` | `922a7045c2bcb065` | 19.956 | 15.938843 | 30.401810 |  | `evaluation_matched` |
| `Outdoor_LOS_2/solver/outputs/v1_to_v4_io_field_check` | 1 | `v2` | `default` | `5484092f61ff25fc` | 6.197 | 1.204110 | 19.179178 |  | `evaluation_matched` |
| `Outdoor_LOS_3/solver/outputs/v1_to_v4_io_field_check` | 1 | `v3-lite` | `default` | `172b3b835dda9c0d` | 15.357 | 3.477793 | 46.242097 |  | `evaluation_matched` |
| `outdoor_20260513/FULL-COMPARE` | 1 | `v1` | `default` | `554f4e554b20b5b2` | 8.313 |  | 8.312674 |  | `geometry_only_low` |
| `outdoor_20260513/FULL-COMPARE-1000` | 1 | `v4-io-roto` | `default` | `21d850e593ebb432` | 15.519 | 18.588164 | 7.539468 |  | `evaluation_matched` |
| `outdoor_20260513/FULL-COMPARE-500` | 1 | `v4-io-roto` | `default` | `c855e34a218d0254` | 16.737 | 19.971847 | 8.324812 |  | `evaluation_matched` |
| `outdoor_20260513/FULL-COMPARE-500+500` | 1 | `v4-io-roto` | `first500` | `fb986c7dd6f2e63b` | 17.840 | 21.334723 | 8.753475 |  | `evaluation_matched` |
| `outdoor_20260513/reports/us_height_alignment_from_fgh_20260523/FULL-COMPARE-1000` | 1 | `v4-io-roto` | `us_height` | `175c6752d9dd2ac4` | 6.927 |  | 6.927102 |  | `geometry_only_low` |
| `outdoor_v4_20260504/FULL-COMPARE` | 1 | `v3-lite` | `default` | `ee1b62b82f6517d2` | 18.024 |  | 18.023965 |  | `geometry_only_low` |

## Erlangen Official: Production vs OptiTrack

| Prod rank | Val rank | Version | Score v2 | Opti score | 3D RMS | 3D p95 | DOP |
|---:|---:|---|---:|---:|---:|---:|---:|
| 1 | 1 | `v2` | 7.700 | 2.540253 | 132.08905070793008 | 233.1077736825216 |  |
| 2 | 2 | `v3-lite` | 8.529 | 2.977048 | 132.28845672708738 | 233.52603084957047 |  |
| 3 | 3 | `v4-io` | 15.157 | 13.593026 | 136.5028285215412 | 270.2552828594085 | 4.012932 |
| 4 | 5 | `v1-old` | 65.038 | 100.000000 | 191.64129425669003 | 314.7898277297456 |  |
| 5 | 4 | `v3-full` | 76.005 | 51.573580 | 158.98876953960595 | 280.0735010456271 |  |

## Bewertung

- `v2` remains the best Erlangen official production-score candidate in this scoring setup.
- `v3-lite` is effectively tied with `v2` on many field metrics and remains a strong candidate.
- `v4-io` has the only currently bound DOP summary and the best median/vertical OptiTrack behavior, but its p95/RMS validation is weaker than `v2`/`v3-lite`.
- `v4-io-roto` is the strongest repeated winner in the outdoor 20260513 evaluated groups.
