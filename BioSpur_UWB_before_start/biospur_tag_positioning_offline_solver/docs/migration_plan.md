# Migration Plan

The purpose of this module is to remove duplicated tag positioning logic from
AutoPos-specific scripts while preserving current behavior first.

## Phase 1: Behavior-Identical Extraction

Copy the existing UI trajectory solver logic from:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/scripts/export_capture_trajectory.py
```

into this module.

No numerical behavior should change in this phase.

Validation:

- run the old script and the new script on the same capture
- compare trajectory frame count
- compare per-frame `x/y/z`
- compare residual RMS and anchor count

Expected tolerance:

```text
position difference < 1e-6 mm
residual difference < 1e-6 mm
```

## Phase 2: Shared CLI

Add a script under:

```text
scripts/export_trajectory.py
```

The Flutter UI should eventually call this script instead of the old
AutoPos-local script.

Keep the old script as a wrapper during transition.

## Phase 3: Report Solver Unification

Move the official field report positioning path from:

```text
autopos_pipeline/outdoor_20260513/run_clean_full_compare.py
```

to this module.

At this point, UI trajectory and report evaluation should share the same core
solver.

## Phase 4: Reliability Layer

Add:

- anchor sigma weighting
- current-frame quality weighting
- short EMA quality memory
- residual EMA
- dynamic-stable soft residual weighting

Each enhancement must be switchable in configuration so older baseline results
can still be reproduced.

## Phase 5: Per-Tag Delay

Only after OptiTrack / Roto / Wand evidence is available, add:

```text
tag_delay_by_tag:
  BSF66F: ...
  BS2DCE: ...
```

This should be treated as calibration metadata, not guessed inside the solver.

## Field Safety Rule

Before Erlangen validation, do not replace the current working pipeline unless
the new module has passed behavior-identical comparison on real captures.
