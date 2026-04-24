# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/recv_tdma_capture_3tag_180s_20260424_094916_20260424_094916`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 87, 'max': 100, 'avg': 93.94}`
- cm_status_counts: `{'ok': 2651, 'timeout': 160}`
- frame_ok_distribution: `{'4': 781, '3': 98, '2': 57, '1': 2}`
- solve_reason_counts: `{'pending': 938}`
- reject_reason_counts: `{'continuity_hard': 3534, 'rx_timeout': 213, 'rx_error': 5}`
- bd_reason_counts: `{'continuity_hard': 900, 'rx_timeout': 37, 'rx_error': 1}`
- top_target_sets: `[{'key': 'E,F,G,H', 'count': 469}, {'key': 'A,B,C,D', 'count': 469}]`
- worst_partial_target_sets: `[{'key': 'E,F,G,H', 'count': 107}, {'key': 'A,B,C,D', 'count': 50}]`

## BS2DCE

- qf: `{'min': 88, 'max': 100, 'avg': 96.17}`
- cm_status_counts: `{'ok': 3112, 'timeout': 166, 'reject': 6}`
- frame_ok_distribution: `{'4': 923, '3': 95, '2': 55, '1': 8}`
- solve_reason_counts: `{'success': 923, 'pending': 158}`
- reject_reason_counts: `{'ok': 4133, 'rx_timeout': 223, 'rx_error': 8, 'raw_outlier': 6}`
- bd_reason_counts: `{'ok': 1025, 'rx_timeout': 63, 'raw_outlier': 4, 'rx_error': 1}`
- top_target_sets: `[{'key': 'C,D,F,H', 'count': 676}, {'key': 'C,D,H,F', 'count': 270}, {'key': 'D,C,F,H', 'count': 135}]`
- worst_partial_target_sets: `[{'key': 'C,D,F,H', 'count': 99}, {'key': 'C,D,H,F', 'count': 39}, {'key': 'D,C,F,H', 'count': 20}]`

## BSDC91

- qf: `{'min': 97, 'max': 100, 'avg': 99.36}`
- cm_status_counts: `{'ok': 2925, 'timeout': 33, 'reject': 4}`
- frame_ok_distribution: `{'4': 928, '3': 42, '2': 1}`
- solve_reason_counts: `{'success': 928, 'pending': 43}`
- reject_reason_counts: `{'ok': 3887, 'rx_timeout': 33, 'rx_error': 11, 'raw_outlier': 5}`
- bd_reason_counts: `{'ok': 1934, 'rx_timeout': 16, 'rx_error': 6}`
- top_target_sets: `[{'key': 'B,D,F,H', 'count': 486}, {'key': 'D,B,F,H', 'count': 243}, {'key': 'B,D,H,F', 'count': 242}]`
- worst_partial_target_sets: `[{'key': 'B,D,F,H', 'count': 20}, {'key': 'D,B,F,H', 'count': 12}, {'key': 'B,D,H,F', 'count': 11}]`

