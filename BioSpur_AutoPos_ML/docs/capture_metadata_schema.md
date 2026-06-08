# Capture Metadata Schema

`DATASETS/processed/capture_metadata.csv` is the capture-level gate for using
raw AutoPos data correctly.

## Purpose

The table answers three separate questions:

1. Can this capture provide candidate layouts?
2. Can it provide multipath or residual-risk evidence?
3. Can it provide supervised localization-error labels?

These are intentionally separate. A no-tag multipath capture can be useful for
risk analysis while still being invalid for supervised ML training.

## Override File

Manual capture semantics live in:

```text
DATASETS/processed/capture_metadata_overrides.csv
```

Blank override cells leave the automatic inference unchanged.

Required columns:

- `capture_id`
- `environment_type`
- `condition`
- `has_tag_capture`
- `has_ground_truth`
- `label_quality`
- `recommended_use`
- `notes`

Recommended values:

- `environment_type`: `indoor_lab`, `outdoor`, `garage`, `basement`, `unknown`
- `condition`: `los`, `nlos`, `multipath`, `multipath_possible`, `controlled_unspecified`
- `has_tag_capture`: `true` only when static/roto/wand/tag replay data exists
- `has_ground_truth`: `true` only when OptiTrack or equivalent true trajectory exists

## Label Quality

- `real_ground_truth_validation`: real validation/calibration capture.
- `proxy_existing_field_evaluation`: field/static/roto/wand evidence but no true position labels.
- `multipath_unlabeled_no_tag`: useful for multipath risk, not for error labels.
- `unlabeled_geometry_only`: layout geometry only.
- `raw_unclassified`: inventory only until metadata is clarified.

## Training Gate

`train_allowed` is currently always `false`.

Reasons:

- Current real labels are too sparse and environment-specific.
- Proxy rows are ranking evidence, not supervised labels.
- No-tag multipath rows do not contain true tag-position error.

GPU training should only start after multiple captures have real trajectory
labels across different environments and conditions.
