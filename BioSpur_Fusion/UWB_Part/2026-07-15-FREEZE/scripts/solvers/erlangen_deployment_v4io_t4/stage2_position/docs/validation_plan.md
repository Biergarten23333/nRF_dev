# Validation Plan

Validation must include both dataset sanity checks and reference comparison.

## Reference Source Check

The copied reference implementations under:

```text
reference_current_implementations/
```

must remain exact copies of the current working scripts unless intentionally
refrozen.

Validation script output:

```text
validation_outputs/outdoor_20260513/reference_source_copy_check.csv
```

## T1 Reference Numerical Check

T1 should match the current official Python solver first.

Validation script:

```bash
python3 biospur_tag_positioning_offline_solver/scripts/validate_outdoor_dataset.py
```

Expected output:

```text
validation_outputs/outdoor_20260513/official_reference_compare.json
```

Current checked result on `outdoor_20260513`, first 300 frames of ID02:

```text
max 3D diff:    2.41e-05 mm
median 3D diff: 3.23e-08 mm
RMS 3D diff:    1.63e-06 mm
```

This means T1 is behavior-compatible with the official reference for that test.

## T2-T4 Sanity Check

The same validation script runs T1-T4 and writes:

```text
validation_outputs/outdoor_20260513/t_method_summary.csv
```

T2/T3/T4 are not expected to match the old solver exactly because they add
quality weighting, residual history, dynamic-stability behavior, or adaptive
runtime policy.
