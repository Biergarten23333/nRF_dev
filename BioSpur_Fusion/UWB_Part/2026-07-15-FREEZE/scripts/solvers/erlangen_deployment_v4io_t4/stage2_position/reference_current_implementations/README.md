# Current Solver Implementations

This folder contains exact copied references of the two tag-positioning solver
paths currently used in the BioSpur/AutoPos workspace.

These files are **not** the new shared solver API yet. They are kept here so the
current behavior is easy to inspect, compare, and migrate without hunting across
the repository.

## Folders

```text
ui_realtime_trajectory_solver_20052026/
  export_capture_trajectory.py

official_report_field_solver_13052026/
  run_clean_full_compare.py
  run_v4io_field_check.py
```

## Meaning

- `ui_realtime_trajectory_solver`: the solver used by the Flutter UI for
  playback/realtime trajectory export.
- `official_report_field_solver`: the solver and wrapper used for field checks
  and static/roto/wand report metrics.

## Migration Rule

First migration step:

```text
new shared solver output == copied reference output
```

Only after behavior is identical should we add quality-aware weighting,
residual-based anchor rejection, or per-tag delay support.
