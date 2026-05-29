# Official Extra Analysis

This directory contains the reproducible analysis layer for the 2026-05-28 Erlangen official dataset.

## Structure

- `scripts/`: analysis scripts, each logging args, seed, axis convention, and source hashes to `run_meta.json`.
- `tables/`: CSV and Markdown analysis outputs.
- `figs/`: report figures at 150 dpi.
- `report.md`: running report draft.
- `run_meta.json`: append-only provenance log.

## Completed So Far

- Task 1 OptiTrack vs AutoPos anchor layout absolute comparison.
- Task 3 VDOP maps at 100 mm, 50 mm, and 25 mm.
- Task 5 pair residual heatmaps and raw sweep asymmetry diagnostics.
- Task 4 bootstrap CIs for non-MC headline metrics.

## Pending

- MC keep-k integrity and aggregate plots once the active MC run reaches 40/40.
- Static tag absolute accuracy across 5 anchor-layout solvers x 4 tag solvers.
- Roto OptiTrack absolute validation, pending external OptiTrack processing.
- Stratified keep-k analysis.

## Important Conventions

- AutoPos layout: `x_mm,y_mm` horizontal, `z_mm` vertical, upper layer is negative `z`.
- Display height is `-z_mm`.
- OptiTrack TRC: Y is vertical.
- Layout alignment must allow reflection; proper-rotation-only Kabsch is a chirality sanity check, not the headline metric.
- G marker labeling is suspect; report all8 and noG.
