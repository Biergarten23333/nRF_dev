# AutoPos Layout Evaluation Pipeline Summary

Generated: `2026-06-07T20:55:08.090437+00:00`

## Scope

This is a deterministic layout evaluation and deployment-screening pipeline.
No model training is started by the CPU pipeline.

GPU policy remains unchanged: GPU0 can be used only for an explicitly justified future training job; GPU1 is reserved and must not be touched.

## Current Inputs

- Raw capture groups: `11`
- Raw files inventoried: `3083`
- Canonical layout files: `117`
- Capture metadata rows: `11`
- No-tag multipath captures: `1`

## Generated Tables

- `DATASETS/processed/raw_inventory.csv`: `3083` rows
- `DATASETS/processed/layout_index.csv`: `117` rows
- `DATASETS/processed/capture_metadata.csv`: `11` rows
- `DATASETS/features/layout_features.csv`: `117` rows
- `DATASETS/features/axis_dop_system_evaluation_by_layout.csv`: `117` rows
- `DATASETS/features/deployment_recommendation_matrix.csv`: `117` rows
- `DATASETS/features/ml_candidate_table.csv`: `117` rows

## Capture Label Quality

- `multipath_unlabeled_no_tag`: `1`
- `proxy_existing_field_evaluation`: `9`
- `real_ground_truth_validation`: `1`

## Capture Environments

- `garage`: `4`
- `indoor_lab`: `2`
- `outdoor`: `5`

## Deployment Class Distribution

- `A`: `8`
- `B`: `20`
- `C`: `55`
- `D`: `34`

## ML Candidate Label Quality

- `proxy_existing_field_evaluation`: `95`
- `real_optitrack_sparse_validation_only`: `5`
- `unlabeled_geometry_only`: `17`

## Training Gate

- Real OptiTrack layout labels: `5`
- Train-allowed rows: `0`
- Current decision: do not start GPU training.

Reason: real labels are still sparse and environment-limited. Proxy and no-tag multipath rows are valuable for ranking, robustness, and risk analysis, but they are not supervised localization-error labels.

## Immediate Engineering Work

1. Use `capture_metadata_overrides.csv` to label future NLOS, basement, and outdoor captures before rerunning the pipeline.
2. Keep full-anchor ranking and degraded-anchor deployment screening as separate decisions.
3. Treat no-tag multipath captures as residual/risk evidence, not ground-truth model targets.
4. Add new true-position captures before considering ML training.
