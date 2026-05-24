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
    current_solver_inventory.md
    migration_plan.md
  reference_current_implementations/
    ui_realtime_trajectory_solver/
      export_capture_trajectory.py
    official_report_field_solver/
      run_clean_full_compare.py
      run_v4io_field_check.py
  scripts/
    README.md
  tests/
    README.md
```

## Non-goals For The First Step

- Do not add Kalman filtering yet.
- Do not change existing UI or report behavior during the first extraction.
- Do not estimate per-tag delay until OptiTrack / Roto / Wand evidence is ready.
- Do not make this an AutoPos-only module.

## Intended Ownership

This module belongs to the broader BioSpur positioning stack. It should be used
by:

- Flutter UI trajectory export
- Erlangen field checks
- static tag repeatability analysis
- RotoArm analysis
- Wand analysis
- future BioSpur multi-tag motion capture analysis
