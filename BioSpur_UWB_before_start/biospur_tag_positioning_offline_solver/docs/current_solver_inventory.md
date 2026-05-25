# Current Tag Solver Inventory

There are currently two active tag-positioning paths.

## 1. UI / Trajectory Export Path

Reference copy:

```text
biospur_tag_positioning_offline_solver/reference_current_implementations/ui_realtime_trajectory_solver_20052026/export_capture_trajectory.py
```

Original source:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/scripts/export_capture_trajectory.py
```

Called by:

```text
flutter_ui_autopos/lib/main.dart
```

Core function:

```text
solve_frame(...)
```

Purpose:

```text
layout + capture -> trajectory for UI visualization / quick check
```

Important note:

This path is visualization and field-debug oriented. Raw capture logs remain
the source of truth.

## 2. Official Field Report Path

Reference copy:

```text
biospur_tag_positioning_offline_solver/reference_current_implementations/official_report_field_solver_13052026/run_clean_full_compare.py
```

Original source:

```text
autopos_pipeline/outdoor_20260513/run_clean_full_compare.py
```

Called by:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py
```

Core functions:

```text
solve_position_fast(...)
solve_positions(...)
```

Purpose:

```text
layout + static/roto/wand captures -> official metrics
```

## Shared Mathematical Model

Both paths solve the same basic multilateration problem:

```text
min_p sum_i w_i * (||p - anchor_i|| + d_anchor_i + d_tag - range_i)^2
```

Differences today:

- UI path is simpler and optimized for fast trajectory export.
- official path includes report-side anchor sigma weighting and broader metric
  generation.

## Desired End State

Both paths should call one shared implementation from this module:

```text
biospur_tag_positioning_offline_solver/
  biospur_tag_positioning_offline_solver/
    multilateration.py
    io.py
    quality.py
    residuals.py
```

The copied files in `reference_current_implementations/` should remain as
behavior references until the migration is proven.
