# AutoPos Layout Evaluation Pipeline Summary

Generated after the latest CPU-only pipeline run.

Last checked: `2026-05-30T02:16:15+02:00`

## Scope

This is a deterministic layout evaluation pipeline, not ML training.

No GPU was used. Current scripts explicitly run CPU-only.

GPU0 is allowed only if the work reaches a justified training job; GPU1 remains
reserved and must not be touched.

## Inputs Found

- Raw capture groups: `3`
- Raw files inventoried: `2414`
- Canonical layout files: `67`
- OptiTrack validation capture: `28052026_Erlangen_Official`

## Generated Datasets

- `DATASETS/processed/raw_inventory.csv`
- `DATASETS/processed/capture_manifest.json`
- `DATASETS/processed/layout_database.jsonl`
- `DATASETS/processed/layout_index.csv`
- `DATASETS/features/layout_features.csv`
- `DATASETS/features/version_evaluation_features.csv`
- `DATASETS/features/dop_grid_features.csv`
- `DATASETS/features/dop_bound_rows.csv`
- `DATASETS/features/dop_summary_by_layout.csv`
- `DATASETS/features/layout_scores.csv`
- `DATASETS/features/layout_scores_v2.csv`
- `DATASETS/features/optitrack_layout_validation.csv`
- `DATASETS/features/optitrack_layout_correlations.csv`
- `DATASETS/features/optitrack_session_validation.csv`
- `DATASETS/features/optitrack_session_correlations.csv`
- `DATASETS/features/optitrack_stratified_error_summary.csv`
- `DATASETS/features/optitrack_stratified_dop_correlations.csv`
- `DATASETS/features/score_sensitivity_objectives.csv`
- `DATASETS/features/score_sensitivity_alignment.csv`
- `DATASETS/features/ml_candidate_table.csv`
- `outputs/reports/stratified_optitrack_bewertung.md`
- `outputs/reports/score_sensitivity_analysis.md`
- `outputs/reports/ml_candidate_table_readiness.md`
- `outputs/reports/bewertung_report.md`

## Current Counts

- Layout feature rows: `67`
- Layouts with matched existing evaluation metrics: `50`
- Geometry-only low-confidence rows: `17`
- DOP feature rows: `1739`
- DOP rows bound to a unique layout: `1128`
- DOP summary rows by layout/mask/grid: `47`
- OptiTrack layout validation rows: `10`
- OptiTrack session validation rows: `192`
- OptiTrack correlation rows: `150`
- Stratified OptiTrack summary rows: `280`
- Stratified DOP/error correlation rows: `2268`
- Layout Score v2 rows: `67`
- Score sensitivity objective rows: `20`
- ML candidate rows: `67`
- ML train-allowed rows: `0`
- Generated figures: `10`

## Anchor Geometry Assumptions Applied

- `A B C D` are the lower physical layer.
- `E F G H` are the upper physical layer.
- Both rings are expected counter-clockwise in native layout coordinates.
- Vertical pairs are `A-E`, `B-F`, `C-G`, `D-H`.
- Native AutoPos layouts use a z sign convention where physical height is
  approximately `-z_mm`.
- `layout_us_height.json` files are treated as already height-aligned.

After applying these assumptions, no layout is currently flagged as structurally
risky.

## Baseline Ranking Notes

Scores are normalized within each source group. They should not be compared
globally across different rooms or experiments yet.

Strong current candidates by source group:

- Erlangen official field check: `v2`, followed closely by `v3-lite`, then `v4-io`.
- Outdoor 20260513 `FULL-COMPARE-1000`: `v4-io-roto`.
- Outdoor 20260513 `FULL-COMPARE-500`: `v4-io-roto`.
- Outdoor 20260513 `FULL-COMPARE-500+500`: `v4-io-roto` consensus.

Score v2 refines this:

- Erlangen official: `v2` remains best, `v3-lite` remains near-tie backup.
- Erlangen `v4-io` is the only DOP-bound candidate and has good median/vertical
  behavior, but p95/RMS OptiTrack validation is weaker than `v2`/`v3-lite`.
- Outdoor 20260513 evaluated groups still repeatedly favor `v4-io-roto`.

Score sensitivity adds one important nuance:

- If the objective is balanced 3D error or tail robustness, `v2` wins.
- If the objective prioritizes median/vertical error, `v4-io` wins.
- Therefore the final deployment choice should state the objective explicitly.

The top-N canonical layout JSON exports are under `outputs/top_layouts/`.

## OptiTrack Validation Notes

Layout-level correlations are based on only 5 solver versions for `all8`, so
they are directional hints only.

The strongest layout-level signal in this first pass is:

- `layout_eval_roto_abs_deltaR_p95_mm` correlates strongly with OptiTrack 3D
  p95/RMS error in the Erlangen official dataset.

Session-level DOP correlations are currently based on Erlangen `v4-io` DOP
tables and should be treated as confounded by spatial sampling and vertical
scale effects. They are useful for feature calibration, not yet for training.

## Next Engineering Step

Review Score v2 manually, then improve calibration:

1. Decide whether Erlangen selection should prioritize median error, p95/RMS
   error, vertical error, or robustness under anchor drop.
2. Add more DOP provenance if future captures include layout-specific DOP grids.
3. Add per-location/height/facing stratified OptiTrack plots before changing
   scoring weights.
4. Build an ML-ready table only after the scoring labels are stable.

ML should still wait until the scoring/validation definitions are stable.

## Training Gate

GPU training has not been started.

Reason:

- Real OptiTrack labels exist for only `5` layouts from one environment.
- `ml_candidate_table.csv` is schema-ready but has `train_allowed=false` for
  all rows.
- GPU0 may be used later only for a justified training job; GPU1 must remain
  untouched.
