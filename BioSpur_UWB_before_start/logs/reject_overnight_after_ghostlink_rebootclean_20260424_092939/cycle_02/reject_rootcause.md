# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/reject_overnight_after_ghostlink_rebootclean_20260424_092939/cycle_02/capture_20260424_093313`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 88, 'max': 100, 'avg': 94.8}`
- cm_status_counts: `{'ok': 665, 'timeout': 37}`
- frame_ok_distribution: `{'4': 197, '3': 23, '2': 14, '1': 1}`
- solve_reason_counts: `{'pending': 235}`
- reject_reason_counts: `{'continuity_hard': 886, 'rx_timeout': 51, 'rx_error': 3}`
- bd_reason_counts: `{'continuity_hard': 234, 'rx_timeout': 1, 'rx_error': 1}`
- top_target_sets: `[{'key': 'A,B,C,D', 'count': 118}, {'key': 'E,F,G,H', 'count': 117}]`
- worst_partial_target_sets: `[{'key': 'E,F,G,H', 'count': 34}, {'key': 'A,B,C,D', 'count': 4}]`

## BS2DCE

- qf: `{'min': 94, 'max': 100, 'avg': 96.67}`
- cm_status_counts: `{'ok': 794, 'timeout': 45}`
- frame_ok_distribution: `{'4': 231, '3': 39, '2': 6, '1': 1}`
- solve_reason_counts: `{'success': 231, 'pending': 46}`
- reject_reason_counts: `{'ok': 1061, 'rx_timeout': 51, 'rx_error': 3}`
- bd_reason_counts: `{'ok': 522, 'rx_timeout': 33, 'rx_error': 1}`
- top_target_sets: `[{'key': 'B,D,G,H', 'count': 154}, {'key': 'D,B,G,H', 'count': 62}, {'key': 'B,D,H,G', 'count': 30}, {'key': 'B,D,F,H', 'count': 15}, {'key': 'B,D,H,F', 'count': 8}]`
- worst_partial_target_sets: `[{'key': 'B,D,G,H', 'count': 28}, {'key': 'D,B,G,H', 'count': 10}, {'key': 'B,D,H,G', 'count': 4}, {'key': 'B,D,F,H', 'count': 2}, {'key': 'B,D,H,F', 'count': 2}]`

## BSDC91

- qf: `{'min': 93, 'max': 100, 'avg': 98.66}`
- cm_status_counts: `{'ok': 740, 'timeout': 14, 'reject': 1}`
- frame_ok_distribution: `{'4': 232, '3': 16, '2': 1}`
- solve_reason_counts: `{'success': 232, 'pending': 17}`
- reject_reason_counts: `{'ok': 984, 'rx_timeout': 16, 'rx_error': 2, 'raw_outlier': 1}`
- bd_reason_counts: `{'ok': 489, 'rx_timeout': 11}`
- top_target_sets: `[{'key': 'B,D,F,H', 'count': 125}, {'key': 'D,B,F,H', 'count': 62}, {'key': 'B,D,H,F', 'count': 62}]`
- worst_partial_target_sets: `[{'key': 'D,B,F,H', 'count': 8}, {'key': 'B,D,F,H', 'count': 6}, {'key': 'B,D,H,F', 'count': 3}]`

