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
- Task 2 static tag absolute accuracy using anchor-locked frame transform, plus A/B/C frame-locking sanity table.
- Task 2 raw replay matrix: `5 Vx x 4 Tx x all8/noG`.
- Task 2 tag ground-truth correction: ID01/ID05 I-ball relabeling plus consensus ball-local `Iantenna` rebuild.
- Static-tag localization metric set: cm-scale percentile/outlier/per-axis tables plus CDF.
- Surveyed-anchor baseline: OptiTrack-truth anchor coordinates with raw, AutoPos-v4io-delay, and inter-anchor delaycal treatments, all8/noG.
- Additional diagnostics: delay common/differential decomposition, tag scale/radial/error-vector structure, worst-point residual fingerprints, anchor health, height/edge/facing stratifications, and single-anchor criticality.
- Task 3 VDOP maps at 100 mm, 50 mm, and 25 mm.
- Task 5 pair residual heatmaps and raw sweep asymmetry diagnostics.
- MC keep-k integrity and aggregate curves for the full 40-block MC5000 run.
- Task 4 bootstrap / MC percentile intervals for headline metrics.
- Task 6 temporal / thermal drift diagnostics over raw static `tr_all.csv`.
- Task 7 stratified keep-k fixed dropped-set replay with upper/lower composition.

## Pending

- Roto OptiTrack absolute validation, pending external OptiTrack processing.

## Important Conventions

- AutoPos layout: `x_mm,y_mm` horizontal, `z_mm` vertical, upper layer is negative `z`.
- Display height is `-z_mm`.
- OptiTrack TRC: Y is vertical.
- Layout alignment must allow reflection; proper-rotation-only Kabsch is a chirality sanity check, not the headline metric.
- G marker labeling is suspect; report all8 and noG for rigor, but the V4-io layout RMS changes only about 0.5 mm.
- Static tag truth uses corrected `Iantenna` for ID01/ID05 and Motive `Iantenna` for the other 22 captures.
- Static tag absolute accuracy must use the transform locked from anchors, never a transform fitted to tag truth.

## Scripts

- `scripts/layout_optitrack_compare.py`: anchor layout absolute comparison, all8 and noG.
- `scripts/static_tag_absolute_accuracy.py`: production-output static tag absolute error and A/B/C frame-locking sanity.
- `scripts/tag_ground_truth.py`: shared corrected static-tag OptiTrack ground-truth construction.
- `scripts/tag_localization_metrics.py`: cm-scale static-tag percentile, outlier-rate, per-axis bias, and CDF post-processing.
- `scripts/surveyed_anchor_baseline.py`: OptiTrack-frame surveyed-anchor control for static tag accuracy.
- `scripts/additional_diagnostics.py`: delay decomposition, tag error-structure diagnostics, anchor health, and single-anchor criticality.
- `scripts/vdop_map.py`: range-only VDOP geometry maps, optional range-bias appendix mode.
- `scripts/pair_residual_heatmap.py`: pairwise residual and raw asymmetry heatmaps.
- `scripts/bootstrap_ci.py`: bootstrap confidence intervals for headline metrics.
- `scripts/mc_integrity_aggregate.py`: MC keep-k completeness check and aggregate plots after 40/40 blocks finish.
- `scripts/static_tag_raw_replay_matrix.py`: raw static capture replay through `5 Vx x 4 Tx`.
- `scripts/temporal_drift_analysis.py`: raw-link drift diagnostics from static `tr_all.csv`.
- `scripts/stratified_keepk_replay.py`: fixed dropped-set keep-k replay with composition tracking.

Example:

```bash
python3 scripts/static_tag_absolute_accuracy.py --official-root ../..
```
