# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/recv_tdma_capture_timeoutfix_rx1800_resp1200_20260424_104532_20260424_104532`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 91, 'max': 100, 'avg': 98.95}`
- cm_status_counts: `{'ok': 2758, 'timeout': 53}`
- frame_ok_distribution: `{'4': 889, '3': 27, '2': 22}`
- solve_reason_counts: `{'pending': 938}`
- reject_reason_counts: `{'continuity_hard': 3681, 'rx_timeout': 69, 'rx_error': 2}`
- bd_reason_counts: `{'continuity_hard': 938}`
- top_target_sets: `[{'key': 'A,B,C,D', 'count': 469}, {'key': 'E,F,G,H', 'count': 469}]`
- worst_partial_target_sets: `[{'key': 'E,F,G,H', 'count': 46}, {'key': 'A,B,C,D', 'count': 3}]`

## BS2DCE

- qf: `{'min': 87, 'max': 98, 'avg': 93.3}`
- cm_status_counts: `{'ok': 2712, 'timeout': 257, 'reject': 31}`
- frame_ok_distribution: `{'4': 734, '3': 200, '2': 48, '1': 2, '0': 1}`
- solve_reason_counts: `{'success': 735, 'pending': 251}`
- reject_reason_counts: `{'ok': 3666, 'rx_timeout': 240, 'rx_error': 46, 'raw_outlier': 33}`
- bd_reason_counts: `{'ok': 905, 'rx_timeout': 81, 'rx_error': 10, 'raw_outlier': 7}`
- top_target_sets: `[{'key': 'C,D,E,H', 'count': 491}, {'key': 'C,D,H,E', 'count': 366}, {'key': 'D,C,E,H', 'count': 122}, {'key': 'B,D,H,E', 'count': 3}, {'key': 'B,D,E,H', 'count': 2}]`
- worst_partial_target_sets: `[{'key': 'C,D,E,H', 'count': 141}, {'key': 'C,D,H,E', 'count': 82}, {'key': 'D,C,E,H', 'count': 26}, {'key': 'B,D,H,E', 'count': 1}, {'key': 'B,D,E,H', 'count': 1}]`

## BSDC91

- qf: `{'min': 79, 'max': 98, 'avg': 90.95}`
- cm_status_counts: `{'ok': 3024, 'timeout': 223, 'reject': 34}`
- frame_ok_distribution: `{'4': 703, '3': 318, '2': 46, '1': 10}`
- solve_reason_counts: `{'success': 702, 'pending': 375}`
- reject_reason_counts: `{'ok': 3907, 'rx_timeout': 355, 'raw_outlier': 56, 'rx_error': 40}`
- bd_reason_counts: `{'ok': 2048, 'rx_timeout': 85, 'raw_outlier': 21, 'rx_error': 14}`
- top_target_sets: `[{'key': 'B,D,F,H', 'count': 539}, {'key': 'D,B,F,H', 'count': 270}, {'key': 'B,D,H,F', 'count': 268}]`
- worst_partial_target_sets: `[{'key': 'B,D,F,H', 'count': 192}, {'key': 'D,B,F,H', 'count': 94}, {'key': 'B,D,H,F', 'count': 88}]`

