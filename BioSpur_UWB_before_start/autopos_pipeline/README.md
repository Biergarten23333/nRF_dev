# AutoPos Pipeline

This is the active workspace for full-chain AutoPos experiments.

Goal: run the complete chain from live inter-anchor sweep capture to layout solve, validation, and report output.

## Directory Layout

- `scripts/`
  - Pipeline wrappers and run helpers specific to full-chain experiments.
- `configs/`
  - Run presets, anchor maps, solver parameters, and hardware profile configs.
- `logs/`
  - Raw capture outputs and per-run artifacts.
- `reports/`
  - Human-readable summaries, comparisons, plots, and final decisions.
- `docs/`
  - Notes, runbooks, and debugging checkpoints for the pipeline.
- `data/`
  - Stable input data used by the pipeline, such as known reference points or frozen pair datasets.

## Active Baseline Context

- SS-TWR / Alt SS-TWR is archived in `../SS-TWR/`.
- Old AutoPos V1/V2/V3 bundles are archived in `../AutoPos_archive/`.
- Active shared AutoPos implementation scripts remain in `../scripts/`.

## Suggested Full-Chain Flow

1. Force/verify all anchors in the expected AutoPos runtime state.
2. Run inter-anchor sweep capture.
3. Extract pair distances into `pairs_all.csv`.
4. Solve layout with the current best solver, likely V3-box or V3-full.
5. Validate layout against holdout/reference data.
6. Generate report and candidate `uwb_anchor_layout.c` update.

## Naming Convention

Use timestamped run directories:

```text
logs/full_chain_YYYYMMDD_HHMMSS/
reports/full_chain_YYYYMMDD_HHMMSS.md
```
