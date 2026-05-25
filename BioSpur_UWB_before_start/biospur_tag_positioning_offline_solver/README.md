# BioSpur Tag Positioning Offline Solver

This folder is the planned standalone offline positioning module for BioSpur tag
captures.

The goal is to keep tag positioning separate from AutoPos anchor
self-calibration. AutoPos produces an anchor layout; this module consumes that
layout plus tag-to-anchor ranges and produces tag trajectories, residuals, and
quality diagnostics.

## Scope

Inputs:

- `layout.json` or `layout_us_height.json`
- `tr_all.csv` or `raw.log` from broadcast SS-TWR tag capture
- optional `anchor_sigma.json`
- optional per-tag delay configuration, added later

Outputs:

- solved per-frame tag trajectory
- per-frame anchor count
- per-frame and per-anchor residuals
- quality / reliability summary
- export files for the Flutter UI and offline reports

## Current Source Implementations

The current positioning logic still lives in two places. Exact copied
references are stored under:

```text
reference_current_implementations/
```

Original sources:

- UI / playback trajectory:
  `autopos_pipeline/erlangen_20260528_mocap/solver/scripts/export_capture_trajectory.py`
- field report / static-roto-wand evaluation:
  `autopos_pipeline/outdoor_20260513/run_clean_full_compare.py`

Both use robust nonlinear least-squares multilateration. The first migration
step should copy behavior into this module without changing numerical results.

See:

```text
docs/current_solver_inventory.md
```

## Solver Model

For each frame, the solver receives valid ranges from at least four anchors and
solves:

```text
min_p sum_i w_i * (||p - anchor_i|| + d_anchor_i + d_tag - range_i)^2
```

where:

- `p` is the unknown tag position.
- `anchor_i` comes from the calibrated layout.
- `d_anchor_i` comes from the AutoPos layout solver.
- `d_tag` is currently normally `0`.
- `w_i` is the anchor/range reliability weight.

The current baseline is Gauss-Newton robust least-squares with Huber-like
outlier handling.

## Planned Structure

```text
biospur_tag_positioning_offline_solver/
  README.md
  biospur_tag_positioning_offline_solver/
    __init__.py
  docs/
    algorithm_design.md
    c_api.md
    current_solver_inventory.md
    migration_plan.md
    t_series_design.md
    validation_plan.md
  reference_current_implementations/
    ui_realtime_trajectory_solver_20052026/
      export_capture_trajectory.py
    official_report_field_solver_13052026/
      run_clean_full_compare.py
      run_v4io_field_check.py
  scripts/
    README.md
    export_trajectory_t.py
    validate_outdoor_dataset.py
  tests/
    README.md
```

## Non-goals For The First Step

- Do not add Kalman filtering yet.
- Do not change existing UI or report behavior during the first extraction.
- Do not estimate per-tag delay until OptiTrack / Roto / Wand evidence is ready.
- Do not make this an AutoPos-only module.

## Current T-Series Implementation

This module now contains a C core and Python wrapper for:

```text
T1: robust WLS multilateration
T2: quality-aware robust WLS
T3: dynamic-stable robust WLS for Roto/body-motion captures
T4: adaptive full-anchor/low-redundancy policy for dynamic robustness
T4_V6_IMU_GATE: T4 v5 plus accelerometer dynamic-index prior gating
```

T1 is intended to be behavior-compatible with the current official Python
solver. T2-T4 are forward-looking extensions and must be validated before being
used for official reports. The current best T4 candidate uses memory-free T1
when all 8 anchors are present and T3-style dynamic stabilization when runtime
anchor redundancy drops below 8.

`T4_V6_IMU_GATE` is the planned IMU-assisted dynamic variant. It is backward
compatible with old captures: if a frame has no valid IMU summary, it falls back
to the same behavior as T4 v5. When valid IMU summary is available and anchor
redundancy is below 8, it weakens the previous-position prior according to
`std(|a|)` from the Tag accelerometer.

See:

```text
docs/version_chain.md
```

Run validation:

```bash
python3 biospur_tag_positioning_offline_solver/scripts/validate_outdoor_dataset.py
```

## Intended Ownership

This module belongs to the broader BioSpur positioning stack. It should be used
by:

- Flutter UI trajectory export
- Erlangen field checks
- static tag repeatability analysis
- RotoArm analysis
- Wand analysis
- future BioSpur multi-tag motion capture analysis
