# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/responder_profile_smoke2_20260425_004506/resp1000_20260425_004506/cycle_01/capture_20260425_004521`
- patch_recommended: `False`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `raw_outlier`

## BSF66F

- qf: `{'min': 96, 'max': 100, 'avg': 98.93}`
- cm_status_counts: `{'ok': 657, 'timeout': 15}`
- frame_ok_distribution: `{'4': 208, '3': 13, '2': 4}`
- solve_reason_counts: `{'pending': 225}`
- reject_reason_counts: `{'ok': 879, 'rx_timeout': 21}`
- bd_reason_counts: `{'ok': 221, 'rx_timeout': 5}`
- top_target_sets: `[{'key': 'A,B,C,D', 'count': 113}, {'key': 'E,F,G,H', 'count': 112}]`
- worst_partial_target_sets: `[{'key': 'E,F,G,H', 'count': 9}, {'key': 'A,B,C,D', 'count': 8}]`

## BS2DCE

- qf: `{'min': 95, 'max': 100, 'avg': 99.7}`
- cm_status_counts: `{'ok': 673, 'reject': 13, 'timeout': 2}`
- frame_ok_distribution: `{'4': 206}`
- solve_reason_counts: `{'success': 206}`
- reject_reason_counts: `{'ok': 881, 'raw_outlier': 13, 'rx_timeout': 2}`
- bd_reason_counts: `{'ok': 19, 'rx_timeout': 1}`
- top_target_sets: `[{'key': 'A,C,E,H', 'count': 78}, {'key': 'A,C,H,E', 'count': 76}, {'key': 'C,A,E,H', 'count': 52}]`
- worst_partial_target_sets: `[]`

## BSDC91

- qf: `{'min': 98, 'max': 100, 'avg': 99.68}`
- cm_status_counts: `{'ok': 691, 'reject': 21, 'timeout': 4}`
- frame_ok_distribution: `{'4': 207, '3': 4}`
- solve_reason_counts: `{'success': 207, 'pending': 4}`
- reject_reason_counts: `{'ok': 902, 'raw_outlier': 22, 'rx_timeout': 6, 'rx_error': 1}`
- bd_reason_counts: `{'ok': 15, 'raw_outlier': 7, 'rx_timeout': 2}`
- top_target_sets: `[{'key': 'A,C,E,H', 'count': 79}, {'key': 'A,C,H,E', 'count': 78}, {'key': 'C,A,E,H', 'count': 54}]`
- worst_partial_target_sets: `[{'key': 'A,C,H,E', 'count': 3}, {'key': 'C,A,E,H', 'count': 1}]`

