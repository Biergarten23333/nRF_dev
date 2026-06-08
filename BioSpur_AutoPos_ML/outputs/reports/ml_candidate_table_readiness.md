# ML Candidate Table Readiness

Generated: `2026-06-07T20:55:05.925037+00:00`

## Summary

- Candidate rows: `117`
- Real OptiTrack labeled layouts: `5`
- No-tag multipath layout rows: `0`
- Train-allowed rows: `0`

## Label Quality Counts

- `proxy_existing_field_evaluation`: 95
- `real_optitrack_sparse_validation_only`: 5
- `unlabeled_geometry_only`: 17

## Bewertung

- The table is ML-ready in schema, but not training-ready in data volume.
- Real labels are only 5 layouts from one OptiTrack environment, so they are validation/calibration only.
- No-tag multipath rows are usable for risk analysis, not supervised error labels.
- Proxy labels from field summaries can support ranking analysis, not supervised generalization claims.
- GPU training is not justified yet; collect more real labeled captures first or use CPU-only exploratory models.
