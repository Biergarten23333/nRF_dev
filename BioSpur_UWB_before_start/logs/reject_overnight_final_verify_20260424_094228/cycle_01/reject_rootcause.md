# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/reject_overnight_final_verify_20260424_094228/cycle_01/capture_20260424_094228`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 74, 'max': 100, 'avg': 89.0}`
- cm_status_counts: `{'ok': 375, 'reject': 66, 'timeout': 27}`
- frame_ok_distribution: `{'3': 74, '4': 67, '2': 15, '1': 1}`
- solve_reason_counts: `{'pending': 157}`
- reject_reason_counts: `{'continuity_hard': 521, 'raw_outlier': 66, 'rx_timeout': 40, 'rx_error': 1}`
- bd_reason_counts: `{'continuity_hard': 89, 'raw_outlier': 66, 'rx_timeout': 3}`
- top_target_sets: `[{'key': 'A,B,C,D', 'count': 79}, {'key': 'E,F,G,H', 'count': 78}]`
- worst_partial_target_sets: `[{'key': 'A,B,C,D', 'count': 66}, {'key': 'E,F,G,H', 'count': 24}]`

## BS2DCE

- qf: `{'min': 92, 'max': 98, 'avg': 95.35}`
- cm_status_counts: `{'ok': 511, 'timeout': 35, 'reject': 6}`
- frame_ok_distribution: `{'4': 140, '3': 19, '2': 5, '1': 4}`
- solve_reason_counts: `{'success': 140, 'pending': 28}`
- reject_reason_counts: `{'ok': 675, 'rx_timeout': 42, 'raw_outlier': 7}`
- bd_reason_counts: `{'ok': 331, 'rx_timeout': 19}`
- top_target_sets: `[{'key': 'B,D,F,H', 'count': 84}, {'key': 'D,B,F,H', 'count': 42}, {'key': 'B,D,H,F', 'count': 42}]`
- worst_partial_target_sets: `[{'key': 'B,D,F,H', 'count': 14}, {'key': 'D,B,F,H', 'count': 7}, {'key': 'B,D,H,F', 'count': 7}]`

## BSDC91

- qf: `{'min': 96, 'max': 100, 'avg': 98.39}`
- cm_status_counts: `{'ok': 514, 'timeout': 10, 'reject': 1}`
- frame_ok_distribution: `{'4': 150, '3': 12, '2': 1}`
- solve_reason_counts: `{'success': 150, 'pending': 13}`
- reject_reason_counts: `{'ok': 675, 'rx_timeout': 7, 'rx_error': 7, 'raw_outlier': 1}`
- bd_reason_counts: `{'ok': 167, 'rx_error': 3, 'rx_timeout': 2, 'raw_outlier': 1}`
- top_target_sets: `[{'key': 'A,D,G,H', 'count': 81}, {'key': 'D,A,G,H', 'count': 62}, {'key': 'A,D,H,G', 'count': 20}]`
- worst_partial_target_sets: `[{'key': 'A,D,G,H', 'count': 6}, {'key': 'D,A,G,H', 'count': 5}, {'key': 'A,D,H,G', 'count': 2}]`

