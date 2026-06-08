# Capture Metadata

Generated: `2026-06-07T20:54:40.448922+00:00`

## Summary

- Captures: `11`
- Ground-truth captures: `1`
- Tag-capture available: `10`
- No-tag multipath usable: `1`
- Train-allowed captures: `0`

## Label Quality Counts

- `multipath_unlabeled_no_tag`: 1
- `proxy_existing_field_evaluation`: 9
- `real_ground_truth_validation`: 1

## Environment Counts

- `garage`: 4
- `indoor_lab`: 2
- `outdoor`: 5

## Condition Counts

- `controlled_unspecified`: 1
- `los`: 5
- `multipath_possible`: 4
- `smoke_test`: 1

## Capture Table

| Capture | Env | Condition | Role | Layouts | Tag | GT | Label Quality | Use | Evidence |
|---|---|---|---|---:|---|---|---|---|---|
| `28052026_Erlangen_Official` | `indoor_lab` | `controlled_unspecified` | `full_process_ground_truth` | 5 | `true` | `true` | `real_ground_truth_validation` | `calibration_validation` | `layouts;tag_capture;ground_truth;dop;residual` |
| `28052026_Erlangen_Smoke` | `indoor_lab` | `smoke_test` | `full_process_proxy` | 5 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Garage_Test` | `garage` | `multipath_possible` | `full_process_proxy` | 5 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Garage_Test_nah` | `garage` | `multipath_possible` | `raw_capture_only` | 0 | `false` | `false` | `multipath_unlabeled_no_tag` | `multipath_risk_analysis` | `no_tag_multipath` |
| `Garage_test_2` | `garage` | `multipath_possible` | `full_process_proxy` | 10 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Garage_test_nah_2` | `garage` | `multipath_possible` | `full_process_proxy` | 5 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Outdoor_LOS` | `outdoor` | `los` | `full_process_proxy` | 10 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Outdoor_LOS_2` | `outdoor` | `los` | `full_process_proxy` | 10 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `Outdoor_LOS_3` | `outdoor` | `los` | `full_process_proxy` | 5 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `outdoor_20260513` | `outdoor` | `los` | `full_process_proxy` | 57 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
| `outdoor_v4_20260504` | `outdoor` | `los` | `full_process_proxy` | 5 | `true` | `false` | `proxy_existing_field_evaluation` | `ranking_and_proxy_analysis` | `layouts;tag_capture;residual` |
