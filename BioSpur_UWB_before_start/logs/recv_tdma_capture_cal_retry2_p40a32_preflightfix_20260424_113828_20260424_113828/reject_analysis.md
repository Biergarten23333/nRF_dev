# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/recv_tdma_capture_cal_retry2_p40a32_preflightfix_20260424_113828_20260424_113828`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `raw_outlier`

## BSF66F

- qf: `{'min': 88, 'max': 98, 'avg': 94.02}`
- cm_status_counts: `{'ok': 1058, 'timeout': 69, 'reject': 49}`
- frame_ok_distribution: `{'4': 264, '3': 123, '2': 26, '1': 4, '0': 1}`
- solve_reason_counts: `{'pending': 418}`
- reject_reason_counts: `{'continuity_hard': 1481, 'not_measured': 69, 'raw_outlier': 50, 'rx_error': 41, 'rx_timeout': 31}`
- bd_reason_counts: `{'continuity_hard': 377, 'not_measured': 29, 'raw_outlier': 8, 'rx_timeout': 4}`
- top_target_sets: `[{'key': 'E,F,G,H', 'count': 209}, {'key': 'A,B,C,D', 'count': 209}]`
- worst_partial_target_sets: `[{'key': 'E,F,G,H', 'count': 79}, {'key': 'A,B,C,D', 'count': 75}]`

## BS2DCE

- qf: `{'min': 96, 'max': 100, 'avg': 98.67}`
- cm_status_counts: `{'ok': 2634, 'reject': 53, 'timeout': 17}`
- frame_ok_distribution: `{'4': 830, '3': 69, '2': 2}`
- solve_reason_counts: `{'success': 830, 'pending': 71}`
- reject_reason_counts: `{'ok': 3531, 'raw_outlier': 54, 'rx_timeout': 10, 'rx_error': 9}`
- bd_reason_counts: `{'ok': 863, 'raw_outlier': 29, 'rx_error': 6, 'rx_timeout': 3}`
- top_target_sets: `[{'key': 'A,B,F,G', 'count': 676}, {'key': 'A,B,G,F', 'count': 113}, {'key': 'B,A,F,G', 'count': 112}]`
- worst_partial_target_sets: `[{'key': 'A,B,F,G', 'count': 54}, {'key': 'A,B,G,F', 'count': 9}, {'key': 'B,A,F,G', 'count': 8}]`

## BSDC91

- qf: `{'min': 96, 'max': 100, 'avg': 99.86}`
- cm_status_counts: `{'ok': 2718, 'timeout': 5, 'reject': 1}`
- frame_ok_distribution: `{'4': 899, '3': 9}`
- solve_reason_counts: `{'success': 899, 'pending': 9}`
- reject_reason_counts: `{'ok': 3623, 'rx_timeout': 8, 'raw_outlier': 1}`
- bd_reason_counts: `{'ok': 1812, 'rx_timeout': 3, 'raw_outlier': 1}`
- top_target_sets: `[{'key': 'B,D,F,H', 'count': 454}, {'key': 'D,B,F,H', 'count': 227}, {'key': 'B,D,H,F', 'count': 227}]`
- worst_partial_target_sets: `[{'key': 'B,D,F,H', 'count': 4}, {'key': 'D,B,F,H', 'count': 3}, {'key': 'B,D,H,F', 'count': 2}]`

