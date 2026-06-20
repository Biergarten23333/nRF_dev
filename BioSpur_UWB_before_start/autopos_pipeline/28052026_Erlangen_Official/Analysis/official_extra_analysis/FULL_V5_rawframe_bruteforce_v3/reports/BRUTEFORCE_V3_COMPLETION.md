# Brute Force V3 Completion

Generated: 2026-06-19T00:21:22

Achievement level: `LEVEL_2`
Paper decision: new range-histogram LOS contribution

## Runtime

Completed-stage wall time: `2256.6 s` (`37.6 min`).

This is below the prompt's 60 minute target because Stage 2 and Stage 3 were fully vectorized/batched on GPU and Stage 4 BA was gate-skipped. Stage 1 did the requested exhaustive double-precision mixture sweep: 8 models x 192 links x 100 initializations with 500 Adam steps plus 200 L-BFGS steps.

Runtime table: `tables/cumulative_runtime_summary.csv`.

## Master Ladder

| method | all_data_median | loo_median | bootstrap_ci | level |
| --- | --- | --- | --- | --- |
| V4 + LOO locked | nan | 57.921 |  | locked baseline |
| V5 baseline locked | nan | 67.849 |  | locked baseline |
| B0 oracle lower bound | 44.596 | nan |  | oracle ceiling |
| Stage2 best all-data | 40.451 | nan |  | transductive |
| Stage3 best lower_trim_20 / V5 / huber30 | 43.942 | 44.485 | [33.4, 82.8] | HONEST |

## GPU Log Summary

| gpu_index | mean_util | max_util | max_memory_mb |
| --- | --- | --- | --- |
| 0.000 | 36.125 | 80.000 | 376.000 |
| 1.000 | 15.725 | 78.000 | 206.000 |
