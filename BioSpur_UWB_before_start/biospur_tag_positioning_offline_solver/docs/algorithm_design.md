# Algorithm Design

## Current Baseline

The current tag position solver is:

```text
robust nonlinear least-squares multilateration
```

For every frame, it uses all valid anchor ranges and solves a 3D tag position.
The solver requires at least four valid anchors.

The residual for anchor `i` is:

```text
r_i = ||p - a_i|| + d_anchor_i + d_tag - range_i
```

The current default is:

```text
d_tag = 0
```

The anchor delay term `d_anchor_i` is loaded from AutoPos `layout.json`.

## Baseline Iteration

The existing implementation uses Gauss-Newton:

1. Choose initial position.
   - first frame: anchor centroid
   - later frames: previous solved tag position
2. Compute range residuals.
3. Compute Jacobian rows from anchor-to-tag direction vectors.
4. Apply robust residual weights.
5. Solve the small 3x3 least-squares update.
6. Repeat for a small fixed number of iterations.

This is not a Kalman filter. The previous frame is used only as an initial
guess, not as a motion prior.

## Proposed Next Improvement

The next useful improvement is not Kalman filtering. It is a reliability layer:

```text
quality-aware robust weighted least-squares
+ residual-based anchor rejection
```

Suggested reliability state per tag/anchor:

```text
q_ema_i[t] = alpha * q_i[t] + (1 - alpha) * q_ema_i[t-1]
r_ema_i[t] = alpha * |residual_i[t]| + (1 - alpha) * r_ema_i[t-1]
```

Recommended starting value:

```text
alpha = 0.3
```

Use these only for weighting at first, not hard rejection.

## Residual-Based Anchor Rejection

Recommended logic:

1. Solve once using all valid anchors.
2. Compute residuals.
3. If one anchor has a large residual, try leave-one-out.
4. Accept leave-one-out only if:
   - enough anchors remain
   - RMS residual improves significantly
   - geometry is still usable

This should be logged clearly per frame.

## Important Cautions

- Do not reject anchors too aggressively when only 4-5 anchors are available.
- Do not use long memory; 10 Hz capture means a short EMA is enough.
- Do not hide bad geometry with smoothing.
- Do not mix per-tag delay estimation into the baseline until ground truth is
  available.

