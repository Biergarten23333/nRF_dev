# ML Candidate Table Readiness

Generated: `2026-05-30T00:15:48.022407+00:00`

## Summary

- Candidate rows: `67`
- Real OptiTrack labeled layouts: `5`
- Train-allowed rows: `0`

## Label Quality Counts

- `proxy_existing_field_evaluation`: 45
- `real_optitrack_sparse_validation_only`: 5
- `unlabeled_geometry_only`: 17

## Bewertung

- The table is ML-ready in schema, but not training-ready in data volume.
- Real labels are only 5 layouts from one OptiTrack environment, so they are validation/calibration only.
- Proxy labels from field summaries can support ranking analysis, not supervised generalization claims.
- GPU training is not justified yet; collect more real labeled captures first or use CPU-only exploratory models.
