# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/reject_overnight_after_ghostlink_cleanrecv_20260424_092104/cycle_02/capture_20260424_092421`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 87, 'max': 100, 'avg': 94.88}`
- cm_status_counts: `{'ok': 668, 'timeout': 34}`
- frame_ok_distribution: `{'4': 210, '1': 12, '3': 10, '2': 3}`
- solve_reason_counts: `{'pending': 232, 'success': 3}`
- reject_reason_counts: `{'continuity_hard': 479, 'ok': 409, 'rx_timeout': 52}`
- bd_reason_counts: `{'continuity_hard': 112, 'ok': 95, 'rx_timeout': 29}`
- top_target_sets: `[{'key': 'A,B,C,D', 'count': 118}, {'key': 'E,F,G,H', 'count': 117}]`
- worst_partial_target_sets: `[{'key': 'A,B,C,D', 'count': 19}, {'key': 'E,F,G,H', 'count': 6}]`

## BS2DCE

- qf: `{'min': 94, 'max': 100, 'avg': 97.17}`
- cm_status_counts: `{'ok': 775, 'timeout': 32, 'reject': 3}`
- frame_ok_distribution: `{'4': 228, '3': 28, '1': 3, '2': 2}`
- solve_reason_counts: `{'success': 228, 'pending': 33}`
- reject_reason_counts: `{'ok': 1030, 'rx_timeout': 40, 'raw_outlier': 3, 'rx_error': 3}`
- bd_reason_counts: `{'ok': 506, 'rx_timeout': 23, 'rx_error': 1}`
- top_target_sets: `[{'key': 'B,D,E,G', 'count': 88}, {'key': 'D,B,E,G', 'count': 44}, {'key': 'B,D,G,E', 'count': 43}, {'key': 'B,D,E,H', 'count': 28}, {'key': 'B,D,H,E', 'count': 28}]`
- worst_partial_target_sets: `[{'key': 'B,D,E,G', 'count': 9}, {'key': 'D,B,E,G', 'count': 8}, {'key': 'B,D,H,E', 'count': 5}, {'key': 'B,D,E,H', 'count': 4}, {'key': 'B,D,G,E', 'count': 4}]`

## BSDC91

- qf: `{'min': 93, 'max': 100, 'avg': 99.14}`
- cm_status_counts: `{'ok': 744, 'timeout': 15, 'reject': 2}`
- frame_ok_distribution: `{'4': 228, '3': 18, '1': 1}`
- solve_reason_counts: `{'success': 228, 'pending': 19}`
- reject_reason_counts: `{'ok': 984, 'rx_timeout': 21, 'raw_outlier': 3, 'rx_error': 2}`
- bd_reason_counts: `{'ok': 248, 'rx_timeout': 5}`
- top_target_sets: `[{'key': 'B,C,E,F', 'count': 183}, {'key': 'B,C,F,E', 'count': 31}, {'key': 'C,B,E,F', 'count': 30}, {'key': 'B,C,E,H', 'count': 2}, {'key': 'C,B,E,H', 'count': 1}]`
- worst_partial_target_sets: `[{'key': 'B,C,E,F', 'count': 10}, {'key': 'B,C,F,E', 'count': 4}, {'key': 'C,B,E,F', 'count': 3}, {'key': 'B,C,E,H', 'count': 1}, {'key': 'C,B,E,H', 'count': 1}]`

