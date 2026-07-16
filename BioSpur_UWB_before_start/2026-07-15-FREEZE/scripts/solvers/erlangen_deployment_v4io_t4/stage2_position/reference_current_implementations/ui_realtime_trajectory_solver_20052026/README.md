# UI Realtime / Trajectory Export Solver

Copied source:

```text
export_capture_trajectory.py
```

Original source:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/scripts/export_capture_trajectory.py
```

Current user:

```text
flutter_ui_autopos/lib/main.dart
```

Main function of interest:

```text
solve_frame(...)
```

Current behavior:

- reads `layout.json` or `layout_us_height.json`
- reads `tr_all.csv` or `raw.log`
- groups data by `(tag, sweep)`
- requires at least four valid anchors
- solves each frame with Gauss-Newton multilateration
- uses `d_anchor_mm + tag_delay_mm`
- uses Huber-like residual weighting
- warm-starts each tag from its previous solved position

This implementation is optimized for quick UI trajectory export. It is the most
important reference for UI behavior.

