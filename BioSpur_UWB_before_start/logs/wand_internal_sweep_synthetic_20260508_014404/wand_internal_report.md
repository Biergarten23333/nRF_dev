# Wand Internal Sweep

- positions_csv: `/tmp/wand_positions_hEZQ1N.csv`
- reference_label: `A`
- match_window_s: `0.1`
- complete_3tag_epochs: `2`
- partial_epochs: `0`

This is the first host-side implementation: it computes the three Wand side lengths from simultaneous UWB position fixes. Direct Tag-to-Tag internal ranging still needs firmware support.

## Pair Statistics

| Pair | N | Mean | Median | Std | MAD | P05 | P95 | Truth | Mean error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AB | 2 | 300.0 | 300.0 | 0.0 | 0.0 | 300.0 | 300.0 | 300.0 | 0.0 |
| AC | 2 | 400.1 | 400.1 | 0.1 | 0.1 | 400.0 | 400.1 | 400.0 | 0.1 |
| BC | 2 | 503.0 | 503.0 | 3.0 | 3.0 | 500.3 | 505.8 | 500.0 | 3.0 |

## Outputs

- sweep CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_synthetic_20260508_014404/wand_internal_sweep.csv`
- pair stats CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_synthetic_20260508_014404/wand_internal_pair_stats.csv`
- summary JSON: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_synthetic_20260508_014404/wand_internal_summary.json`
