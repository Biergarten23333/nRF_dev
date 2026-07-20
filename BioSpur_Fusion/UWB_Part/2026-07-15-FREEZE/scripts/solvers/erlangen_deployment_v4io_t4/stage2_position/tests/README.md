# Tests

Planned tests for the standalone positioning module.

Minimum useful tests:

- layout JSON loading preserves anchor coordinates and `d_anchor_mm`
- `tr_all.csv` grouping produces one frame per `(tag, sweep)`
- old solver and new solver match on a recorded capture
- fewer than four anchors returns no solution
- T3 keeps dynamic anchor usage stable and does not hard-reject single-frame anchors
- T4 uses the T1 path for full 8-anchor frames and the T3 path below 8 anchors
- `layout_us_height.json` and raw `layout.json` are both accepted

Use recorded small captures from Erlangen/outdoor data as fixtures only after
they are small enough to keep in the repository.
