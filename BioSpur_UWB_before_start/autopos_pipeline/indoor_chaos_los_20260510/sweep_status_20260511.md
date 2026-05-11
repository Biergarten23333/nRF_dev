# Indoor Chaos LOS/NLOS AutoPos Sweep Status - 2026-05-11

## Firmware/Control State

- Anchors A-H: `a40-pendingguard-g1200-r1000`
- Master_Anchor/B120: `a43-okpoll20-no-sw-accept`, LFRC internal oscillator build
- Master_Anchor mode: anchor-only control path; ordinary Tag devices are rejected by UUID/name filter
- Session role guard: enabled; waits for A-H matrix runtime ready before sweeping
- Host sweep script: `run_autopos_sweep_loop.py` now retries `autopos apply` up to 3 times when a transient reconnect produces `rc=-128`

## Successful Captures

| Capture | Output directory | Result |
|---|---|---|
| A-only 10set | `anchor_sweep_A_10set_a40a43_20260511_020321` | A `10/10` |
| A-H 10set | `anchor_sweep_10set_a40a43_20260511_020534` | A-H all `10/10` |
| A-H 100set | `anchor_sweep_100set_a40a43_20260511_020726` | A-H all `100/100` |
| A-H 500set | `anchor_sweep_500set_a40a43_applyretry_20260511_021242` | A-H all `500/500` |

## 500set Summary

Source summary:

```text
autopos_pipeline/indoor_chaos_los_20260510/anchor_sweep_500set_a40a43_applyretry_20260511_021242/summary.json
```

Extracted solver input:

```text
autopos_pipeline/indoor_chaos_los_20260510/sweeps/inter_anchor_500set_20260511_021242/pairs_all.csv
```

`pairs_all.csv` contains 27,818 rows, 28 unique pairs, and all A-H master directions. Pair row counts range from 962 to 1001 because some indoor chaos/NLOS frames had missing or low-quality peer outputs.

V3 robust fused distances:

```text
autopos_pipeline/indoor_chaos_los_20260510/sweeps/inter_anchor_500set_20260511_021242/fused_v3/inter_anchor_matrix_v3fused.json
autopos_pipeline/indoor_chaos_los_20260510/sweeps/inter_anchor_500set_20260511_021242/fused_v3/final_pair_distances_v3.csv
```

Diagnostic inter-anchor-only layout:

```text
autopos_pipeline/indoor_chaos_los_20260510/sweeps/inter_anchor_500set_20260511_021242/layout_v3fused_interonly.json
```

The diagnostic layout solved, but the inter-anchor RMS is very high: `1276.17mm`. Treat this as evidence that the indoor chaos/NLOS measurement set is intentionally harsh and should be analyzed as a bad-environment comparison, not as a clean calibration result.

| Master | Success | SW rows | Raw rows | Reconnect retry | Switch time | Collect time | Total time |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | true | 500/500 | 500 | false | 4.0s | 42.1s | 48.1s |
| B | true | 500/500 | 500 | true | 30.9s | 42.5s | 75.4s |
| C | true | 500/500 | 500 | false | 4.3s | 43.5s | 49.8s |
| D | true | 501/500 | 501 | false | 3.8s | 43.9s | 49.7s |
| E | true | 500/500 | 500 | false | 4.2s | 42.3s | 48.4s |
| F | true | 500/500 | 500 | false | 4.8s | 42.5s | 49.2s |
| G | true | 500/500 | 500 | true | 5.1s | 43.0s | 50.1s |
| H | true | 500/500 | 500 | false | 6.6s | 42.7s | 51.2s |

Total elapsed: 7m44s.

## Remaining Warnings

Warnings are range-quality warnings in the indoor chaos placement, not control-link failures:

- A low quality as matrix in rounds C/D/E/G/H
- B low quality as matrix in rounds C/D/G/H
- D low quality as matrix in round C
- E low quality as matrix in rounds A/C/D/G/H
- F low quality as matrix in rounds C/D/G/H

This is expected to be useful for the intended indoor chaos comparison. The control flow is now able to hold A-H through 10set, 100set, and 500set sweeps.
