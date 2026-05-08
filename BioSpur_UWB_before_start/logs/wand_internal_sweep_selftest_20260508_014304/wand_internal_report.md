# Wand Internal Sweep

- positions_csv: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/rotoarm_tilted_3tag_motion60_20260503_175031/recv_20260503_175032/positions_all.csv`
- complete_3tag_sweeps: `0`
- partial_sweeps: `618`

This is the first host-side implementation: it computes the three Wand side lengths from simultaneous UWB position fixes. Direct Tag-to-Tag internal ranging still needs firmware support.

## Pair Statistics

| Pair | N | Mean | Median | Std | MAD | P05 | P95 | Truth | Mean error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| AB | 0 | - | - | - | - | - | - | 500.0 | - |
| AC | 0 | - | - | - | - | - | - | 600.0 | - |
| BC | 0 | - | - | - | - | - | - | 700.0 | - |

## Outputs

- sweep CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_selftest_20260508_014304/wand_internal_sweep.csv`
- pair stats CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_selftest_20260508_014304/wand_internal_pair_stats.csv`
- summary JSON: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/wand_internal_sweep_selftest_20260508_014304/wand_internal_summary.json`
