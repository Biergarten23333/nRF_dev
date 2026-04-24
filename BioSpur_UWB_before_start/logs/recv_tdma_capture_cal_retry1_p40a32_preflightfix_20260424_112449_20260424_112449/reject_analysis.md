# Recv TDMA Reject Root-Cause Summary

- session_dir: `logs/recv_tdma_capture_cal_retry1_p40a32_preflightfix_20260424_112449_20260424_112449`
- patch_recommended: `True`
- dominant_bd_reason_global: `ok`
- dominant_bd_non_ok_reason_global: `rx_timeout`

## BSF66F

- qf: `{'min': 90, 'max': 100, 'avg': 98.87}`
- cm_status_counts: `{'ok': 2671, 'timeout': 29}`
- frame_ok_distribution: `{'4': 850, '3': 47, '2': 3}`
- solve_reason_counts: `{'pending': 900}`
- reject_reason_counts: `{'continuity_hard': 3548, 'rx_timeout': 53}`
- bd_reason_counts: `{'continuity_hard': 877, 'rx_timeout': 23}`
- top_target_sets: `[{'key': 'E,F,G,H', 'count': 450}, {'key': 'A,B,C,D', 'count': 450}]`
- worst_partial_target_sets: `[{'key': 'A,B,C,D', 'count': 30}, {'key': 'E,F,G,H', 'count': 20}]`

## BS2DCE

- qf: `{'min': 95, 'max': 100, 'avg': 98.81}`
- cm_status_counts: `{'ok': 2827, 'timeout': 29, 'reject': 6}`
- frame_ok_distribution: `{'4': 893, '3': 51}`
- solve_reason_counts: `{'success': 893, 'pending': 51}`
- reject_reason_counts: `{'ok': 3752, 'rx_timeout': 51, 'raw_outlier': 6}`
- bd_reason_counts: `{'ok': 1887, 'rx_timeout': 11}`
- top_target_sets: `[{'key': 'B,D,F,G', 'count': 381}, {'key': 'B,D,G,H', 'count': 210}, {'key': 'D,B,F,G', 'count': 152}, {'key': 'D,B,G,H', 'count': 84}, {'key': 'B,D,G,F', 'count': 76}]`
- worst_partial_target_sets: `[{'key': 'B,D,F,G', 'count': 18}, {'key': 'D,B,F,G', 'count': 14}, {'key': 'B,D,G,H', 'count': 8}, {'key': 'B,D,G,F', 'count': 8}, {'key': 'D,B,G,H', 'count': 2}]`

## BSDC91

- qf: `{'min': 94, 'max': 100, 'avg': 98.6}`
- cm_status_counts: `{'ok': 2858, 'timeout': 28}`
- frame_ok_distribution: `{'4': 896, '3': 58, '2': 1}`
- solve_reason_counts: `{'success': 896, 'pending': 59}`
- reject_reason_counts: `{'ok': 3779, 'rx_timeout': 61}`
- bd_reason_counts: `{'ok': 987, 'rx_timeout': 15}`
- top_target_sets: `[{'key': 'A,D,F,H', 'count': 218}, {'key': 'D,A,F,H', 'count': 216}, {'key': 'A,D,H,F', 'count': 146}, {'key': 'A,D,G,H', 'count': 144}, {'key': 'D,A,G,H', 'count': 111}]`
- worst_partial_target_sets: `[{'key': 'A,D,F,H', 'count': 17}, {'key': 'A,D,G,H', 'count': 11}, {'key': 'D,A,F,H', 'count': 11}, {'key': 'A,B,G,H', 'count': 7}, {'key': 'A,D,H,F', 'count': 6}]`

