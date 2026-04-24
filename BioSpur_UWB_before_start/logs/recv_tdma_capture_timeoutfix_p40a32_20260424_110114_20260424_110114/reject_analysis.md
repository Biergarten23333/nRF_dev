# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/recv_tdma_capture_timeoutfix_p40a32_20260424_110114_20260424_110114`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 89, 'max': 100, 'avg': 97.03}`
- cm_status_counts: `{'ok': 2629, 'timeout': 71}`
- frame_ok_distribution: `{'4': 814, '3': 59, '2': 27}`
- solve_reason_counts: `{'pending': 900}`
- reject_reason_counts: `{'continuity_hard': 3488, 'rx_timeout': 113}`
- bd_reason_counts: `{'continuity_hard': 871, 'rx_timeout': 29}`
- top_target_sets: `[{'key': 'E,F,G,H', 'count': 450}, {'key': 'A,B,C,D', 'count': 450}]`
- worst_partial_target_sets: `[{'key': 'A,B,C,D', 'count': 49}, {'key': 'E,F,G,H', 'count': 37}]`

## BS2DCE

- qf: `{'min': 93, 'max': 100, 'avg': 98.07}`
- cm_status_counts: `{'ok': 2878, 'timeout': 62, 'reject': 8}`
- frame_ok_distribution: `{'4': 894, '3': 73, '2': 4}`
- solve_reason_counts: `{'success': 894, 'pending': 77}`
- reject_reason_counts: `{'ok': 3828, 'rx_timeout': 81, 'raw_outlier': 9}`
- bd_reason_counts: `{'ok': 1950, 'raw_outlier': 1, 'rx_timeout': 1}`
- top_target_sets: `[{'key': 'B,D,E,F', 'count': 583}, {'key': 'D,B,E,F', 'count': 233}, {'key': 'B,D,F,E', 'count': 117}, {'key': 'B,D,E,H', 'count': 17}, {'key': 'B,D,H,E', 'count': 11}]`
- worst_partial_target_sets: `[{'key': 'B,D,E,F', 'count': 41}, {'key': 'D,B,E,F', 'count': 17}, {'key': 'B,D,F,E', 'count': 12}, {'key': 'B,D,E,H', 'count': 4}, {'key': 'B,D,H,E', 'count': 2}]`

## BSDC91

- qf: `{'min': 86, 'max': 100, 'avg': 97.79}`
- cm_status_counts: `{'ok': 2893, 'timeout': 49, 'reject': 3}`
- frame_ok_distribution: `{'4': 888, '3': 82, '2': 1}`
- solve_reason_counts: `{'success': 888, 'pending': 83}`
- reject_reason_counts: `{'ok': 3830, 'rx_timeout': 81, 'raw_outlier': 4, 'rx_error': 4}`
- bd_reason_counts: `{'ok': 954, 'rx_timeout': 24, 'raw_outlier': 1, 'rx_error': 1}`
- top_target_sets: `[{'key': 'C,D,E,H', 'count': 487}, {'key': 'C,D,H,E', 'count': 363}, {'key': 'D,C,E,H', 'count': 121}]`
- worst_partial_target_sets: `[{'key': 'C,D,E,H', 'count': 47}, {'key': 'C,D,H,E', 'count': 30}, {'key': 'D,C,E,H', 'count': 6}]`

