# AutoPos Layout Evaluation Pipeline

## Goal

Improve AutoPos-generated UWB anchor layouts using geometry analysis, sweep
evaluation, and later optional ML-assisted scoring.

The first version should be a deterministic and interpretable ranking pipeline:

```text
raw captures -> cleaning -> layout DB -> feature extraction -> validation -> scoring -> top-N layouts
```

## Near-Term Phases

1. Build a unified layout database.
2. Extract geometry, DOP, coverage, and quality features.
3. Validate correlations against the limited OptiTrack dataset.
4. Implement an interpretable scoring engine.
5. Select top-N layouts for real-world testing.

## ML Direction

Future ML should predict expected layout error or confidence from geometry and
quality features. It should not replace the multilateration geometry solver.
