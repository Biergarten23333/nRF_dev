# Algorithm Design

## Current Baseline / T1

The current tag position solver is represented by T1:

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

## T2-T3 Improvements

The next useful improvement is not a full Kalman filter. It is a reliability
and dynamic-stability layer:

```text
quality-aware robust weighted least-squares
+ persistent-residual soft downweighting
+ weak previous-position prior for dynamic captures
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

Use these for weighting, not hard single-frame rejection. RotoArm results show
that hard leave-one-out can reduce range residuals while making dynamic
trajectory consistency worse.

## T-Series Summary

```text
T1: robust WLS multilateration
T2: T1 + quality-aware weighting
T3: T2 + dynamic-stable soft residual weighting + weak motion prior
T4: adaptive policy; full-anchor frames use memory-free T1, low-redundancy
    frames use T3-style stabilization
T4_V6_IMU_GATE: T4 plus accelerometer dynamic-index gating of the
    low-redundancy previous-position prior
```

## T4 Candidate Rationale

Outdoor Monte Carlo testing showed two separate regimes:

- With all 8 anchors available, memory-free robust WLS is often better for
  RotoArm center consistency and injected persistent NLOS bias because it does
  not carry bias through the previous-position prior.
- With fewer than 8 anchors, T3-style temporal stabilization is much better
  because geometry becomes the dominant failure mode.

The current T4 candidate therefore switches by runtime redundancy:

```text
n >= 8 anchors: solve as T1, without previous-position prior
n < 8 anchors: solve as T3, with weak previous-position prior
```

Several alternatives were tested and rejected:

- signed residual memory: helped some static tails, but degraded Roto metrics
- hard leave-one-out rejection: reduced residuals but damaged trajectory
  consistency
- motion-gated switching: did not outperform the simpler redundancy switch
- Tukey bisquare on the full-anchor path: degraded clean keep-8 and Roto
  metrics, even with same-frame Huber initialization
- continuous prior blend by anchor count: weakened the low-redundancy prior and
  degraded keep-7/6/5 robustness

## Planned IMU-Assisted T4_V6_IMU_GATE

DWM1001C Tags contain a LIS2DH12 accelerometer, but Tag orientation is unknown.
The planned IMU input therefore does not use raw `ax/ay/az` as a world-frame
acceleration. Instead, the Tag firmware computes a short-window summary of the
acceleration magnitude:

```text
|a| = sqrt(ax^2 + ay^2 + az^2)
sigma_acc = std(|a|)
```

The host capture stores one IMU summary per UWB frame. The solver uses only
`acc_norm_std_mg`; raw 100 Hz IMU samples are not required by the offline
solver.

For low-redundancy frames (`n < 8`), T4_V6_IMU_GATE scales the T3-style
previous-position prior:

```text
prior_scale = exp(-ln(2) * sigma_acc_mps2 / half_sigma_mps2)
sigma_prior_used = sigma_prior_base / sqrt(prior_scale)
```

The default `half_sigma_mps2` is 0.5, so `sigma_acc ~= 0.5 m/s^2` halves the
prior weight. With all 8 anchors available, T4_V6_IMU_GATE keeps the T4 v5
memory-free T1 path. If no valid IMU summary is available, the method falls
back to T4 v5 behavior.

## Important Cautions

- Do not reject anchors too aggressively when only 4-5 anchors are available.
- Do not use single-frame hard rejection as the default dynamic solver.
- Do not use long memory; 10 Hz capture means a short EMA is enough.
- Do not hide bad geometry with smoothing.
- Do not mix per-tag delay estimation into the baseline until ground truth is
  available.
