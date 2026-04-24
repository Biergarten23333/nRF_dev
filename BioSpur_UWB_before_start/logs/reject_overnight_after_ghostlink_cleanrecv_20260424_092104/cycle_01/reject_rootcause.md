# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/reject_overnight_after_ghostlink_cleanrecv_20260424_092104/cycle_01/capture_20260424_092104`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 88, 'max': 97, 'avg': 93.81}`
- cm_status_counts: `{'ok': 643, 'timeout': 59}`
- frame_ok_distribution: `{'4': 189, '3': 28, '1': 14, '2': 4}`
- solve_reason_counts: `{'pending': 235}`
- reject_reason_counts: `{'continuity_hard': 862, 'rx_timeout': 74, 'rx_error': 4}`
- bd_reason_counts: `{'continuity_hard': 210, 'rx_timeout': 23, 'rx_error': 1}`
- top_target_sets: `[{'key': 'E,F,G,H', 'count': 118}, {'key': 'A,B,C,D', 'count': 117}]`
- worst_partial_target_sets: `[{'key': 'A,B,C,D', 'count': 24}, {'key': 'E,F,G,H', 'count': 22}]`

## BS2DCE

- qf: `{'min': 89, 'max': 100, 'avg': 95.93}`
- cm_status_counts: `{'ok': 760, 'timeout': 38, 'reject': 6}`
- frame_ok_distribution: `{'4': 222, '3': 22, '2': 15}`
- solve_reason_counts: `{'success': 222, 'pending': 37}`
- reject_reason_counts: `{'ok': 1007, 'rx_timeout': 51, 'raw_outlier': 6, 'rx_error': 1}`
- bd_reason_counts: `{'ok': 503, 'rx_timeout': 20, 'raw_outlier': 2, 'rx_error': 1}`
- top_target_sets: `[{'key': 'B,D,E,G', 'count': 103}, {'key': 'D,B,E,G', 'count': 52}, {'key': 'B,D,G,E', 'count': 52}, {'key': 'B,D,F,H', 'count': 16}, {'key': 'B,D,G,H', 'count': 12}]`
- worst_partial_target_sets: `[{'key': 'B,D,E,G', 'count': 12}, {'key': 'B,D,G,E', 'count': 11}, {'key': 'D,B,E,G', 'count': 7}, {'key': 'B,D,H,G', 'count': 2}, {'key': 'B,D,G,H', 'count': 2}]`

## BSDC91

- qf: `{'min': 84, 'max': 100, 'avg': 98.1}`
- cm_status_counts: `{'ok': 731, 'timeout': 13, 'reject': 6}`
- frame_ok_distribution: `{'4': 229, '3': 13, '2': 1}`
- solve_reason_counts: `{'success': 229, 'pending': 14}`
- reject_reason_counts: `{'ok': 972, 'rx_timeout': 15, 'raw_outlier': 6, 'rx_error': 1}`
- bd_reason_counts: `{'ok': 242, 'rx_timeout': 4, 'raw_outlier': 3}`
- top_target_sets: `[{'key': 'A,B,G,H', 'count': 182}, {'key': 'B,A,G,H', 'count': 31}, {'key': 'A,B,H,G', 'count': 30}]`
- worst_partial_target_sets: `[{'key': 'A,B,G,H', 'count': 8}, {'key': 'B,A,G,H', 'count': 4}, {'key': 'A,B,H,G', 'count': 2}]`

