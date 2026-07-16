# Official Report / Field Check Solver

Copied sources:

```text
run_clean_full_compare.py
run_v4io_field_check.py
```

Original sources:

```text
autopos_pipeline/outdoor_20260513/run_clean_full_compare.py
autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py
```

Main functions of interest:

```text
run_clean_full_compare.py:
  solve_position_fast(...)
  solve_positions(...)

run_v4io_field_check.py:
  main(...)
```

Current behavior:

- solves V4-io anchor layout from staged AutoPos sweep data
- evaluates static/roto/wand captures
- uses the report-side tag positioning solver
- uses anchor delay from layout
- uses `tag_delay_mm`, usually `0`
- uses anchor sigma and Huber-like weighting
- optionally applies F/G/H ultrasound height alignment after layout solve

This is the current source of truth for formal field-check and report metrics.

