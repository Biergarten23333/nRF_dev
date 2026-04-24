# Reject Root-Cause Patch Comparison

- baseline: `logs/recv_tdma_capture_reject_rootcause_fixvalid_baseline_20260424_002854_20260424_002854`
- patched: `logs/recv_tdma_capture_reject_rootcause_rawoutlierfix_manual_20260424_005532`
## BS2DCE
- frame_ok before: `{'4': 261, '3': 107, '2': 13, '1': 4, '0': 1}`
- frame_ok after: `{'4': 291, '3': 66, '2': 21, '1': 1}`
- raw_outlier before/after: `111 -> 5`
- B/D raw_outlier before/after: `56 -> 1`
- B/D ok before/after: `234 -> 709`
- rx_timeout before/after: `46 -> 115`
- qf avg before/after: `91.93 -> 94.14`
- top target sets after: `[{'key': 'B,D,E,G', 'count': 173}, {'key': 'D,B,E,G', 'count': 89}, {'key': 'B,D,G,E', 'count': 85}]`
## BSDC91
- frame_ok before: `{'4': 220, '3': 158, '2': 42, '1': 10, '0': 7}`
- frame_ok after: `{'4': 301, '3': 22, '2': 3, '1': 1}`
- raw_outlier before/after: `239 -> 10`
- B/D raw_outlier before/after: `108 -> 3`
- B/D ok before/after: `395 -> 325`
- rx_timeout before/after: `62 -> 31`
- qf avg before/after: `84.89 -> 98.1`
- top target sets after: `[{'key': 'A,D,F,H', 'count': 123}, {'key': 'D,A,F,H', 'count': 123}, {'key': 'A,D,H,F', 'count': 81}]`
